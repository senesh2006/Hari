"""Kapruka gift concierge — agentic product search over the Kapruka MCP.

POST /api/search   body: {"query": "..."} or {"messages": [...], ...}
GET  /api/search?q=<natural language requirements>

Pipeline:
  1. Open an MCP session and discover tools at runtime.
  2. Run an NVIDIA NIM tool-calling loop (OpenAI-compatible chat API).
  3. Execute Kapruka searches, cart actions, and clarifying questions.
  4. Return a warm conversational answer plus product cards for the UI.

Requires NVIDIA_API_KEY. Standard library only — self-contained (no sibling module imports).
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
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler

# =============================================================================
# Personalization (inlined — Vercel bundles each api/*.py handler alone)
# =============================================================================

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

PERSONALITY_LABELS = {
    "thoughtful_planner": "Thoughtful Planner",
    "last_minute_hero": "Last-Minute Hero",
    "practical_gifter": "Practical Gifter",
    "big_spender": "Big Spender",
    "sentimental_soul": "Sentimental Soul",
    "creative_maker": "Creative Maker",
}

PERSONALITY_SEARCH_HEURISTICS = {
    "thoughtful_planner": (
        "Bias toward curated hampers, classic flowers, and items that feel planned; "
        "suggest complementary add-ons (card, cake) when appropriate."
    ),
    "last_minute_hero": (
        "Prioritize items likely available for fast delivery; smaller ready-to-ship "
        "bundles over custom builds; mention delivery timing."
    ),
    "practical_gifter": (
        "Bias toward useful hampers, gift cards, kitchen/home essentials, and "
        "items with clear everyday value."
    ),
    "big_spender": (
        "Include premium-tier search queries (luxury hamper, premium bouquet) when "
        "budget allows; suggest wow-factor combinations."
    ),
    "sentimental_soul": (
        "Bias toward flowers, personalized touches, traditional sweets, and gifts "
        "with emotional meaning."
    ),
    "creative_maker": (
        "Bias toward unique hampers, playful items, soft toys for kids, and "
        "surprising combinations."
    ),
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

LANG_NAMES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}

_OCCASIONS_CACHE: dict | None = None
_FACTS_CACHE: dict | None = None


def _load_json_data(name: str) -> dict:
    path = os.path.join(_DATA_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_occasions() -> dict:
    global _OCCASIONS_CACHE
    if _OCCASIONS_CACHE is None:
        _OCCASIONS_CACHE = _load_json_data("occasions.json")
    return _OCCASIONS_CACHE


def get_kapruka_facts() -> dict:
    global _FACTS_CACHE
    if _FACTS_CACHE is None:
        _FACTS_CACHE = _load_json_data("kapruka_facts.json")
    return _FACTS_CACHE


def detect_occasion(text: str) -> str | None:
    low = (text or "").lower()
    if not low:
        return None
    for key, meta in get_occasions().items():
        for kw in meta.get("keywords") or []:
            if kw in low:
                return key
    return None


def playbook_message(user_text: str, conversation: list | None = None) -> str | None:
    occasion_key = detect_occasion(user_text)
    if not occasion_key and conversation:
        for turn in reversed(conversation):
            if turn.get("role") == "user":
                occasion_key = detect_occasion(str(turn.get("content") or ""))
                if occasion_key:
                    break
    lines = []
    facts = get_kapruka_facts()
    if facts:
        lines.append("KAPRUKA FACTS (guidance, not product data):")
        for tip in (facts.get("search_tips") or [])[:3]:
            lines.append(f"- {tip}")
        cats = facts.get("popular_categories") or []
        if cats:
            lines.append(f"- Popular areas: {', '.join(cats[:6])}")
    if occasion_key:
        meta = get_occasions().get(occasion_key) or {}
        lines.append(f"OCCASION PLAYBOOK ({occasion_key.replace('_', ' ')}):")
        if meta.get("tone"):
            lines.append(f"- Tone: {meta['tone']}")
        if meta.get("notes"):
            lines.append(f"- Notes: {meta['notes']}")
        ideas = meta.get("search_ideas") or []
        if ideas:
            lines.append(f"- Search ideas: {', '.join(ideas)}")
    if not lines:
        return None
    return "\n".join(lines)


def profile_message(profile: dict | None) -> str | None:
    if not profile:
        return None
    lines = ["USER PROFILE (use to personalize searches and tone; do not recite verbatim):"]
    personality = profile.get("gifting_personality")
    if personality:
        label = PERSONALITY_LABELS.get(personality, personality.replace("_", " "))
        lines.append(f"- Gifting personality: {label}")
        hint = PERSONALITY_SEARCH_HEURISTICS.get(personality)
        if hint:
            lines.append(f"- Search bias: {hint}")
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
            lines.append(f"- Style: {', '.join(styles) if isinstance(styles, list) else styles}")
        avoid = prefs.get("avoid_list")
        if avoid and isinstance(avoid, list):
            lines.append(f"- Always avoid: {', '.join(avoid)}")
        dietary = prefs.get("dietary")
        if dietary:
            lines.append(f"- Dietary: {dietary}")
        if prefs.get("corporate_gifting"):
            lines.append("- Often shops for colleagues / corporate gifts")
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


def session_facts_message(profile: dict | None) -> str | None:
    facts = (profile or {}).get("session_facts") or {}
    if not isinstance(facts, dict) or not facts:
        return None
    lines = ["SESSION MEMORY (from recent chats; trust unless user contradicts):"]
    for key in ("occasion", "recipient", "budget", "city", "constraints", "tone"):
        val = facts.get(key)
        if val:
            lines.append(f"- {key.replace('_', ' ')}: {val}")
    bullets = facts.get("bullets")
    if isinstance(bullets, list):
        for b in bullets[:8]:
            if b:
                lines.append(f"- {b}")
    if len(lines) <= 1:
        return None
    return "\n".join(lines)


def recipients_message(recipients: list | None) -> str | None:
    if not recipients:
        return None
    lines = ["KNOWN RECIPIENTS (gift contacts — use when user mentions them):"]
    for r in recipients[:12]:
        name = r.get("name") or "Someone"
        parts = [name]
        if r.get("relationship"):
            parts.append(f"({r['relationship']})")
        detail = []
        if r.get("city"):
            detail.append(f"city: {r['city']}")
        if r.get("interests"):
            ints = r["interests"]
            if isinstance(ints, list) and ints:
                detail.append(f"likes: {', '.join(ints[:5])}")
        if r.get("avoid"):
            av = r["avoid"]
            if isinstance(av, list) and av:
                detail.append(f"avoid: {', '.join(av[:5])}")
        if r.get("birthday"):
            detail.append(f"birthday: {r['birthday']}")
        if r.get("anniversary"):
            detail.append(f"anniversary: {r['anniversary']}")
        if r.get("last_gift_summary"):
            detail.append(f"last gift: {r['last_gift_summary']}")
        if r.get("notes"):
            detail.append(f"notes: {r['notes']}")
        line = " ".join(parts)
        if detail:
            line += " — " + "; ".join(detail)
        lines.append(f"- {line}")
    return "\n".join(lines)


def wishlist_message(items: list | None) -> str | None:
    if not items:
        return None
    lines = ["SAVED WISHLIST (user hearted these — suggest similar or remind them):"]
    for w in items[:10]:
        name = w.get("name") or "item"
        price = w.get("price")
        line = f"- {name}"
        if price:
            line += f" ({price})"
        lines.append(line)
    return "\n".join(lines)


def order_history_message(orders: list | None) -> str | None:
    if not orders:
        return None
    lines = ["RECENT ORDERS (reference for repeat gifts):"]
    for o in orders[:5]:
        parts = []
        if o.get("recipient_name"):
            parts.append(f"for {o['recipient_name']}")
        if o.get("items_summary"):
            parts.append(o["items_summary"])
        if o.get("order_ref"):
            parts.append(f"ref {o['order_ref']}")
        if o.get("ordered_at"):
            parts.append(str(o["ordered_at"])[:10])
        if parts:
            lines.append(f"- {' — '.join(parts)}")
    if len(lines) <= 1:
        return None
    return "\n".join(lines)


def upcoming_occasions_message(recipients: list | None, within_days: int = 21) -> str | None:
    if not recipients:
        return None
    today = date.today()
    horizon = today + timedelta(days=within_days)
    upcoming = []
    for r in recipients:
        name = r.get("name") or "Someone"
        for field, label in (("birthday", "birthday"), ("anniversary", "anniversary")):
            raw = r.get(field)
            if not raw:
                continue
            try:
                d = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            try:
                this_year = d.replace(year=today.year)
            except ValueError:
                this_year = date(today.year, d.month, min(d.day, 28))
            if this_year < today:
                try:
                    this_year = d.replace(year=today.year + 1)
                except ValueError:
                    this_year = date(today.year + 1, d.month, min(d.day, 28))
            if today <= this_year <= horizon:
                days = (this_year - today).days
                upcoming.append(f"{name}'s {label} in {days} days ({this_year.isoformat()})")
    if not upcoming:
        return None
    lines = ["UPCOMING OCCASIONS (proactive context):"]
    for u in upcoming[:5]:
        lines.append(f"- {u}")
    return "\n".join(lines)


_SESSION_BUDGET_RE = re.compile(
    r"(?:under|below|max|budget|rs\.?|lkr)\s*([0-9][0-9,]*)",
    re.I,
)


def extract_session_facts(
    conversation: list,
    last_user: str,
    answer: str,
    existing: dict | None,
) -> dict:
    facts = dict(existing or {})
    text = f"{last_user} {answer}".lower()
    occasion = detect_occasion(last_user) or detect_occasion(answer)
    if occasion:
        facts["occasion"] = occasion.replace("_", " ")
    budget_m = _SESSION_BUDGET_RE.search(last_user or "")
    if budget_m:
        facts["budget"] = f"LKR {budget_m.group(1).replace(',', '')}"
    city_m = re.search(
        r"(?:deliver(?:y|ed)? to|in|to)\s+([A-Za-z][A-Za-z0-9\s.-]{2,40})",
        last_user or "",
        re.I,
    )
    if city_m:
        facts["city"] = city_m.group(1).strip()
    recip_m = re.search(
        r"(?:for my|for)\s+(mom|mother|dad|father|wife|husband|partner|boss|friend|colleague|sister|brother|grandma|grandpa|[\w]+)",
        last_user or "",
        re.I,
    )
    if recip_m:
        facts["recipient"] = recip_m.group(1)
    avoid_words = []
    for pat in ("no chocolate", "no alcohol", "no perfume", "vegetarian", "no nuts"):
        if pat in text:
            avoid_words.append(pat)
    if avoid_words:
        facts["constraints"] = "; ".join(avoid_words)
    if any(w in text for w in ("sorry", "condolence", "passed", "funeral")):
        facts["tone"] = "somber"
    elif occasion and get_occasions().get(occasion, {}).get("tone") == "somber":
        facts["tone"] = "somber"
    bullets = list(facts.get("bullets") or [])
    if last_user and len(last_user) > 10:
        snippet = last_user.strip()[:120]
        if snippet and snippet not in bullets:
            bullets.append(snippet)
    facts["bullets"] = bullets[-8:]
    facts["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return facts

# =============================================================================
# Configuration
# =============================================================================

MCP_URL = os.environ.get("KAPRUKA_MCP_URL", "https://mcp.kapruka.com/mcp")
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT = float(os.environ.get("KAPRUKA_MCP_TIMEOUT", "30"))
USER_AGENT = os.environ.get(
    "KAPRUKA_MCP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY")
NIM_TIMEOUT = float(os.environ.get("NVIDIA_NIM_TIMEOUT", "60"))
NIM_RETRIES = int(os.environ.get("NVIDIA_NIM_RETRIES", "2"))
NIM_TEMPERATURE = float(os.environ.get("NVIDIA_NIM_TEMPERATURE", "0.3"))

MAX_TOOL_ROUNDS = int(os.environ.get("SEARCH_MAX_ROUNDS", "5"))
SEARCH_BUDGET = float(os.environ.get("SEARCH_BUDGET", "55"))
MIN_ROUND_SECONDS = float(os.environ.get("SEARCH_MIN_ROUND_SECONDS", "8"))

TRANSLATE_TIMEOUT = float(os.environ.get("TRANSLATE_TIMEOUT", "18"))
LANGBLY_API_KEY = os.environ.get("LANGBLY_API_KEY")
LANGBLY_URL = os.environ.get("LANGBLY_URL", "https://api.langbly.com/language/translate/v2")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

SITE_BASE = os.environ.get("KAPRUKA_SITE_BASE", "https://www.kapruka.com")

_TOOLS_CACHE: list | None = None
NULLISH = frozenset({"null", "none", "nil", "undefined", "na", "n/a", ""})

# =============================================================================
# Prompts
# =============================================================================

SYSTEM_PROMPT = """\
You are a warm, emotionally intelligent gifting partner for Kapruka — a Sri Lankan online store.

