"""Personalization helpers for api/search.py — knowledge, profile context, memory."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

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


def _load_json(name: str) -> dict:
    path = os.path.join(_DATA_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_occasions() -> dict:
    global _OCCASIONS_CACHE
    if _OCCASIONS_CACHE is None:
        _OCCASIONS_CACHE = _load_json("occasions.json")
    return _OCCASIONS_CACHE


def get_kapruka_facts() -> dict:
    global _FACTS_CACHE
    if _FACTS_CACHE is None:
        _FACTS_CACHE = _load_json("kapruka_facts.json")
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
    """Occasion playbook slice + Kapruka facts for the model."""
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
    if len(lines) <= 0:
        return None
    return "\n".join(lines)


def profile_message(profile: dict | None) -> str | None:
    if not profile:
        return None
    lines = [
        "USER PROFILE (use to personalize searches and tone; do not recite verbatim):"
    ]
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
            if isinstance(styles, list):
                lines.append(f"- Style: {', '.join(styles)}")
            else:
                lines.append(f"- Style: {styles}")
        avoid = prefs.get("avoid_list")
        if avoid and isinstance(avoid, list):
            lines.append(f"- Always avoid: {', '.join(avoid)}")
        dietary = prefs.get("dietary")
        if dietary:
            lines.append(f"- Dietary: {dietary}")
        corporate = prefs.get("corporate_gifting")
        if corporate:
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
            # Compare month/day this year
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


_BUDGET_RE = re.compile(
    r"(?:under|below|max|budget|rs\.?|lkr)\s*([0-9][0-9,]*)",
    re.I,
)


def extract_session_facts(
    conversation: list,
    last_user: str,
    answer: str,
    existing: dict | None,
) -> dict:
    """Merge rolling session facts from the latest turn (no extra model call)."""
    facts = dict(existing or {})
    text = f"{last_user} {answer}".lower()
    occasion = detect_occasion(last_user) or detect_occasion(answer)
    if occasion:
        facts["occasion"] = occasion.replace("_", " ")
    budget_m = _BUDGET_RE.search(last_user or "")
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


def personality_greeting(profile: dict | None) -> str | None:
    p = (profile or {}).get("gifting_personality")
    if not p:
        return None
    label = PERSONALITY_LABELS.get(p, p.replace("_", " "))
    templates = {
        "thoughtful_planner": f"Welcome back — as a {label}, I'll help you find gifts that feel carefully chosen.",
        "last_minute_hero": f"Hey — {label} mode: tell me who it's for and I'll find something great, fast.",
        "practical_gifter": f"Hi! I'll keep things useful and within budget — that's the {label} way.",
        "big_spender": f"Welcome — let's find something memorable. Your {label} picks tend to impress.",
        "sentimental_soul": f"Hi there — I'll help you find something heartfelt. That's your {label} style.",
        "creative_maker": f"Welcome! Let's find something a little unexpected — perfect for a {label}.",
    }
    return templates.get(p)
