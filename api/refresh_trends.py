"""Vercel Python serverless function: refresh gift/fashion trends from the web.

GET /api/refresh_trends — searches the web via Tavily, distils the results into
the concierge's trends schema with the NIM model, and upserts them into the
Supabase ``trends_cache`` table. Designed to be hit by a daily Vercel Cron.

Protected by CRON_SECRET: Vercel automatically sends
``Authorization: Bearer <CRON_SECRET>`` on cron invocations, and manual calls
must present the same secret.

Self-contained (standard library only) — no sibling imports, which keeps the
Vercel bundle happy.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL = os.environ.get("TAVILY_URL", "https://api.tavily.com/search")

NIM_BASE_URL = os.environ.get("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_MODEL = os.environ.get("NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

CRON_SECRET = os.environ.get("CRON_SECRET", "")
TIMEOUT = float(os.environ.get("TRENDS_REFRESH_TIMEOUT", "45"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Fashion items the concierge knows how to bias toward (mirror of trends.json).
STYLE_ITEMS = ["dress", "saree", "outfit", "jewellery", "watch", "perfume", "handbag", "shoes"]

# Web searches whose snippets we distil into trends.
TREND_QUERIES = [
    "trending gift ideas in Sri Lanka this month",
    "popular fashion dresses sarees and jewellery trends in Sri Lanka",
]


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict | None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def tavily_search(query: str) -> dict | None:
    if not TAVILY_API_KEY:
        return None
    return _post_json(
        TAVILY_URL,
        {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "max_results": 5,
            "search_depth": "basic",
            "include_answer": True,
        },
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
        timeout=20,
    )


def gather_web_context() -> tuple[str, int]:
    """Run the trend searches and return a compact text blob + result count."""
    chunks: list[str] = []
    count = 0
    for q in TREND_QUERIES:
        data = tavily_search(q)
        if not isinstance(data, dict):
            continue
        answer = (data.get("answer") or "").strip()
        if answer:
            chunks.append(f"Q: {q}\nSummary: {answer}")
        for r in (data.get("results") or [])[:5]:
            snippet = (r.get("content") or "").strip()
            title = (r.get("title") or "").strip()
            if snippet:
                count += 1
                chunks.append(f"- {title}: {snippet[:300]}")
    return "\n".join(chunks), count


# --------------------------------------------------------------------------- #
# LLM distillation
# --------------------------------------------------------------------------- #
def _nim_complete(messages: list, timeout: float) -> str | None:
    if not NIM_API_KEY:
        return None
    data = _post_json(
        f"{NIM_BASE_URL}/chat/completions",
        {"model": NIM_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 700},
        headers={"Authorization": f"Bearer {NIM_API_KEY}", "Accept": "application/json"},
        timeout=timeout,
    )
    try:
        return data["choices"][0]["message"]["content"]
    except (TypeError, KeyError, IndexError):
        return None


def _parse_json_object(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    # Strip ```json fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.I | re.M).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def distil_trends(web_context: str) -> dict | None:
    schema_items = ", ".join(STYLE_ITEMS)
    system = (
        "You distil web search snippets into structured gift and fashion trends "
        "for a Sri Lankan online gift store. Output ONLY a JSON object, no prose."
    )
    user = (
        "From the web snippets below, produce current trends as JSON with this exact shape:\n"
        "{\n"
        '  "bestsellers": [6-8 short, popular giftable product types e.g. "flower bouquets", "chocolate hampers"],\n'
        '  "style_trends": { ' + schema_items + ': [2-3 short trendy style/colour cues each] }\n'
        "}\n"
        "Rules: keep every entry to a few words; only include items that are realistically "
        "giftable/deliverable in Sri Lanka; if the snippets say nothing about an item, give your "
        "best current general guess for it; do NOT invent brand names or prices.\n\n"
        "WEB SNIPPETS:\n" + (web_context or "(none)")
    )
    raw = _nim_complete(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=30,
    )
    parsed = _parse_json_object(raw or "")
    if not parsed:
        return None
    out: dict = {}
    bs = parsed.get("bestsellers")
    if isinstance(bs, list):
        clean = [str(x).strip() for x in bs if str(x).strip()]
        if clean:
            out["bestsellers"] = clean[:8]
    st = parsed.get("style_trends")
    if isinstance(st, dict):
        styles = {}
        for item, cues in st.items():
            key = str(item).strip().lower()
            if key in STYLE_ITEMS and isinstance(cues, list):
                vals = [str(c).strip() for c in cues if str(c).strip()]
                if vals:
                    styles[key] = vals[:3]
        if styles:
            out["style_trends"] = styles
    # Require at least one usable dimension before we overwrite the cache.
    if not out.get("bestsellers") and not out.get("style_trends"):
        return None
    out["_source"] = "tavily"
    return out


# --------------------------------------------------------------------------- #
# Supabase upsert (service role)
# --------------------------------------------------------------------------- #
def store_trends(data: dict) -> bool:
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return False
    body = [{
        "id": "global",
        "data": data,
        "source": "tavily",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }]
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/trends_cache?on_conflict=id",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_refresh() -> dict:
    if not TAVILY_API_KEY:
        return {"ok": False, "error": "TAVILY_API_KEY is not set"}
    web_context, n = gather_web_context()
    if not web_context:
        return {"ok": False, "error": "no web results from Tavily", "results": n}
    trends = distil_trends(web_context)
    if not trends:
        return {"ok": False, "error": "could not distil trends from snippets", "results": n}
    stored = store_trends(trends)
    return {
        "ok": stored,
        "stored": stored,
        "results_used": n,
        "bestsellers": len(trends.get("bestsellers") or []),
        "style_items": sorted((trends.get("style_trends") or {}).keys()),
        "error": None if stored else "failed to write trends_cache (check SUPABASE_SERVICE_ROLE_KEY)",
    }


def _authorized(headers) -> bool:
    # If no secret is configured, allow (useful for first manual run); once set,
    # require it so the endpoint can't be triggered by the public.
    if not CRON_SECRET:
        return True
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    return auth == f"Bearer {CRON_SECRET}"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel/BaseHTTPRequestHandler API)
        if not _authorized(self.headers):
            payload, code = {"ok": False, "error": "unauthorized"}, 401
        else:
            try:
                payload = run_refresh()
                code = 200 if payload.get("ok") else 502
            except Exception as exc:  # surface any failure as JSON
                payload, code = {"ok": False, "error": str(exc)}, 502

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