You are NOT customer support. You are NOT a search engine. You are a thoughtful Sri Lankan friend who genuinely wants to help someone pick the right gift.

VOICE & PERSONALITY
- Sound like a real person texting a friend — never corporate.
- Never say: "I'd be happy to assist", "Please let me know", "Certainly", "Kindly provide".
- Use natural Sri Lankan warmth. Occasional emojis are fine when they fit (😊 🥹 💔 😭 — don't overdo it).
- Good tone examples:
  • "Aww 🥹 that's such a lovely milestone."
  • "Aiyo 😭 Okay, don't panic. We can save this."
  • "Oh no 💔. That must be difficult."
  • "Haha, I think she'll love that."
  • Forgot anniversary: "Aiyo 😭 Don't panic. We can recover from this. A thoughtful note and flowers go much further than something expensive."
  • Girlfriend angry: "Aiyo 💔. Here's the plan — keep it sincere. Effort beats extravagance."
  • Condolences: "I'm really sorry to hear that 💙. Simple and respectful means the most right now."

EMOTIONAL INTELLIGENCE (read feelings first)
- Acknowledge how the person feels BEFORE getting practical.
- Celebrate happy moments with them.
- Be gentle during apologies, illness, grief, or hardship.
- In sombre situations: understated, respectful, never cheerful or celebratory.
- NEVER suggest cakes, balloons, or party items for loss, illness, condolences, or sombre apologies.
- NEVER ask insensitive questions (e.g. hobbies/interests of someone who passed away).
- Suggest only what is fitting — e.g. a respectful condolence flower arrangement.

CONVERSATION
- Talk naturally. Never interrogate. At most 1–2 questions at a time.
- Never sound like a form. Remember earlier context (occasion, budget, city, recipient).
- Think about relationships and emotions, not just products.
- Reassure on a small budget — thoughtful options always exist.

SMALL TALK
- Greetings ("hi", "hello", "thanks") are NOT gift requests.
- Reply warmly and briefly. Do NOT call ask_user for a simple greeting.
- Hi → something like "Hi 😊 I'm your gift concierge. Who are we spoiling today?"
- Thanks → something like "Haha, anytime 😊"

GIFT REASONING
- Do NOT search the user's literal words. Understand recipient, occasion, and the feeling they want.
- Decide what that moment calls for — sometimes one item, sometimes a thoughtful package.
- If they want one specific thing, find good options for it; don't pad unnecessarily.

SEARCH RULES (mandatory)
- Turn each idea into a short, specific product noun and run a SEPARATE kapruka_search_products call \
(e.g. q='flower bouquet', q='fruit basket', q='cookware'). Never search vague phrases like 'gift' or 'birthday gift'.
- Tailor to age and interests (cooking → cookware; reading → book).
- Do NOT pass a category filter unless you got the exact name from kapruka_list_categories.

UI PRESENTATION
- The app renders product cards below your message (photo, name, price, Add to cart).
- Write ONLY a warm conversational intro (1–3 sentences). The cards carry product details.
- NEVER output numbered/bulleted product catalogues, markdown (**bold**, [links]), prices, or kapruka.com links.
- Reference items by number only when the user must choose (e.g. "Option 2 feels the most romantic").
- Proactive follow-ups feel natural: "I've got the flowers sorted. Trust me, hand-delivering them lands \
much better than a courier 😊. Shall I add a note card too?"

WHEN TO ASK vs SEARCH (critical)
- DEFAULT TO SEARCH. If you can make reasonable gift picks, search first — show options, refine later.
- NEVER call ask_user for: angry/upset partner, apology/make-up, forgotten anniversary, stress/panic \
("aiyo", "help me", "don't panic"), condolences, get-well, or when recipient + situation are already clear.
- For "gf is mad at me" / "forgot anniversary" / "aiyoo help": empathize briefly IN your final reply, \
then kapruka_search_products immediately (flowers, chocolate, card). Do NOT ask budget or interests first.
- ask_user is ONLY for truly ambiguous requests with no recipient AND no occasion AND no situation \
(e.g. just "I need a gift" with zero context). Even then, ask at most ONE question.
- NEVER ask "What's your budget?" as a first response when the user is emotional — use profile budget or pick sensible mid-range options.
- NEVER ask "what does she like?" for apology/relationship-repair — flowers and chocolate are safe defaults.
- When asking, questions must sound like a friend texting — never "To pick the best option" or form-style bullets.

FOLLOW-UPS & CONTEXT
- Honour budget and context from earlier turns.
- For celebratory occasions, naturally offer complementary items (cake, flowers, card).
- Never offer celebration items in sombre situations.

CART & SELECTIONS
- add_to_cart: user picks from CURRENT SUGGESTIONS by 1-based number, name, or description.
- add_all_suggestions_to_cart: user wants everything suggested.
- remove_from_cart: drop items or clear the cart.
- add_instruction: save gift-wrapping, hamper requests, delivery notes, card messages verbatim.
- After cart/instruction actions, confirm in one short natural sentence.
- Use suggestion/cart numbers exactly as given in context.

BUDGET CHECK
- Before add_to_cart or add_all_suggestions_to_cart, check the cart subtotal in context.
- If adding would exceed their stated budget, warn them (item price, new total, overage) and ask before adding.
- Only add once within budget OR they clearly confirm going over.

DATA RULES
- Rely ONLY on tool data from this conversation — never invent products or prices.
- Never say "prices may change, check the website".
- Never duplicate the same product. Omit unset optional params — never pass "null" placeholders.
- If a search returns nothing, try a different concrete idea.

When USER PROFILE context is provided, bias searches and suggestions toward their gifting personality, \
style, typical budget, and default city — without ignoring their current request."""

UI_PRESENTATION_PROMPT = (
    "REMINDER: Product cards render below your reply. Your text is conversational context ONLY — "
    "no numbered lists, no bullet catalogues, no markdown, no (LKR …) price lines, no kapruka.com links."
)

TOOL_JSON_NUDGE = (
    "Do not output tool calls or JSON as your message. "
    "Either call the tool through the proper function-calling interface, "
    "or reply to me directly in plain, warm natural language."
)

TEXT_MODE_RESULT_PREFIX = (
    "Tool results — the UI shows these as product cards below your reply. "
    "Write a brief warm intro only; do NOT list names, prices or links in your message.\n\n"
)

SEARCH_FIRST_NUDGE = (
    "IMPORTANT — search now, do NOT call ask_user. The user gave enough context (who + situation). "
    "Call kapruka_search_products for fitting items immediately. "
    "Your reply should open with warm empathy (e.g. 'Aiyo 💔' / 'Don't panic 😭') then the cards do the rest."
)

_RECIPIENT_RE = re.compile(
    r"\b(mom|mother|dad|father|wife|husband|girlfriend|boyfriend|partner|gf|bf|"
    r"her|him|sister|brother|friend|boss|colleague|grandma|grandpa|hubby|babe|baby)\b",
    re.I,
)
_EMOTIONAL_URGENCY_RE = re.compile(
    r"\b(mad|angry|upset|furious|annoyed|fight|fighting|forgot|forgotten|"
    r"sorry|apolog|make up|make it up|panic|help me|save this|mess up|messed up|"
    r"aiyo|aiyoo|aiyyo|oh no|in trouble)\b",
    re.I,
)


def _should_search_first(text: str, profile: dict | None = None) -> bool:
    """True when ask_user would be wrong — search immediately instead."""
    low = (text or "").lower().strip()
    if not low:
        return False

    if _EMOTIONAL_URGENCY_RE.search(low):
        return True

    if detect_occasion(text):
        return True

    has_recipient = bool(_RECIPIENT_RE.search(low))
    has_gift_intent = bool(re.search(
        r"\b(gift|birthday|anniversary|flowers|cake|hamper|bouquet|present|something for)\b",
        low,
        re.I,
    ))
    if has_recipient and has_gift_intent:
        return True

    if has_recipient and profile and profile.get("default_budget"):
        if re.search(r"\b(gift|something|help|ideas|suggest|need)\b", low, re.I):
            return True

    return False

# =============================================================================
# Synthetic tools (not part of the MCP)
# =============================================================================

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "LAST RESORT ONLY — when the request has NO recipient, NO occasion, and NO situation at all. "
            "Do NOT use for angry partner, apology, forgot anniversary, stress/panic, grief, or get-well. "
            "Do NOT ask budget as first question when user is emotional. Max 1 conversational question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1–3 concise questions for the user.",
                }
            },
            "required": ["questions"],
        },
    },
}

CART_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": (
                "Add one or more CURRENT SUGGESTIONS to the cart. "
                "Use 1-based suggestion numbers from context."
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
            "description": "Add every current suggestion to the cart (e.g. 'add everything').",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": "Remove cart items by 1-based cart number, or clear the entire cart.",
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
                "Save a special instruction — gift wrapping, hamper build, delivery note, "
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

# =============================================================================
# JSON / tool-call parsing helpers
# =============================================================================

_PYTHON_TAG = "<|python_tag|>"
_TOOL_NAME_RE = re.compile(r'["\']name["\']\s*:\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']')
_ARGS_BLOCK_RE = re.compile(r'(?:parameters|arguments)["\']\s*:\s*(\{.*?\})', re.S)
_QUESTIONS_BLOCK_RE = re.compile(r'questions["\']\s*:\s*\[(.*)', re.S)


def _parse_json_or_literal(text: str):
    """Parse JSON or Python-literal text; return None on failure."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None


def _parse_tool_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = _parse_json_or_literal(raw)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _decode_escapes(s: str) -> str:
    """Turn literal \\uXXXX sequences into real characters (Tamil/Sinhala salvage)."""
    if not isinstance(s, str) or "\\u" not in s:
        return s

    def repl(m):
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)

    return re.sub(r"\\u([0-9a-fA-F]{4})", repl, s)


