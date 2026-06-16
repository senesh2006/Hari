"""Vercel Python serverless function: generic Kapruka MCP tool access.

GET  /api/tool                       -> list every tool the MCP exposes (+schema)
POST /api/tool {"name","arguments"}  -> invoke one tool and return its result

This surfaces *all* of the Kapruka MCP's tools (search, categories, orders,
etc.) without hard-coding any of them — the catalogue is discovered at runtime.
Product-shaped results are also returned as a normalized `products` array so the
UI can render cards.

Standard library only — no third-party packages.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

MCP_URL = os.environ.get("KAPRUKA_MCP_URL", "https://mcp.kapruka.com/mcp")
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT = float(os.environ.get("KAPRUKA_MCP_TIMEOUT", "30"))
USER_AGENT = os.environ.get(
    "KAPRUKA_MCP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
SITE_BASE = os.environ.get("KAPRUKA_SITE_BASE", "https://www.kapruka.com")

# Tools whose names suggest a side effect (orders/payments). The UI requires an
# explicit confirmation before invoking these.
WRITE_HINTS = ("order", "checkout", "buy", "purchase", "pay", "payment", "create", "place", "cancel")

_TOOLS_CACHE = None


# === MCP plumbing (self-contained so the function bundles cleanly) ===========
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
                "clientInfo": {"name": "kapruka-tool", "version": "1.0.0"},
            },
            1,
        )
        self._send("notifications/initialized", {}, None)

    def list_tools(self) -> list:
        return self._send("tools/list", {}, 2).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments}, 3).get("result", {})
        parts = []
        for block in result.get("content", []) or []:
            parts.append(block.get("text", "") if block.get("type") == "text" else json.dumps(block))
        return "\n".join(parts) if parts else json.dumps(result)


# === Product normalization (mirrors api/search.py) ===========================
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


def _first(d, keys):
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


def _looks_like_product(d):
    if not isinstance(d, dict) or _first(d, NAME_KEYS) is None:
        return False
    return any(_first(d, ks) is not None for ks in (PRICE_KEYS, URL_KEYS, IMAGE_KEYS))


def _normalize_product(d):
    return {
        "name": _first(d, NAME_KEYS),
        "price": _first(d, PRICE_KEYS),
        "currency": _first(d, CURRENCY_KEYS),
        "image": _abs_url(_first(d, IMAGE_KEYS)),
        "url": _abs_url(_first(d, URL_KEYS)),
        "description": _first(d, DESC_KEYS),
    }


def _walk(obj, found):
    if isinstance(obj, dict):
        if _looks_like_product(obj):
            found.append(_normalize_product(obj))
            return
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, found)


def _coerce_json(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if 0 <= start < end:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    continue
    return None


def _norm_url(u):
    if not isinstance(u, str) or not u.strip():
        return None
    s = re.sub(r"^www\.", "", re.sub(r"^https?://", "", u.strip().lower()))
    s = s.split("?")[0].split("#")[0]
    return s.rstrip("/") or None


def _norm_text(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def extract_products(output: str) -> list:
    data = _coerce_json(output)
    if data is None:
        return []
    found = []
    _walk(data, found)
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


# === Operations ==============================================================
def list_tools_payload() -> dict:
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        mcp = MCPSession()
        mcp.initialize()
        _TOOLS_CACHE = mcp.list_tools()
    tools = []
    for t in _TOOLS_CACHE:
        name = t.get("name", "")
        tools.append(
            {
                "name": name,
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
                "writes": any(h in name.lower() for h in WRITE_HINTS),
            }
        )
    return {"ok": True, "url": MCP_URL, "tools": tools}


def invoke_tool(name: str, arguments: dict) -> dict:
    mcp = MCPSession()
    mcp.initialize()
    output = mcp.call_tool(name, arguments or {})
    return {
        "ok": True,
        "tool": name,
        "arguments": arguments or {},
        "output": output,
        "products": extract_products(output),
    }


# === HTTP handler ============================================================
class handler(BaseHTTPRequestHandler):
    def _respond(self, code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        try:
            self._respond(200, list_tools_payload())
        except Exception as exc:
            self._respond(502, {"ok": False, "error": str(exc)})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._respond(400, {"ok": False, "error": "Body must be JSON."})
        name = data.get("name")
        if not name:
            return self._respond(400, {"ok": False, "error": "Missing 'name'."})
        try:
            self._respond(200, invoke_tool(name, data.get("arguments") or {}))
        except Exception as exc:
            self._respond(502, {"ok": False, "error": str(exc)})
