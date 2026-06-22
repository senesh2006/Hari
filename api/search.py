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
import html as _html
import json
import os
import re
import time
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
# Transient upstream errors (5xx) are worth a quick retry before degrading.
NIM_RETRIES = int(os.environ.get("NVIDIA_NIM_RETRIES", "2"))
# A full celebration package needs several searches (cake, flowers, card,
# gift, decorations), so allow a few more rounds.
MAX_TOOL_ROUNDS = int(os.environ.get("SEARCH_MAX_ROUNDS", "5"))
# Overall wall-clock budget for one request. Kept just under Vercel's function
# limit so we always return our own (partial) answer instead of being killed
# with a raw 504. Each model call is capped to the time that remains.
SEARCH_BUDGET = float(os.environ.get("SEARCH_BUDGET", "55"))
# Don't start another model round unless this many seconds remain.
MIN_ROUND_SECONDS = float(os.environ.get("SEARCH_MIN_ROUND_SECONDS", "8"))

# Tool schemas are static; cache them across warm invocations to skip a
# tools/list round-trip on every request.
_TOOLS_CACHE = None

SYSTEM_PROMPT = (
    "You are a warm, emotionally intelligent gifting & shopping concierge for "
    "Kapruka, a Sri Lankan online store. You are a kind human helper first and a "
    "search engine second. Do NOT just search the user's literal words. "
    "Understand what they actually need — the recipient, the occasion or "
    "situation, and the feeling they want to create — and use your own judgement "
    "to decide what would genuinely suit it.\n\n"
    "EMOTIONAL INTELLIGENCE — this matters most:\n"
    "- Read how the person feels and meet them there. If they sound excited, "
    "share the joy; if they sound stressed, anxious or sad, be calm, gentle and "
    "reassuring. Acknowledge the feeling in a few natural words before getting "
    "practical (e.g. 'That's such a lovely milestone' or 'I'm really sorry you're "
    "going through this').\n"
    "- Greetings and small talk are NOT gift requests. If the user just says "
    "'hi', 'hello', 'how are you', thanks you, or chats, reply warmly and "
    "briefly as a person would, then gently invite them to tell you who the gift "
    "is for and the occasion. Do NOT fire off clarifying questions and do NOT "
    "call ask_user for a simple greeting — just talk.\n"
    "- Never sound like a form. Ask at most a couple of things at a time, "
    "conversationally, and only once there is an actual gifting request.\n"
    "- Be encouraging and never judgemental about budget, relationships or taste. "
    "Reassure on a small budget that thoughtful options exist.\n\n"
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
    "Ask before guessing, but only what is APPROPRIATE and necessary. If the "
    "user has already told you the recipient AND the occasion (e.g. 'a birthday "
    "cake for my mom'), you have enough — go straight to kapruka_search_products, "
    "do NOT ask anything first. Budget and delivery city are usually safe to ask; "
    "ask about the recipient's interests ONLY when a personalised gift is "
    "suitable — never in a sombre or sensitive situation. NEVER ask the user for "
    "their OWN name, date of birth, age or home address as a clarifying question "
    "— those are not needed to suggest gifts (only collected later at checkout). "
    "When you do ask, call the ask_user tool with 1-3 short, tactful questions "
    "BEFORE searching, and only when it would materially improve the choice; "
    "otherwise proceed.\n\n"
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
    "BUDGET CHECK before adding to the cart: if the user has given a budget, "
    "always check it before calling add_to_cart or add_all_suggestions_to_cart. "
    "The current cart subtotal is in the context — work out the NEW total if you "
    "added the requested item(s). If that new total would go OVER their budget, "
    "do NOT add it silently: first warn the user — state the item's price, the "
    "new total, and how much it exceeds the budget — and ask whether they'd like "
    "to add it anyway, or remove/swap something to stay within budget. Only call "
    "the add tool once it fits the budget OR the user clearly confirms they're "
    "happy to go over. If there's no budget, just add it.\n\n"
    "Rules: rely ONLY on tool data from this conversation — never list products "
    "or prices from memory, and never say 'prices may change, check the "
    "website'. Never list the same product twice. Only include tool parameters "
    "you actually need — never pass the string 'null' or placeholder values; "
    "omit them. If a search returns nothing, try a different concrete idea "
    "rather than repeating it.\n\n"
    "When a USER PROFILE is provided in context, bias kapruka_search_products "
    "queries and your suggestions toward that person's gifting personality, "
    "style, typical budget, and default city — without ignoring their current "
    "request."
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


def _decode_escapes(s):
    r"""Turn literal \uXXXX sequences into real characters.

    When we salvage an `ask_user` call out of raw text with regex, escaped
    unicode (e.g. Tamil/Sinhala as "\u0ba8...") is captured verbatim instead of
    decoded. Convert those back so the questions render — and read aloud —
    correctly. Anything that isn't a \uXXXX escape is left untouched.
    """
    if not isinstance(s, str) or "\\u" not in s:
        return s

    def repl(m):
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)

    return re.sub(r"\\u([0-9a-fA-F]{4})", repl, s)


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
    return [_decode_escapes(str(q).strip()) for q in raw if str(q).strip()][:3]


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


