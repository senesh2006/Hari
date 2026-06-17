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
# Default to the higher-quality 70B model for better judgement, tone and
# reliable tool calls; override with NVIDIA_NIM_MODEL (e.g.
# "meta/llama-3.1-8b-instruct") to trade quality for lower latency.
NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY")
NIM_TIMEOUT = float(os.environ.get("NVIDIA_NIM_TIMEOUT", "60"))
# A full celebration package needs several searches (cake, flowers, card,
# gift, decorations), so allow a few more rounds.
MAX_TOOL_ROUNDS = int(os.environ.get("SEARCH_MAX_ROUNDS", "5"))

# Tool schemas are static; cache them across warm invocations to skip a
# tools/list round-trip on every request.
_TOOLS_CACHE = None

SYSTEM_PROMPT = (
    "You are a thoughtful gifting & shopping concierge for Kapruka, a Sri Lankan "
    "online store. Do NOT just search the user's literal words. Understand what "
    "they actually need — the recipient, the occasion or situation, and the "
    "feeling they want to create — and use your own judgement to decide what "
    "would genuinely suit it.\n\n"
    "Reason it through yourself: think about what that specific moment calls for "
    "and which items would make it complete and appropriate. Sometimes that is a "
    "thoughtful multi-part package; sometimes it's just one well-chosen item — "
    "decide based on the request. Only include things that genuinely fit, and "
    "leave out anything that would be inappropriate for the situation. If the "
    "user clearly wants a single specific item, simply find good options for it "
    "rather than padding it out.\n\n"
    "READ THE TONE and be sensitive. Some situations are joyful and some are "
    "sombre (a loss, illness, hardship, an apology). Match your wording and your "
    "suggestions to the mood: in a sombre or sensitive situation be respectful "
    "and understated, never cheerful or celebratory, never suggest cakes, "
    "balloons or party items, and NEVER ask insensitive questions such as the "
    "deceased's or recipient's 'hobbies/interests'. Suggest only what is fitting "
    "to send (for example a respectful condolence flower arrangement). Use your "
    "judgement about what is appropriate.\n\n"
    "MANDATORY search rules:\n"
    "- Translate each idea into a short, specific product noun and run a "
    "SEPARATE kapruka_search_products call for each (e.g. q='flower bouquet', "
    "q='fruit basket', q='cookware'). Never search vague phrases like 'gift', "
    "'birthday gift', or 'gifts'.\n"
    "- Tailor ideas to the recipient's age and interests (cooking → q='cookware'; "
    "reading → q='book').\n"
    "- Do NOT pass a category filter unless you got the exact category name from "
    "kapruka_list_categories — a wrong category returns zero results.\n\n"
    "Present your suggestions clearly: when it's a package, group items by their "
    "role with a short heading and an emoji; for each pick give the name, price, "
    "link, and a one-line reason it fits. Keep the combined total within the "
    "user's budget and note the rough total.\n\n"
    "Ask before guessing, but only what is APPROPRIATE and necessary. Budget and "
    "delivery city are usually safe to ask; ask about the recipient's interests "
    "ONLY when a personalised gift is suitable — never in a sombre or sensitive "
    "situation. Call the ask_user tool with 1-3 short, tactful questions BEFORE "
    "searching, and only when it would materially improve the choice; otherwise "
    "proceed.\n\n"
    "Follow-ups: this is a conversation — remember earlier details (occasion, "
    "budget, interests, city, date). When the user asks for something new, call "
    "kapruka_search_products for it and recommend from those fresh results; "
    "honour the budget and context already given. For a celebratory or warm "
    "occasion, after you give gift ideas, offer to add a cake and/or flowers "
    "too (e.g. 'Would you like me to add a cake or flowers as well?'). Never "
    "offer cakes, flowers-for-celebration or party items in a sombre or "
    "sensitive situation.\n\n"
    "CART & SELECTIONS: the user builds a cart from your suggestions. When they "
    "pick specific items from your LATEST suggestions (by number, name or "
    "description) call add_to_cart with those suggestion numbers. When they want "
    "all of them ('add everything', 'the whole list') call "
    "add_all_suggestions_to_cart. To drop items or empty the cart call "
    "remove_from_cart. When they give a special request — gift-wrapping, "
    "combining items into a hamper, a custom build, a delivery note or a message "
    "for the card/store — call add_instruction to save it verbatim. You may both "
    "act and talk in the same turn; after any cart or instruction action, "
    "confirm what you did in one short, natural sentence. Use the suggestion and "
    "cart numbers exactly as given in the context.\n\n"
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


# Synthetic cart/instruction tools (not part of the MCP). They let the model
# act on the cart the user is building in the browser. We resolve them against
# the suggestions/cart the client sends and return `cart_actions` for the UI to
# apply — nothing is sent to the MCP here.
CART_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": (
                "Add one or more of the CURRENT SUGGESTIONS to the user's cart. "
                "Use the 1-based suggestion numbers from the context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-based suggestion numbers to add.",
                    },
                    "quantities": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional quantities, parallel to items (default 1 each).",
                    },
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_all_suggestions_to_cart",
            "description": (
                "Add EVERY current suggestion to the cart. Use when the user "
                "wants the whole suggested list (e.g. 'add everything')."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": "Remove items from the cart by their 1-based cart numbers, or clear it entirely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {"type": "array", "items": {"type": "integer"}},
                    "clear": {"type": "boolean", "description": "Set true to empty the cart."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_instruction",
            "description": (
                "Save a special instruction for the store/order — gift wrapping, "
                "combining items into a hamper, a custom build, a delivery note, "
                "or message-card text."
            ),
            "parameters": {
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
            },
        },
    },
]
CART_TOOL_NAMES = {t["function"]["name"] for t in CART_TOOLS}


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


