"""Kapruka gift concierge — agentic product search over the Kapruka MCP.

POST /api/search   body: {"query": "..."} or {"messages": [...], ...}
GET  /api/search?q=<natural language requirements>

Pipeline:
  1. Open an MCP session and discover tools at runtime.
  2. Run a tool-calling loop via Google Gemini (primary) with NVIDIA NIM fallback.
  3. Execute Kapruka searches, cart actions, and clarifying questions.
  4. Return a warm conversational answer plus product cards for the UI.

Requires GEMINI_API_KEY and/or NVIDIA_API_KEY. Standard library only — self-contained (no sibling module imports).
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
from concurrent.futures import ThreadPoolExecutor
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
_TRENDS_CACHE: dict | None = None
_TRENDS_CACHE_AT: float = 0.0
_TRENDS_TTL = float(os.environ.get("TRENDS_TTL_SECONDS", "21600"))  # 6h in-memory cache


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


def _fetch_trends_from_supabase() -> dict | None:
    """Latest web-distilled trends from the trends_cache table (anon read).
    Returns None on any failure so the static file can take over."""
    rows = _supabase_request("/rest/v1/trends_cache?id=eq.global&select=data", timeout=4)
    if isinstance(rows, list) and rows:
        data = rows[0].get("data")
        if isinstance(data, dict) and (data.get("bestsellers") or data.get("style_trends")):
            return data
    return None


def get_trends() -> dict:
    """Curated static trends (api/data/trends.json) overlaid with the latest
    web-refreshed trends from Supabase. Cached in-memory per warm instance so
    the chat path pays at most one short DB read every TTL window."""
    global _TRENDS_CACHE, _TRENDS_CACHE_AT
    now = time.monotonic()
    if _TRENDS_CACHE is not None and (now - _TRENDS_CACHE_AT) < _TRENDS_TTL:
        return _TRENDS_CACHE
    merged = _load_json_data("trends.json") or {}
    try:
        live = _fetch_trends_from_supabase()
    except Exception:
        live = None
    if live:
        if isinstance(live.get("bestsellers"), list) and live["bestsellers"]:
            merged["bestsellers"] = live["bestsellers"]
        if isinstance(live.get("style_trends"), dict):
            base = dict(merged.get("style_trends") or {})
            base.update({k: v for k, v in live["style_trends"].items() if isinstance(v, list) and v})
            merged["style_trends"] = base
        merged["_source"] = live.get("_source") or "web"
    _TRENDS_CACHE = merged
    _TRENDS_CACHE_AT = now
    return _TRENDS_CACHE


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


# --- Order tracking -------------------------------------------------------- #
_TRACK_STRONG_RE = re.compile(
    r"\b(track|tracking|order status|delivered yet|arrived yet|on its way|"
    r"dispatch(ed)?|shipped|out for delivery)\b",
    re.I,
)
_TRACK_SOFT_RE = re.compile(
    r"\b((any |an )?update on|update (on|about)|status (of|on)|where('?s| is)|"
    r"how('?s| is)|has it arrived|did it arrive|when will it)\b",
    re.I,
)
_OWNED_ORDER_RE = re.compile(
    r"\b(my|the|her|his|their|our)\b[^.?!]{0,40}\b"
    r"(order|gift|delivery|parcel|package|present|flowers|cake|hamper|bouquet)\b",
    re.I,
)
# Relationship words -> the relationship values stored on recipients.
_REL_SYNONYMS = {
    "sister": {"sister"}, "brother": {"brother"},
    "mom": {"mother", "mom", "mum"}, "mum": {"mother", "mom", "mum"},
    "mother": {"mother", "mom", "mum"}, "dad": {"father", "dad"},
    "father": {"father", "dad"}, "wife": {"wife"}, "husband": {"husband"},
    "gf": {"girlfriend", "partner"}, "girlfriend": {"girlfriend", "partner"},
    "bf": {"boyfriend", "partner"}, "boyfriend": {"boyfriend", "partner"},
    "partner": {"girlfriend", "boyfriend", "partner", "wife", "husband"},
    "friend": {"friend"}, "grandma": {"grandmother", "grandma"},
    "grandpa": {"grandfather", "grandpa"},
}


def _is_order_tracking_request(text: str) -> bool:
    """True when the user is asking about an existing order's status, not shopping."""
    low = (text or "").lower()
    if _TRACK_STRONG_RE.search(low):
        return True
    if _TRACK_SOFT_RE.search(low) and (_OWNED_ORDER_RE.search(low) or "my order" in low):
        return True
    return False


def _resolve_tracked_orders(text: str, orders: list | None, recipients: list | None) -> list:
    """Pick the order(s) the user means — by recipient name, by relationship
    (sister -> the recipient named X -> their order), else the most recent."""
    if not orders:
        return []
    low = (text or "").lower()
    by_name = []
    for o in orders:
        rn = (o.get("recipient_name") or "").lower().strip()
        if rn and rn.split()[0] in low:
            by_name.append(o)
    if by_name:
        return by_name

    rel_to_names: dict[str, set] = {}
    for r in recipients or []:
        rel = (r.get("relationship") or "").lower().strip()
        nm = (r.get("name") or "").lower().strip()
        if rel and nm:
            rel_to_names.setdefault(rel, set()).add(nm)
    wanted_names: set = set()
    for word in re.findall(r"[a-z]+", low):
        syns = _REL_SYNONYMS.get(word)
        if not syns:
            continue
        for rel, names in rel_to_names.items():
            if rel in syns:
                wanted_names |= names
    if wanted_names:
        by_rel = [o for o in orders if (o.get("recipient_name") or "").lower().strip() in wanted_names]
        if by_rel:
            return by_rel

    # Generic "any update on my order / is it here yet" -> most recent order.
    return [orders[0]]


def order_tracking_message(text: str, orders: list | None, recipients: list | None) -> str | None:
    matched = _resolve_tracked_orders(text, orders, recipients)
    lines = [
        "ORDER TRACKING REQUEST — the user wants a STATUS UPDATE on an existing order, "
        "NOT a product search. Do NOT call kapruka_search_products and do NOT show product cards."
    ]
    if not matched:
        lines.append(
            "- No order is on file for this user. If they're signed in, gently say there's no order on "
            "record yet. Otherwise ask for their order reference number, then call any available Kapruka "
            "order-tracking/status tool with it."
        )
    else:
        lines.append("- Matching order(s) on file:")
        for o in matched[:3]:
            parts = []
            if o.get("recipient_name"):
                parts.append(f"for {o['recipient_name']}")
            if o.get("items_summary"):
                parts.append(str(o["items_summary"]))
            if o.get("order_ref"):
                parts.append(f"ref {o['order_ref']}")
            if o.get("grand_total"):
                parts.append(f"total {o.get('currency', 'LKR')} {o['grand_total']}")
            if o.get("ordered_at"):
                parts.append(f"ordered {str(o['ordered_at'])[:10]}")
            lines.append("  • " + " — ".join(parts))
        lines.append(
            "- If a Kapruka tool for order status/tracking exists (e.g. a tool whose name contains "
            "'track', 'order', or 'status'), CALL it with the order reference above to fetch live "
            "delivery status, then report what it returns."
        )
        lines.append(
            "- If there is no live tracking tool or it returns nothing, give a clear report from the "
            "details above (what was ordered, for whom, the reference, the order date and total) and tell "
            "them they can track it on Kapruka with that reference."
        )
    lines.append("- Reply as a tidy, warm status report in plain sentences — no product cards, no upsell.")
    return "\n".join(lines)


# --- Direct account/session workflows (cart, wishlist, orders, checkout) ---- #
# The user is managing their own session ("what's in my cart", "show my
# wishlist", "what did I order", "empty my cart", "checkout") rather than
# shopping. These must break out of the gift-question script and answer from
# data already in context, never trigger a product search.
_VIEW_CUE_RE = re.compile(
    r"\b(show|see|view|display|look at|preview|pull up|open|check|list|read|tell me|"
    r"what(?:'?s| is| are| ?have| ?did)?|whats|how many|how much|"
    r"anything|do i have|is there anything)\b",
    re.I,
)
_CART_NOUN_RE = re.compile(r"\b(cart|basket|bag|trolley)\b", re.I)
_WISHLIST_NOUN_RE = re.compile(
    r"\b(wish ?list|saved (items?|gifts?|things?)|favou?rites?|hearted)\b", re.I
)
_ORDERS_STRONG_RE = re.compile(
    r"\b(order history|purchase history|bought before|ordered before|"
    r"past orders?|previous orders?|earlier orders?|my orders)\b",
    re.I,
)
_ORDERS_WEAK_RE = re.compile(r"\b(orders?|purchases?)\b", re.I)
_CLEAR_CART_RE = re.compile(
    r"\b(empty|clear|wipe|reset|remove everything|delete everything|"
    r"start over)\b[^.?!]{0,25}\b(cart|basket|bag)\b"
    r"|\b(cart|basket|bag)\b[^.?!]{0,25}\b(empty|clear|wipe|reset)\b",
    re.I,
)
_CHECKOUT_RE = re.compile(
    r"\b(check ?out|place (?:my |the )?order|pay(?: now)?|payment|"
    r"proceed to (?:pay|checkout)|complete (?:my |the )?order|buy now)\b",
    re.I,
)
_CART_ADD_RE = re.compile(r"\b(add|put|drop|throw|place|save)\b", re.I)


def _view_cue(low: str) -> bool:
    return bool(_VIEW_CUE_RE.search(low) or re.search(r"\b(total|subtotal|cost)\b", low))


def _account_intent(text: str) -> str | None:
    """Direct session-management intent, or None. Order matters (most specific
    first). Callers should only use this when it is NOT an order-tracking
    request, which takes priority."""
    low = (text or "").lower()
    if not low.strip():
        return None
    if _CLEAR_CART_RE.search(low):
        return "clear_cart"
    if _CHECKOUT_RE.search(low):
        return "checkout"
    if _CART_NOUN_RE.search(low) and not _CART_ADD_RE.search(low) and _view_cue(low):
        return "view_cart"
    if _WISHLIST_NOUN_RE.search(low) and not re.search(
        r"\b(add|save|put|remove|delete|take off)\b", low
    ):
        return "view_wishlist"
    if _ORDERS_STRONG_RE.search(low):
        return "view_orders"
    if _ORDERS_WEAK_RE.search(low) and _view_cue(low):
        return "view_orders"
    return None


# "see/show them" referring to the cart or wishlist just mentioned.
_VIEW_THEM_RE = re.compile(
    r"\b(see|show|view|display|look at|check out|preview|pull up)\b"
    r"[^.?!]{0,30}\b(them|these|those|it|the (items?|list|ones?))\b",
    re.I,
)


def _is_view_them_request(text: str) -> bool:
    return bool(_VIEW_THEM_RE.search(text or ""))


def _price_amount(raw) -> str | None:
    if raw in (None, ""):
        return None
    m = re.search(r"[\d,]+(?:\.\d+)?", str(raw))
    return m.group(0).replace(",", "") if m else None


def _saved_item_to_product(it: dict, group: str) -> dict:
    """Normalise a wishlist row or cart entry into the product-card shape."""
    return {
        "id": it.get("id") or it.get("product_id"),
        "name": it.get("name"),
        "price": _price_amount(it.get("price")),
        "currency": it.get("currency") or "LKR",
        "image": it.get("image"),
        "url": it.get("url"),
        "description": it.get("description") or "",
        "customizable": bool(it.get("customizable")),
        "customization_type": it.get("customization_type"),
        "in_stock": True,
        "group": group,
    }


def _format_cart_lines(cart: list) -> tuple[list, float, str]:
    lines, subtotal, cur = [], 0.0, "LKR"
    for p in cart:
        qty = p.get("qty", 1) or 1
        cur = p.get("currency") or cur
        price = p.get("price")
        try:
            line_total = float(re.sub(r"[^0-9.]", "", str(price))) * qty
        except (TypeError, ValueError):
            line_total = 0.0
        subtotal += line_total
        ptxt = f" — {cur} {price} each" if price not in (None, "") else ""
        lines.append(f"  • {p.get('name')} x{qty}{ptxt}")
    return lines, subtotal, cur


def account_intent_message(
    intent: str, cart: list | None, wishlist: list | None, orders: list | None
) -> str | None:
    cart = cart or []
    if intent == "view_cart":
        head = [
            "CART VIEW REQUEST — the user wants to know what's in their cart. "
            "Do NOT search products and do NOT show cards. Read it back from the live cart."
        ]
        if not cart:
            head.append("- The cart is currently EMPTY. Say so warmly and offer to find something.")
        else:
            lines, subtotal, cur = _format_cart_lines(cart)
            head.append("- Items in the cart:")
            head.extend(lines)
            head.append(f"- Subtotal: {cur} {subtotal:,.0f}")
            head.append("- List each item (name + qty) and the subtotal in a tidy, friendly reply.")
        return "\n".join(head)
    if intent == "view_wishlist":
        head = [
            "WISHLIST VIEW REQUEST — the user wants to see their saved/wishlisted items. "
            "Do NOT search products and do NOT show cards."
        ]
        if not wishlist:
            head.append(
                "- Nothing is saved to the wishlist yet (or they're not signed in). "
                "Say so gently and offer to find something they could save."
            )
        else:
            head.append("- Saved items:")
            for w in wishlist[:10]:
                nm = w.get("name") or "item"
                pr = w.get("price")
                head.append(f"  • {nm}" + (f" ({pr})" if pr else ""))
            head.append("- List them warmly; offer to add any to the cart.")
        return "\n".join(head)
    if intent == "view_orders":
        head = [
            "ORDER HISTORY REQUEST — the user wants to see what they've ordered before "
            "(NOT live tracking). Do NOT search products."
        ]
        if not orders:
            head.append("- No past orders on file (or not signed in). Say so gently.")
        else:
            head.append("- Past orders:")
            for o in orders[:5]:
                parts = []
                if o.get("recipient_name"):
                    parts.append(f"for {o['recipient_name']}")
                if o.get("items_summary"):
                    parts.append(str(o["items_summary"]))
                if o.get("order_ref"):
                    parts.append(f"ref {o['order_ref']}")
                if o.get("ordered_at"):
                    parts.append(str(o["ordered_at"])[:10])
                if parts:
                    head.append("  • " + " — ".join(parts))
            head.append("- Summarise warmly; offer to reorder or find something similar.")
        return "\n".join(head)
    if intent == "clear_cart":
        if not cart:
            return (
                "CLEAR CART REQUEST — but the cart is already EMPTY. Just tell them there's "
                "nothing to clear, and offer to help find a gift. Do NOT call remove_from_cart."
            )
        return (
            "CLEAR CART REQUEST — the user wants to empty their cart. Call remove_from_cart with "
            "clear=true, then confirm warmly that the cart is now empty. Do NOT search products."
        )
    if intent == "checkout":
        head = ["CHECKOUT REQUEST — the user wants to pay / place the order."]
        if not cart:
            head.append(
                "- The cart is EMPTY, so there's nothing to check out. Gently say so and offer "
                "to find something first. Do NOT search beyond that."
            )
        else:
            lines, subtotal, cur = _format_cart_lines(cart)
            head.append("- Cart ready to check out:")
            head.extend(lines)
            head.append(f"- Subtotal: {cur} {subtotal:,.0f}")
            head.append(
                "- Confirm the cart and subtotal, then tell them to open the cart and tap "
                "Checkout in the app to enter delivery details and pay — there is no in-chat "
                "payment step. Do NOT search products."
            )
        return "\n".join(head)
    return None


def trends_message(user_text: str, recipients: list | None = None, orders: list | None = None) -> str | None:
    """Soft trend bias: lean toward what's popular / in-style / their own patterns,
    but only when the user hasn't pinned down specifics. Never overrides a clear request."""
    trends = get_trends()
    if not trends:
        return None
    lines = [
        "TRENDS TO CONSIDER (soft bias only — apply when the user is open or vague; "
        "NEVER override what they explicitly asked for, and don't skip a clarifying question for it):"
    ]
    bestsellers = trends.get("bestsellers") or []
    if bestsellers:
        lines.append(
            f"- Currently popular gifts: {', '.join(bestsellers[:8])}. "
            "When unsure, favour proven, well-loved picks over obscure ones."
        )
    low = (user_text or "").lower()
    styles = trends.get("style_trends") or {}
    matched = []
    for item, cues in styles.items():
        if isinstance(cues, list) and cues and re.search(rf"\b{re.escape(item)}s?\b", low):
            matched.append(f"{item} → {', '.join(cues[:3])}")
    if matched:
        lines.append(
            "- In style right now (use to enrich a fashion search only if they gave no colour/style of their own): "
            + "; ".join(matched[:4])
        )
    if recipients or orders:
        lines.append(
            "- Personal trend: if their saved recipients, wishlist, or recent orders show a repeated theme, "
            "lean into that pattern rather than a generic pick."
        )
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


def _soonest_occasion(recipients: list | None, within_days: int = 10):
    """The nearest saved birthday/anniversary within the window: (name, label,
    days, isodate) or None."""
    if not recipients:
        return None
    today = date.today()
    best = None
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
            days = (this_year - today).days
            if 0 <= days <= within_days and (best is None or days < best[2]):
                best = (name, label, days, this_year.isoformat())
    return best


def delivery_timing_message(recipients: list | None, within_days: int = 10) -> str | None:
    """Nudge the agent to flag delivery timing when a saved occasion is near."""
    occ = _soonest_occasion(recipients, within_days)
    if not occ:
        return None
    name, label, days, iso = occ
    when = "today" if days == 0 else ("tomorrow" if days == 1 else f"in {days} days")
    urgency = "It's tight on time — " if days <= 3 else ""
    return (
        f"DELIVERY TIMING — {name}'s {label} is {when} ({iso}). {urgency}"
        "When suggesting, prioritise items that can realistically arrive in time and gently flag the "
        "timing (e.g. 'to land before the day, let's lock this in soon'). Do NOT invent exact delivery "
        "dates or couriers — just nudge on timeliness and ordering promptly."
    )


_SESSION_BUDGET_RE = re.compile(
    # A budget cue (under/budget/around/rs/lkr…) followed by a number, allowing a
    # few filler words in between ("budget around 5000", "around 5k", "rs. 2,000").
    r"(?:under|below|max|budget|around|about|approx(?:imately)?|roughly|rs\.?|lkr)"
    r"[^\d]{0,10}([0-9][0-9,]*\s*k?)",
    re.I,
)


def extract_session_facts(
    conversation: list,
    last_user: str,
    answer: str,
    existing: dict | None,
    scenario_reset: bool = False,
) -> dict:
    facts = dict(existing or {})
    if scenario_reset:
        for key in ("recipient_profile", "recipient", "occasion", "constraints", "tone", "bullets"):
            facts.pop(key, None)
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
    # Persist the richer recipient profile (age/colour/style/tastes) so the gift
    # brief can recall it in a future chat, not just within this session.
    mem = _extract_session_memory(conversation, last_user)
    if mem.get("recipient") or _session_has_specifics(mem):
        rp = dict(facts.get("recipient_profile") or {})
        for key in ("recipient", "age", "color", "style", "taste"):
            if mem.get(key):
                rp[key] = mem[key]
        facts["recipient_profile"] = rp
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
# Per-call cap for a single model request. Kept well under the function's total
# budget so one slow call can never consume the whole request — the loop, the
# curation step and the salvage search all still get their turn.
NIM_TIMEOUT = float(os.environ.get("NVIDIA_NIM_TIMEOUT", "35"))
NIM_RETRIES = int(os.environ.get("NVIDIA_NIM_RETRIES", "2"))
NIM_TEMPERATURE = float(os.environ.get("NVIDIA_NIM_TEMPERATURE", "0.3"))