def looks_like_tool_blob(content) -> bool:
    """True when the model's 'final' text is really a (mangled) tool call.

    Models sometimes print a function call as JSON/text instead of using the
    tool_calls field. That JSON must never reach the user — detect it so we can
    salvage or suppress it.
    """
    if not content:
        return False
    s = content.strip()
    if "<|python_tag|>" in s:
        return True
    has_name = bool(re.search(r'["\']name["\']\s*:\s*["\'][a-zA-Z_]+["\']', s))
    looks_jsonish = s.startswith("{") or '"type"' in s or '"parameters"' in s or '"arguments"' in s
    return has_name and looks_jsonish


def salvage_text_tool_calls(content, valid_names) -> list:
    """Best-effort recovery of a tool call from malformed JSON/text.

    `parse_text_tool_calls` only handles well-formed objects. When the JSON is
    broken (e.g. an unterminated questions array), fall back to regex so an
    `ask_user` attempt still becomes real questions instead of leaking JSON.
    """
    if not content:
        return []
    name_m = re.search(r'["\']name["\']\s*:\s*["\']([a-zA-Z_]+)["\']', content)
    if not name_m:
        return []
    name = name_m.group(1)
    if name not in valid_names:
        return []
    if name == "ask_user":
        block = re.search(r'questions["\']\s*:\s*\[(.*)', content, re.S)
        scope = block.group(1) if block else content
        questions = []
        for q in re.findall(r'["\']([^"\']{3,}?)["\']', scope):
            q = q.strip()
            if q and q.lower() not in ("questions", "ask_user", "function", "parameters", "arguments", "type"):
                questions.append(q)
        return [{"name": "ask_user", "arguments": {"questions": questions[:3]}}] if questions else []
    # Other tools: only salvage if we can actually recover an args object —
    # otherwise let the caller nudge the model to retry cleanly.
    am = re.search(r'(?:parameters|arguments)["\']\s*:\s*(\{.*?\})', content, re.S)
    if am:
        try:
            args = json.loads(am.group(1))
        except json.JSONDecodeError:
            try:
                args = ast.literal_eval(am.group(1))
            except (ValueError, SyntaxError):
                args = None
        if isinstance(args, dict) and args:
            return [{"name": name, "arguments": args}]
    return []


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
ID_KEYS = ("id", "product_id", "productId", "productid", "sku", "product_code")

SITE_BASE = os.environ.get("KAPRUKA_SITE_BASE", "https://www.kapruka.com")