def _normalize_questions(raw) -> list[str]:
    """Coerce ask_user questions into a clean list of strings."""
    if isinstance(raw, str):
        parsed = _parse_json_or_literal(raw)
        if isinstance(parsed, list):
            raw = parsed
        else:
            original = raw
            s = original.strip()
            if s.startswith("[") and s.endswith("]"):
                s = s[1:-1]
            parts = re.split(r"['\"]\s*,\s*['\"]", s)
            cleaned = [p.strip().strip("'\"").strip() for p in parts]
            raw = [c for c in cleaned if c] or [original]
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    return [_decode_escapes(str(q).strip()) for q in raw if str(q).strip()][:3]


def _extract_balanced_objects(text: str) -> list[str]:
    """Yield balanced {...} chunks from text."""
    chunks = []
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                chunks.append(text[start : i + 1])
                start = None
    return chunks


def _dict_to_tool_call(obj: dict, valid_names: set[str]) -> dict | None:
    """Convert a parsed {name, parameters/arguments} dict into a normalized call."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if name not in valid_names:
        return None
    args = obj.get("parameters")
    if args is None:
        args = obj.get("arguments")
    if isinstance(args, str):
        args = _parse_tool_arguments(args)
    if not isinstance(args, dict):
        args = {}
    return {"name": name, "arguments": args}


def parse_text_tool_calls(content: str, valid_names: set[str]) -> list[dict]:
    """Recover well-formed tool calls the model printed as plain text."""
    if not content or "name" not in content:
        return []
    text = content.replace(_PYTHON_TAG, " ")
    calls = []
    for chunk in _extract_balanced_objects(text):
        obj = _parse_json_or_literal(chunk)
        call = _dict_to_tool_call(obj, valid_names) if isinstance(obj, dict) else None
        if call:
            calls.append(call)
    return calls


def salvage_text_tool_calls(content: str, valid_names: set[str]) -> list[dict]:
    """Best-effort recovery when JSON is malformed."""
    if not content:
        return []
    name_m = _TOOL_NAME_RE.search(content)
    if not name_m:
        return []
    name = name_m.group(1)
    if name not in valid_names:
        return []

    if name == "ask_user":
        block = _QUESTIONS_BLOCK_RE.search(content)
        scope = block.group(1) if block else content
        questions = []
        for q in re.findall(r'["\']([^"\']{3,}?)["\']', scope):
            q = q.strip()
            if q.lower() not in ("questions", "ask_user", "function", "parameters", "arguments", "type"):
                questions.append(_decode_escapes(q))
        return [{"name": "ask_user", "arguments": {"questions": questions[:3]}}] if questions else []

    am = _ARGS_BLOCK_RE.search(content)
    if am:
        args = _parse_json_or_literal(am.group(1))
        if isinstance(args, dict) and args:
            return [{"name": name, "arguments": args}]
    return []


def looks_like_tool_blob(content: str) -> bool:
    """True when assistant text is really a mangled tool call — must not reach the user."""
    if not content:
        return False
    s = content.strip()
    if _PYTHON_TAG in s:
        return True
    if re.search(r'\b(function_call|tool_calls?)\b', s, re.I):
        return True
    has_name = bool(_TOOL_NAME_RE.search(s))
    looks_jsonish = (
        s.startswith("{")
        or s.startswith("[")
        or '"type"' in s
        or '"parameters"' in s
        or '"arguments"' in s
        or '"function"' in s
    )
    return has_name and looks_jsonish


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


def normalize_tool_calls(message: dict, valid_names: set[str]) -> tuple[list[dict], bool]:
    """Return (normalized_calls, is_text_mode). is_text_mode=True when recovered from content."""
    structured = message.get("tool_calls") or []
    if structured:
        norm = []
        for c in structured:
            fn = c.get("function") or {}
            norm.append({
                "name": fn.get("name"),
                "arguments": _parse_tool_arguments(fn.get("arguments") or "{}"),
                "id": c.get("id"),
            })
        return norm, False

    content = message.get("content") or ""
    norm = parse_text_tool_calls(content, valid_names) or salvage_text_tool_calls(content, valid_names)
    return norm, True


# =============================================================================
# MCP client
# =============================================================================


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
                payload = line[len("data:") :].strip()
                if payload and payload != "[DONE]":
                    return _loads(payload)
        raise ValueError(f"No JSON data in SSE stream: {text[:300]}")
    return _loads(text)


class MCPSession:
    """Minimal MCP Streamable HTTP client."""

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
                "clientInfo": {"name": "kapruka-nim-search", "version": "2.0.0"},
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
    return [
        {
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


# =============================================================================
# Product extraction
# =============================================================================

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


def _id_from_url(url):
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
    if isinstance(u, list):
        u = u[0] if u else None
    if not isinstance(u, str) or not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return SITE_BASE.rstrip("/") + u
    return u


def _money(v):
    if isinstance(v, dict):
        return v.get("amount"), v.get("currency")
    return v, None


def _clean_desc(text):
    if not isinstance(text, str):
        return text
    s = text.strip()
    m = re.match(r"^\s*specialGifts\b.*?\b([A-Z]{4,})\b\s*", s)
    if m:
        s = s[m.end() :].strip()
    return s


def _looks_like_product(d: dict) -> bool:
    if not isinstance(d, dict) or _first(d, NAME_KEYS) is None:
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
            return
        for v in obj.values():
            _walk_products(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk_products(v, found)


def _coerce_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    parsed = _parse_json_or_literal(text)
    if parsed is not None:
        return parsed
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            parsed = _parse_json_or_literal(text[start : end + 1])
            if parsed is not None:
                return parsed
    return None


def _norm_url(u) -> str | None:
    if not isinstance(u, str) or not u.strip():
        return None
    s = u.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?")[0].split("#")[0]
    return s.rstrip("/") or None


def _norm_text(t) -> str:
    if t is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def extract_products(results: list) -> list:
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


# =============================================================================
# Answer sanitization
# =============================================================================

_CATALOG_LINE = re.compile(
    r"^\s*(?:\d+\.|[-•*])\s+.*(?:LKR|Rs\.?\s*\d|\(\s*LKR|\bLKR\b)",
    re.I,
)
_NUMBERED_PRODUCT = re.compile(r"^\s*\d+\.\s+")


def _line_mentions_product(line: str, names: set[str]) -> bool:
    norm = _norm_text(re.sub(r"[*_#\[\]()]", " ", line))
    return any(n and n in norm for n in names)


def _strip_catalog_from_answer(answer: str, products: list) -> str:
    if not answer or not products:
        return answer
    names = {
        _norm_text(p.get("name"))
        for p in products
        if p.get("name") and len(_norm_text(p.get("name"))) >= 3
    }
    if not names:
        return answer

    out = []
    for line in answer.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        is_catalog = (
            _CATALOG_LINE.match(s)
            or (_NUMBERED_PRODUCT.match(s) and _line_mentions_product(s, names))
            or (re.match(r"^\s*[-•*]\s+", s) and _line_mentions_product(s, names))
        )
        if not is_catalog:
            out.append(line.rstrip())

    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_EMPTY_CATALOG_FALLBACK = (
    "I pulled together some options that should work — take a look below 😊 "
    "Say a number to add one, or tell me if you'd like something different."
)


# =============================================================================
# NVIDIA NIM
# =============================================================================


class NimTimeout(Exception):
    """Transient NIM failure — degrade gracefully instead of 502."""


def nim_chat(messages: list, tools: list, timeout: float | None = None) -> dict:
    if not NIM_API_KEY:
        raise PermissionError(
            "NVIDIA_API_KEY is not set. Add it in your Vercel project's "
            "Environment Variables (get a key at https://build.nvidia.com)."
        )
    payload = {
        "model": NIM_MODEL,
        "messages": messages,
        "temperature": NIM_TEMPERATURE,
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
            if exc.code >= 500 and attempt < NIM_RETRIES:
                time.sleep(0.6 * (attempt + 1))
                continue
            if exc.code >= 500:
                raise NimTimeout(f"NIM HTTP {exc.code}: {detail[:200]}") from exc
            raise ValueError(f"NVIDIA NIM HTTP {exc.code}: {detail[:500]}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise NimTimeout(str(exc)) from exc


# =============================================================================
# Greeting / language / cart context
# =============================================================================

GREETING_WORDS = {
    "hi", "hii", "hiya", "hello", "helo", "hey", "heya", "yo", "hai", "sup",
    "howdy", "greetings", "ayubowan", "kohomada", "halo",
    "හායි", "හලෝ", "කොහොමද", "ආයුබෝවන්", "ඕව", "හායී",
    "வணக்கம்", "ஹலோ", "நலமா", "வாங்க",
}
THANKS_WORDS = {
    "thanks", "thank", "thx", "ty", "thankyou", "thanku", "appreciate",
    "ස්තූතියි", "ඔයාට ස්තූතියි", "ස්තුතියි",
    "நன்றி", "நன்றி உங்களுக்கு",
}
GREETING_PHRASES = (
    "how are you", "how r u", "how are u", "whats up", "what's up",
    "good morning", "good evening", "good afternoon", "good day", "nice to meet",
    "කොහොමද", "සුභ උදෑසනක්", "සුබ උදෑසනක්", "ඔයාට කොහොමද",
    "எப்படி இருக்கிறீர்கள்", "எப்படி இருக்கீங்க", "காலை வணக்கம்",
)
THANKS_PHRASES = (
    "thank you", "thanks a lot", "thanks so much", "much appreciated",
    "ස්තූතියි", "බොහොම ස්තූතියි",
    "மிக்க நன்றி", "ரொம்ப நன்றி",
)


def _normalize_short_text(text: str) -> str:
    raw = str(text or "").strip()
    norm = re.sub(r"[!?.,;:\"'()\[\]\-_/\\…]+", " ", raw.lower())
    return re.sub(r"\s+", " ", norm).strip()


def _is_thanks(text: str) -> bool:
    norm = _normalize_short_text(text)
    if not norm or len(norm.split()) > 6:
        return False
    if any(p in norm for p in THANKS_PHRASES):
        return True
    words = norm.split()
    return any(w in THANKS_WORDS for w in words)


def _is_greeting(text: str) -> bool:
    norm = _normalize_short_text(text)
    if not norm or len(norm.split()) > 5:
        return False
    if any(p in norm for p in GREETING_PHRASES):
        return True
    words = norm.split()
    return any(w in GREETING_WORDS for w in words)


def _greeting_reply(language: str | None, text: str = "") -> str:
    lang = (language or "en").lower()
    if _is_thanks(text):
        if lang == "si":
            return "හහ, කමක් නෑ 😊"
        if lang == "ta":
            return "Haha, anytime 😊"
        return "Haha, anytime 😊"

    if lang == "si":
        return "ආයුබෝවන් 😊 මම ඔයාගේ තෑගි උපදේශක. අද කාවද spoil කරන්නේ?"
    if lang == "ta":
        return "வணக்கம் 😊 நான் உங்கள் பரிசு உதவியாளர். இன்று யாருக்கு surprise?"
    return "Hi 😊 I'm your gift concierge. Who are we spoiling today?"


def _language_message(language: str | None) -> str | None:
    name = LANG_NAMES.get((language or "").lower())
    if not name or name == "English":
        return None
    return (
        f"The user writes in {name}. Reply and any clarifying questions must be fluent, "
        f"natural {name}. Keep product names, prices, and currency codes as the tools return them."
    )


def _context_message(suggestions: list, cart: list, instructions: list) -> str | None:
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
        "Live cart/selection context (NOT catalogue data — use only for cart actions):\n"
        + "\n".join(lines)
    )


# =============================================================================
# Supabase
# =============================================================================


def _supabase_request(
    path: str,
    token: str | None = None,
    method: str = "GET",
    body: dict | None = None,
    timeout: float = 10,
):
    """Supabase REST call. Returns parsed JSON or None on any failure."""
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
        req = urllib.request.Request(
            url, data=json.dumps(body or {}).encode("utf-8"), headers=headers, method=method
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError:
        # Invalid/expired token, missing table, RLS denial — search must still work.
        return None
    except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _verify_supabase_user(token: str | None) -> dict | None:
    if not token or not SUPABASE_URL:
        return None
    try:
        data = _supabase_request("/auth/v1/user", token=token)
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def _load_profile(token: str | None) -> dict | None:
    user = _verify_supabase_user(token)
    if not user:
        return None
    uid = user.get("id")
    if not uid:
        return None
    rows = _supabase_request(f"/rest/v1/profiles?id=eq.{uid}&select=*", token=token)
    return rows[0] if isinstance(rows, list) and rows else None


def _load_recipients(token: str, uid: str) -> list:
    rows = _supabase_request(
        f"/rest/v1/recipients?user_id=eq.{uid}&select=*&order=updated_at.desc&limit=20",
        token=token,
    )
    return rows if isinstance(rows, list) else []


def _load_wishlist(token: str, uid: str) -> list:
    rows = _supabase_request(
        f"/rest/v1/wishlist_items?user_id=eq.{uid}&select=*&order=created_at.desc&limit=15",
        token=token,
    )
    return rows if isinstance(rows, list) else []


def _load_order_history(token: str, uid: str) -> list:
    rows = _supabase_request(
        f"/rest/v1/order_history?user_id=eq.{uid}&select=*&order=ordered_at.desc&limit=8",
        token=token,
    )
    return rows if isinstance(rows, list) else []


def _patch_profile(token: str, uid: str, patch: dict) -> None:
    if patch:
        _supabase_request(
            f"/rest/v1/profiles?id=eq.{uid}",
            token=token,
            method="PATCH",
            body=patch,
        )


def _persist_session_facts(
    token: str,
    uid: str,
    profile: dict,
    conversation: list,
    last_user: str,
    answer: str,
) -> None:
    if not token or not uid or not answer:
        return
    new_facts = extract_session_facts(
        conversation, last_user, answer, profile.get("session_facts") or {}
    )
    try:
        _patch_profile(token, uid, {"session_facts": new_facts})
    except Exception:
        pass


def _load_user_context(access_token: str | None) -> tuple[dict | None, str | None, list, list, list]:
    """Return (profile, uid, recipients, wishlist, orders). Never raises."""
    if not access_token:
        return None, None, [], [], []
    try:
        profile = _load_profile(access_token)
        if not profile:
            return None, None, [], [], []
        auth_user = _verify_supabase_user(access_token)
        uid = auth_user.get("id") if auth_user else None
        if not uid:
            return profile, None, [], [], []
        return (
            profile,
            uid,
            _load_recipients(access_token, uid),
            _load_wishlist(access_token, uid),
            _load_order_history(access_token, uid),
        )
    except Exception:
        return None, None, [], [], []


# =============================================================================
# Translation
# =============================================================================


def _translate(text, source, target, timeout=TRANSLATE_TIMEOUT):
    text = (text or "").strip()
    src, tgt = (source or "").lower(), (target or "").lower()
    if not text or not tgt or src == tgt or not LANGBLY_API_KEY:
        return text
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
        out = _html.unescape(out).strip()
        return out or text
    except Exception:
        return text


# =============================================================================
# Message assembly & cart actions
# =============================================================================


def _build_messages(
    conversation: list,
    cart_context: str | None = None,
    language: str | None = None,
    profile_context: str | None = None,
    extra_contexts: list | None = None,
) -> list:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": UI_PRESENTATION_PROMPT},
    ]
    lang_msg = _language_message(language)
    if lang_msg:
        msgs.append({"role": "system", "content": lang_msg})
    if profile_context:
        msgs.append({"role": "system", "content": profile_context})
    for block in extra_contexts or []:
        if block:
            msgs.append({"role": "system", "content": block})
    if cart_context:
        msgs.append({"role": "system", "content": cart_context})
    for turn in conversation[-12:]:
        role, content = turn.get("role"), str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    return msgs


def _resolve_cart_action(name: str, args: dict, suggestions: list):
    args = args or {}
    if name == "add_all_suggestions_to_cart":
        return {"action": "add", "products": [dict(p, qty=1) for p in suggestions]}
    if name == "add_to_cart":
        items, qtys = args.get("items") or [], args.get("quantities") or []
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
                        pass
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


def _append_assistant_message(messages: list, message: dict, structured: list, text_mode: bool) -> None:
    if text_mode:
        messages.append({"role": "assistant", "content": message.get("content") or ""})
    else:
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": structured,
        })


def _execute_tool_calls(
    norm: list[dict],
    *,
    text_mode: bool,
    mcp: MCPSession,
    suggestions: list,
    trace: list,
    results: list,
    cart_actions: list,
    messages: list,
) -> tuple[bool, list[str]]:
    """Run tool calls. Returns (did_cart, text_outputs for text_mode)."""
    text_outputs = []
    did_cart = False

    for c in norm:
        name = c.get("name")
        args = c.get("arguments") or {}
        call_id = c.get("id")

        if name == "ask_user":
            if not text_mode and call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": "(no questions)"})
            continue

        if name in CART_TOOL_NAMES:
            action = _resolve_cart_action(name, args, suggestions)
            if action:
                cart_actions.append(action)
                did_cart = True
            conf = _cart_confirmation(action)
            trace.append({"tool": name, "arguments": args})
            if not text_mode and call_id:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": conf})
            continue

        args = sanitize_args(args)
        try:
            output = mcp.call_tool(name, args)
        except Exception as exc:
            output = f"ERROR calling {name}: {exc}"
        trace.append({"tool": name, "arguments": args})
        results.append({"tool": name, "arguments": args, "output": output})
        if text_mode:
            text_outputs.append(f"{name} ->\n{output}")
        elif call_id:
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})

    return did_cart, text_outputs


# =============================================================================
# Search orchestration
# =============================================================================


def search(conversation, allow_questions: bool = True, context: dict | None = None) -> dict:
    global _TOOLS_CACHE

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

    # Fast path: greetings / thanks without an active cart or suggestions.
    if (_is_greeting(last_user) or _is_thanks(last_user)) and not suggestions and not cart:
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": _greeting_reply("en", last_user),
            "answer_local": _greeting_reply(language, last_user),
            "user_en": last_user,
            "products": [],
            "cart_actions": [],
            "tools_available": [],
            "tool_calls": [],
            "results": [],
        }

    mcp = MCPSession()
    try:
        mcp.initialize()
    except Exception as exc:
        return {
            "ok": False,
            "query": last_user,
            "model": NIM_MODEL,
            "error": f"Could not reach Kapruka search service: {exc}",
        }

    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = mcp.list_tools()
    tools = _TOOLS_CACHE

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
    profile, uid, recipients, wishlist, orders = _load_user_context(access_token)

    search_first = _should_search_first(user_en, profile)
    if search_first:
        allow_questions = False

    openai_tools = mcp_tools_to_openai(tools) + CART_TOOLS
    if allow_questions:
        openai_tools = openai_tools + [ASK_USER_TOOL]

    extra_ctx = [
        playbook_message(user_en, conv),
        session_facts_message(profile),
        recipients_message(recipients),
        wishlist_message(wishlist),
        order_history_message(orders),
        upcoming_occasions_message(recipients),
    ]
    messages = _build_messages(
        conv,
        _context_message(suggestions, cart, instructions),
        None,
        profile_message(profile),
        extra_ctx,
    )
    if search_first:
        messages.append({"role": "system", "content": SEARCH_FIRST_NUDGE})

    trace, results, cart_actions = [], [], []
    tool_names = [t.get("name") for t in tools]
    valid_names = set(tool_names) | CART_TOOL_NAMES
    if allow_questions:
        valid_names.add("ask_user")

    def finalize(answer: str) -> dict:
        products = extract_products(results)
        if products:
            answer = _strip_catalog_from_answer(answer, products)
            if not (answer or "").strip():
                answer = _EMPTY_CATALOG_FALLBACK
        local = _translate(answer, "en", target_lang) if (target_lang and answer) else answer
        if uid and profile and answer:
            _persist_session_facts(access_token, uid, profile, conv, user_en, answer)
        return {
            "ok": True,
            "query": last_user,
            "model": NIM_MODEL,
            "answer": answer,
            "answer_local": local,
            "user_en": user_en,
            "products": products,
            "cart_actions": cart_actions,
            "tools_available": tool_names,
            "tool_calls": trace,
            "results": results,
        }

    def ask(questions: list) -> dict:
        qs = questions[:3]
        local = qs
        if target_lang and qs:
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

    reserve = 12 if target_lang else 0
    deadline = time.monotonic() + max(20.0, SEARCH_BUDGET - reserve)
    json_corrections = 0
    max_json_corrections = 2

    def degrade(partial: str | None = None) -> dict:
        if partial:
            return finalize(partial)
        if results:
            return finalize(
                "Got some options for you — took me a sec longer than usual, but they're below 😊"
            )
        if cart_actions:
            return finalize("Done — cart's updated 👍")
        return finalize(
            "Sorry, that took longer than I expected on my end. "
            "Send your last message again and we'll pick up where we left off."
        )

    for _ in range(MAX_TOOL_ROUNDS):
        remaining = deadline - time.monotonic()
        if remaining < MIN_ROUND_SECONDS:
            return degrade()

        try:
            completion = nim_chat(messages, openai_tools, timeout=min(NIM_TIMEOUT, remaining))
        except NimTimeout:
            return degrade()

        message = completion["choices"][0]["message"]
        structured = message.get("tool_calls") or []
        norm, text_mode = normalize_tool_calls(message, valid_names)

        _append_assistant_message(messages, message, structured, text_mode)

        if not norm:
            content = message.get("content") or ""
            if looks_like_tool_blob(content) and json_corrections < max_json_corrections:
                json_corrections += 1
                messages.append({"role": "user", "content": TOOL_JSON_NUDGE})
                continue
            if looks_like_tool_blob(content):
                return finalize("Hi 😊 I'm your gift concierge. Who are we spoiling today?")
            return finalize(content)

        if allow_questions:
            for c in norm:
                if c["name"] == "ask_user":
                    questions = _normalize_questions((c.get("arguments") or {}).get("questions"))
                    if questions:
                        return ask(questions)

        if not allow_questions and any(c.get("name") == "ask_user" for c in norm):
            norm = [c for c in norm if c.get("name") != "ask_user"]
            if not norm:
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not ask clarifying questions. Search Kapruka now — "
                        "flower bouquet, chocolate hamper, greeting card — then reply with empathy."
                    ),
                })
                continue

        did_cart, text_outputs = _execute_tool_calls(
            norm,
            text_mode=text_mode,
            mcp=mcp,
            suggestions=suggestions,
            trace=trace,
            results=results,
            cart_actions=cart_actions,
            messages=messages,
        )

        if text_mode and (text_outputs or did_cart):
            chunks = []
            if text_outputs:
                chunks.append(TEXT_MODE_RESULT_PREFIX + "\n\n".join(text_outputs))
            if did_cart:
                chunks.append(
                    "Cart/instructions updated — confirm to the user in one short, natural sentence."
                )
            chunks.append("Do NOT call tools again; reply to the user now.")
            messages.append({"role": "user", "content": "\n\n".join(chunks)})

    if results:
        return finalize("Pulled together some options — take a look below 😊")
    if cart_actions:
        return finalize("Done — cart's updated 👍")
    return finalize(
        "Hmm, I couldn't quite finish the search in time. "
        "Try again with a bit more detail and we'll nail it."
    )


# =============================================================================
# HTTP handler
# =============================================================================


class handler(BaseHTTPRequestHandler):
    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _run(self, conversation, allow_questions: bool = True, context: dict | None = None):
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

        conversation = data.get("messages")
        if not isinstance(conversation, list) or not conversation:
            conversation = [{"role": "user", "content": data.get("query", "")}]

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
