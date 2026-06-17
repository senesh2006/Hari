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

import ast
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
    "You are a thoughtful gift & shopping concierge for Kapruka, a Sri Lankan "
    "online store. Do NOT just search the user's literal words. First REASON "
    "about the recipient and the occasion, then brainstorm several DIFFERENT "
    "concrete product types that would make good gifts. For example, for a "
    "50-year-old mother's birthday think across categories: perfume, a flower "
    "bouquet, a chocolate hamper, a wrist watch, a handbag, a saree, jewellery, "
    "a scented candle set, a personalized photo frame, a tea/spa gift set.\n\n"
    "MANDATORY search rules:\n"
    "- Run a SEPARATE kapruka_search_products call for EACH idea, using a short "
    "specific noun as q (e.g. q='perfume', q='wrist watch', q='handbag', "
    "q='flower bouquet'). Do at least 4 searches covering DIFFERENT categories.\n"
    "- NEVER search vague phrases like 'birthday gift', 'gift for mom', or "
    "'gifts'. They return generic cakes, not a thoughtful spread.\n"
    "- Do NOT pass a category filter unless you got the exact category name from "
    "kapruka_list_categories — a wrong category returns zero results.\n"
    "- Cakes are fine as ONE option, but the final list must be VARIED: do not "
    "recommend 5 items of the same type. Aim for a mix across categories.\n\n"
    "After searching, curate ~5 of the best real products spanning different "
    "categories. For each give the name, price, link, and a one-line reason it "
    "suits the recipient.\n\n"
    "Ask before guessing: if information needed to make a genuinely good "
    "recommendation is missing — such as the budget, the recipient's age or "
    "interests, the delivery city, or the occasion date — call the ask_user "
    "tool with 1-3 short, specific questions BEFORE searching. Only ask when it "
    "would materially improve the choice; if you already have enough, proceed.\n\n"
    "Follow-ups: this is a conversation — remember earlier details (budget, "
    "interests, city, occasion). When the user asks for something new (e.g. 'I "
    "also want a cake'), call kapruka_search_products for it and recommend from "
    "those fresh results; honour the budget and context already given.\n\n"
    "Rules: rely ONLY on tool data from this conversation — never list products "
    "or prices from memory, and never say 'prices may change, check the "
    "website'. Never list the same product twice. Only include tool parameters "
    "you actually need — never pass the string 'null' or placeholder values; "
    "omit them. If a search returns nothing, try a different concrete idea "
    "rather than repeating it."
)

# A synthetic tool (not part of the MCP) that lets the model pause and ask the
# user for missing details instead of guessing.
ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user 1-3 short clarifying questions when key details "
            "(budget, recipient interests/age, delivery city, occasion date) "
            "are missing and would materially improve the recommendation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-3 concise questions for the user.",
                }
            },
            "required": ["questions"],
        },
    },
}


# Values a model emits as placeholders for "unset" optional parameters. These
# must be dropped, otherwise the MCP treats e.g. category="null" as a real
# filter and returns nothing.
NULLISH = {"null", "none", "nil", "undefined", "na", "n/a", ""}


def _normalize_questions(raw) -> list:
    """Coerce the model's `questions` argument into a clean list of strings.

    Models sometimes return this as a stringified list rather than a real
    array — either JSON ("[\"a\",\"b\"]") or Python-style single quotes
    ("['a','b']"). Handle string, stringified-list, and list.
    """
    if isinstance(raw, str):
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, list):
            raw = parsed
        else:
            # Last resort: a quoted list whose items contain apostrophes
            # (e.g. "['mom's age?', 'budget?']") that neither parser accepts.
            s = raw.strip()
            if s.startswith("[") and s.endswith("]"):
                s = s[1:-1]
            parts = re.split(r"['\"]\s*,\s*['\"]", s)
            cleaned = [p.strip().strip("'\"").strip() for p in parts]
            raw = [c for c in cleaned if c] or [raw]
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return [str(q).strip() for q in raw if str(q).strip()][:3]


def parse_text_tool_calls(content, valid_names) -> list:
    """Recover tool calls that a model emitted as plain text instead of as
    structured tool_calls.

    Some models (notably Llama 3.1 8B) sometimes answer with a Llama
    "<|python_tag|>" block such as:
        <|python_tag|>{"name": "search", "parameters": {...}}; {"name": ...}
    using Python literals (True/False) and semicolon separators. Extract each
    balanced {...} object, parse it (JSON or Python literal), and keep only
    objects whose name is a real tool.
    """
    if not content or "name" not in content:
        return []
    text = content.replace("<|python_tag|>", " ")
    calls, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                chunk = text[start:i + 1]
                start = None
                obj = None
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    try:
                        obj = ast.literal_eval(chunk)
                    except (ValueError, SyntaxError):
                        obj = None
                if isinstance(obj, dict) and obj.get("name") in valid_names:
                    args = obj.get("parameters")
                    if args is None:
                        args = obj.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            try:
                                args = ast.literal_eval(args)
                            except (ValueError, SyntaxError):
                                args = {}
                    calls.append({"name": obj["name"], "arguments": args if isinstance(args, dict) else {}})
    return calls