def _id_from_url(url):
    """Kapruka product URLs end in /kid/<product_id>; pull that out."""
    if not isinstance(url, str):
        return None
    m = re.search(r"/kid/([^/?#]+)", url)
    return m.group(1) if m else None


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
    url = _abs_url(_first(d, URL_KEYS))
    return {
        "id": _first(d, ID_KEYS) or _id_from_url(url),
        "name": _first(d, NAME_KEYS),
        "price": amount,
        "currency": _first(d, CURRENCY_KEYS) or currency_from_price,
        "image": _abs_url(_first(d, IMAGE_KEYS)),
        "url": url,
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
class NimTimeout(Exception):
    """The NIM model didn't answer in time (transient — worth degrading gracefully)."""


def nim_chat(messages: list, tools: list, timeout: float | None = None) -> dict:
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
    for attempt in range(NIM_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout or NIM_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code >= 500:
                # Transient upstream engine error (e.g. EngineCore 500). Retry a
                # couple of times; if it persists, degrade gracefully instead of
                # leaking raw JSON to the user.
                if attempt < NIM_RETRIES:
                    time.sleep(0.6)
                    continue
                raise NimTimeout(f"NIM HTTP {exc.code}: {detail[:200]}") from exc
            raise ValueError(f"NVIDIA NIM HTTP {exc.code}: {detail[:500]}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # socket.timeout (read timed out) and transient network errors land
            # here; signal a graceful degrade rather than a hard 502.
            raise NimTimeout(str(exc)) from exc


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
        subtotal = 0.0
        cart_cur = "LKR"
        for i, p in enumerate(cart, 1):
            qty = p.get("qty", 1) or 1
            cur = p.get("currency") or "LKR"
            cart_cur = cur
            price = p.get("price")
            try:
                line_total = float(re.sub(r"[^0-9.]", "", str(price))) * qty
            except (TypeError, ValueError):
                line_total = 0.0
            subtotal += line_total
            price_txt = f" — {cur} {price} each" if price not in (None, "") else ""
            lines.append(f"{i}. {p.get('name')} x{qty}{price_txt}")
        lines.append(f"CART SUBTOTAL SO FAR: {cart_cur} {subtotal:,.0f}")
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


LANG_NAMES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}

# Single-word greetings / small-talk openers in English, Sinhala and Tamil.
GREETING_WORDS = {
    "hi", "hii", "hiya", "hello", "helo", "hey", "heya", "yo", "hai", "sup",
    "howdy", "greetings", "thanks", "thank", "ayubowan", "kohomada", "halo",
    "හායි", "හලෝ", "කොහොමද", "ආයුබෝවන්", "ඕව", "හායී",
    "வணக்கம்", "ஹலோ", "நலமா", "வாங்க",
}
GREETING_PHRASES = (
    "how are you", "how r u", "how are u", "whats up", "what's up",
    "good morning", "good evening", "good afternoon", "good day", "nice to meet",
    "කොහොමද", "සුභ උදෑසනක්", "සුබ උදෑසනක්", "ඔයාට කොහොමද",
    "எப்படி இருக்கிறீர்கள்", "எப்படி இருக்கீங்க", "காலை வணக்கம்",
)


def _is_greeting(text: str) -> bool:
    """True when a message is just a greeting / small talk, not a gift request."""
    raw = str(text or "").strip()
    if not raw:
        return False
    # Only strip ASCII punctuation — keep letters and Indic combining marks so
    # Sinhala/Tamil words survive intact.
    norm = re.sub(r"[!?.,;:\"'()\[\]\-_/\\…]+", " ", raw.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return False
    words = norm.split()
    if len(words) > 5:  # anything longer is almost certainly a real request
        return False
    if any(phrase in norm for phrase in GREETING_PHRASES):
        return True
    return any(w in GREETING_WORDS for w in words)


def _greeting_reply(language: str | None) -> str:
    lang = (language or "en").lower()
    if lang == "si":
        return ("ආයුබෝවන්! 😊 මම ඔබේ තෑගි උපදේශකයා. ඔබ කා සඳහාද තෑග්ගක් සොයන්නේ, "
                "මොන අවස්ථාවටද? (උපන්දිනයක්, සංවත්සරයක්, සුවය පැතීමක්... ඕනෑම දෙයක්)")
    if lang == "ta":
        return ("வணக்கம்! 😊 நான் உங்கள் பரிசு உதவியாளர். யாருக்காக, எந்த "
                "நிகழ்விற்காக பரிசு தேடுகிறீர்கள்? (பிறந்தநாள், ஆண்டுவிழா, "
                "நலம் விசாரிப்பு... எதுவாக இருந்தாலும்)")
    return ("Hi! 😊 I'm your gift concierge. Who are you shopping for, and what's "
            "the occasion? (A birthday, anniversary, get-well, condolences — anything.)")


def _language_message(language: str | None) -> str | None:
    name = LANG_NAMES.get((language or "").lower())
    if not name or name == "English":
        return None
    return (
        f"IMPORTANT: The user has chosen {name} as their language. Write ALL your "
        f"replies and any clarifying questions in fluent, natural {name}. You may "
        "keep product names, prices, currency codes and links exactly as the "
        "tools return them, but everything else you write must be in "
        f"{name}."
    )


TRANSLATE_TIMEOUT = float(os.environ.get("TRANSLATE_TIMEOUT", "18"))
LANGBLY_API_KEY = os.environ.get("LANGBLY_API_KEY")
LANGBLY_URL = os.environ.get("LANGBLY_URL", "https://api.langbly.com/language/translate/v2")

# --- Supabase (user profiles) ------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

PERSONALITY_LABELS = {
    "thoughtful_planner": "Thoughtful Planner",
    "last_minute_hero": "Last-Minute Hero",
    "practical_gifter": "Practical Gifter",
    "big_spender": "Big Spender",
    "sentimental_soul": "Sentimental Soul",
    "creative_maker": "Creative Maker",
}

BUDGET_BAND_LABELS = {
    "under_2000": "under LKR 2,000",
    "2000_5000": "LKR 2,000–5,000",
    "5000_10000": "LKR 5,000–10,000",
    "over_10000": "LKR 10,000+",
}

SHOPPING_STYLE_LABELS = {
    "weeks_ahead": "plans weeks ahead",
    "few_days": "shops a few days before",
    "last_minute": "last-minute shopper",
}

RECIPIENT_LABELS = {
    "family": "family",
    "partner": "partner",
    "colleagues": "colleagues",
    "kids": "kids",
    "mixed": "mixed recipients",
}


def _supabase_request(
    path: str,
    token: str | None = None,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 10,
):
    """Minimal Supabase REST helper (stdlib only)."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None
    url = f"{SUPABASE_URL}{path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token or SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }
    if method == "GET":
        req = urllib.request.Request(url, headers=headers, method="GET")
    else:
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise ValueError(f"Supabase HTTP {exc.code}: {detail[:300]}") from exc
    except (TimeoutError, urllib.error.URLError, OSError):
        return None


def _verify_supabase_user(token: str | None) -> dict | None:
    """Validate JWT and return the auth user object, or None."""
    if not token or not SUPABASE_URL:
        return None
    data = _supabase_request("/auth/v1/user", token=token)
    return data if isinstance(data, dict) and data.get("id") else None


def _load_profile(token: str | None) -> dict | None:
    """Load the signed-in user's profile row via RLS."""
    user = _verify_supabase_user(token)
    if not user:
        return None
    uid = user.get("id")
    if not uid:
        return None
    rows = _supabase_request(
        f"/rest/v1/profiles?id=eq.{uid}&select=*",
        token=token,
    )
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def _profile_message(profile: dict | None) -> str | None:
    """Compact profile block for the model."""
    if not profile:
        return None
    lines = [
        "USER PROFILE (use to personalize searches and tone; do not recite verbatim):"
    ]
    personality = profile.get("gifting_personality")
    if personality:
        label = PERSONALITY_LABELS.get(personality, personality.replace("_", " "))
        lines.append(f"- Gifting personality: {label}")
    scores = profile.get("personality_scores") or {}
    if isinstance(scores, dict) and scores:
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
        traits = ", ".join(f"{k.replace('_', ' ')} ({v})" for k, v in top if v)
        if traits:
            lines.append(f"- Trait scores: {traits}")
    budget = profile.get("default_budget")
    if budget is not None:
        try:
            lines.append(f"- Typical budget: LKR {float(budget):,.0f}")
        except (TypeError, ValueError):
            pass
    quiz = profile.get("quiz_answers") or {}
    if isinstance(quiz, dict):
        band = quiz.get("budget_band")
        if band:
            lines.append(f"- Budget band: {BUDGET_BAND_LABELS.get(band, band)}")
        shop = quiz.get("shopping_style")
        if shop:
            lines.append(f"- Shops: {SHOPPING_STYLE_LABELS.get(shop, shop)}")
        recip = quiz.get("recipient_focus")
        if recip:
            lines.append(f"- Usually buys for: {RECIPIENT_LABELS.get(recip, recip)}")
    prefs = profile.get("preferences") or {}
    if isinstance(prefs, dict):
        styles = prefs.get("styles")
        if styles:
            if isinstance(styles, list):
                lines.append(f"- Style: {', '.join(styles)}")
            else:
                lines.append(f"- Style: {styles}")
        avoid = prefs.get("avoid_list")
        if avoid and isinstance(avoid, list):
            lines.append(f"- Avoid: {', '.join(avoid)}")
    city = profile.get("default_city")
    if city:
        lines.append(f"- Default delivery city: {city}")
    lang = profile.get("default_language")
    if lang and lang != "en":
        lines.append(f"- Preferred language: {LANG_NAMES.get(lang, lang)}")
    saved = profile.get("saved_instructions") or []
    if saved:
        lines.append(f"- Saved notes: {'; '.join(str(x) for x in saved)}")
    if len(lines) <= 1:
        return None
    return "\n".join(lines)


def _translate(text, source, target, timeout=TRANSLATE_TIMEOUT):
    """Translate short text between en/si/ta via the Langbly API.

    We run the concierge entirely in English (best reasoning + all features) and
    translate the user's message in and the reply back out. Langbly is a Google
    Translate v2-compatible service — a single fast synchronous call, which keeps
    latency low. Falls back to the original text on any error or missing key.
    """
    text = (text or "").strip()
    src = (source or "").lower()
    tgt = (target or "").lower()
    if not text or not tgt or src == tgt:
        return text
    if not LANGBLY_API_KEY:
        return text  # no key configured — leave text untouched rather than fail
    payload = {"q": text, "target": tgt, "format": "text"}
    if src:
        payload["source"] = src
    req = urllib.request.Request(
        LANGBLY_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"X-API-Key": LANGBLY_API_KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        translations = ((data or {}).get("data") or {}).get("translations") or []
        out = (translations[0].get("translatedText") if translations else "") or ""
        out = _html.unescape(out).strip()  # v2 can HTML-escape entities like &#39;
        return out or text
    except Exception:
        return text


def _build_messages(
    conversation: list,
    cart_context: str | None = None,
    language: str | None = None,
    profile_context: str | None = None,
) -> list:
    """System prompt + language pref + user profile + cart context + recent turns."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    lang_msg = _language_message(language)
    if lang_msg:
        msgs.append({"role": "system", "content": lang_msg})
    if profile_context:
        msgs.append({"role": "system", "content": profile_context})
    if cart_context:
        msgs.append({"role": "system", "content": cart_context})
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
    language = context.get("language")

    # Greetings / small talk are not gift requests. Answer them warmly in the
    # chosen language WITHOUT calling the model — this keeps the reply correct
    # (no nonsensical clarifying questions) and instant.
    if _is_greeting(last_user) and not suggestions and not cart:
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": _greeting_reply("en"),
            "answer_local": _greeting_reply(language),
            "user_en": last_user,
            "products": [],
            "cart_actions": [],
            "tools_available": [],
            "tool_calls": [],
            "results": [],
        }

    mcp = MCPSession()
    mcp.initialize()
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = mcp.list_tools()
    tools = _TOOLS_CACHE
    openai_tools = mcp_tools_to_openai(tools) + CART_TOOLS
    if allow_questions:
        openai_tools = openai_tools + [ASK_USER_TOOL]

    # Run the concierge in English for best quality and full feature support,
    # then translate the reply back to the user's language. Translate the latest
    # user message in so the model understands it.
    target_lang = (language or "en").lower()
    if target_lang not in ("si", "ta"):
        target_lang = None
    conv = [dict(t) for t in conversation]
    user_en = last_user
    if target_lang:
        user_en = _translate(last_user, target_lang, "en")
        for t in reversed(conv):
            if t.get("role") == "user":
                t["content"] = user_en
                break

    access_token = context.get("access_token")
    profile = _load_profile(access_token) if access_token else None
    profile_msg = _profile_message(profile)
    cart_msg = _context_message(suggestions, cart, instructions)
    messages = _build_messages(conv, cart_msg, None, profile_msg)
    trace = []
    results = []
    cart_actions = []
    tool_names = [t.get("name") for t in tools]
    valid_names = set(tool_names) | CART_TOOL_NAMES
    if allow_questions:
        valid_names = valid_names | {"ask_user"}

    def finalize(answer: str) -> dict:
        local = _translate(answer, "en", target_lang) if (target_lang and answer) else answer
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": answer,
            "answer_local": local,
            "user_en": user_en,
            "products": extract_products(results),
            "cart_actions": cart_actions,
            "tools_available": tool_names,
            "tool_calls": trace,
            "results": results,
        }

    def ask(questions: list) -> dict:
        qs = questions[:3]
        local = qs
        if target_lang and qs:
            # Translate the questions in one call, then re-split; if the line
            # count doesn't survive, fall back to the English questions.
            joined = _translate("\n".join(qs), "en", target_lang)
            parts = [p.strip() for p in joined.split("\n") if p.strip()]
            local = parts if len(parts) == len(qs) else qs
        return {
            "ok": True,
            "needs_input": True,
            "query": last_user,
            "model": NIM_MODEL,
            "questions": qs,
            "questions_local": local,
            "user_en": user_en,
            "tools_available": tool_names,
        }

    # Leave headroom for the outbound translation so the whole call stays within
    # the serverless limit when we still need to translate the reply.
    reserve = 12 if target_lang else 0
    deadline = time.monotonic() + max(20.0, SEARCH_BUDGET - reserve)
    corrections = 0  # times we've nudged the model to stop printing tool JSON

    def degrade() -> dict:
        """Hand back whatever we have when the time budget runs out."""
        if results:
            return finalize("Here are the best matches I found so far — that took a little long on my side.")
        if cart_actions:
            return finalize("Done — I've updated your cart.")
        return finalize(
            "Sorry, that took longer than expected on my end. Please send your "
            "last message again and I'll pick up right where we left off."
        )

    for _ in range(MAX_TOOL_ROUNDS):
        remaining = deadline - time.monotonic()
        if remaining < MIN_ROUND_SECONDS:
            return degrade()
        try:
            completion = nim_chat(messages, openai_tools, timeout=min(NIM_TIMEOUT, remaining))
        except NimTimeout:
            # The model stalled; return whatever we already gathered.
            return degrade()
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
            content = message.get("content") or ""
            norm = parse_text_tool_calls(content, valid_names) or salvage_text_tool_calls(content, valid_names)
            messages.append({"role": "assistant", "content": content})
            if not norm:
                if looks_like_tool_blob(content) and corrections < 1:
                    # The model printed a tool call as text we couldn't use.
                    # Ask it once to answer properly instead of leaking JSON.
                    corrections += 1
                    messages.append({
                        "role": "user",
                        "content": "Do not output tool calls or JSON as your message. "
                        "Either call the tool through the proper function-calling "
                        "interface, or reply to me directly in plain natural language.",
                    })
                    continue
                if looks_like_tool_blob(content):
                    # Still leaking — don't show raw JSON; give a clean fallback.
                    return finalize("Hi! 🎁 Who are you shopping for, and what's the occasion?")
                return finalize(content)  # genuine final answer
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
            "language": data.get("language"),
            "access_token": data.get("access_token"),
        }
        self._run(
            conversation,
            allow_questions=bool(data.get("allow_questions", True)),
            context=context,
        )