# Google Gemini — primary agent model (OpenAI-compatible endpoint).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT", "35"))
GEMINI_RETRIES = int(os.environ.get("GEMINI_RETRIES", "1"))
GEMINI_TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.3"))
# Which provider to try first when both keys are set: gemini (default) or nim.
LLM_PRIMARY = os.environ.get("LLM_PRIMARY", "gemini").strip().lower()

MAX_TOOL_ROUNDS = int(os.environ.get("SEARCH_MAX_ROUNDS", "5"))
SEARCH_BUDGET = float(os.environ.get("SEARCH_BUDGET", "55"))
MIN_ROUND_SECONDS = float(os.environ.get("SEARCH_MIN_ROUND_SECONDS", "8"))
# Time set aside at the end of the budget so the model's curation/scoring step
# (which decides the cards) always has room to run, even if search ran long.
CURATION_RESERVE = float(os.environ.get("CURATION_RESERVE", "16"))

# Strategy layer — a dedicated model that turns the gathered context (occasion,
# relationship, personality, constraints, budget) into an explicit gifting
# STRATEGY (angle + concrete search queries + a reject rule) BEFORE the search
# loop runs. Defaults to the main NIM model/key so it works out of the box;
# point STRATEGY_NIM_MODEL at a separate (e.g. second 70B) deployment to split it.
STRATEGY_ENABLED = os.environ.get("STRATEGY_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
STRATEGY_NIM_BASE_URL = os.environ.get("STRATEGY_NIM_BASE_URL", NIM_BASE_URL)
STRATEGY_NIM_MODEL = os.environ.get("STRATEGY_NIM_MODEL", NIM_MODEL)
STRATEGY_NIM_API_KEY = os.environ.get("STRATEGY_NIM_API_KEY", NIM_API_KEY)
STRATEGY_GEMINI_BASE_URL = os.environ.get("STRATEGY_GEMINI_BASE_URL", GEMINI_BASE_URL)
STRATEGY_GEMINI_MODEL = os.environ.get("STRATEGY_GEMINI_MODEL", GEMINI_MODEL)
STRATEGY_GEMINI_API_KEY = os.environ.get("STRATEGY_GEMINI_API_KEY", GEMINI_API_KEY)
# The strategy step only emits a small JSON object, so it doesn't need a big
# slice of the budget. Keeping it tight leaves more time for the product search
# below — and when the search finds matches we curate them directly and skip the
# slower agent loop entirely.
STRATEGY_TIMEOUT = float(os.environ.get("STRATEGY_TIMEOUT", "13"))
STRATEGY_MAX_TOKENS = int(os.environ.get("STRATEGY_MAX_TOKENS", "420"))
STRATEGY_TEMPERATURE = float(os.environ.get("STRATEGY_TEMPERATURE", "0.4"))

TRANSLATE_TIMEOUT = float(os.environ.get("TRANSLATE_TIMEOUT", "9"))
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
You are Hari, a warm, emotionally intelligent gifting partner for Kapruka — a Sri Lankan online store.

You are NOT customer support. You are NOT a search engine. You are a thoughtful Sri Lankan friend who genuinely wants to help someone pick the right gift.
Your name is Hari (Kapruka is the store you shop from, not your name). If asked who you are, you're Hari.

VOICE & PERSONALITY
- Sound like a real person texting a friend — never corporate.
- Never say: "I'd be happy to assist", "Please let me know", "Certainly", "Kindly provide".
- Use natural Sri Lankan warmth. Occasional emojis are fine when they fit (😊 🥹 💔 😭 — don't overdo it).
- Do NOT start every message with "Aiyo" or "Aiyoo" — only echo it if the user said it first or they're clearly panicking. \
Vary openings: "Okay listen", "Right", "Don't panic", "Hmm yeah", or jump straight into helpful advice.
- Never say: "Here are a few options", "Let me know if you'd like", "I'd be happy to help".
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
- Hi → something like "Hi 😊 I'm Hari, your gift concierge. Who are we spoiling today?"
- Thanks → something like "Haha, anytime 😊"

GIFT REASONING
- Do NOT search the user's literal words. Understand recipient, occasion, and the feeling they want.
- Decide what that moment calls for — sometimes one item, sometimes a thoughtful package.
- If they want one specific thing, find good options for it; don't pad unnecessarily.
- Consider what's trending (see TRENDS TO CONSIDER): lean toward currently popular/bestselling gifts and \
in-style colours/cuts when the user is open, and notice the recipient's own repeat patterns from their \
orders/wishlist — but never override an explicit request or skip a needed clarifying question for it.

SEARCH RULES (mandatory)
- Turn each idea into a short, specific product noun and run a SEPARATE kapruka_search_products call \
(e.g. q='flower bouquet', q='fruit basket', q='cookware'). Never search vague phrases like 'gift' or 'birthday gift'.
- Tailor to age and interests (cooking → cookware; reading → book).
- When the recipient likes a SPECIFIC thing (ramen, cricket, skincare, coffee), search THAT thing or a close \
match (q='ramen', q='instant noodles', q='Japanese snacks') — do NOT silently swap in a generic fruit/gift basket.
- If the user names SEVERAL different things ("ramen and chocolates and a card"), run a SEPARATE search for \
EACH one and show them all — don't collapse them into a single query.
- MATCH THE RECIPIENT'S GENDER: for a woman (mom, wife, girlfriend, sister, daughter, grandmother…) search \
'ladies'/'women's' variants (q='ladies watch', q='women's perfume'); for a man, 'men's'/'gents'. NEVER show \
men's items for a female recipient or women's items for a male one.
- Do NOT pass a category filter unless you got the exact name from kapruka_list_categories.

UI PRESENTATION
- The app renders product cards below your message (photo, name, price, Add to cart).
- Write ONLY a warm conversational intro (1–2 short sentences). The cards carry ALL product details.
- NEVER name products, list options, or use numbers like "1." or "2." in your text — the cards already show them.
- NEVER say "here are a few options", "here are some picks", or "let me know if you'd like to add to cart".
- NEVER output numbered/bulleted catalogues, markdown (**bold**, [links]), prices, or kapruka.com links.
- If you want to highlight one pick, say it in prose without the full product title (e.g. "the spa hamper feels like the thoughtful pick").
- Proactive follow-ups feel natural: "I've got the flowers sorted. Trust me, hand-delivering them lands \
much better than a courier 😊. Shall I add a note card too?"

WHEN TO ASK vs SEARCH (critical)
- ORDER TRACKING is different from shopping: if they ask for an update/status on an existing order or gift \
("any update on my sister's gift", "track my order", "has it arrived?"), do NOT search products — use the order \
on file, call a Kapruka tracking/status tool if one exists, and reply with a short status report (see ORDER TRACKING REQUEST context).
- DEFAULT TO SEARCH when occasion + recipient + enough detail are clear.
- For vague ideas or opinions ("thinking about a dress", "what do you think"): ask ONE warm question first — \
who it's for, occasion, size/style/colour — then search. Do NOT dump random products on a half-formed idea.
- For apology / angry partner / make-up gifts ("gf is mad at me"): empathize briefly, then ask ONE warm question \
about what she's into — hobbies, favourite things, flowers vs food vs something sentimental. Not everyone wants chocolate and roses.
- Do NOT ask budget as the first question when someone is emotional — use profile budget or sensible mid-range picks later.
- NEVER call ask_user for: condolences, get-well, or pure panic with zero recipient ("help me" alone).
- For clear occasions with recipient ("birthday cake for mom"): search immediately.
- When asking, ONE friend-like question — never a form with budget + interests + city together.
- Good taste question example: "Before I pick something — what's she usually into? More flowers-and-chocolate, or something she'd actually care about?"

FOLLOW-UPS & CONTEXT
- Honour budget and context from earlier turns.
- For celebratory occasions, naturally offer complementary items (cake, flowers, card).
- Never offer celebration items in sombre situations.
- If a saved occasion is near (see DELIVERY TIMING), proactively flag delivery timing and lean toward \
items that can arrive in time — but never fabricate exact courier ETAs.

CART & SELECTIONS
- add_to_cart: user picks from CURRENT SUGGESTIONS by 1-based number, name, or description.
- add_all_suggestions_to_cart: user wants everything suggested.
- remove_from_cart: drop items or clear the cart.
- add_instruction: save gift-wrapping, hamper requests, delivery notes, card messages verbatim.
- build_hamper: when the user asks to build/make/put together a hamper, drive it from the SENDER'S preferences — \
reuse their stored USER PROFILE preferences (style, budget, dietary, avoid) when present and pass them straight in; \
only ask ONE warm question for their hamper preference (sweet/savoury/wellness, budget, anything to avoid) if nothing \
is on file. Then search the catalogue separately for each preference to fill the hamper with real items.
- set_customization: for items marked CUSTOMIZABLE in context, ask for the name/message to print or \
engrave (for a photo item, tell them they can attach a photo in the app), then record it with the item's number. \
Do this before or right as you add a customizable item to the cart — don't let it go to checkout blank.
- After cart/instruction actions, confirm in one short natural sentence.
- Use suggestion/cart numbers exactly as given in context.

BUDGET CHECK
- Before add_to_cart or add_all_suggestions_to_cart, check the cart subtotal in context.
- If adding would exceed their stated budget, warn them (item price, new total, overage) and ask before adding.
- Only add once within budget OR they clearly confirm going over.

DATA RULES
- Rely ONLY on tool data from this conversation — never invent products or prices.
- NEVER describe a product as something it is not. If they wanted ramen but the search returned fruit \
baskets, do NOT call one a "ramen basket" — say honestly you couldn't find ramen and offer what you found \
as alternatives, described accurately. A mismatch the user can see destroys trust faster than "no match".
- Never say "prices may change, check the website".
- Never duplicate the same product. Omit unset optional params — never pass "null" placeholders.
- Only recommend items that are in stock; if the catalogue marks something out of stock / sold out / \
unavailable, don't suggest it — pick an available one instead.
- NEVER suggest adult / sexual / intimate-hygiene products (sex toys, lubricants, intimate washes, etc.) \
as gifts, even if a fuzzy search returns them — silently skip them.
- kapruka_list_categories returns CATEGORY names (e.g. "Books", "Jewellery"), NOT products. Use them only \
to refine a search; never present a category name as a product to add to cart.
- If a query is a typo or nonsense (e.g. "beofre"), don't dump random matches — ask what they meant or \
re-read the conversation for what they actually want.
- If a search returns nothing relevant, try a different concrete idea before settling.
- ALWAYS ACT: never promise or describe gift options without actually calling kapruka_search_products in \
the SAME turn. "Let me find", "here are some options" or "what do you think of these" are only honest once \
real products were returned this turn — otherwise the user sees an empty reply.

When USER PROFILE context is provided, bias searches and suggestions toward their gifting personality, \
style, typical budget, and default city — without ignoring their current request."""

UI_PRESENTATION_PROMPT = (
    "REMINDER: Product cards render below your reply. Your text is 1–2 warm sentences ONLY — "
    "no product names, no numbered lists (1. 2.), no 'here are options', no 'let me know'. "
    "Do not start every reply with Aiyo — vary your opening."
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
    "IMPORTANT — search now, do NOT call ask_user. The user gave enough context (who + situation + tastes). "
    "Call kapruka_search_products for fitting items immediately. "
    "Your reply: 1–2 warm sentences max, NO product names, NO numbered lists — cards show everything below."
)

BUDGET_ANSWER_NUDGE = (
    "IMPORTANT — the user answered the budget question (open budget / no limit / skip / a number). "
    "Do NOT ask another question. Call kapruka_search_products NOW for fitting gifts from the chat context. "
    "If they said no budget or no limit, treat budget as open — include premium options too. "
    "Reply in 1–2 warm sentences only; product cards render below."
)

DISCOVERY_NUDGE = (
    "IMPORTANT — you could search, but you don't yet know the recipient's taste, so the picks would be "
    "a guess. Ask exactly ONE warm, friend-like question to sharpen them — tailored to what they asked "
    "for, never a form, and NOT about budget (already handled). Examples by category: "
    "food/snacks (ramen, chocolate, tea) → the flavour they like — e.g. ramen → spicy, cheese, "
    "seafood, or chicken; chocolate → dark or milk; tea → green or black; "
    "flowers → roses, mixed, or orchids, and a colour or romantic-vs-cheerful vibe; "
    "cake → flavour and roughly how many people (or eggless); "
    "clothing → casual/party/formal, plus a colour or size; "
    "jewellery → gold or silver, and classic vs trendy; "
    "perfume → fresh, floral, or woody; "
    "hamper → sweet, savoury, or wellness. "
    "Use ask_user with that single question in your warm voice. If they answer, are unsure, or say "
    "'you pick' / 'surprise me', search right away — never ask a second round."
)

MORE_SUGGESTIONS_NUDGE = (
    "IMPORTANT — the user asked for MORE / other ideas. Do NOT repeat anything already shown or in "
    "the cart (see MORE SUGGESTIONS REQUEST). Run fresh searches for different, complementary items "
    "and present only NEW products. Reply in 1–2 warm sentences, no product names."
)

PICK_BEST_NUDGE = (
    "IMPORTANT — the user wants you to pick the best from the CURRENT SUGGESTIONS already on screen "
    "(see that list in context). Do NOT search again and do NOT show new products. Recommend ONE (or "
    "the top two) and — unlike normal replies — DO name the specific item(s) you're recommending, with "
    "a short, concrete reason it fits the recipient, occasion and budget. Offer to add it to the cart."
)

ORDER_TRACKING_NUDGE = (
    "IMPORTANT — this is an ORDER TRACKING request, not shopping. Use the ORDER TRACKING REQUEST context. "
    "Do NOT call kapruka_search_products and do NOT show product cards. If a Kapruka order-status/tracking "
    "tool is available, call it with the order reference to fetch live status; otherwise report the order "
    "details we have on file. Reply as a short, warm status report — what it is, who it's for, the reference, "
    "and where it stands."
)

ACCOUNT_ACTION_NUDGE = (
    "IMPORTANT — this is a direct request about the user's OWN cart, wishlist, saved items, "
    "past orders, or checkout (see the matching context block above). Do NOT call "
    "kapruka_search_products and do NOT show product cards. Answer directly and warmly in plain "
    "sentences from that context, then offer one helpful next step."
)

TASTE_QUESTION_NUDGE = (
    "IMPORTANT — ask about her tastes before searching. You know WHO and the SITUATION but not what she'd actually like. "
    "Call ask_user with exactly ONE warm, conversational question about her personality, hobbies, or gift preferences "
    "(e.g. flowers vs books vs food vs sentimental). Do NOT assume chocolate-and-roses. Do NOT ask budget yet. "
    "Do NOT search until they answer (or say skip)."
)

REPAIR_INTERESTS_NUDGE = (
    "IMPORTANT — search now using this recipient's known interests: {interests}. "
    "Do NOT default to chocolate-and-roses unless those interests say so. "
    "Run separate kapruka_search_products calls tailored to their tastes. "
    "Reply: 1–2 warm sentences max, NO product names — cards show below."
)

REPAIR_FOLLOWUP_NUDGE = (
    "IMPORTANT — this is a make-up / apology gift after a fight. "
    "Use the user's latest reply about her tastes (or pick varied thoughtful items they mentioned). "
    "Do NOT default to chocolate hampers or roses unless they asked for that. "
    "Try distinct ideas: spa hamper, books, personalized gift, her favourite food, flowers. "
    "Reply: 1–2 warm sentences max, NO product names."
)

CLARIFICATION_NUDGE = (
    "IMPORTANT — the user's request is vague or ambiguous. "
    "Call ask_user with exactly ONE clear, warm, conversational question to clarify what they want to find or do. "
    "Do NOT search yet until they clarify."
)

BUDGET_QUESTION_NUDGE = (
    "IMPORTANT — ask the user about their budget before searching. "
    "Call ask_user with exactly ONE warm, conversational question about their budget or price range "
    "so you can tailor the search correctly. Do NOT search until they answer."
)

HAMPER_QUESTION_NUDGE = (
    "IMPORTANT — the user wants to build a hamper but you don't know their preferences. "
    "Call ask_user with exactly ONE warm, conversational question asking what theme or types of "
    "items they want to include (e.g. chocolates, cookies, savouries, or a specific wellness theme)."
)

_BRAINSTORM_RE = re.compile(
    r"\b(what do you think|what do u think|was thinking|thinking about|"
    r"good idea|would that work|should i get|do you reckon|your opinion|"
    r"what about|how about)\b",
    re.I,
)
_VAGUE_PRODUCT_RE = re.compile(
    r"\b(dress|dresses|saree|sari|jewell?ery|jewelry|watch|perfume|handbag|purse|"
    r"shoes|clothing|clothes|outfit|shirt|skirt|suit|handbag)\b",
    re.I,
)
_SEARCH_READY_RE = re.compile(
    r"\b(birthday|anniversary|wedding|valentine|party|formal|casual|evening|"
    r"size\s*\d|size\s*(xs|s|m|l|xl|xxl)|\b(xs|s|m|l|xl|xxl)\b|"
    r"red|blue|black|white|pink|green|gold|silver|silk|cotton|"
    r"for my|for her|for him|for\s+(mom|dad|wife|husband|gf|bf|friend|sister|brother))\b",
    re.I,
)
# Concrete details that make a vague product (dress, saree, watch...) ready to
# search. Unlike _SEARCH_READY_RE this deliberately excludes "for my/for her"
# recipient phrases — knowing WHO it's for is not the same as knowing WHAT to
# pick (style, size, colour, occasion).
_PRODUCT_SPECIFIC_RE = re.compile(
    r"\b(party|formal|casual|evening|work|office|daytime|"
    r"size\s*\d|size\s*(xs|s|m|l|xl|xxl)|\b(xs|s|m|l|xl|xxl)\b|"
    r"red|blue|black|white|pink|green|gold|silver|navy|maroon|beige|"
    r"silk|cotton|linen|chiffon|denim|lace|floral|striped|pastel)\b",
    re.I,
)

_RECIPIENT_RE = re.compile(
    r"\b(mom|mother|mum|mummy|dad|father|wife|husband|girlfriend|boyfriend|partner|gf|bf|"
    r"fiance|fiancee|her|him|sister|brother|daughter|son|friend|firend|freind|frnd|bestie|boss|colleague|"
    r"grandma|grandpa|granny|aunt|uncle|cousin|hubby|babe|baby)\b",
    re.I,
)
# A pronoun referring to the recipient ("she likes…", "he wants…") — means we
# already know who the gift is for, even when the noun was a typo or earlier turn.
_RECIPIENT_PRONOUN_RE = re.compile(r"\b(she|he|her|him|hers|his|they|them|their)\b", re.I)
_EMOTIONAL_URGENCY_RE = re.compile(
    r"\b(mad|angry|angr\w*|upset|furious|annoyed|cross|hurt|fight|fighting|forgot|forgotten|"
    r"sorry|apolog|make up|make it up|panic|help me|save this|mess up|messed up|"
    r"aiyo|aiyoo|aiyyo|oh no|in trouble)\b",
    re.I,
)
_TASTE_HINTS_RE = re.compile(
    r"\b(likes|loves|enjoys|into|favourite|favorite|fan of|prefers|"
    r"book|reading|cooking|music|perfume|chocolate|flowers|sports|tea|coffee|"
    r"jewellery|jewelry|makeup|games|plants|wine|spa)\b",
    re.I,
)
_APOLOGY_SITUATION_RE = re.compile(
    r"\b(mad at|angry at|upset with|fight with|make up|make it up|messed up|"
    r"forgot our|forgot the|forgot anniversary|say sorry|in trouble with|"
    r"(gf|bf|girlfriend|boyfriend|wife|husband|partner|her|him)\s+is\s+"
    r"(mad|angry|angr\w*|upset|annoyed|furious|cross|hurt))\b",
    re.I,
)

_SKIP_REPLIES = frozenset({"skip", "just pick", "surprise me", "anything", "you pick", "whatever"})


def _repair_context_in_conversation(conversation: list | None) -> str | None:
    for turn in reversed(conversation or []):
        if turn.get("role") != "user":
            continue
        text = str(turn.get("content") or "")
        if _is_repair_situation(text):
            return text
    return None


def _is_repair_follow_up(conversation: list | None, last_user: str) -> bool:
    if _is_repair_situation(last_user):
        return True
    if not _repair_context_in_conversation(conversation):
        return False
    low = (last_user or "").strip().lower()
    if _message_has_taste_hints(last_user):
        return True
    return low in _SKIP_REPLIES


_PARTNER_RE = re.compile(
    r"\b(gf|bf|girlfriend|boyfriend|wife|husband|partner|her|him|hubby|babe)\b",
    re.I,
)


def _is_repair_situation(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low or not _RECIPIENT_RE.search(low):
        return False
    if _APOLOGY_SITUATION_RE.search(low):
        return True
    if _EMOTIONAL_URGENCY_RE.search(low):
        return True
    return False


def _recipient_interests_for_repair(text: str, recipients: list | None) -> list[str] | None:
    """Known interests for the partner/recipient mentioned in a repair scenario."""
    if not _is_repair_situation(text):
        return None
    low = (text or "").lower()
    for r in recipients or []:
        ints = r.get("interests")
        if not (isinstance(ints, list) and ints):
            continue
        name = (r.get("name") or "").lower()
        rel = (r.get("relationship") or "").lower()
        if name and name in low:
            return [str(x) for x in ints[:8]]
        if rel and rel in low:
            return [str(x) for x in ints[:8]]
        if rel in ("partner", "girlfriend", "boyfriend", "wife", "husband") and _PARTNER_RE.search(low):
            return [str(x) for x in ints[:8]]
    return None


def _repair_taste_question(text: str) -> str:
    low = (text or "").lower()
    if re.search(r"\b(gf|girlfriend|wife|her|mom|mother|sister|grandma)\b", low):
        who = "she"
    elif re.search(r"\b(bf|boyfriend|husband|him|dad|father|brother|grandpa)\b", low):
        who = "he"
    else:
        who = "they"
    return (
        f"What's {who} usually into when you're picking something thoughtful — "
        "flowers, food, books, something sentimental?"
    )


def _repair_ask_intro(text: str) -> str:
    return (
        "Okay, don't panic 💔 Effort and sincerity matter more than price here. "
        "Not everyone wants the same make-up gift."
    )


def _message_has_taste_hints(text: str) -> bool:
    return bool(_TASTE_HINTS_RE.search(text or ""))


def _has_taste_context(text: str, profile: dict | None = None) -> bool:
    if _message_has_taste_hints(text):
        return True
    prefs = (profile or {}).get("preferences") or {}
    if isinstance(prefs, dict) and prefs.get("styles"):
        return True
    facts = (profile or {}).get("session_facts") or {}
    if isinstance(facts, dict) and facts.get("constraints"):
        return True
    return False


def _has_search_ready_context(text: str) -> bool:
    if detect_occasion(text):
        return True
    if _SEARCH_READY_RE.search(text or ""):
        return True
    if _message_has_taste_hints(text):
        return True
    low = (text or "").lower()
    if _RECIPIENT_RE.search(low) and re.search(
        r"\b(gift|birthday|anniversary|flowers|cake|hamper|bouquet|present|dress|saree|watch)\b",
        low,
        re.I,
    ):
        return True
    return False


def _clarification_context_in_conversation(conversation: list | None) -> bool:
    for turn in reversed(conversation or []):
        if turn.get("role") != "assistant":
            continue
        content = str(turn.get("content") or "").lower()
        if any(
            phrase in content
            for phrase in (
                "who's the dress for",
                "who is it for",
                "who are we shopping for",
                "good question — happy to help",
            )
        ):
            return True
    return False


def _is_clarification_follow_up(conversation: list | None, last_user: str) -> bool:
    if not _clarification_context_in_conversation(conversation):
        return False
    low = (last_user or "").strip().lower()
    if low in _SKIP_REPLIES:
        return True
    if _has_search_ready_context(last_user):
        return True
    if len(low) > 12:
        return True
    return False


def _vague_product_is_specified(text: str) -> bool:
    """A vague product (dress, saree, watch...) is only ready to search once the
    user gives concrete details — an occasion, style/vibe, size, colour, or
    fabric. Naming the recipient alone does not count."""
    if _PRODUCT_SPECIFIC_RE.search(text or ""):
        return True
    if detect_occasion(text):
        return True
    return False


# --- Conversation (temporary) memory --------------------------------------- #
# Facts established earlier in THIS chat, so the agent reuses them instead of
# re-asking (e.g. after "my gf, casual, white, ~23", "also a watch" must search).
_COLOR_WORDS_RE = re.compile(
    r"\b(red|blue|black|white|pink|green|gold|silver|navy|maroon|beige|purple|"
    r"yellow|orange|grey|gray|cream|brown|teal|turquoise|lavender|peach|coral|"
    r"burgundy|olive|mustard|pastel|floral|multicolou?r)\b",
    re.I,
)
_STYLE_WORDS_RE = re.compile(
    r"\b(casual|party|formal|evening|work|office|daytime|sporty|elegant|trendy|"
    r"minimal|minimalist|classic|chic|cute|simple|fancy|smart|vintage|boho|"
    r"modern|traditional|ethnic|streetwear|girly|edgy|sophisticated)\b",
    re.I,
)
_AGE_RE = re.compile(
    r"\b(?:around|about|aged|age|she'?s|he'?s|she is|he is|turning|roughly)\s+(\d{1,2})\b"
    r"|\b(\d{1,2})\s*(?:years?|yrs?|yo|year[- ]old)\b",
    re.I,
)
_FOLLOWUP_ADD_RE = re.compile(r"\b(also|too|as well|and a|another|plus a|add a|need a)\b", re.I)

_TASTE_VERB_WORDS = frozenset(
    {"likes", "loves", "enjoys", "into", "favourite", "favorite", "fan of", "prefers"}
)
# Grab the interest phrase that follows a "likes/loves/into …" verb, so we catch
# tastes the fixed vocabulary misses ("loves cricket", "into hiking").
_INTEREST_VERB_RE = re.compile(
    r"\b(?:likes?|loves?|loving|enjoys?|enjoy|into|prefers?|prefer|fan of|"
    r"favou?rite|obsessed with|crazy about|keen on)\s+([a-z][a-z\-& ]{1,40})",
    re.I,
)
_INTEREST_STOP = frozenset({
    "a", "an", "the", "to", "and", "but", "so", "it", "its", "her", "him", "his", "she",
    "he", "they", "them", "really", "very", "always", "just", "more", "most", "that",
    "this", "when", "while", "because", "for", "with", "stuff", "things", "thing",
    "something", "anything", "everything", "lot", "lots", "kind", "sort", "type", "bit",
    "colour", "color", "size", "white", "black", "blue", "red", "pink", "green",
    "casual", "formal", "party", "dress", "dresses", "watch", "shoes", "gift",
})


def _interest_phrases(blob: str) -> list[str]:
    out: list[str] = []
    for m in _INTEREST_VERB_RE.finditer(blob or ""):
        for part in re.split(r"\band\b|,|&|/", m.group(1).lower()):
            words = [w for w in re.findall(r"[a-z][a-z\-]+", part) if w not in _INTEREST_STOP]
            if not words:
                continue
            cand = " ".join(words[:2]).strip()
            if len(cand) >= 3 and cand not in out:
                out.append(cand)
    return out[:6]


def _extract_session_memory(conversation: list | None, current_text: str | None = None) -> dict:
    """Collect gift facts the user has shared across this chat's user turns."""
    texts: list[str] = []
    for turn in conversation or []:
        if turn.get("role") == "user":
            texts.append(str(turn.get("content") or ""))
    if current_text and (not texts or texts[-1] != current_text):
        texts.append(current_text)
    if not texts:
        return {}
    blob = " \n ".join(texts)
    low = blob.lower()
    mem: dict = {}
    # Active recipient = most recently mentioned, preferring a specific role over
    # a bare pronoun; remember earlier ones so we don't conflate two people.
    recs = [m.group(0).lower() for m in _RECIPIENT_RE.finditer(low)]
    if recs:
        pronouns = {"her", "him", "babe", "baby"}
        specifics = [r for r in recs if r not in pronouns]
        mem["recipient"] = specifics[-1] if specifics else recs[-1]
        uniq: list[str] = []
        for r in (specifics or recs):
            if r not in uniq:
                uniq.append(r)
        others = [r for r in uniq if r != mem["recipient"]]
        if others:
            mem["other_recipients"] = others[-3:]
    colors = sorted({c.lower() for c in _COLOR_WORDS_RE.findall(blob)})
    if colors:
        mem["color"] = colors[:4]
    styles = sorted({s.lower() for s in _STYLE_WORDS_RE.findall(blob)})
    if styles:
        mem["style"] = styles[:4]
    for m in _AGE_RE.finditer(blob):
        g = m.group(1) or m.group(2)
        if g and 1 <= int(g) <= 99:
            mem["age"] = g
            break
    # _TASTE_HINTS_RE doubles as a verb detector; keep only real interest nouns,
    # then add free-text interests captured after a "likes/loves/into" verb.
    tastes = {t.lower() for t in _TASTE_HINTS_RE.findall(low)} - _TASTE_VERB_WORDS
    ordered = sorted(tastes) + [p for p in _interest_phrases(blob) if p not in tastes]
    if ordered:
        mem["taste"] = ordered[:6]
    return mem



def _session_has_specifics(mem: dict) -> bool:
    return bool(mem.get("color") or mem.get("style") or mem.get("age") or mem.get("taste"))


_PRODUCT_NOUN_RE = re.compile(
    r"\b(dresses|dress|saree|sari|outfit|skirt|shirt|suit|sneakers|sneaker|sandals|shoes|shoe|"
    r"watches|watch|jewellery|jewelry|necklace|earrings|bracelet|ring|perfume|cologne|"
    r"handbag|purse|wallet|bouquet|flowers|cake|chocolates|chocolate|hamper|book|mug|candle|plant|toy)\b",
    re.I,
)
# Canonical, nicely-pluralised display label for each product noun.
_ITEM_DISPLAY = {
    "dress": "dresses", "dresses": "dresses", "saree": "sarees", "sari": "sarees",
    "outfit": "outfits", "skirt": "skirts", "shirt": "shirts", "suit": "suits",
    "sneaker": "sneakers", "sneakers": "sneakers", "sandals": "sandals",
    "shoe": "shoes", "shoes": "shoes", "watch": "watches", "watches": "watches",
    "jewellery": "jewellery", "jewelry": "jewellery", "necklace": "necklaces",
    "earrings": "earrings", "bracelet": "bracelets", "ring": "rings",
    "perfume": "perfume", "cologne": "cologne", "handbag": "handbags",
    "purse": "purses", "wallet": "wallets", "bouquet": "bouquets", "flowers": "flowers",
    "cake": "cake", "chocolate": "chocolates", "chocolates": "chocolates",
    "hamper": "hampers", "book": "books", "mug": "mugs", "candle": "candles",
    "plant": "plants", "toy": "toys",
}
_REC_LABEL = {
    "gf": "their girlfriend", "girlfriend": "their girlfriend",
    "bf": "their boyfriend", "boyfriend": "their boyfriend",
    "wife": "their wife", "husband": "their husband", "hubby": "their husband",
    "mom": "their mum", "mother": "their mum", "mum": "their mum",
    "dad": "their dad", "father": "their dad",
    "sister": "their sister", "brother": "their brother",
    "friend": "their friend", "partner": "their partner",
    "grandma": "their grandmother", "grandpa": "their grandfather",
    "boss": "their boss", "colleague": "their colleague", "baby": "their little one",
}


def _recipient_label(recipient: str | None) -> str:
    r = (recipient or "").lower()
    if r in _REC_LABEL:
        return _REC_LABEL[r]
    if r in {"her", "him", "babe"}:
        return "the recipient"
    return f"their {r}" if r else "the recipient"


def _recipient_pronoun(recipient: str | None) -> tuple[str, str]:
    """Returns (subject, object) pronouns for natural-language briefs."""
    r = (recipient or "").lower()
    if r in {"gf", "girlfriend", "wife", "mom", "mother", "mum", "mummy", "sister", "grandma",
             "granny", "daughter", "aunt", "fiancee", "her"}:
        return ("she", "her")
    if r in {"bf", "boyfriend", "husband", "hubby", "dad", "father", "brother", "grandpa",
             "son", "uncle", "fiance", "him"}:
        return ("he", "him")
    return ("they", "them")


def _items_discussed(conversation: list | None, current_text: str | None = None) -> list[str]:
    texts: list[str] = []
    for turn in conversation or []:
        if turn.get("role") == "user":
            texts.append(str(turn.get("content") or ""))
    if current_text:
        texts.append(current_text)
    found: list[str] = []
    for t in texts:
        for w in _PRODUCT_NOUN_RE.findall(t):
            disp = _ITEM_DISPLAY.get(w.lower())
            if disp and disp not in found:
                found.append(disp)
    return found[:6]


def _context_search_queries(conversation: list | None, current_text: str | None) -> list[str]:
    """Kapruka search queries from gift context across recent user turns — used when
    the current message is only a budget answer (e.g. 'no budget') and carries no
    product signal on its own."""
    blob = _recent_user_blob(conversation, current_text, n=8)
    if not blob.strip():
        return []
    queries: list[str] = []
    items = _items_discussed(conversation, current_text)
    occ = detect_occasion(blob)
    for item in items[:3]:
        q = f"{occ} {item}".strip() if occ else item
        queries.append(q)
    if not items:
        if re.search(r"\bflowers?\b", blob, re.I):
            queries.append(f"{occ} flowers".strip() if occ else "flowers bouquet")
        for m in re.finditer(
            r"\b(hamper|chocolate|cake|perfume|jewellery|jewelry|watch|gift basket|bouquet)\b",
            blob,
            re.I,
        ):
            q = f"{occ} {m.group(1)}".strip() if occ else m.group(1)
            queries.append(q)
        if occ and not queries:
            queries.append(f"{occ} gift")
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = re.sub(r"\s+", " ", q.strip())
        key = q.lower()
        if q and key not in seen:
            seen.add(key)
            out.append(q)
    return out[:3]


def _persisted_recipient_profile(profile: dict | None, current_text: str | None = None) -> dict:
    """The recipient profile saved across sessions (set by extract_session_facts)."""
    facts = (profile or {}).get("session_facts") or {}
    rp = facts.get("recipient_profile") if isinstance(facts, dict) else None
    if not isinstance(rp, dict) or not rp:
        return {}
    if current_text:
        cur = _recipient_token(current_text)
        stored = _recipient_token(str(rp.get("recipient") or ""))
        if cur and stored and cur != stored:
            return {}
    return rp


def conversation_memory_message(
    conversation: list | None,
    current_text: str | None = None,
    profile: dict | None = None,
) -> str | None:
    """A detailed, natural-language brief of what we've learned in THIS chat, so
    the model understands the recipient, their preferences, and the factors that
    should shape every pick — instead of a terse fact list it might ignore."""
    mem = _extract_session_memory(conversation, current_text)
    # Cross-session recall: fill gaps this chat hasn't re-established from the
    # recipient profile saved for signed-in users.
    persisted = _persisted_recipient_profile(profile, current_text)
    recalled = not mem.get("recipient") and not _session_has_specifics(mem)
    for key in ("recipient", "age", "color", "style", "taste"):
        if not mem.get(key) and persisted.get(key):
            mem[key] = persisted[key]
    if not mem.get("recipient") and not _session_has_specifics(mem):
        return None

    subj, obj = _recipient_pronoun(mem.get("recipient"))
    sentences: list[str] = []
    lead = "From what they've told you before, you" if recalled else "You"

    # Who we're shopping for.
    if mem.get("recipient"):
        age = f", who is around {mem['age']} years old" if mem.get("age") else ""
        sentences.append(f"{lead} are helping the user pick a gift for {_recipient_label(mem.get('recipient'))}{age}.")
    elif mem.get("age"):
        sentences.append(f"The person we're shopping for is around {mem['age']} years old.")

    # Their preferences.
    prefs: list[str] = []
    if mem.get("style"):
        prefs.append(f"leans {', '.join(mem['style'])} in style")
    if mem.get("color"):
        prefs.append(f"likes {', '.join(mem['color'])}")
    if mem.get("taste"):
        prefs.append(f"is into {', '.join(mem['taste'])}")
    if prefs:
        sentences.append(f"{subj.capitalize()} {'; '.join(prefs)}.")

    # Factors that shape the picks.
    occ = None
    for turn in conversation or []:
        if turn.get("role") == "user":
            occ = detect_occasion(str(turn.get("content") or "")) or occ
            if occ:
                break
    if not occ and current_text:
        occ = detect_occasion(current_text)
    if occ:
        sentences.append(f"This gift is for {occ.replace('_', ' ')}.")
    budget = (profile or {}).get("default_budget")
    if budget:
        try:
            sentences.append(f"Their usual budget is around LKR {float(budget):,.0f}, so stay near that unless they say otherwise.")
        except (TypeError, ValueError):
            pass

    items = _items_discussed(conversation, current_text)
    if items:
        sentences.append(f"So far in this chat you've already looked at {', '.join(items)} for {obj}.")
    if mem.get("other_recipients"):
        sentences.append(
            f"Heads up — this chat also involves {', '.join(mem['other_recipients'])}; keep each "
            "person's gifts separate and apply the right preferences to each."
        )

    # How to use the brief.
    sentences.append(
        f"Treat this as the active brief: carry the same recipient, age, colour and style into every new item "
        f"they add (\"also a watch\", \"a shoe too\"), weigh these preferences when choosing, and only ask about "
        f"something genuinely new such as budget or size. Never re-ask who it's for or what {subj} likes — you already know."
    )

    return "GIFT BRIEF (what we've learned in this conversation — keep applying it):\n" + " ".join(sentences)


def _needs_clarification_question(
    text: str,
    conversation: list | None = None,
) -> bool:
    """Ask before searching when the user has a vague product idea or wants an opinion."""
    low = (text or "").lower().strip()
    if not low or low in _SKIP_REPLIES:
        return False
    if _is_clarification_follow_up(conversation, text):
        return False
    if _is_repair_situation(text):
        return False
    # The user is answering a question we just asked, with real taste/colour/style.
    # Use it — don't throw another canned clarification (that reads as forgetting).
    if _last_assistant_was_question(conversation) and (
        _message_has_taste_hints(text)
        or _COLOR_WORDS_RE.search(text or "")
        or _STYLE_WORDS_RE.search(text or "")
    ):
        return False
    has_brainstorm = bool(_BRAINSTORM_RE.search(text))
    has_vague_product = bool(_VAGUE_PRODUCT_RE.search(text))
    # Fold in temporary chat memory: a recipient or specifics named earlier in
    # the conversation count just as much as in the current message.
    mem = _extract_session_memory(conversation, text)
    # A recipient pronoun ("she likes…") means we already know who it's for, even
    # if the noun was a typo ("wifeee") or only appeared earlier in the chat.
    has_recipient = (
        bool(_RECIPIENT_RE.search(low))
        or bool(mem.get("recipient"))
        or bool(_RECIPIENT_PRONOUN_RE.search(low))
    )
    specified = _vague_product_is_specified(text) or _session_has_specifics(mem)
    # A vague product needs BOTH who it's for AND real specifics before we
    # search. "my gf wants a dress" names the recipient but tells us nothing
    # about her style, size, colour or the occasion — so ask first.
    if has_vague_product and not (has_recipient and specified):
        return True
    if _has_search_ready_context(text):
        return False
    if has_brainstorm and not has_recipient:
        return True
    if re.search(r"\b(gift|something|ideas?|suggest)\b", low) and not has_recipient:
        if not detect_occasion(text):
            return True
    return False


def _clarification_ask(text: str) -> tuple[str, str]:
    low = (text or "").lower()
    has_recipient = bool(_RECIPIENT_RE.search(low))
    intro = "Good question — happy to help you figure that out 😊"
    if re.search(r"\b(dress|dresses|saree|sari|outfit|clothing|clothes|skirt|shirt|suit)\b", low):
        if has_recipient:
            question = (
                "What's her style usually — casual, party, or more formal? "
                "And any colours, a size, or even her age range that'd help me pick?"
            )
        else:
            question = (
                "Who's the dress for, and what's the vibe — casual, party, or formal? "
                "Any size or colour in mind?"
            )
    elif re.search(r"\b(watch|jewell?ery|jewelry|perfume|handbag|purse|shoes)\b", low):
        if has_recipient:
            question = (
                "What's their taste like — classic, trendy, or minimal? "
                "And any colours, brands, or a size I should keep in mind?"
            )
        else:
            question = (
                "Who is it for, and do you know their style or size? "
                "That'll help me pick something they'll actually love."
            )
    else:
        question = (
            "Who are we shopping for, and what's the occasion? "
            "That way I won't throw random options at you."
        )
    return intro, question


def _needs_taste_question(
    text: str,
    profile: dict | None = None,
    recipients: list | None = None,
    conversation: list | None = None,
) -> bool:
    """Ask what the recipient is into before defaulting to generic apology gifts."""
    if _message_has_taste_hints(text):
        return False
    if _is_repair_situation(text):
        return True
    low = (text or "").lower()
    has_recipient = bool(_RECIPIENT_RE.search(low))
    if has_recipient and not _has_known_preferences(text, profile, recipients, conversation):
        return True
    return False


# --- Recipient discovery (who is it for, really?) -------------------------- #
# When we know there IS a recipient but not their gender, taste, occasion, or any
# concrete product, asking ONE warm question ("guy or girl, and what are they
# into?") beats blurting random gendered items.
_RECIPIENT_DISCOVERY_MARKER_RE = re.compile(r"guy or a girl|a guy or a girl", re.I)


def _last_assistant_asked_recipient_discovery(conversation: list | None) -> bool:
    for turn in reversed(conversation or []):
        if turn.get("role") == "assistant":
            return bool(_RECIPIENT_DISCOVERY_MARKER_RE.search(str(turn.get("content") or "")))
    return False


def _recipient_has_known_interests(blob: str, recipients: list | None) -> bool:
    low = (blob or "").lower()
    for r in recipients or []:
        ints = r.get("interests")
        if not (isinstance(ints, list) and ints):
            continue
        name = (r.get("name") or "").lower()
        rel = (r.get("relationship") or "").lower()
        if (name and name in low) or (rel and rel in low):
            return True
    return False


def _needs_recipient_discovery_question(
    text: str,
    conversation: list | None,
    profile: dict | None,
    recipients: list | None,
) -> bool:
    """Ask who the recipient is (guy/girl + interests) before guessing — when we
    have a recipient but no gender, taste, occasion, or concrete product yet."""
    low = (text or "").lower().strip()
    if not low or low in _SKIP_REPLIES:
        return False
    if _is_repair_situation(text):
        return False  # the repair flow runs its own taste question
    if _last_assistant_asked_recipient_discovery(conversation):
        return False  # already asked — don't loop, let it search
    blob = _recent_user_blob(conversation, text)
    mem = _extract_session_memory(conversation, text)
    has_recipient = (
        bool(_RECIPIENT_RE.search(blob))
        or bool(mem.get("recipient"))
        or bool(_RECIPIENT_PRONOUN_RE.search(blob))
    )
    if not has_recipient:
        return False
    # Only when gender is genuinely unknown across the whole chat.
    if _recipient_gender(conversation, recipients, text) is not None:
        return False
    # Already know their taste / interests / specifics? Then just search.
    if _has_taste_context(text, profile) or _message_has_taste_hints(blob):
        return False
    if _recipient_has_known_interests(blob, recipients) or _session_has_specifics(mem):
        return False
    # A concrete occasion, product, or category means we can search instead.
    if detect_occasion(blob) or _has_search_ready_context(blob):
        return False
    if _VAGUE_PRODUCT_RE.search(blob) or _PREF_RICH_RE.search(blob):
        return False
    return True


def _recipient_discovery_ask(text: str) -> tuple[str, str]:
    intro = "Happy to help you pick 😊 One quick thing so I don't throw random stuff at you —"
    question = (
        "is it a guy or a girl, and what are they usually into? "
        "Even a hobby or two helps me find something they'll actually love."
    )
    return intro, question


# --- Scenario / recipient reset (req #6) ----------------------------------- #
# When the user pivots to a DIFFERENT gift recipient mid-chat, the previous
# recipient's interests and on-screen products must be discarded so they don't
# bleed into the new gift.
_SCENARIO_RECIPIENT_RE = re.compile(
    r"\b(wife|husband|hubby|girlfriend|boyfriend|gf|bf|partner|fiance|fiancee|crush|"
    r"mom|mother|mum|mummy|dad|father|daddy|sister|brother|son|daughter|"
    r"friend|firend|freind|bestie|boss|colleague|co-?worker|coworker|client|cousin|"
    r"aunt|uncle|grandma|grandpa|granny|niece|nephew|teacher|neighbour|neighbor)\b",
    re.I,
)
_RECIPIENT_GROUPS = {
    "mother": "mom", "mum": "mom", "mummy": "mom", "mom": "mom",
    "father": "dad", "daddy": "dad", "dad": "dad",
    "girlfriend": "partner", "gf": "partner", "boyfriend": "partner", "bf": "partner",
    "partner": "partner", "fiance": "partner", "fiancee": "partner",
    "hubby": "husband", "husband": "husband", "wife": "wife", "crush": "crush",
    "bestie": "friend", "friend": "friend", "firend": "friend", "freind": "friend",
    "co-worker": "colleague", "coworker": "colleague", "colleague": "colleague",
    "neighbour": "neighbour", "neighbor": "neighbour",
}


_CRUSH_CUE_RE = re.compile(
    r"\b(crush|girl i like|guy i like|someone i like|this girl|this guy|"
    r"girl (i|at|from)|guy (i|at|from)|girl at (uni|university|class|college|work))\b",
    re.I,
)


def _recipient_token(text: str) -> str | None:
    if _CRUSH_CUE_RE.search(text or ""):
        return "crush"
    m = _SCENARIO_RECIPIENT_RE.search(text or "")
    if not m:
        return None
    r = m.group(1).lower()
    return _RECIPIENT_GROUPS.get(r, r)


def _scenario_changed(conversation: list | None, last_user: str) -> bool:
    """True when this message names a different recipient than the most recent
    one earlier in the chat — a fresh gifting scenario."""
    cur = _recipient_token(last_user)
    if not cur:
        return False
    for turn in reversed((conversation or [])[:-1]):
        if turn.get("role") == "user":
            prev = _recipient_token(str(turn.get("content") or ""))
            if prev:
                return prev != cur
    return False


SCENARIO_RESET_NUDGE = (
    "NEW RECIPIENT — the user has switched to a DIFFERENT person to buy for. Build a FRESH recipient "
    "profile from THIS message only. Ignore every earlier recipient, their interests, the earlier budget, "
    "and any products already shown — none of it applies to this new person."
)


def _should_search_first(
    text: str,
    profile: dict | None = None,
    recipients: list | None = None,
    conversation: list | None = None,
) -> bool:
    """True when ask_user would be wrong — search immediately instead."""
    if _needs_taste_question(text, profile, recipients, conversation):
        return False
    if _needs_clarification_question(text, conversation):
        return False
    low = (text or "").lower().strip()
    if not low:
        return False

    # Ongoing gift session: once the recipient and their tastes are known in this
    # chat, adding another item ("also a watch", "a shoe too") should search now.
    mem = _extract_session_memory(conversation, text)
    if mem.get("recipient") and _session_has_specifics(mem):
        if _VAGUE_PRODUCT_RE.search(low) or _FOLLOWUP_ADD_RE.search(low):
            return True

    occ = detect_occasion(text)
    if occ:
        if occ == "apology":
            return _message_has_taste_hints(text)
        if _has_search_ready_context(text):
            return True
        return False

    if _has_search_ready_context(text):
        return True

    if _message_has_taste_hints(text):
        return True

    facts = (profile or {}).get("session_facts") or {}
    if isinstance(facts, dict) and facts.get("constraints"):
        return True

    return False


_BUDGET_WORD_RE = re.compile(r"\bbudget\b", re.I)
_BUDGET_SKIP_RE = re.compile(
    r"\b(no limit|any budget|doesn'?t matter|don'?t mind|whatever|the usual|"
    r"usual|default|same as|stick with|go with|skip|surprise)\b",
    re.I,
)
_OPEN_BUDGET_RE = re.compile(
    r"(?i)(?:\bno budget\w*(?:\s+constraints?)?|\b(?:there'?s|theres|there is) no budget\b|"
    r"\b(?:don'?t|do not) have (?:a )?budget\b|\bno limit\b|\bunlimited\b|\bopen budget\b|"
    r"\bwithout (?:a )?budget\b|\bbudget(?:ary)? constraints?\b|"
    r"\bmoney is no object\b|\bnot worried about (?:the )?price\b|"
    r"\bprice (?:isn'?t|doesn'?t) matter\b|\bno monetary\b)",
)


def _user_open_budget(conversation: list | None, current_text: str | None) -> bool:
    blob = _recent_user_blob(conversation, current_text, n=8)
    return bool(_OPEN_BUDGET_RE.search(blob) or _BUDGET_SKIP_RE.search(blob))


def _budget_was_asked(conversation: list | None) -> bool:
    for turn in conversation or []:
        if turn.get("role") == "assistant" and _BUDGET_WORD_RE.search(str(turn.get("content") or "")):
            return True
    return False


def _is_budget_answer(text: str, conversation: list | None) -> bool:
    """User is replying to our one-time budget question."""
    if not text:
        return False
    low = text.lower().strip()
    answered = (
        bool(_SESSION_BUDGET_RE.search(text))
        or bool(_OPEN_BUDGET_RE.search(text))
        or bool(_BUDGET_SKIP_RE.search(low))
        or low in _SKIP_REPLIES
    )
    if not answered:
        return False
    if _last_assistant_was_question(conversation):
        for turn in reversed(conversation or []):
            if turn.get("role") == "assistant":
                return bool(_BUDGET_WORD_RE.search(str(turn.get("content") or "")))
    return _budget_was_asked(conversation)


def _should_search_after_budget(conversation: list | None, text: str | None) -> bool:
    """Budget was asked and the user answered (incl. open/no limit) — search now."""
    if not _budget_was_asked(conversation):
        return False
    return _is_budget_answer(text or "", conversation) or _user_open_budget(conversation, text)


def _budget_already_handled(
    conversation: list | None, current_text: str | None, profile: dict | None
) -> bool:
    """True once budget has come up in THIS chat — the user stated one, or we've
    already asked about it. Ensures the budget question is asked at most once per
    conversation, never for every item."""
    if current_text and (
        _SESSION_BUDGET_RE.search(current_text)
        or _OPEN_BUDGET_RE.search(current_text)
        or _BUDGET_SKIP_RE.search(current_text)
    ):
        return True
    if _user_open_budget(conversation, current_text):
        return True
    for turn in conversation or []:
        content = str(turn.get("content") or "")
        role = turn.get("role")
        if role == "user" and (
            _SESSION_BUDGET_RE.search(content)
            or _OPEN_BUDGET_RE.search(content)
            or _BUDGET_SKIP_RE.search(content)
        ):
            return True
        # We already asked the budget question earlier in this chat.
        if role == "assistant" and _BUDGET_WORD_RE.search(content):
            return True
    return False


_NON_GIFT_OCCASIONS = frozenset({"thank_you", "condolence", "get_well"})


def _budget_intent(text: str) -> bool:
    """A real shopping intent worth pricing (so we don't ask budget on a hello,
    a thank-you, or a condolence)."""
    low = (text or "").lower().strip()
    if not low or _is_greeting(text) or _is_thanks(text):
        return False
    occ = detect_occasion(text)
    if occ and occ not in _NON_GIFT_OCCASIONS:
        return True
    if occ is None and _has_search_ready_context(text):
        return True
    if _RECIPIENT_RE.search(low) and re.search(
        r"\b(gift|present|flowers?|cake|hamper|bouquet|something for|buy|get|shop)\b",
        low,
        re.I,
    ):
        return True
    return False


def _needs_budget_question(
    text: str, profile: dict | None, conversation: list | None
) -> bool:
    """Ask about budget once, before the first real search of a chat."""
    low = (text or "").lower().strip()
    if not low or low in _SKIP_REPLIES or _BUDGET_SKIP_RE.search(low):
        return False
    # Never lead with budget when someone is emotional / making up after a fight.
    if _is_repair_situation(text):
        return False
    if _budget_already_handled(conversation, text, profile):
        return False
    return _budget_intent(text)


def _budget_ask(profile: dict | None) -> tuple[str, str]:
    """Intro + question for the one-time budget check. Offers the saved default
    vs. a new amount when we know their usual budget."""
    budget = (profile or {}).get("default_budget")
    amt = None
    if budget:
        try:
            amt = f"LKR {float(budget):,.0f}"
        except (TypeError, ValueError):
            amt = None
    intro = "Lovely choice 😊 Before I pull a few options —"
    if amt:
        question = (
            f"want me to stick with your usual budget (around {amt}), "
            "or are we setting a different one this time?"
        )
    else:
        question = (
            "roughly what budget are you thinking? A number's perfect, "
            "or just say modest, mid-range, or no limit."
        )
    return intro, question


# --- Hamper build flow ----------------------------------------------------- #
# "Build me a hamper" is its own intent: assemble a multi-item gift basket from
# the SENDER'S preferences — use their stored profile preferences when present,
# otherwise ask once for them, then fill the hamper with real catalogue items.
_HAMPER_BUILD_RE = re.compile(
    r"\b(build|make|put together|create|assemble|curate|prepare|design|do|arrange)\b"
    r"[^.?!]{0,30}\bhamper\b"
    r"|\bhamper\b[^.?!]{0,20}\bfor\b"
    r"|\b(custom|gift)\s+hamper\b",
    re.I,
)
# Concrete hamper preferences the user might state (theme or specific contents).
_HAMPER_PREF_RE = re.compile(
    r"\b(sweet|savou?ry|wellness|spa|luxury|premium|healthy|pamper|"
    r"chocolates?|dark chocolate|tea|coffee|biscuits?|cookies?|snacks?|"
    r"fruits?|nuts?|wine|cheese|skincare|beauty|vegetarian|vegan|"
    r"flowers?|candles?|honey|jam)\b",
    re.I,
)


def _is_hamper_build_request(text: str) -> bool:
    return bool(_HAMPER_BUILD_RE.search(text or ""))


def _stored_sender_prefs(profile: dict | None) -> list[str]:
    """The sender's preferences already on file (profile + rolling session facts)."""
    prefs: list[str] = []
    p = profile or {}
    pref_obj = p.get("preferences") or {}
    if isinstance(pref_obj, dict):
        styles = pref_obj.get("styles")
        if isinstance(styles, list):
            prefs.extend(str(s) for s in styles if s)
        elif styles:
            prefs.append(str(styles))
        dietary = pref_obj.get("dietary")
        if dietary:
            prefs.append(f"dietary: {dietary}")
        avoid = pref_obj.get("avoid_list")
        if isinstance(avoid, list) and avoid:
            prefs.append("avoid: " + ", ".join(str(a) for a in avoid))
    facts = p.get("session_facts") or {}
    if isinstance(facts, dict) and facts.get("constraints"):
        prefs.append(str(facts["constraints"]))
    return prefs


def _hamper_prefs_known(text: str, profile: dict | None, conversation: list | None) -> bool:
    """True when we already know enough of the sender's hamper preferences to
    build without asking — either stated in chat or stored on their profile."""
    blob = _recent_user_blob(conversation, text)
    if _HAMPER_PREF_RE.search(blob):
        return True
    if _stored_sender_prefs(profile):
        return True
    return False


def _hamper_pref_question() -> tuple[str, str]:
    intro = "Love it — let's build you a hamper 🧺"
    question = (
        "What kind are you after — sweet, savoury, or wellness? "
        "And anything to leave out (like nuts or alcohol)?"
    )
    return intro, question


# Distinctive phrases from the hamper preference question, so a reply to it is
# recognised as the continuation of the hamper build (even without the word
# "hamper" in the user's answer).
_HAMPER_ASK_MARKER_RE = re.compile(r"build you a hamper|sweet,\s*savou?ry,\s*or\s*wellness", re.I)


def _last_assistant_was_hamper_question(conversation: list | None) -> bool:
    for turn in reversed(conversation or []):
        if turn.get("role") == "assistant":
            return bool(_HAMPER_ASK_MARKER_RE.search(str(turn.get("content") or "")))
    return False


HAMPER_NUDGE = (
    "IMPORTANT — the user asked you to BUILD A HAMPER. Assemble it from the SENDER'S preferences: "
    "use their stored profile preferences when present, otherwise the ones they just gave. "
    "FIRST call build_hamper with those preferences (and the theme/budget if known, and "
    "from_stored_preferences=true when they came from the profile). THEN run a SEPARATE "
    "kapruka_search_products call for EACH preference (e.g. q='dark chocolate', q='ceylon tea') so the "
    "hamper is filled with real items. Respect their budget and anything to AVOID — if they said 'no "
    "nuts', never include nuts; keep the items together within the stated budget. "
    "Reply in 1–2 warm sentences describing the hamper you've put together — no numbered lists, no prices."
)
# Categories where a quick taste question genuinely sharpens the picks (flowers,
# cake, clothing, jewellery...). For these, let the model ask ONE warm,
# category-aware question before the first search instead of guessing.
_PREF_RICH_RE = re.compile(
    r"\b(flowers?|bouquet|roses?|orchids?|lilies|cake|cakes|hamper|gift basket|"
    r"chocolates?|chocolate|ramen|noodles?|tea|coffee|wine|snacks?|"
    r"dress|dresses|saree|sari|outfit|clothing|clothes|shirt|skirt|"
    r"suit|jewell?ery|jewelry|necklace|pendant|earrings?|bracelet|ring|watch|"
    r"perfume|fragrance|cologne|handbag|purse|wallet|shoes?|spa|wellness|"
    r"toy|toys|soft toy|plant|plants|book|books)\b",
    re.I,
)


def _recent_user_blob(conversation: list | None, current_text: str | None, n: int = 4) -> str:
    parts: list[str] = []
    for turn in (conversation or [])[-(n * 2):]:
        if turn.get("role") == "user":
            parts.append(str(turn.get("content") or ""))
    if current_text:
        parts.append(current_text)
    return " ".join(parts)


def _assistant_question_count(conversation: list | None) -> int:
    """How many questions we've already put to the user this chat — used to cap
    discovery so it never becomes an interrogation."""
    n = 0
    for turn in conversation or []:
        if turn.get("role") == "assistant" and "?" in str(turn.get("content") or ""):
            n += 1
    return n


def _last_assistant_was_question(conversation: list | None) -> bool:
    """True when the most recent assistant turn asked something — i.e. the user's
    current message is likely answering us (e.g. the budget reply)."""
    for turn in reversed(conversation or []):
        if turn.get("role") == "assistant":
            return "?" in str(turn.get("content") or "")
    return False


def _has_known_preferences(
    text: str, profile: dict | None, recipients: list | None, conversation: list | None
) -> bool:
    """True when we already know enough about taste/style to pick well — from the
    message, this chat's memory, or the saved recipient profile."""
    if _message_has_taste_hints(text):
        return True
    if _COLOR_WORDS_RE.search(text or "") or _STYLE_WORDS_RE.search(text or ""):
        return True
    mem = _extract_session_memory(conversation, text)
    # A taste entry that just echoes the product category ("flowers", "cake")
    # is not a real preference — we still don't know which kind / colour / vibe.
    real_taste = [t for t in (mem.get("taste") or []) if not _PREF_RICH_RE.search(str(t))]
    if mem.get("style") or mem.get("color") or real_taste:
        return True
    prefs = (profile or {}).get("preferences") or {}
    if isinstance(prefs, dict) and prefs.get("styles"):
        return True
    rp = _persisted_recipient_profile(profile)
    if isinstance(rp, dict) and any(rp.get(k) for k in ("style", "color", "taste")):
        return True
    return False


def _should_discover_first(
    text: str, profile: dict | None, recipients: list | None, conversation: list | None
) -> bool:
    """True when we're ready to search a preference-rich category but don't yet
    know the recipient's taste — so the model should ask ONE smart question
    first. Only consulted when a search was otherwise about to happen."""
    low = (text or "").lower().strip()
    if not low or low in _SKIP_REPLIES or _BUDGET_SKIP_RE.search(low):
        return False
    if _is_greeting(text) or _is_thanks(text):
        return False
    # Repair/apology already has its own taste flow; don't double up.
    if _is_repair_situation(text):
        return False
    if not _PREF_RICH_RE.search(_recent_user_blob(conversation, text)):
        return False
    if _has_known_preferences(text, profile, recipients, conversation):
        return False
    # Cap total questions per chat (budget counts) so we never interrogate.
    if _assistant_question_count(conversation) >= 2:
        return False
    return True


# --- "Show me more / something else" --------------------------------------- #
_MORE_RE = re.compile(
    r"\b(what else|anything else|something else|"
    r"other (options|suggestions|ideas|ones|gifts?)|"
    r"more (options|suggestions|ideas|gifts?|stuff|like (this|that|these))|"
    r"any other|some more|show (me )?more|give me more|"
    r"(else|other) (can|do|would|could) (you|u)|"
    r"different (options|ideas|ones|suggestions))\b",
    re.I,
)


def _is_more_request(text: str) -> bool:
    return bool(_MORE_RE.search(text or ""))


# --- "Show me gifts / what do you suggest" (after context is gathered) ----- #
_EXPLICIT_PRODUCTS_RE = re.compile(
    r"(?i)(?:\bwhat gifts?\b|\bwhat (?:do|would|can) (?:you|u)\b).{0,48}\b(?:suggest(?:ions?)?|recommend(?:ations?)?|options?|ideas?|picks?)\b"
    r"|\bshow me (?:some )?(?:options?|gifts?|ideas?|suggestions?|products?)\b"
    r"|\b(?:any|some) (?:suggestions?|options?|ideas?|gifts?)\b"
    r"|\bwhat should i (?:get|buy|gift)\b"
    r"|\bgive me (?:some )?(?:ideas?|options?|suggestions?)\b",
)


def _is_explicit_products_request(text: str, conversation: list | None) -> bool:
    """User is explicitly asking to see gift options — search now, don't clarify."""
    if not _EXPLICIT_PRODUCTS_RE.search(text or ""):
        return False
    blob = _recent_user_blob(conversation, text, n=6)
    if _RECIPIENT_RE.search(blob) or detect_occasion(blob) or _has_search_ready_context(blob):
        return True
    for turn in conversation or []:
        if turn.get("role") != "user":
            continue
        c = str(turn.get("content") or "")
        if _RECIPIENT_RE.search(c) or detect_occasion(c):
            return True
    return False


# --- "Pick the best of what's already shown" ------------------------------- #
_PICK_BEST_RE = re.compile(
    r"\b(best (option|one|pick|choice|gift|of these)|"
    r"which (one |is )?(is )?(best|better)|"
    r"which (one |do you |would you )?(recommend|suggest|pick|choose|prefer)|"
    r"pick (the |one |me )?(best|one)|choose (the |one )?(best|one|for me)|"
    r"you (choose|pick|decide|recommend)|"
    r"your (favou?rite|pick|recommendation|choice|best)|"
    r"what'?s the best|recommend (one|me one|the best|your|something)|"
    r"which should i (get|pick|choose|buy)|which of these)\b",
    re.I,
)


def _is_pick_best_request(text: str) -> bool:
    return bool(_PICK_BEST_RE.search(text or ""))


def _shown_names(suggestions: list | None, cart: list | None) -> list[str]:
    names: list[str] = []
    for p in (suggestions or []):
        nm = (p.get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
    for c in (cart or []):
        nm = (c.get("name") or "").strip()
        if nm and nm not in names:
            names.append(nm)
    return names


def more_suggestions_message(suggestions: list | None, cart: list | None) -> str | None:
    names = _shown_names(suggestions, cart)
    if not names:
        return None
    return (
        "MORE SUGGESTIONS REQUEST — the user wants DIFFERENT ideas. They have already been shown or "
        "added the items below, so do NOT suggest any of these again:\n"
        + "; ".join(names[:20])
        + "\nSearch COMPLEMENTARY or adjacent items instead (e.g. if they picked ramen: kimchi, Korean "
        "snacks, sauces, drinks, a snack hamper to bundle, chopsticks). Run fresh searches with "
        "different queries and present only genuinely new products."
    )

# =============================================================================
# Synthetic tools (not part of the MCP)
# =============================================================================

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask ONE warm, friend-like question when you need taste/personality info before searching — "
            "especially for apology/make-up gifts (e.g. what she's into). "
            "Do NOT use for condolences or get-well. Do NOT ask budget first. Max 1 question."
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
    {
        "type": "function",
        "function": {
            "name": "set_customization",
            "description": (
                "Record the personalisation the user gives for a CUSTOMIZABLE product "
                "(one marked customizable in context) — the text to print/engrave, or a "
                "note that they're attaching a photo. Use the 1-based suggestion or cart "
                "number to say which item it's for. A photo itself is uploaded in the app, "
                "not here — only capture the text and the intent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer", "description": "1-based suggestion/cart number being customised."},
                    "text": {"type": "string", "description": "Custom text to print/engrave (e.g. a name or message)."},
                    "wants_photo": {"type": "boolean", "description": "True if they want to attach a photo."},
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_hamper",
            "description": (
                "Build a custom gift hamper for the user (the sender). Call this when they ask to "
                "'build a hamper', 'make a hamper', or 'put together a hamper'. Drive the hamper from "
                "the SENDER'S preferences: first reuse what's on file in USER PROFILE (style, typical "
                "budget, dietary needs, things to avoid) — pass those as preferences without re-asking. "
                "Only when NO preference is stored, ask the user ONE warm question for their hamper "
                "preferences (sweet, savoury, or wellness; budget; anything to avoid) before calling this. "
                "After recording the brief, run separate kapruka_search_products calls for each "
                "preference to fill the hamper with real items."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preferences": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "The sender's preferences driving the hamper — favourite foods/items, theme "
                            "leanings, and anything to avoid. Use the stored profile preferences when available."
                        ),
                    },
                    "theme": {
                        "type": "string",
                        "description": "Overall hamper theme/style, e.g. sweet, savoury, wellness, luxury.",
                    },
                    "budget": {
                        "type": "string",
                        "description": "Optional target budget for the hamper, e.g. 'LKR 5000'.",
                    },
                    "recipient": {
                        "type": "string",
                        "description": "Optional recipient the hamper is for.",
                    },
                    "from_stored_preferences": {
                        "type": "boolean",
                        "description": "True when the preferences came from the sender's stored profile rather than a fresh answer.",
                    },
                },
                "required": ["preferences"],
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
# Availability — only used to hide items the catalogue clearly marks unavailable.
STOCK_TRUE_KEYS = ("in_stock", "instock", "is_available", "available", "availability", "stock_status", "stock")
STOCK_FALSE_KEYS = ("out_of_stock", "outofstock", "sold_out", "soldout", "is_sold_out")
_OUT_OF_STOCK_RE = re.compile(r"\b(out[\s-]?of[\s-]?stock|sold[\s-]?out|unavailable|not available|discontinued)\b", re.I)


def _in_stock(d: dict) -> bool:
    """False only when the catalogue clearly flags the item unavailable; otherwise
    assume it's buyable (most products carry no stock field)."""
    for k in STOCK_FALSE_KEYS:
        v = _first(d, (k,))
        if v is True or (isinstance(v, (int, float)) and v) or \
           (isinstance(v, str) and v.strip().lower() in ("true", "yes", "1", "y")):
            return False
    for k in STOCK_TRUE_KEYS:
        v = _first(d, (k,))
        if v is None:
            continue
        if v is False or (isinstance(v, (int, float)) and v == 0):
            return False
        if isinstance(v, str):
            if _OUT_OF_STOCK_RE.search(v):
                return False
            if v.strip().lower() in ("false", "no", "0", "n"):
                return False
    return True


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


# Products that take a buyer-supplied photo or message. Kapruka exposes no
# explicit flag, so infer it from the name/description.
_CUSTOM_PHOTO_RE = re.compile(
    r"\b(photo|picture|pic|image|collage|portrait)\b", re.I
)
_CUSTOM_TEXT_RE = re.compile(
    r"\b(personali[sz]ed|customi[sz]ed|custom|engrav\w*|monogram\w*|name\s*(printed|engraved)?|"
    r"your\s+(name|message|text|photo)|with\s+name|printed\s+with)\b",
    re.I,
)


def _detect_customization(name: str | None, desc: str | None) -> str | None:
    """Return 'photo', 'text', or None for what custom input a product needs."""
    blob = f"{name or ''} {desc or ''}"
    photo = bool(_CUSTOM_PHOTO_RE.search(blob)) and bool(
        re.search(r"\b(personali[sz]ed|custom|your|upload|print)\b", blob, re.I)
    )
    text = bool(_CUSTOM_TEXT_RE.search(blob))
    if photo:
        return "photo"
    if text:
        return "text"
    return None


def _normalize_product(d: dict) -> dict:
    amount, currency_from_price = _money(_first(d, PRICE_KEYS))
    url = _abs_url(_first(d, URL_KEYS))
    name = _first(d, NAME_KEYS)
    description = _clean_desc(_first(d, DESC_KEYS))
    custom = _detect_customization(name, description)
    return {
        "id": _first(d, ID_KEYS) or _id_from_url(url),
        "name": name,
        "price": amount,
        "currency": _first(d, CURRENCY_KEYS) or currency_from_price,
        "image": _abs_url(_first(d, IMAGE_KEYS)),
        "url": url,
        "description": description,
        "customizable": bool(custom),
        "customization_type": custom,
        "in_stock": _in_stock(d),
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


_GROUP_FILLER_RE = re.compile(
    r"\b(gift|gifts|present|for|her|him|them|the|a|an|best|nice|good|to|of|with|"
    r"buy|some|any)\b",
    re.I,
)


def _group_label(query: str) -> str:
    """Turn a search query into a tidy section label for grouped display."""
    q = _GROUP_FILLER_RE.sub(" ", str(query or ""))
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return "Suggestions"
    return " ".join(w.capitalize() for w in q.split())[:40]


# Taxonomy/category tools return category labels, not buyable products.
_NON_PRODUCT_TOOL_RE = re.compile(r"categor|taxonom|facet|departments?", re.I)
# Never surface adult / NSFW items as gift suggestions.
_ADULT_RE = re.compile(
    r"\b(adult\s*products?|adultproducts?|sex\s*toys?|sextoys?|sex\s*toy|dildos?|"
    r"vibrators?|penis|vagina|vaginal|erotic|eroticaccessor|g[\s-]?spot|"
    r"strap[\s-]?on|strapon|anal\b|bdsm|bondage|fleshlight|masturbat|"
    r"lubricants?|\blube\b|condoms?|intimate\s*wash|intimatewash|"
    r"delay\s*(tissue|spray|gel|wet)|delaywet|peniswiping|sensory\s*play|"
    r"sensoryplay|aphrodisiac)\b",
    re.I,
)


def _is_adult(p: dict) -> bool:
    blob = f"{p.get('name') or ''} {p.get('description') or ''}".lower()
    return bool(_ADULT_RE.search(blob))


def _is_facet(p: dict) -> bool:
    """A catalogue category/facet that slipped through as a 'product' — it has a
    name but no price and no real description (e.g. 'Automobile', 'Books')."""
    price = p.get("price")
    desc = (p.get("description") or "").strip()
    return price in (None, "") and len(desc) < 12


def extract_products(results: list) -> list:
    found = []
    for r in results:
        # Skip taxonomy/category listings — those are filters, not products.
        if _NON_PRODUCT_TOOL_RE.search(r.get("tool") or ""):
            continue
        data = _coerce_json(r.get("output", ""))
        if data is None:
            continue
        group = _group_label((r.get("arguments") or {}).get("q") or "")
        before = len(found)
        _walk_products(data, found)
        for p in found[before:]:
            if not p.get("group"):
                p["group"] = group

    seen_urls, seen_names, unique = set(), set(), []
    for p in found:
        # Never surface adult/NSFW items; drop category facets that slipped in.
        if _is_adult(p) or _is_facet(p):
            continue
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
    # Hide items the catalogue clearly marks unavailable — but never end up with
    # nothing because of it (if every result is flagged, show them anyway).
    in_stock = [p for p in unique if p.get("in_stock", True)]
    return in_stock if in_stock else unique


# =============================================================================
# Answer sanitization
# =============================================================================

_CATALOG_LINE = re.compile(
    r"^\s*(?:\d+\.|[-•*])\s+.*(?:LKR|Rs\.?\s*\d|\(\s*LKR|\bLKR\b)",
    re.I,
)
_NUMBERED_LINE = re.compile(r"^\s*\d+\.\s+")
_CATALOG_INTRO_RE = re.compile(
    r"(?i)\b(?:here are (?:a few |some )?(?:options|picks|ideas|gift ideas)|"
    r"i(?:'ve| have) (?:found|got|pulled together) (?:some |a few )?(?:options|ideas|picks|gift ideas))"
    r"[^.!\n]*[.:]?\s*",
)
_CORPORATE_PHRASES_RE = re.compile(
    r"(?i)\s*(?:let me know if (?:you(?:'d| would) like|you want)|"
    r"please let me know|feel free to (?:let me know|ask)|"
    r"i(?:'d be happy to| would be happy to) (?:help|assist))[^.!\n]*[.!]?\s*",
)


def _line_mentions_product(line: str, names: set[str]) -> bool:
    norm = _norm_text(re.sub(r"[*_#\[\]()]", " ", line))
    if not norm:
        return False
    # Substring match on normalized product titles (handles partial names in lists).
    return any(n and (n in norm or norm in n) for n in names if len(n) >= 8)


def _strip_catalog_from_answer(answer: str, products: list) -> str:
    if not answer:
        return answer

    text = _CATALOG_INTRO_RE.sub("", answer)
    text = _CORPORATE_PHRASES_RE.sub("", text)

    if not products:
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    names = {
        _norm_text(p.get("name"))
        for p in products
        if p.get("name") and len(_norm_text(p.get("name"))) >= 3
    }

    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if _NUMBERED_LINE.match(s):
            continue
        if _CATALOG_LINE.match(s):
            continue
        if re.match(r"^\s*[-•*]\s+", s) and (
            _line_mentions_product(s, names) or len(s) > 40
        ):
            continue
        if names and _line_mentions_product(s, names) and len(s) > 30:
            continue
        out.append(line.rstrip())

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    # Catch numbered items embedded mid-paragraph: "options: 2. Product Name"
    text = re.sub(r"(?i)(?:here are|options:?)\s*\d+\.\s+[^.!\n]+", "", text)
    text = re.sub(r"\s*\d+\.\s+[A-Z][^.!\n]{20,}", "", text)
    return text.strip()


_EMPTY_CATALOG_FALLBACK = (
    "Pulled a few things that should work — they're right below 😊 "
    "Tell me a number if one jumps out, or say what vibe you're going for."
)


# --- Result grounding ------------------------------------------------------- #
# Guard against the model claiming a search found what the user asked for when it
# didn't (e.g. user says she likes ramen, search returns fruit baskets, and the
# model calls one a "ramen gift basket"). We compare the recipient's concrete
# stated interests against what the products actually are.
_GENERIC_WANT_WORDS = frozenset({
    "gift", "gifts", "present", "presents", "basket", "baskets", "hamper",
    "hampers", "something", "anything", "stuff", "things", "thing", "item",
    "items", "set", "food", "snack", "snacks", "treat", "treats", "eat",
    "eating", "drink", "stuff", "nice", "good", "best", "love", "loves",
})


def _salient_want_terms(conversation: list | None, current_text: str | None) -> set[str]:
    """Concrete interest words the user named (e.g. 'ramen', 'cricket') — the
    things a relevant result should actually be about."""
    blob = _recent_user_blob(conversation, current_text, n=3)
    terms: set[str] = set()
    for phrase in _interest_phrases(blob):
        for w in re.findall(r"[a-z]+", phrase.lower()):
            if len(w) >= 4 and w not in _INTEREST_STOP and w not in _GENERIC_WANT_WORDS:
                terms.add(w)
    return terms


def _term_in_blob(term: str, blob: str) -> bool:
    if term in blob:
        return True
    stem = term[:-1] if term.endswith("s") and len(term) > 4 else term
    return stem in blob


def _results_cover_terms(terms: set[str], products: list) -> bool:
    """True if at least one stated interest actually shows up in the products."""
    if not terms:
        return True
    blob = " ".join(
        _norm_text(f"{p.get('name') or ''} {p.get('description') or ''}") for p in products
    )
    if not blob.strip():
        return False
    return any(_term_in_blob(t, blob) for t in terms)


_HONEST_MISS_RE = re.compile(
    r"\b(could ?n'?t find|could not find|did ?n'?t find|did not find|can'?t find|"
    r"cannot find|do ?n'?t have|does ?n'?t have|not available|no exact|"
    r"unfortunately|was ?n'?t able|were ?n'?t any|instead of|as an alternative|"
    r"alternative|closest i|nothing exactly)\b",
    re.I,
)


def _uncovered_terms_in_answer(terms: set[str], products: list, answer: str) -> list[str]:
    """Interest terms the answer asserts as a match but no product supports — i.e.
    claims the model made up. If the reply already admits it couldn't find the
    thing, trust it and leave the wording alone."""
    if not terms or _results_cover_terms(terms, products):
        return []
    if _HONEST_MISS_RE.search(answer or ""):
        return []
    low = _norm_text(answer)
    return [t for t in terms if _term_in_blob(t, low)]


GROUNDING_NUDGE = (
    "GROUNDING CHECK — the search results do NOT actually match what the recipient likes "
    "({terms}). Do NOT describe these products as {terms} or claim they're what she wanted — "
    "that would be a lie the user will instantly catch. Either run another kapruka_search_products "
    "with a closer term (e.g. the exact food/interest, a synonym, or a fitting category), OR, if "
    "Kapruka genuinely doesn't carry it, tell the user honestly that you couldn't find {terms} and "
    "offer what you DID find as alternative ideas — described accurately for what they are."
)


def _requested_product_terms(text: str) -> set[str]:
    """Product types the user explicitly asked for in THIS message (e.g. a 'cake'
    in 'i want a cake also') — the results should actually contain them."""
    return {w.lower() for w in _PRODUCT_NOUN_RE.findall(text or "")}


def _uncovered_terms(terms: set[str], products: list) -> list[str]:
    """Which of the terms no product actually covers (per-term, so a multi-item
    request flags each missing item)."""
    if not terms:
        return []
    blob = " ".join(
        _norm_text(f"{p.get('name') or ''} {p.get('description') or ''}") for p in products
    )
    if not blob.strip():
        return list(terms)
    return [t for t in terms if not _term_in_blob(t, blob)]


MISSING_ITEMS_NUDGE = (
    "GROUNDING CHECK — the user asked for {terms}, but NONE of the current results are that. "
    "Do NOT claim you found {terms}. Run another kapruka_search_products for it (these are common "
    "categories, so try a plain query like q='cake'); if it truly returns nothing, keep the items you "
    "DID find and tell the user honestly you couldn't find a {terms} — never imply it's there when it isn't."
)

# The model sometimes narrates options ("let me find…", "what do you think of
# these?") without ever calling search, so the user sees nothing.
_PROMISES_RESULTS_RE = re.compile(
    r"\b(here are|take a look|have a look|what do you think of (these|those)|these options|"
    r"those options|i (?:have |'?ve )?(found|pulled|got)|pulled (together|up)|check (these|them) out|"
    r"options? (below|for you)|some (?:great |lovely |nice |beautiful )?(options|picks|ideas|gifts)|"
    # "let me / I'll / let me try again to … find/search/look/pull" (words between)
    r"(let me|i'?ll|i will|i'?m going to|going to|let'?s|let me try)\s+(?:\w+\s+){0,5}?"
    r"(find|search|look|pull|get|grab|track down|hunt|dig up))\b",
    re.I,
)


def _promises_results(text: str) -> bool:
    return bool(_PROMISES_RESULTS_RE.search(text or ""))


FORCE_SEARCH_NUDGE = (
    "You described or promised gift options but never called kapruka_search_products, so the user sees "
    "NOTHING below. Search NOW with concrete queries for the recipient, occasion and any stated style/"
    "colour, then give a one-line warm intro. Do NOT say 'let me find', 'here are options' or 'what do "
    "you think of these' unless real products were returned this turn."
)


HONEST_NO_MATCH_FALLBACK = (
    "I couldn't find {terms} itself on Kapruka, but I've pulled a few thoughtful "
    "options that still suit her below 😊 Want me to keep hunting for something closer to {terms}?"
)


# --- Recipient gender grounding -------------------------------------------- #
# Don't show men's items for a woman (or vice versa). Works across the chat's
# languages — kinship/pronoun cues in English, Sinhala and Tamil.
_FEMALE_CUES_EN = re.compile(
    r"\b(mom|mother|mum|mummy|amma|wife|girlfriend|gf|sister|akka|nangi|daughter|"
    r"grandma|grandmother|granny|aunt|auntie|niece|lady|ladies|woman|women|girl|"
    r"her|she|hers|mrs|miss|bride|queen)\b",
    re.I,
)
_MALE_CUES_EN = re.compile(
    r"\b(dad|father|daddy|thaththa|husband|boyfriend|bf|brother|aiya|malli|son|"
    r"grandpa|grandfather|uncle|nephew|man|men|gentleman|gents|boy|guy|him|his|he|"
    r"mr|groom|king)\b",
    re.I,
)
_FEMALE_CUES_INTL = ("අම්මා", "ඇය", "අක්කා", "නංගි", "බිරිඳ", "දුව", "ආච්චි",
                     "அம்மா", "அவள்", "அக்கா", "தங்கை", "மனைவி", "மகள்", "பாட்டி")
_MALE_CUES_INTL = ("තාත්තා", "ඔහු", "අයියා", "මල්ලි", "සැමියා", "පුතා", "සීයා",
                   "அப்பா", "அவன்", "அண்ணா", "தம்பி", "கணவன்", "மகன்", "தாத்தா")

_FEMALE_PROD_RE = re.compile(r"\b(ladies|lady'?s?|women'?s?|woman|female|girls?|girl'?s)\b", re.I)
_MALE_PROD_RE = re.compile(r"\b(men'?s?|gents?|gentlemen|gentleman|male|boys?|boy'?s)\b", re.I)


def _recipient_gender(
    conversation: list | None, recipients: list | None, current_text: str | None = None
) -> str | None:
    """'f', 'm', or None — inferred from kinship/pronoun cues anywhere in the chat."""
    parts = [str(t.get("content") or "") for t in (conversation or []) if t.get("role") == "user"]
    if current_text:
        parts.append(current_text)
    blob = " ".join(parts)
    low = blob.lower()
    f = len(_FEMALE_CUES_EN.findall(low)) + sum(blob.count(w) for w in _FEMALE_CUES_INTL)
    m = len(_MALE_CUES_EN.findall(low)) + sum(blob.count(w) for w in _MALE_CUES_INTL)
    if f > m and f > 0:
        return "f"
    if m > f and m > 0:
        return "m"
    return None


def _product_gender(p: dict) -> str | None:
    blob = f"{p.get('name') or ''} {p.get('description') or ''}".lower()
    if _FEMALE_PROD_RE.search(blob):
        return "f"
    if _MALE_PROD_RE.search(blob):
        return "m"
    return None


def _drop_wrong_gender(products: list, recipient_gender: str | None) -> list:
    """Remove clearly opposite-gender items — but never empty the list."""
    if recipient_gender not in ("f", "m"):
        return products
    opp = "f" if recipient_gender == "m" else "m"
    kept = [p for p in products if _product_gender(p) != opp]
    return kept if kept else products


GENDER_NUDGE = (
    "GENDER CHECK — this gift is for {who}, but several results are the wrong gender. "
    "Re-run kapruka_search_products with a gendered query (e.g. q='{qword} watch', "
    "q='{qword} perfume') and show ONLY {who}'s items — never the opposite gender's."
)


# =============================================================================
# LLM providers (Gemini primary, NIM fallback)
# =============================================================================


class NimTimeout(Exception):
    """Transient LLM failure — degrade gracefully instead of 502."""


def _openai_compat_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list,
    tools: list | None = None,
    timeout: float | None = None,
    temperature: float = 0.3,
    max_tokens: int = 700,
    response_format=None,
    retries: int = 2,
    provider: str = "llm",
) -> dict:
    if not api_key:
        raise PermissionError(f"{provider} API key is not set.")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if response_format:
        payload["response_format"] = response_format

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout or NIM_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code >= 500 and attempt < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            if exc.code >= 500 or exc.code in (408, 429):
                raise NimTimeout(f"{provider} HTTP {exc.code}: {detail[:200]}") from exc
            raise ValueError(f"{provider} HTTP {exc.code}: {detail[:500]}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise NimTimeout(str(exc)) from exc


def _gemini_chat(messages: list, tools: list, timeout: float | None = None, response_format=None) -> dict:
    return _openai_compat_chat(
        base_url=GEMINI_BASE_URL,
        api_key=GEMINI_API_KEY or "",
        model=GEMINI_MODEL,
        messages=messages,
        tools=tools or None,
        timeout=timeout or GEMINI_TIMEOUT,
        temperature=GEMINI_TEMPERATURE,
        max_tokens=700,
        response_format=response_format,
        retries=GEMINI_RETRIES,
        provider="Gemini",
    )


def nim_chat(messages: list, tools: list, timeout: float | None = None, response_format=None) -> dict:
    if not NIM_API_KEY:
        raise PermissionError(
            "NVIDIA_API_KEY is not set. Add it in your Vercel project's "
            "Environment Variables (get a key at https://build.nvidia.com)."
        )
    return _openai_compat_chat(
        base_url=NIM_BASE_URL,
        api_key=NIM_API_KEY,
        model=NIM_MODEL,
        messages=messages,
        tools=tools or None,
        timeout=timeout or NIM_TIMEOUT,
        temperature=NIM_TEMPERATURE,
        max_tokens=700,
        response_format=response_format,
        retries=NIM_RETRIES,
        provider="NIM",
    )


def agent_chat(messages: list, tools: list, timeout: float | None = None, response_format=None) -> tuple[dict, str]:
    """Call the configured primary LLM, falling back to the other provider on failure."""
    providers: list[tuple[str, callable]] = []
    if LLM_PRIMARY == "nim":
        if NIM_API_KEY:
            providers.append(("nim", lambda: nim_chat(messages, tools, timeout=timeout, response_format=response_format)))
        if GEMINI_API_KEY:
            providers.append(("gemini", lambda: _gemini_chat(messages, tools, timeout=timeout, response_format=response_format)))
    else:
        if GEMINI_API_KEY:
            providers.append(("gemini", lambda: _gemini_chat(messages, tools, timeout=timeout, response_format=response_format)))
        if NIM_API_KEY:
            providers.append(("nim", lambda: nim_chat(messages, tools, timeout=timeout, response_format=response_format)))

    if not providers:
        raise PermissionError(
            "No LLM API key is set. Add GEMINI_API_KEY (https://aistudio.google.com/apikey) "
            "and/or NVIDIA_API_KEY in your Vercel environment variables."
        )

    last_err: Exception | None = None
    for name, fn in providers:
        try:
            return fn(), name
        except (NimTimeout, ValueError, PermissionError, OSError) as exc:
            last_err = exc
            continue
    raise NimTimeout(str(last_err) if last_err else "All LLM providers failed")


def _active_agent_model(provider: str) -> str:
    return GEMINI_MODEL if provider == "gemini" else NIM_MODEL


def _default_llm_provider() -> str:
    if LLM_PRIMARY == "nim":
        if NIM_API_KEY:
            return "nim"
        return "gemini" if GEMINI_API_KEY else "nim"
    if GEMINI_API_KEY:
        return "gemini"
    return "nim"


# =============================================================================
# Gift strategy layer (dedicated model)
# =============================================================================

STRATEGY_SYSTEM_PROMPT = """\
You are the STRATEGY brain behind Hari, a Sri Lankan gift concierge. You do NOT talk to the user and you do NOT \
search. You read everything known about this gift and produce ONE gifting strategy the shopping agent then executes.

Reason through these steps in your head before answering:
1. RELATIONSHIP — who is the recipient to the sender (crush, girlfriend, wife, childhood friend, boss, client, \
parent, colleague…) and HOW CLOSE are they really? "Barely know her", "talked a few times", "a girl from uni" = \
NOT close.
2. OCCASION — birthday, anniversary, reunion, apology, condolence, just-because…
3. BUDGET — the ceiling. It is HARD. Pick queries that return items at or under it.
4. PERSONALITY / INTERESTS — what the recipient actually likes.
5. GIFTING GOAL — what feeling should this create? (delight, comfort, romance, respect, low-pressure friendliness.)
6. RISK ASSESSMENT — is the gift too romantic, too expensive, too intimate, or socially/culturally awkward for \
THIS relationship? A near-stranger or a crush is HIGH risk: avoid couple gifts, jewellery, expensive watches, \
perfume, and anything romantic — prefer small, casual, low-pressure picks. A boss/client is PROFESSIONAL: neutral, \
tasteful, never personal. Sombre occasions: never cake/balloons/celebration.

EXAMPLES (study these failures):
- Childhood friend + 10-year reunion + watches + meaningful-not-emotional → collector/practical angle, NOT generic \
"watch" search; prefer display box, strap, streetwear accessory; avoid cheesy sentimental plaques.
- University crush + LKR 5000 + barely talked → HIGH risk; avoid couple watches, jewellery, perfume; prefer casual \
low-pressure (snack box, book, mug, small plant) under budget.
- Wife + loves chocolate + diabetic + peanut allergy → goal is love/special, NOT medical supplements; search \
sugar-free/diabetic-safe chocolate and safe treats; reject peanut items and random health products.
7. GIFT CATEGORY — first pick the TYPE that fits, then the product: experience, accessory, personalized, luxury, \
practical, sentimental, tech, fashion, or food. Don't go straight from a keyword to a search.

Then turn it into an ANGLE, not a literal keyword: "watch enthusiast + reunion + open budget" → "a collector-grade \
watch plus a small sentimental touch", NOT just "watch". Match gender (a woman gets ladies' items, a man gets men's).

ASK ONE QUESTION FIRST when a single answer would materially change the gift and you can't infer it — e.g. a broad \
interest ("likes watches" → luxury, smart, or fashion?; "perfume" → fresh, floral, or woody?), or an unknown that \
matters. Put it in "clarify_question". If you have enough to choose well, leave it empty and just strategise. Never \
ask more than one, and never ask about budget here.

First build the RECIPIENT PROFILE, then the strategy. Reply with ONLY a JSON object, no prose, no markdown, \
exactly this shape:
{
  "relationship": "crush | girlfriend | wife | childhood friend | boss | client | parent | colleague | unknown",
  "occasion": "birthday | anniversary | reunion | apology | just-because | condolence | ... | unknown",
  "recipient_type": "short label, e.g. 'watch-enthusiast guy friend', 'health-conscious wife'",
  "interests": ["the recipient's interests you can infer"],
  "budget": "the budget ceiling or 'open'",
  "constraints": ["hard constraints: allergies, dietary, things to avoid, tone"],
  "gifting_goal": "the feeling to create, e.g. 'make her feel loved without overdoing it'",
  "risk_level": "high | normal | formal",
  "avoid": ["categories/items to NOT show for this relationship/constraint"],
  "prefer": ["categories/items that fit best"],
  "category": "experience | accessory | personalized | luxury | practical | sentimental | tech | fashion | food",
  "clarify_question": "ONE warm question to sharpen the gift, or empty string if none needed",
  "angle": "one sentence: the gifting strategy and why it fits THIS relationship + occasion + goal",
  "recipient_read": "short read on who they are / what they'd value",
  "search_queries": ["2-5 SHORT concrete product nouns tailored to the angle, goal AND budget, e.g. 'sugar free dark chocolate', 'mens leather wallet'"],
  "reject_rule": "one sentence: what to DROP (off-strategy, wrong gender, over budget, unsafe for a constraint, too romantic/expensive for this relationship)",
  "explain_hint": "one warm sentence justifying the pick AND, if relevant, why you avoided something (e.g. 'since you've only spoken a few times, I kept it light')"
}
Keep queries specific, gendered, occasion- and budget-appropriate. Never include adult/intimate items. If you truly \
lack enough to strategise, return {"insufficient": true}."""


def strategy_chat(messages: list, timeout: float | None = None) -> dict:
    providers: list[tuple[str, dict]] = []
    if LLM_PRIMARY == "nim":
        if STRATEGY_NIM_API_KEY:
            providers.append(("nim", {
                "base_url": STRATEGY_NIM_BASE_URL,
                "api_key": STRATEGY_NIM_API_KEY,
                "model": STRATEGY_NIM_MODEL,
            }))
        if STRATEGY_GEMINI_API_KEY:
            providers.append(("gemini", {
                "base_url": STRATEGY_GEMINI_BASE_URL,
                "api_key": STRATEGY_GEMINI_API_KEY,
                "model": STRATEGY_GEMINI_MODEL,
            }))
    else:
        if STRATEGY_GEMINI_API_KEY:
            providers.append(("gemini", {
                "base_url": STRATEGY_GEMINI_BASE_URL,
                "api_key": STRATEGY_GEMINI_API_KEY,
                "model": STRATEGY_GEMINI_MODEL,
            }))
        if STRATEGY_NIM_API_KEY:
            providers.append(("nim", {
                "base_url": STRATEGY_NIM_BASE_URL,
                "api_key": STRATEGY_NIM_API_KEY,
                "model": STRATEGY_NIM_MODEL,
            }))
    if not providers:
        raise PermissionError("Strategy model key not set.")

    last_err: Exception | None = None
    for _name, cfg in providers:
        try:
            return _openai_compat_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=cfg["model"],
                messages=messages,
                tools=None,
                timeout=timeout or STRATEGY_TIMEOUT,
                temperature=STRATEGY_TEMPERATURE,
                max_tokens=STRATEGY_MAX_TOKENS,
                retries=0,
                provider="strategy",
            )
        except Exception as exc:
            last_err = exc
            continue
    raise last_err or PermissionError("Strategy model key not set.")


def build_gift_strategy(conv: list, context_blocks: list, last_user: str, timeout: float) -> dict | None:
    """Run the strategy model and return a parsed strategy dict, or None on any
    failure / insufficiency so the search degrades to its normal behaviour."""
    if not STRATEGY_ENABLED or not (STRATEGY_GEMINI_API_KEY or STRATEGY_NIM_API_KEY):
        return None
    context = "\n\n".join([b for b in (context_blocks or []) if b])
    convo_tail = []
    for turn in conv[-8:]:
        role, content = turn.get("role"), str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            convo_tail.append(f"{role.upper()}: {content}")
    user_payload = (
        "CONTEXT KNOWN ABOUT THIS GIFT:\n"
        + (context or "(little structured context — infer from the conversation)")
        + "\n\nCONVERSATION:\n"
        + "\n".join(convo_tail)
        + f"\n\nLATEST USER MESSAGE: {last_user}\n\nProduce the gifting strategy JSON now."
    )
    messages = [
        {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
    ]
    try:
        completion = strategy_chat(messages, timeout=timeout)
    except Exception:
        return None
    content = ((completion.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    data = _coerce_json(content)
    if not isinstance(data, dict) or data.get("insufficient"):
        return None
    queries = data.get("search_queries")
    if not (isinstance(queries, list) and any(str(q).strip() for q in queries)):
        return None
    data["search_queries"] = [str(q).strip() for q in queries if str(q).strip()][:5]
    return data


def strategy_message(strategy: dict | None) -> str | None:
    """Render the strategy as a hard directive for the shopping agent."""
    if not strategy:
        return None
    lines = [
        "GIFTING STRATEGY (decided by the strategy brain — FOLLOW IT this turn; do NOT free-associate):",
    ]
    rel = strategy.get("relationship")
    risk = str(strategy.get("risk_level") or "").lower()
    if rel and rel != "unknown":
        lines.append(f"- Relationship: {rel}.")
    if risk in ("high", "formal"):
        lines.append(f"- Risk level: {risk} — let this shape how personal/expensive you go.")
    if strategy.get("angle"):
        lines.append(f"- Angle: {strategy['angle']}")
    if strategy.get("recipient_read"):
        lines.append(f"- Recipient: {strategy['recipient_read']}")
    cons = strategy.get("constraints")
    if isinstance(cons, list) and cons:
        kept = [str(c).strip() for c in cons if str(c).strip()]
        if kept:
            lines.append(f"- Constraints (honour strictly): {'; '.join(kept)}")
    queries = strategy.get("search_queries") or []
    if queries:
        lines.append(
            "- Run a SEPARATE kapruka_search_products call for EACH of these exact queries, and ONLY these: "
            + "; ".join(f"q='{q}'" for q in queries)
        )
    if strategy.get("reject_rule"):
        lines.append(
            f"- Reject rule: {strategy['reject_rule']} — silently DROP any returned product that breaks it; "
            "never show it as a card."
        )
    if strategy.get("explain_hint"):
        lines.append(f"- Explain like this: {strategy['explain_hint']}")
    goal = strategy.get("gifting_goal")
    if goal:
        lines.append(f"- Gifting goal: {goal}")
    avoid = strategy.get("avoid")
    if isinstance(avoid, list) and avoid:
        kept = [str(a).strip() for a in avoid if str(a).strip()]
        if kept:
            lines.append(f"- AVOID showing: {', '.join(kept)}")
    prefer = strategy.get("prefer")
    if isinstance(prefer, list) and prefer:
        kept = [str(p).strip() for p in prefer if str(p).strip()]
        if kept:
            lines.append(f"- PREFER: {', '.join(kept)}")
    lines.append(
        "- CURATE, don't dump: present only the best 1–3 on-strategy items, not everything returned. It's good to "
        "say you filtered (e.g. 'found a bunch but these two fit best') and to name your top pick with a one-line "
        "reason it suits them — and, when it matters, why you skipped something (too personal, too pricey). "
        "Keep it warm and short; the cards show the details and prices."
    )
    return "\n".join(lines)


def _prefilter_by_strategy(products: list, strategy: dict | None) -> list:
    """Drop obvious strategy violations before LLM curation."""
    if not strategy or not products:
        return products
    avoid = [str(a).lower() for a in (strategy.get("avoid") or []) if str(a).strip()]
    if not avoid:
        return products
    kept = []
    for p in products:
        blob = _norm_text(f"{p.get('name')} {p.get('description')}")
        if any(a in blob for a in avoid):
            continue
        kept.append(p)
    return kept if kept else products


def _needs_input_response(
    *,
    last_user: str,
    user_en: str,
    target_lang: str | None,
    tool_names: list,
    questions: list,
    intro: str | None = None,
    tr_timeout: float = 18.0,
    model: str | None = None,
) -> dict:
    qs = questions[:3]
    local_qs = qs
    if target_lang and qs:
        joined = _translate("\n".join(qs), "en", target_lang, tr_timeout)
        parts = [p.strip() for p in joined.split("\n") if p.strip()]
        local_qs = parts if len(parts) == len(qs) else qs
    intro_en = (intro or "").strip()
    intro_local = intro_en
    if target_lang and intro_en:
        intro_local = _translate(intro_en, "en", target_lang, tr_timeout) or intro_en
    return {
        "ok": True,
        "needs_input": True,
        "query": last_user,
        "model": model or _active_agent_model(_default_llm_provider()),
        "answer": intro_en,
        "answer_local": intro_local,
        "questions": qs,
        "questions_local": local_qs,
        "user_en": user_en,
        "tools_available": tool_names,
    }


def _run_strategy_searches(
    strategy: dict,
    tools: list,
    deadline: float,
    results: list,
    trace: list,
    max_price: float | None = None,
) -> bool:
    """Execute strategy search_queries in parallel with concurrency cap and staggering.
    Deduplicates overlapping search terms case-insensitively.
    """
    raw_queries = [str(q).strip() for q in (strategy.get("search_queries") or []) if str(q).strip()]
    seen = set()
    queries = []
    for q in raw_queries:
        low = q.lower()
        if low not in seen:
            seen.add(low)
            queries.append(q)
    queries = queries[:5]
    if not queries or (deadline - time.monotonic()) < (MIN_ROUND_SECONDS + 4):
        return False
    try:
        tool_name = _resolve_search_tool_name(tools)
        search_until = time.monotonic() + max(6.0, min(22.0, (deadline - time.monotonic()) - 14))
        with ThreadPoolExecutor(max_workers=min(2, len(queries))) as ex:
            futs = {}
            for i, q in enumerate(queries):
                if i > 0:
                    time.sleep(0.15)
                futs[ex.submit(_one_strategy_search, q, tool_name, max_price)] = q
            for fut in futs:
                rem = search_until - time.monotonic()
                if rem <= 0:
                    break
                try:
                    out = fut.result(timeout=rem)
                except Exception:
                    out = ""
                if out:
                    qq = futs[fut]
                    args = {"params": {"q": qq}} if tool_name == "kapruka_search_products" else {"q": qq}
                    if max_price is not None and tool_name == "kapruka_search_products":
                        args["params"]["max_price"] = max_price
                    results.append({"tool": tool_name, "arguments": args, "output": out})
                    trace.append({"tool": tool_name, "arguments": args})
    except Exception:
        return False
    return bool(results)


def _salvage_search(
    strategy: dict | None,
    fallback_terms: list,
    tools: list,
    results: list,
    trace: list,
    hard_deadline: float,
    max_price: float | None = None,
) -> bool:
    """Last-ditch fast product search when the main pipeline ran out of time
    before finding anything.

    The strategy + agent + curation pipeline is three sequential model calls; on
    a slow upstream the agent loop can time out before it ever searches, leaving
    the user with a dead-end "try again" message. This reuses whatever time is
    left in the curation reserve to surface real Kapruka cards instead, using the
    strategy's concrete queries, or — failing that — the concrete interest/product
    terms gathered this turn. It deliberately does NOT search the user's raw
    sentence, which returns junk or nothing; if there's nothing concrete to search
    we'd rather ask than show irrelevant cards. Returns True if it found
    anything."""
    budget = (hard_deadline - time.monotonic()) - 9.0  # leave ~9s for curation
    if budget < 5:
        return False
    queries = []
    if strategy:
        queries = [str(q).strip() for q in (strategy.get("search_queries") or []) if str(q).strip()]
    if not queries:
        queries = [str(t).strip() for t in (fallback_terms or []) if str(t).strip()]
    queries = queries[:3]
    if not queries:
        return False
    # _run_strategy_searches derives its window from (deadline - now) - 14, so
    # offset the deadline we pass by +14 to grant exactly `budget` of search time.
    search_deadline = time.monotonic() + budget + 14.0
    return _run_strategy_searches({"search_queries": queries}, tools, search_deadline, results, trace, max_price=max_price)


def _resolve_search_tool_name(tools: list) -> str:
    """The Kapruka product-search tool name (discovered at runtime)."""
    names = [str(t.get("name") or "") for t in (tools or [])]
    if "kapruka_search_products" in names:
        return "kapruka_search_products"
    for n in names:
        nl = n.lower()
        if "search" in nl and ("product" in nl or "gift" in nl):
            return n
    for n in names:
        if "search" in n.lower():
            return n
    return "kapruka_search_products"


def _one_strategy_search(query: str, tool_name: str, max_price: float | None = None) -> str:
    """Run a single product search on its own MCP session (thread-safe)."""
    try:
        m = MCPSession()
        m.initialize()
        if tool_name == "kapruka_search_products":
            args = {"params": {"q": query, "response_format": "json"}}
            if max_price is not None:
                args["params"]["max_price"] = max_price
        else:
            args = {"q": query}
        return m.call_tool(tool_name, args)
    except Exception as exc:
        return f"ERROR: {exc}"


def _is_rate_limited(results: list) -> bool:
    for r in results or []:
        out = str(r.get("output") or "").lower()
        if "rate limit" in out or "429" in out or "too many requests" in out:
            return True
    return False


# =============================================================================
# Budget parsing (surface the number to the model — no hard filter)
# =============================================================================

def _coerce_budget(raw) -> float | None:
    if raw in (None, "", False):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    s = str(raw).strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k)?", s)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    if m.group(2):  # "5k" -> 5000
        val *= 1000
    return val if val > 0 else None


def _effective_budget(context: dict, conv: list, profile: dict | None) -> float | None:
    """The active budget ceiling: explicit from the client, else the most recent
    amount stated in chat, else the profile default — unless the user said open/no limit."""
    if context.get("open_budget") or _user_open_budget(conv, None):
        return None
    val = _coerce_budget((context or {}).get("budget"))
    if val:
        return val
    for turn in reversed(conv or []):
        if turn.get("role") == "user":
            m = _SESSION_BUDGET_RE.search(str(turn.get("content") or ""))
            if m:
                val = _coerce_budget(m.group(1))
                if val:
                    return val
            if _OPEN_BUDGET_RE.search(str(turn.get("content") or "")):
                return None
    return _coerce_budget((profile or {}).get("default_budget"))


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
        return "ආයුබෝවන් 😊 මම Hari, ඔයාගේ තෑගි උපදේශක. අද කාවද spoil කරන්නේ?"
    if lang == "ta":
        return "வணக்கம் 😊 நான் Hari, உங்கள் பரிசு உதவியாளர். இன்று யாருக்கு surprise?"
    return "Hi 😊 I'm Hari, your gift concierge. Who are we spoiling today?"


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
        any_custom = False
        for i, p in enumerate(suggestions, 1):
            cur = p.get("currency") or "LKR"
            price = p.get("price")
            price_txt = f" — {cur} {price}" if price not in (None, "") else ""
            tag = ""
            if p.get("customizable"):
                any_custom = True
                ctype = p.get("customization_type") or "text"
                tag = f"  [CUSTOMIZABLE: needs a {'photo' if ctype == 'photo' else 'custom name/message'}]"
            lines.append(f"{i}. {p.get('name')}{price_txt}{tag}")
        if any_custom:
            lines.append(
                "Note: items marked CUSTOMIZABLE need personalisation. Before/at add-to-cart, ask for "
                "the name or message to print (and, for a photo item, tell them they can attach a photo "
                "in the app), then call set_customization with that item's number."
            )
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
    scenario_reset: bool = False,
) -> None:
    if not token or not uid or not answer:
        return
    new_facts = extract_session_facts(
        conversation, last_user, answer, profile.get("session_facts") or {}, scenario_reset=scenario_reset
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
    if name == "set_customization":
        try:
            n = int(args.get("item"))
        except (TypeError, ValueError):
            return None
        product = suggestions[n - 1] if 0 < n <= len(suggestions) else None
        return {
            "action": "customization",
            "item": n,
            "product_name": (product or {}).get("name"),
            "text": str(args.get("text") or "").strip() or None,
            "wants_photo": bool(args.get("wants_photo")),
        }
    if name == "build_hamper":
        prefs = args.get("preferences")
        if isinstance(prefs, str):
            prefs = [prefs]
        prefs = [str(p).strip() for p in (prefs or []) if str(p).strip()]
        theme = str(args.get("theme") or "").strip()
        budget = str(args.get("budget") or "").strip()
        recipient = str(args.get("recipient") or "").strip()
        if not (prefs or theme):
            return None
        return {
            "action": "hamper",
            "theme": theme or None,
            "preferences": prefs,
            "budget": budget or None,
            "recipient": recipient or None,
            "from_stored_preferences": bool(args.get("from_stored_preferences")),
        }
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
    if kind == "customization":
        bits = []
        if action.get("text"):
            bits.append(f"text “{action['text']}”")
        if action.get("wants_photo"):
            bits.append("photo to be attached in the app")
        detail = ", ".join(bits) if bits else "noted"
        return f"Saved customisation for item {action.get('item')}: {detail}."
    if kind == "hamper":
        bits = []
        if action.get("theme"):
            bits.append(f"{action['theme']} theme")
        if action.get("preferences"):
            bits.append("prefs: " + ", ".join(action["preferences"]))
        if action.get("budget"):
            bits.append(f"budget {action['budget']}")
        if action.get("recipient"):
            bits.append(f"for {action['recipient']}")
        detail = "; ".join(bits) if bits else "noted"
        source = "stored profile preferences" if action.get("from_stored_preferences") else "the preferences given"
        return (
            f"Hamper brief saved ({detail}) using {source}. "
            "Now run separate kapruka_search_products calls for each preference to fill the hamper with real items."
        )
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
        if name == "kapruka_search_products":
            if "params" not in args or not isinstance(args["params"], dict):
                args["params"] = {}
            args["params"]["response_format"] = "json"
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

    # Anchor the whole-request budget here so translation, context loading and
    # MCP setup all count against it — the loop then can't overrun the platform
    # limit, and translated (si/ta) sessions degrade gracefully instead of 502.
    t0 = time.monotonic()

    if isinstance(conversation, str):
        conversation = [{"role": "user", "content": conversation}]
    last_user = next(
        (t.get("content", "") for t in reversed(conversation) if t.get("role") == "user"), ""
    )

    context = context or {}
    # Whether we may ask a question this turn (false once the user is answering a
    # pending one). Captured before the search gates can flip allow_questions, so
    # the strategy can still ask ONE sharpening question even on a search turn.
    ask_allowed = allow_questions
    suggestions = context.get("suggestions") or []
    cart = context.get("cart") or []
    instructions = context.get("instructions") or []
    language = context.get("language")
    llm_provider = _default_llm_provider()
    agent_model = _active_agent_model(llm_provider)

    # Fast path: greetings / thanks without an active cart or suggestions.
    if (_is_greeting(last_user) or _is_thanks(last_user)) and not suggestions and not cart:
        return {
            "ok": True,
            "query": last_user,
            "model": agent_model,
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
            "model": agent_model,
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
    access_token = context.get("access_token")
    if target_lang:
        # Inbound translation and the Supabase context load are independent IO —
        # run them together so translation latency overlaps the DB round-trips.
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_tr = ex.submit(_translate, last_user, target_lang, "en")
            f_ctx = ex.submit(_load_user_context, access_token)
            user_en = f_tr.result() or last_user
            profile, uid, recipients, wishlist, orders = f_ctx.result()
        for t in reversed(conv):
            if t.get("role") == "user":
                t["content"] = user_en
                break
    else:
        profile, uid, recipients, wishlist, orders = _load_user_context(access_token)

    # Active budget number (client > stated-in-chat > profile default) — surfaced
    # to the model so it can judge prices itself; not a hard filter.
    budget_ceiling = _effective_budget(context, conv, profile)
    budget_answer_turn = _should_search_after_budget(conv, user_en)
    if budget_answer_turn or context.get("open_budget"):
        budget_ceiling = None

    track_request = _is_order_tracking_request(user_en)
    account_intent = None if track_request else _account_intent(user_en)
    # "see/show them" right after a cart/wishlist mention → view those items.
    if not account_intent and not track_request and _is_view_them_request(user_en):
        recent = ""
        for t in reversed(conv):
            if t.get("role") == "assistant":
                recent = str(t.get("content") or "").lower()
                break
        if "wishlist" in recent:
            account_intent = "view_wishlist"
        elif "cart" in recent:
            account_intent = "view_cart"
        elif wishlist:
            account_intent = "view_wishlist"
        elif cart:
            account_intent = "view_cart"

    # Show the cart / wishlist as real cards — deterministically, with no model
    # search (so a typo can never spray random products at the user).
    if account_intent in ("view_wishlist", "view_cart"):
        if account_intent == "view_wishlist":
            items = [_saved_item_to_product(w, "Wishlist") for w in (wishlist or []) if w.get("name")]
            text = (
                "Here's what's saved in your wishlist 😊 — add any to your cart, or tap the heart to remove one."
                if items else
                "Your wishlist's empty right now — tap the heart on any suggestion to save it here."
            )
        else:
            items = [_saved_item_to_product(c, "Your cart") for c in (cart or []) if c.get("name")]
            if items:
                _, subtotal, cur = _format_cart_lines(cart)
                text = f"Here's what's in your cart — subtotal {cur} {subtotal:,.0f}. Want to change anything or check out?"
            else:
                text = "Your cart's empty right now — let's find something to add 😊"
        local = _translate(text, "en", target_lang) if (target_lang and text) else text
        return {
            "ok": True,
            "query": last_user,
            "model": agent_model,
            "answer": text,
            "answer_local": local,
            "user_en": user_en,
            "products": items,
            "cart_actions": [],
            "tools_available": [],
            "tool_calls": [],
            "results": [],
        }

    # An order-tracking or account request breaks out of the gift-question
    # script — never ask a taste/clarify question or force a product search.
    direct_request = track_request or bool(account_intent)
    # Req #6 — recipient/scenario reset. If the user pivots to a different person,
    # discard the previous recipient's on-screen products so they can't bleed in,
    # and tell the model to build a fresh profile from this message only.
    scenario_reset = (not direct_request) and _scenario_changed(conv, user_en)
    if scenario_reset:
        suggestions = []
    # "Pick the best for me" — recommend from what's already on screen, no new search.
    pick_best = (
        not direct_request
        and bool(suggestions)
        and _is_pick_best_request(user_en)
    )
    # "What else / show me more" — search fresh, complementary items and never
    # repeat what's already on screen or in the cart.
    more_request = (
        not direct_request
        and not pick_best
        and _is_more_request(user_en)
        and bool(_shown_names(suggestions, cart))
    )
    # "Build me a hamper" — its own flow: ask the sender's preferences once if we
    # don't know them, otherwise force the build (build_hamper + per-pref searches).
    hamper_follow = _last_assistant_was_hamper_question(conv)
    hamper_request = (
        not direct_request
        and not more_request
        and not pick_best
        and (_is_hamper_build_request(user_en) or hamper_follow)
    )
    # A reply to our hamper question means the prefs are in hand (their answer, or
    # "you pick"); otherwise check the message/profile for stated preferences.
    hamper_prefs_known = (
        hamper_follow or _hamper_prefs_known(user_en, profile, conv)
    ) if hamper_request else False
    hamper_question = hamper_request and not hamper_prefs_known and allow_questions
    search_first = False if direct_request else _should_search_first(user_en, profile, recipients, conv)
    taste_question = False if direct_request else _needs_taste_question(user_en, profile, recipients, conv)
    clarify_question = False if direct_request else _needs_clarification_question(user_en, conv)
    if more_request:
        # They want more — just search, don't gate behind a question.
        search_first, taste_question, clarify_question = True, False, False
    if pick_best:
        # Recommend from current suggestions — no search, no question.
        search_first, taste_question, clarify_question = False, False, False
    if hamper_request and not hamper_question:
        # Preferences are known — build now; don't divert to taste/clarify questions.
        search_first, taste_question, clarify_question = True, False, False
    # Recipient discovery: we know WHO it's for but not their gender/taste/occasion
    # and they gave no concrete product — ask one warm question instead of blurting
    # random (often wrong-gender) items.
    recipient_discovery = (
        not direct_request
        and not more_request
        and not pick_best
        and not hamper_request
        and not taste_question
        and not clarify_question
        and allow_questions
        and _needs_recipient_discovery_question(user_en, conv, profile, recipients)
    )
    if recipient_discovery:
        search_first = False
    # Ask the budget ONCE per chat before the first real search — offering the
    # saved default vs. a new amount. Never re-ask it for later items, and never
    # lead with it when the user is emotional.
    budget_question = (
        not direct_request
        and not more_request
        and not pick_best
        and not taste_question
        and not clarify_question
        and not hamper_request
        and not recipient_discovery
        and allow_questions
        and _needs_budget_question(user_en, profile, conv)
    )
    if budget_question:
        search_first = False
    explicit_products_request = _is_explicit_products_request(user_en, conv)
    if explicit_products_request:
        search_first = True
        taste_question = False
        clarify_question = False
        recipient_discovery = False
        budget_question = False
        discover_first = False
        force_search = True
        allow_questions = False
    if budget_answer_turn:
        search_first = True
        taste_question = False
        clarify_question = False
        recipient_discovery = False
        budget_question = False
        discover_first = False
        force_search = True
        allow_questions = False
    # Smart preference discovery. When there's an active preference-rich shopping
    # intent — a fresh search-ready message OR the user answering a question we
    # just asked (e.g. their budget reply) — but we don't yet know the
    # recipient's taste, let the model ask ONE targeted question before guessing.
    # Once taste is known (or they say "you pick"), search instead of stalling.
    repair_interests = _recipient_interests_for_repair(user_en, recipients)
    discover_first = False
    if not budget_answer_turn and not explicit_products_request:
        force_search = False
    if allow_questions and not direct_request and not budget_question and not more_request \
            and not pick_best and not repair_interests and not hamper_request and not recipient_discovery:
        momentum = search_first or _last_assistant_was_question(conv)
        pref_rich = bool(_PREF_RICH_RE.search(_recent_user_blob(conv, user_en)))
        if momentum and pref_rich:
            if _should_discover_first(user_en, profile, recipients, conv):
                discover_first = True
            elif not search_first:
                force_search = True
    if force_search:
        search_first = True
    # if search_first and not discover_first:
    #     allow_questions = False

    openai_tools = mcp_tools_to_openai(tools) + CART_TOOLS
    if allow_questions:
        openai_tools = openai_tools + [ASK_USER_TOOL]
    if pick_best:
        # Force a recommendation from the on-screen suggestions: no search tools.
        openai_tools = CART_TOOLS
    elif allow_questions and (taste_question or clarify_question or recipient_discovery or budget_question or hamper_question):
        # Force the model to only ask the required question and not search/build yet
        openai_tools = [ASK_USER_TOOL]

    extra_ctx = [
        SCENARIO_RESET_NUDGE if scenario_reset else None,
        playbook_message(user_en, conv),
        # On a scenario switch, drop the carried recipient memory/session facts so
        # the previous person's interests can't leak into the new gift (req #6).
        None if scenario_reset else conversation_memory_message(conv, user_en, profile),
        None if scenario_reset else session_facts_message(profile),
        recipients_message(recipients),
        wishlist_message(wishlist),
        order_history_message(orders),
        trends_message(user_en, recipients, orders),
        upcoming_occasions_message(recipients),
    ]
    if budget_ceiling and not direct_request:
        extra_ctx.append(f"The user's budget is about LKR {budget_ceiling:,.0f} — keep picks at or under it.")
    elif _user_open_budget(conv, user_en) and not direct_request:
        extra_ctx.append(
            "Budget: OPEN — the user said no limit / no budget constraints. "
            "Include premium and mid-range picks; do not treat the profile default as a ceiling."
        )
    if track_request:
        extra_ctx.append(order_tracking_message(user_en, orders, recipients))
    if account_intent:
        extra_ctx.append(account_intent_message(account_intent, cart, wishlist, orders))
    if more_request:
        extra_ctx.append(more_suggestions_message(suggestions, cart))
    if not direct_request:
        extra_ctx.append(delivery_timing_message(recipients))
    messages = _build_messages(
        conv,
        _context_message(suggestions, cart, instructions),
        None,
        profile_message(profile),
        extra_ctx,
    )
    if track_request:
        messages.append({"role": "system", "content": ORDER_TRACKING_NUDGE})
    elif hamper_request:
        if hamper_question:
            messages.append({"role": "system", "content": HAMPER_QUESTION_NUDGE})
        else:
            messages.append({"role": "system", "content": HAMPER_NUDGE})
    elif account_intent:
        messages.append({"role": "system", "content": ACCOUNT_ACTION_NUDGE})
    elif pick_best:
        messages.append({"role": "system", "content": PICK_BEST_NUDGE})
    elif more_request:
        messages.append({"role": "system", "content": MORE_SUGGESTIONS_NUDGE})
    elif taste_question:
        messages.append({"role": "system", "content": TASTE_QUESTION_NUDGE})
    elif clarify_question:
        messages.append({"role": "system", "content": CLARIFICATION_NUDGE})
    elif recipient_discovery:
        messages.append({"role": "system", "content": DISCOVERY_NUDGE})
    elif budget_question:
        messages.append({"role": "system", "content": BUDGET_QUESTION_NUDGE})
    elif repair_interests and search_first:
        messages.append({
            "role": "system",
            "content": REPAIR_INTERESTS_NUDGE.format(interests=", ".join(repair_interests)),
        })
    elif discover_first:
        messages.append({"role": "system", "content": DISCOVERY_NUDGE})
    elif _is_repair_follow_up(conv, user_en) and search_first:
        messages.append({"role": "system", "content": REPAIR_FOLLOWUP_NUDGE})
    elif budget_answer_turn:
        messages.append({"role": "system", "content": BUDGET_ANSWER_NUDGE})
    elif search_first:
        messages.append({"role": "system", "content": SEARCH_FIRST_NUDGE})

    trace, results, cart_actions = [], [], []
    tool_names = [t.get("name") for t in tools]
    valid_names = set(tool_names) | CART_TOOL_NAMES
    if allow_questions:
        valid_names.add("ask_user")
    if pick_best:
        # No searching when recommending from on-screen suggestions.
        valid_names = set(CART_TOOL_NAMES)
    elif allow_questions and (taste_question or clarify_question or recipient_discovery or budget_question or hamper_question):
        valid_names = {"ask_user"}

    reserve = 8 if target_lang else 0
    curation_deadline = t0 + max(20.0, SEARCH_BUDGET - reserve)
    deadline = max(t0 + 12.0, curation_deadline - CURATION_RESERVE)
    active_strategy = None

    # Concrete interests the result should actually be about (e.g. "ramen").
    # For "show me more" we want DIFFERENT items, so skip interest grounding.
    salient_terms = set() if more_request else _salient_want_terms(conv, user_en)
    grounding_warned = False
    # Product types explicitly requested this turn (e.g. "a cake also") must
    # actually appear in the results — don't claim a category we didn't return.
    requested_terms = set() if more_request else _requested_product_terms(user_en)
    if budget_answer_turn:
        ctx_blob = _recent_user_blob(conv, user_en, n=8)
        requested_terms |= {w.lower() for w in _PRODUCT_NOUN_RE.findall(ctx_blob)}
        if not salient_terms:
            salient_terms = _salient_want_terms(conv, ctx_blob)
    items_warned = False
    already_shown = {_norm_text(n) for n in _shown_names(suggestions, cart)} if more_request else set()
    # Don't show the wrong gender's items (works across the chat's languages).
    recipient_gender = _recipient_gender(conv, recipients, user_en)
    gender_warned = False

    def _tr_timeout() -> float:
        # Cap outbound translation by the time left in the whole-request budget,
        # so a slow Langbly call can never push the function past the platform limit.
        return max(3.0, min(TRANSLATE_TIMEOUT, t0 + SEARCH_BUDGET - time.monotonic()))

    def finalize(answer: str, keep_names: list | None = None) -> dict:
        products = _prefilter_by_strategy(extract_products(results), active_strategy)
        if already_shown:
            products = [p for p in products if _norm_text(p.get("name")) not in already_shown]
        # When the model curated which items to show (its scoring/rejection step),
        # trust it — present only those, in its order. Match loosely so a slightly
        # paraphrased name still resolves. Otherwise show what we found.
        if keep_names is not None:
            def _toks(x):
                return {t for t in re.findall(r"[a-z0-9]+", _norm_text(x)) if len(t) >= 3}
            entries = [(i, _toks(n)) for i, n in enumerate(keep_names)]
            entries = [(i, kt) for i, kt in entries if kt]
            scored = []
            for p in products:
                pt = _toks(p.get("name"))
                idx = next((i for i, kt in entries if kt <= pt or pt <= kt), None)
                if idx is not None:
                    scored.append((idx, p))
            scored.sort(key=lambda t: t[0])
            products = [p for _, p in scored]
            if not products:
                # Curation names didn't match catalogue titles — show top finds.
                products = _prefilter_by_strategy(extract_products(results), active_strategy)[:4]
        if products:
            answer = _strip_catalog_from_answer(answer, products)
            if not (answer or "").strip():
                answer = _EMPTY_CATALOG_FALLBACK
            # Honesty backstop: if the reply still claims an interest the products
            # don't actually match ("ramen gift basket" over fruit baskets),
            # replace the false claim with an honest framing.
            missed = _uncovered_terms_in_answer(salient_terms, products, answer)
            if missed:
                answer = HONEST_NO_MATCH_FALLBACK.format(terms=", ".join(sorted(missed)))
        local = _translate(answer, "en", target_lang, _tr_timeout()) if (target_lang and answer) else answer
        if uid and profile and answer:
            _persist_session_facts(
                access_token, uid, profile, conv, user_en, answer, scenario_reset=scenario_reset
            )
        return {
            "ok": True,
            "query": last_user,
            "model": agent_model,
            "answer": answer,
            "answer_local": local,
            "user_en": user_en,
            "products": products,
            "cart_actions": cart_actions,
            "tools_available": tool_names,
            "tool_calls": trace,
            "results": results,
            "debug": {
                "raw_found": len(extract_products(results)),
                "shown": len(products),
                "had_strategy": any(t.get("tool") == "gift_strategy" for t in trace),
                "llm_provider": llm_provider,
                "searches": [
                    (t.get("arguments") or {}).get("q")
                    for t in trace
                    if "search" in str(t.get("tool") or "")
                ],
            },
        }

    def curate_then_finalize(fallback_answer: str) -> dict:
        """The model scores the found products and picks which to show (its reject
        step) — pure LLM, no keyword filters. On failure, show NO unvetted cards
        rather than risk an unsafe/over-budget item."""
        nonlocal llm_provider, agent_model
        prods = _prefilter_by_strategy(extract_products(results), active_strategy)
        if already_shown:
            prods = [p for p in prods if _norm_text(p.get("name")) not in already_shown]
        if not prods:
            if _is_rate_limited(results):
                return finalize(
                    "Kapruka's search server is currently receiving too many requests. "
                    "Please wait a moment and try again 😊"
                )
            return finalize(fallback_answer)
        ans_rem = curation_deadline - time.monotonic()
        if ans_rem < 5:
            return finalize(
                "Found a few, but I want to double-check they fit before showing — resend and I'll be quick 😊",
                keep_names=[],
            )
        lines = []
        for i, p in enumerate(prods[:24], 1):
            desc = (p.get("description") or "")[:110]
            lines.append(f"{i}. {p.get('name')} — {p.get('currency') or 'LKR'} {p.get('price')}. {desc}")
        strat_ctx = ""
        if active_strategy:
            sm = strategy_message(active_strategy)
            if sm:
                strat_ctx = "\n\n" + sm
            if budget_ceiling:
                strat_ctx += f"\nBudget ceiling: LKR {budget_ceiling:,.0f} — reject over-budget items."
        cur_msgs = messages + [{
            "role": "user",
            "content": "Candidate products found (numbered):\n" + "\n".join(lines)
            + strat_ctx
            + "\n\nAs the concierge, SCORE each on: relationship fit, occasion fit, interest fit, budget fit, "
            "constraint SAFETY (allergy/dietary/etc.), social appropriateness, and gift quality. REJECT every poor, "
            "unsafe, over-budget, or irrelevant one — never keep a product just because it was returned. RANK the "
            "survivors best-first. The product CARDS render below your reply with full details and prices, so do NOT "
            "list products or prices in your reply. Respond with ONLY a JSON object: "
            '{"keep": [<item NUMBERS to show, RANKED best first>], '
            '"reply": "Start by showing you understand the relationship and occasion in one warm line. Then lead with '
            'your #1 best match (🥇) by name and one line on why it fits the relationship/occasion/goal. If relevant, '
            'one line on what you skipped and why. Finally, ask the user if they would like to see other options, '
            'recommending some other specific products or gift categories (e.g. flowers, a cake, or a giftset) that '
            'could also fit. 3-4 warm sentences total, no numbered list, no prices."}. '
            "Keep only items that truly fit (usually 1-4). If none are safe/appropriate, keep:[] and say so kindly.",
        }]
        content = ""
        for rf in ({"type": "json_object"}, None):
            try:
                completion, llm_provider = agent_chat(
                    cur_msgs, [], timeout=min(GEMINI_TIMEOUT, NIM_TIMEOUT, ans_rem), response_format=rf
                )
                agent_model = _active_agent_model(llm_provider)
                content = ((completion.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                break
            except NimTimeout:
                content = ""
                break
            except Exception:
                continue  # response_format unsupported on this endpoint -> retry without
        obj = None
        for chunk in _extract_balanced_objects(content):
            o = _parse_json_or_literal(chunk)
            if isinstance(o, dict) and ("keep" in o or "reply" in o):
                obj = o
                break
        if obj is None:
            if prods:
                return finalize(
                    fallback_answer or "Pulled a few options — take a look below 😊",
                    keep_names=[p.get("name") for p in prods[:4]],
                )
            return finalize(
                "Let me double-check these actually fit before showing them — mind resending? Was a touch slow my end 😊",
                keep_names=[],
            )
        reply = str(obj.get("reply") or "").strip()
        keep_names = []
        for k in (obj.get("keep") or []):
            if isinstance(k, bool):
                continue
            if isinstance(k, (int, float)) or (isinstance(k, str) and k.strip().isdigit()):
                n = int(k) - 1
                if 0 <= n < len(prods):
                    keep_names.append(prods[n].get("name"))
            elif isinstance(k, str):
                keep_names.append(k)
        if not keep_names:
            # Curation rejected everything — still show top picks so the user sees cards.
            if prods:
                return finalize(
                    reply or "Pulled a few options — take a look below 😊",
                    keep_names=[p.get("name") for p in prods[:4]],
                )
            return finalize(
                reply or "Hmm, nothing in what I found is a safe, on-budget fit. Want me to try a "
                "different angle — a specific brand, or a non-food gift? 😊",
                keep_names=[],
            )
        return finalize(reply or fallback_answer, keep_names=keep_names)

    def ask(questions: list, intro: str | None = None) -> dict:
        return _needs_input_response(
            last_user=last_user,
            user_en=user_en,
            target_lang=target_lang,
            tool_names=tool_names,
            questions=questions,
            intro=intro,
            tr_timeout=_tr_timeout(),
            model=agent_model,
        )

    # if hamper_question:
    #     intro, question = _hamper_pref_question()
    #     return ask([question], intro=intro)

    # === Strategy-first: recipient profile → gifting strategy → search → curate ===
    will_strategize = (
        STRATEGY_ENABLED
        and not direct_request
        and not pick_best
        and not hamper_request
        and not more_request
        and not budget_answer_turn
    )
    if will_strategize:
        strat_timeout = min(STRATEGY_TIMEOUT, (deadline - time.monotonic()) - (MIN_ROUND_SECONDS + 14))
        if strat_timeout >= 5:
            try:
                active_strategy = build_gift_strategy(
                    conv, [profile_message(profile)] + extra_ctx, user_en, timeout=strat_timeout
                )
            except Exception:
                active_strategy = None
            if active_strategy:
                trace.append({"tool": "gift_strategy", "arguments": active_strategy})
                clarify = str(active_strategy.get("clarify_question") or "").strip()
                if (
                    clarify
                    and ask_allowed
                    and not _last_assistant_was_question(conv)
                    and not explicit_products_request
                ):
                    intro = str(active_strategy.get("explain_hint") or "").strip() or None
                    return ask([clarify], intro=intro)
                strat_msg = strategy_message(active_strategy)
                if strat_msg:
                    messages.append({"role": "system", "content": strat_msg})
                if _run_strategy_searches(active_strategy, tools, deadline, results, trace, max_price=budget_ceiling):
                    hint = str(active_strategy.get("explain_hint") or "").strip()
                    return curate_then_finalize(
                        hint or "Pulled together some options — take a look below 😊"
                    )

    # if taste_question and not active_strategy:
    #     return ask([_repair_taste_question(user_en)], intro=_repair_ask_intro(user_en))
    # 
    # if clarify_question and not active_strategy:
    #     intro, question = _clarification_ask(user_en)
    #     return ask([question], intro=intro)
    # 
    # if recipient_discovery and not active_strategy:
    #     intro, question = _recipient_discovery_ask(user_en)
    #     return ask([question], intro=intro)
    # 
    # if budget_question and not active_strategy:
    #     intro, question = _budget_ask(profile)
    #     return ask([question], intro=intro)

    if budget_answer_turn:
        ctx_queries = _context_search_queries(conv, user_en)
        if ctx_queries and _run_strategy_searches(
            {"search_queries": ctx_queries}, tools, deadline, results, trace, max_price=budget_ceiling
        ):
            if extract_products(results):
                return curate_then_finalize(
                    "Lovely — here are a few that should work for that occasion 😊"
                )

    json_corrections = 0
    max_json_corrections = 2
    search_retries = 0
    max_search_retries = 2

    def degrade(partial: str | None = None) -> dict:
        if partial:
            return finalize(partial)
        if _is_rate_limited(results):
            return finalize(
                "Kapruka's search server is currently receiving too many requests. "
                "Please wait a moment and try again 😊"
            )
        # The heavy pipeline ran out of time before finding anything — spend the
        # leftover curation reserve on one fast direct search so the user still
        # gets cards instead of a dead-end message that just re-triggers the same
        # slow path when they resend.
        if not extract_products(results) and not cart_actions:
            salvage_terms = sorted(salient_terms | requested_terms)
            if not salvage_terms and budget_answer_turn:
                salvage_terms = _context_search_queries(conv, user_en)
            try:
                _salvage_search(
                    active_strategy,
                    salvage_terms,
                    tools,
                    results,
                    trace,
                    curation_deadline,
                    max_price=budget_ceiling,
                )
            except Exception:
                pass
        # Only claim we have options when real products actually came back. A
        # non-empty `results` can still hold zero parseable products (e.g. an
        # empty Kapruka response to a vague query), which would otherwise show
        # "they're below" with no cards underneath.
        if extract_products(results):
            return curate_then_finalize("Got some options for you — they're below 😊")
        if cart_actions:
            return finalize("Done — cart's updated 👍")
        if budget_answer_turn or _user_open_budget(conv, user_en):
            return finalize(
                "Kapruka's being a bit slow right now — try sending the gift idea again "
                "(e.g. anniversary flowers) and I'll search straight away 😊"
            )
        return finalize(
            "I couldn't pull up good matches just now — tell me who it's for and a "
            "rough budget and I'll get some options up for you. 😊"
        )

    for _ in range(MAX_TOOL_ROUNDS):
        remaining = deadline - time.monotonic()
        if remaining < MIN_ROUND_SECONDS:
            return degrade()

        try:
            completion, llm_provider = agent_chat(
                messages, openai_tools, timeout=min(GEMINI_TIMEOUT, NIM_TIMEOUT, remaining)
            )
            agent_model = _active_agent_model(llm_provider)
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
                return finalize("Hi 😊 I'm Hari, your gift concierge. Who are we spoiling today?")
            # Promised options but never searched -> make it actually search.
            if (not results and not cart_actions and _promises_results(content)
                    and search_retries < max_search_retries):
                search_retries += 1
                messages.append({"role": "user", "content": FORCE_SEARCH_NUDGE})
                continue
            # If we have product results, the model curates which cards to show
            # (its reject step). A pure reply with no results just goes through.
            return curate_then_finalize(content) if results else finalize(content)

        if allow_questions:
            ask_call = next((c for c in norm if c["name"] == "ask_user"), None)
            if ask_call is not None:
                questions = _normalize_questions((ask_call.get("arguments") or {}).get("questions"))
                if questions:
                    # Don't ask "what do you think of these options?" when there are
                    # none — search for real products instead.
                    if (not results and not suggestions
                            and any(_promises_results(q) for q in questions)
                            and search_retries < max_search_retries):
                        search_retries += 1
                        messages.append({"role": "user", "content": FORCE_SEARCH_NUDGE})
                        continue
                    return ask(questions)

        if not allow_questions and any(c.get("name") == "ask_user" for c in norm):
            norm = [c for c in norm if c.get("name") != "ask_user"]
            if not norm:
                messages.append({
                    "role": "user",
                    "content": (
                        "Do not ask clarifying questions. Search Kapruka now for items that fit "
                        "the recipient and situation — then reply with brief empathy."
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

        # Grounding: if the search didn't return anything matching the recipient's
        # stated interest, tell the model once — so it re-searches or stays honest
        # instead of relabelling unrelated products.
        if not grounding_warned and salient_terms and results:
            prods = extract_products(results)
            if prods and not _results_cover_terms(salient_terms, prods):
                grounding_warned = True
                messages.append({
                    "role": "system",
                    "content": GROUNDING_NUDGE.format(terms=", ".join(sorted(salient_terms))),
                })

        # Requested-item grounding: an item the user explicitly asked for ("a cake
        # also") that no result actually is — nudge a re-search or honest wording.
        if not items_warned and requested_terms and results:
            prods = extract_products(results)
            missing = _uncovered_terms(requested_terms, prods)
            if prods and missing:
                items_warned = True
                messages.append({
                    "role": "system",
                    "content": MISSING_ITEMS_NUDGE.format(terms=", ".join(sorted(missing))),
                })

        # Gender grounding: if the gift is for a woman but the results are
        # mostly men's items (or vice versa), nudge a gendered re-search once.
        if not gender_warned and recipient_gender and results:
            prods = extract_products(results)
            opp = "f" if recipient_gender == "m" else "m"
            wrong = [p for p in prods if _product_gender(p) == opp]
            if prods and len(wrong) * 2 >= len(prods):
                gender_warned = True
                messages.append({
                    "role": "system",
                    "content": GENDER_NUDGE.format(
                        who="a woman" if recipient_gender == "f" else "a man",
                        qword="ladies" if recipient_gender == "f" else "men's",
                    ),
                })

    if results:
        return curate_then_finalize("Pulled together some options — take a look below 😊")
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
            "budget": data.get("budget"),
        }
        self._run(
            conversation,
            allow_questions=bool(data.get("allow_questions", True)),
            context=context,
        )