def sanitize_args(obj):
    """Recursively drop placeholder/nullish values from tool arguments."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            v = sanitize_args(v)
            if v is None:
                continue
            if isinstance(v, str) and v.strip().lower() in NULLISH:
                continue
            cleaned[k] = v
        return cleaned
    if isinstance(obj, list):
        return [sanitize_args(v) for v in obj]
    return obj


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
    "picture", "photo", "image_link", "imageLink", "images",
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
    if isinstance(u, list):  # e.g. images: [url, ...]
        u = u[0] if u else None
    if not isinstance(u, str) or not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return SITE_BASE.rstrip("/") + u
    return u


def _money(v):
    """Kapruka returns price as {"amount", "currency"}; also accept scalars."""
    if isinstance(v, dict):
        return v.get("amount"), v.get("currency")
    return v, None


def _looks_like_product(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    if _first(d, NAME_KEYS) is None:
        return False
    return any(_first(d, ks) is not None for ks in (PRICE_KEYS, URL_KEYS, IMAGE_KEYS))


def _normalize_product(d: dict) -> dict:
    amount, currency_from_price = _money(_first(d, PRICE_KEYS))
    return {
        "name": _first(d, NAME_KEYS),
        "price": amount,
        "currency": _first(d, CURRENCY_KEYS) or currency_from_price,
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
def _build_messages(conversation: list) -> list:
    """System prompt + the recent user/assistant turns (capped for latency)."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in conversation[-12:]:
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs


def search(conversation, allow_questions: bool = True) -> dict:
    global _TOOLS_CACHE
    # Accept a plain string (single turn) or a full conversation list.
    if isinstance(conversation, str):
        conversation = [{"role": "user", "content": conversation}]
    last_user = next(
        (t.get("content", "") for t in reversed(conversation) if t.get("role") == "user"), ""
    )

    mcp = MCPSession()
    mcp.initialize()
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = mcp.list_tools()
    tools = _TOOLS_CACHE
    openai_tools = mcp_tools_to_openai(tools)
    # Offer the clarifying-question tool only on the first turn.
    if allow_questions:
        openai_tools = openai_tools + [ASK_USER_TOOL]

    messages = _build_messages(conversation)
    trace = []
    results = []
    tool_names = [t.get("name") for t in tools]
    valid_names = set(tool_names) | {"ask_user"}

    def finalize(answer: str) -> dict:
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": answer,
            "products": extract_products(results),
            "tools_available": tool_names,
            "tool_calls": trace,
            "results": results,
        }

    def ask(questions: list) -> dict:
        return {
            "ok": True,
            "needs_input": True,
            "query": last_user,
            "model": NIM_MODEL,
            "questions": questions[:3],
            "tools_available": tool_names,
        }

    for _ in range(MAX_TOOL_ROUNDS):
        completion = nim_chat(messages, openai_tools)
        message = completion["choices"][0]["message"]
        structured = message.get("tool_calls") or []

        # Normalize calls from structured tool_calls OR from text the model
        # emitted (e.g. a Llama <|python_tag|> block) when it failed to use the
        # proper tool_calls field.
        if structured:
            norm = []
            for c in structured:
                fn = c.get("function", {})
                try:
                    a = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    a = {}
                norm.append({"name": fn.get("name"), "arguments": a, "id": c.get("id")})
            messages.append(
                {"role": "assistant", "content": message.get("content") or "", "tool_calls": structured}
            )
            text_mode = False
        else:
            norm = parse_text_tool_calls(message.get("content"), valid_names)
            messages.append({"role": "assistant", "content": message.get("content") or ""})
            if not norm:
                return finalize(message.get("content", ""))  # genuine final answer
            text_mode = True

        # Clarifying questions take priority.
        for c in norm:
            if c["name"] == "ask_user" and allow_questions:
                questions = _normalize_questions((c["arguments"] or {}).get("questions"))
                if questions:
                    return ask(questions)

        # Execute the real tool calls.
        text_outputs = []
        for c in norm:
            name = c["name"]
            if name == "ask_user":
                if not text_mode:  # structured calls must each get a tool reply
                    messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": "(no questions)"})
                continue
            args = sanitize_args(c["arguments"] or {})
            try:
                output = mcp.call_tool(name, args)
            except Exception as exc:  # feed tool errors back to the model
                output = f"ERROR calling {name}: {exc}"
            trace.append({"tool": name, "arguments": args})
            results.append({"tool": name, "arguments": args, "output": output})
            if text_mode:
                text_outputs.append(f"{name} ->\n{output}")
            else:
                messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": output})

        if text_mode and text_outputs:
            messages.append(
                {
                    "role": "user",
                    "content": "Tool results — use ONLY these to recommend products "
                    "(name, price, link, one-line reason). Do NOT call tools again; "
                    "reply with your recommendations now.\n\n" + "\n\n".join(text_outputs),
                }
            )

    # Ran out of rounds; if we did gather products, present them anyway.
    if results:
        return finalize("Here are the best matches I found.")
    return finalize(
        "I couldn't complete the search in time — please try again with a bit more detail."
    )


# === HTTP handler ============================================================
class handler(BaseHTTPRequestHandler):
    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _run(self, conversation, allow_questions: bool = True):
        # conversation is a list of {role, content} turns (or [] if empty).
        has_user = any(
            t.get("role") == "user" and str(t.get("content") or "").strip()
            for t in conversation
        )
        if not has_user:
            return self._respond(400, {"ok": False, "error": "Missing 'query'/'messages'."})
        try:
            self._respond(200, search(conversation, allow_questions=allow_questions))
        except PermissionError as exc:
            self._respond(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._respond(502, {"ok": False, "error": str(exc)})

    def do_GET(self):  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(self.path).query)
        q = (params.get("q") or [""])[0]
        self._run([{"role": "user", "content": q}])

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._respond(400, {"ok": False, "error": "Body must be JSON."})
        # Prefer full conversation history ('messages'); fall back to single 'query'.
        conversation = data.get("messages")
        if not isinstance(conversation, list) or not conversation:
            conversation = [{"role": "user", "content": data.get("query", "")}]
        # allow_questions defaults true; the client sets it false when resubmitting
        # with answers so the model proceeds to search instead of asking again.
        self._run(conversation, allow_questions=bool(data.get("allow_questions", True)))