def _clean_desc(text):
    """Strip Kapruka's internal tag/category prefix from a description.

    Raw descriptions look like:
        "specialGifts - Kpc, Electronics, Toasters ELECTRONICS Make 3 mini..."
    i.e. a tag list, then an ALL-CAPS category token, then the real copy. Cut
    everything up to and including that leading category token.
    """
    if not isinstance(text, str):
        return text
    s = text.strip()
    m = re.match(r"^\s*specialGifts\b.*?\b([A-Z]{4,})\b\s*", s)
    if m:
        s = s[m.end():].strip()
    return s


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
        "description": _clean_desc(_first(d, DESC_KEYS)),
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
def _context_message(suggestions: list, cart: list, instructions: list) -> str | None:
    """Describe the browser-side suggestions/cart so the model can act on them."""
    lines = []
    if suggestions:
        lines.append("CURRENT SUGGESTIONS (use these numbers for add_to_cart):")
        for i, p in enumerate(suggestions, 1):
            cur = p.get("currency") or "LKR"
            price = p.get("price")
            price_txt = f" — {cur} {price}" if price not in (None, "") else ""
            lines.append(f"{i}. {p.get('name')}{price_txt}")
    if cart:
        lines.append("")
        lines.append("CURRENT CART (use these numbers for remove_from_cart):")
        for i, p in enumerate(cart, 1):
            lines.append(f"{i}. {p.get('name')} x{p.get('qty', 1)}")
    if instructions:
        lines.append("")
        lines.append("SAVED INSTRUCTIONS: " + "; ".join(str(x) for x in instructions))
    if not lines:
        return None
    return (
        "Live cart/selection context (this is NOT catalogue data to recommend; "
        "use it only to resolve add_to_cart / remove_from_cart / "
        "add_all_suggestions_to_cart actions):\n" + "\n".join(lines)
    )


