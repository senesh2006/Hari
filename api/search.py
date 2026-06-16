"""Vercel Python serverless function: AI product search over the Kapruka MCP.

POST /api/search   body: {"query": "<natural language requirements>"}
GET  /api/search?q=<natural language requirements>

Pipeline (an agentic tool-calling loop):
  1. Open an MCP session to the Kapruka server and discover its tools.
  2. Hand those tools to an NVIDIA NIM model (OpenAI-compatible chat API).
  3. The model picks tools + arguments from the user's requirements; we execute
     them against the MCP and feed results back until the model answers.
  4. Return the model's ranked answer plus the raw tool results.

Tools are discovered at runtime, so this works regardless of the exact tool
names/schemas the Kapruka MCP exposes.

Requires the NVIDIA_API_KEY environment variable (NIM needs auth). The Kapruka
MCP itself needs no auth.

Standard library only — no third-party packages.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

# --- Kapruka MCP config ------------------------------------------------------
MCP_URL = os.environ.get("KAPRUKA_MCP_URL", "https://mcp.kapruka.com/mcp")
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT = float(os.environ.get("KAPRUKA_MCP_TIMEOUT", "30"))
# Cloudflare bans the default "Python-urllib" agent (error 1010).
USER_AGENT = os.environ.get(
    "KAPRUKA_MCP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

# --- NVIDIA NIM config -------------------------------------------------------
NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Default to a fast tool-calling model; override with NVIDIA_NIM_MODEL (e.g.
# "meta/llama-3.3-70b-instruct") for higher quality at the cost of latency.
NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.1-8b-instruct")
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY")
NIM_TIMEOUT = float(os.environ.get("NVIDIA_NIM_TIMEOUT", "60"))
MAX_TOOL_ROUNDS = int(os.environ.get("SEARCH_MAX_ROUNDS", "3"))

# Tool schemas are static; cache them across warm invocations to skip a
# tools/list round-trip on every request.
_TOOLS_CACHE = None

SYSTEM_PROMPT = (
    "You are a shopping assistant for Kapruka, a Sri Lankan online store. "
    "Use the available tools to find real products that match the user's "
    "requirements (budget, category, occasion, recipient, etc.). Call the "
    "search tools as needed, then recommend the best matches. For each "
    "recommendation give the product name, price, and a link if available, "
    "and a one-line reason it fits. If nothing matches, say so honestly. "
    "Only rely on data returned by the tools — never invent products or prices. "
    "Never list the same product twice: if the tools return duplicate or "
    "near-identical items (same product, same price, or same link), keep only "
    "one of them. Be efficient: issue a single well-chosen search call when "
    "possible and avoid repeating the same search."
)


# === MCP plumbing ============================================================
def _loads(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Non-JSON response (host reachable?): {text[:300]}") from exc


def _parse_body(content_type: str, raw: bytes):
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload and payload != "[DONE]":
                    return _loads(payload)
        raise ValueError(f"No JSON data in SSE stream: {text[:300]}")
    return _loads(text)


class MCPSession:
    """A minimal MCP Streamable HTTP client session."""

    def __init__(self, url: str = MCP_URL):
        self.url = url
        self.session_id = None

    def _send(self, method, params, request_id):
        message = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            message["id"] = request_id
        if params is not None:
            message["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = urllib.request.Request(
            self.url, data=json.dumps(message).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status, hdrs, raw = resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            status, hdrs, raw = exc.code, exc.headers, exc.read()

        self.session_id = hdrs.get("Mcp-Session-Id") or self.session_id
        body = _parse_body(hdrs.get("Content-Type", ""), raw)
        if status >= 400:
            raise ValueError(f"MCP {method} -> HTTP {status}: {body}")
        if body and "error" in body:
            raise ValueError(f"MCP {method} error: {body['error']}")
        return body

    def initialize(self):
        self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kapruka-nim-search", "version": "1.0.0"},
            },
            1,
        )
        self._send("notifications/initialized", {}, None)

    def list_tools(self) -> list:
        body = self._send("tools/list", {}, 2)
        return body.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        body = self._send("tools/call", {"name": name, "arguments": arguments}, 3)
        result = body.get("result", {})
        parts = []
        for block in result.get("content", []) or []:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(json.dumps(block))
        return "\n".join(parts) if parts else json.dumps(result)


def mcp_tools_to_openai(tools: list) -> list:
    """Convert MCP tool descriptors into OpenAI/NIM function-tool format."""
    converted = []
    for t in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


# === Product extraction ======================================================
# Field aliases used to recognise product-shaped objects in arbitrary MCP
# tool output, so the UI can render cards without knowing the exact schema.
NAME_KEYS = ("name", "title", "product_name", "productname", "productName", "product")
PRICE_KEYS = (
    "price", "price_lkr", "priceLkr", "amount", "selling_price", "sellingPrice",
    "sale_price", "salePrice", "cost", "mrp", "unit_price", "unitPrice",
)
IMAGE_KEYS = (
    "image", "image_url", "imageUrl", "imageURL", "img", "thumbnail", "thumb",
    "picture", "photo", "image_link", "imageLink",
)
URL_KEYS = (
    "url", "link", "product_url", "productUrl", "href", "permalink",
    "product_link", "productLink", "page",
)
DESC_KEYS = ("description", "desc", "summary", "details", "short_description")
CURRENCY_KEYS = ("currency", "currency_code", "currencyCode")

SITE_BASE = os.environ.get("KAPRUKA_SITE_BASE", "https://www.kapruka.com")


def _first(d: dict, keys) -> object:
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, "", []):
            return v
    return None


def _abs_url(u):
    if not isinstance(u, str) or not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return SITE_BASE.rstrip("/") + u
    return u


def _looks_like_product(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    if _first(d, NAME_KEYS) is None:
        return False
    return any(_first(d, ks) is not None for ks in (PRICE_KEYS, URL_KEYS, IMAGE_KEYS))


def _normalize_product(d: dict) -> dict:
    return {
        "name": _first(d, NAME_KEYS),
        "price": _first(d, PRICE_KEYS),
        "currency": _first(d, CURRENCY_KEYS),
        "image": _abs_url(_first(d, IMAGE_KEYS)),
        "url": _abs_url(_first(d, URL_KEYS)),
        "description": _first(d, DESC_KEYS),
    }


def _walk_products(obj, found: list) -> None:
    if isinstance(obj, dict):
        if _looks_like_product(obj):
            found.append(_normalize_product(obj))
            return  # don't descend into a product's own sub-fields
        for v in obj.values():
            _walk_products(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_products(v, found)


def _coerce_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # The output may wrap JSON in prose; grab the outermost [...] or {...}.
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if 0 <= start < end:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None


def _norm_url(u) -> str | None:
    """Canonicalize a URL for dedup: drop scheme, www, query and trailing slash."""
    if not isinstance(u, str) or not u.strip():
        return None
    s = u.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?")[0].split("#")[0]
    return s.rstrip("/") or None


def _norm_text(t) -> str:
    """Collapse to lowercase alphanumerics+spaces for fuzzy dedup."""
    if t is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def extract_products(results: list) -> list:
    """Pull normalized, de-duplicated product objects out of MCP tool outputs.

    The agent often calls the search tool several times, so the same product
    reappears (sometimes with a different query string on its URL). Dedup by
    canonical URL first, then by name+price for items that lack a URL.
    """
    found = []
    for r in results:
        data = _coerce_json(r.get("output", ""))
        if data is not None:
            _walk_products(data, found)

    seen_urls, seen_names, unique = set(), set(), []
    for p in found:
        url_key = _norm_url(p.get("url"))
        name_key = _norm_text(p.get("name")) + "|" + _norm_text(p.get("price"))
        if url_key and url_key in seen_urls:
            continue
        if name_key.strip("|") and name_key in seen_names:
            continue
        if url_key:
            seen_urls.add(url_key)
        seen_names.add(name_key)
        unique.append(p)
    return unique


# === NVIDIA NIM plumbing =====================================================
def nim_chat(messages: list, tools: list) -> dict:
    if not NIM_API_KEY:
        raise PermissionError(
            "NVIDIA_API_KEY is not set. Add it in your Vercel project's "
            "Environment Variables (get a key at https://build.nvidia.com)."
        )
    payload = {
        "model": NIM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 700,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        f"{NIM_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NIM_API_KEY}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=NIM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"NVIDIA NIM HTTP {exc.code}: {detail[:500]}") from exc


# === Orchestration ===========================================================
def search(query: str) -> dict:
    global _TOOLS_CACHE
    mcp = MCPSession()
    mcp.initialize()
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = mcp.list_tools()
    tools = _TOOLS_CACHE
    openai_tools = mcp_tools_to_openai(tools)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    trace = []
    results = []

    for _ in range(MAX_TOOL_ROUNDS):
        completion = nim_chat(messages, openai_tools)
        message = completion["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        # Re-append the assistant turn so the model keeps its own context.
        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                **({"tool_calls": tool_calls} if tool_calls else {}),
            }
        )

        if not tool_calls:
            return {
                "ok": True,
                "query": query,
                "model": NIM_MODEL,
                "answer": message.get("content", ""),
                "products": extract_products(results),
                "tools_available": [t.get("name") for t in tools],
                "tool_calls": trace,
                "results": results,
            }

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                output = mcp.call_tool(name, args)
            except Exception as exc:  # feed tool errors back to the model
                output = f"ERROR calling {name}: {exc}"
            trace.append({"tool": name, "arguments": args})
            results.append({"tool": name, "arguments": args, "output": output})
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id"), "content": output}
            )

    return {
        "ok": True,
        "query": query,
        "model": NIM_MODEL,
        "answer": "Stopped after the maximum number of tool rounds without a "
        "final answer. Try a more specific query.",
        "products": extract_products(results),
        "tools_available": [t.get("name") for t in tools],
        "tool_calls": trace,
        "results": results,
    }


# === HTTP handler ============================================================
class handler(BaseHTTPRequestHandler):
    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _run(self, query: str):
        if not query or not query.strip():
            return self._respond(400, {"ok": False, "error": "Missing 'query'."})
        try:
            self._respond(200, search(query.strip()))
        except PermissionError as exc:
            self._respond(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._respond(502, {"ok": False, "error": str(exc)})

    def do_GET(self):  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(self.path).query)
        self._run((params.get("q") or [""])[0])

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._respond(400, {"ok": False, "error": "Body must be JSON."})
        self._run(data.get("query", ""))