def _build_messages(conversation: list, context: str | None = None) -> list:
    """System prompt + cart context + the recent user/assistant turns."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        msgs.append({"role": "system", "content": context})
    for turn in conversation[-12:]:
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs


def _resolve_cart_action(name: str, args: dict, suggestions: list):
    """Turn a synthetic cart tool call into a concrete action for the UI."""
    args = args or {}
    if name == "add_all_suggestions_to_cart":
        return {"action": "add", "products": [dict(p, qty=1) for p in suggestions]}
    if name == "add_to_cart":
        items = args.get("items") or []
        qtys = args.get("quantities") or []
        products = []
        for i, idx in enumerate(items):
            try:
                n = int(idx) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= n < len(suggestions):
                qty = 1
                if i < len(qtys):
                    try:
                        qty = max(1, int(qtys[i]))
                    except (TypeError, ValueError):
                        qty = 1
                products.append(dict(suggestions[n], qty=qty))
        return {"action": "add", "products": products}
    if name == "remove_from_cart":
        if args.get("clear"):
            return {"action": "clear"}
        items = []
        for idx in args.get("items") or []:
            try:
                items.append(int(idx))
            except (TypeError, ValueError):
                continue
        return {"action": "remove", "items": items}
    if name == "add_instruction":
        text = str(args.get("instruction") or "").strip()
        return {"action": "instruction", "text": text} if text else None
    return None


def _cart_confirmation(action) -> str:
    if not action:
        return "(no cart change)"
    kind = action.get("action")
    if kind == "add":
        n = len(action.get("products") or [])
        return f"Added {n} item(s) to the cart." if n else "Nothing matched to add."
    if kind == "clear":
        return "Cart cleared."
    if kind == "remove":
        return f"Removed {len(action.get('items') or [])} item(s) from the cart."
    if kind == "instruction":
        return f"Saved instruction: {action.get('text')}"
    return "Cart updated."


def search(conversation, allow_questions: bool = True, context: dict | None = None) -> dict:
    global _TOOLS_CACHE
    # Accept a plain string (single turn) or a full conversation list.
    if isinstance(conversation, str):
        conversation = [{"role": "user", "content": conversation}]
    last_user = next(
        (t.get("content", "") for t in reversed(conversation) if t.get("role") == "user"), ""
    )

    context = context or {}
    suggestions = context.get("suggestions") or []
    cart = context.get("cart") or []
    instructions = context.get("instructions") or []

    mcp = MCPSession()
    mcp.initialize()
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = mcp.list_tools()
    tools = _TOOLS_CACHE
    openai_tools = mcp_tools_to_openai(tools) + CART_TOOLS
    # Offer the clarifying-question tool only on the first turn.
    if allow_questions:
        openai_tools = openai_tools + [ASK_USER_TOOL]

    messages = _build_messages(conversation, _context_message(suggestions, cart, instructions))
    trace = []
    results = []
    cart_actions = []
    tool_names = [t.get("name") for t in tools]
    valid_names = set(tool_names) | {"ask_user"} | CART_TOOL_NAMES

    def finalize(answer: str) -> dict:
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": answer,
            "products": extract_products(results),
            "cart_actions": cart_actions,
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

        # Execute the real tool calls (and resolve synthetic cart actions).
        text_outputs = []
        did_cart = False
        for c in norm:
            name = c["name"]
            if name == "ask_user":
                if not text_mode:  # structured calls must each get a tool reply
                    messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": "(no questions)"})
                continue
            if name in CART_TOOL_NAMES:
                action = _resolve_cart_action(name, c["arguments"] or {}, suggestions)
                if action:
                    cart_actions.append(action)
                    did_cart = True
                conf = _cart_confirmation(action)
                trace.append({"tool": name, "arguments": c["arguments"]})
                if not text_mode:
                    messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": conf})
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

        if text_mode and (text_outputs or did_cart):
            chunks = []
            if text_outputs:
                chunks.append(
                    "Tool results — use ONLY these to recommend products "
                    "(name, price, link, one-line reason).\n\n" + "\n\n".join(text_outputs)
                )
            if did_cart:
                chunks.append("Cart/instructions updated as requested — confirm to the user in one short sentence.")
            chunks.append("Do NOT call tools again; reply to the user now.")
            messages.append({"role": "user", "content": "\n\n".join(chunks)})

    # Ran out of rounds; present whatever we gathered.
    if results:
        return finalize("Here are the best matches I found.")
    if cart_actions:
        return finalize("Done — I've updated your cart.")
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

    def _run(self, conversation, allow_questions: bool = True, context: dict | None = None):
        # conversation is a list of {role, content} turns (or [] if empty).
        has_user = any(
            t.get("role") == "user" and str(t.get("content") or "").strip()
            for t in conversation
        )
        if not has_user:
            return self._respond(400, {"ok": False, "error": "Missing 'query'/'messages'."})
        try:
            self._respond(200, search(conversation, allow_questions=allow_questions, context=context))
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
        context = {
            "suggestions": data.get("suggestions") or [],
            "cart": data.get("cart") or [],
            "instructions": data.get("instructions") or [],
        }
        self._run(
            conversation,
            allow_questions=bool(data.get("allow_questions", True)),
            context=context,
        )
