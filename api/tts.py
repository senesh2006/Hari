"""Vercel Python serverless function: server-side text-to-speech via CAMB.AI.

The browser's Web Speech API has no Sinhala or Tamil voices on most devices, so
we proxy those (and optionally any) languages through CAMB.AI's MARS models,
which speak 140+ languages. CAMB's `/apis/tts-stream` endpoint returns finished
audio in one synchronous call, so no task polling is needed.

POST /api/tts  body: {"text": "...", "lang": "si"}  -> audio/mpeg bytes.

Requires the CAMB_API_KEY environment variable. Set it in your Vercel project's
Environment Variables (and locally for testing).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

CAMB_API_KEY = os.environ.get("CAMB_API_KEY")
CAMB_URL = os.environ.get("CAMB_TTS_URL", "https://client.camb.ai/apis/tts-stream")
CAMB_MODEL = os.environ.get("CAMB_SPEECH_MODEL", "mars-8.1-flash-beta")
CAMB_VOICE_ID = int(os.environ.get("CAMB_VOICE_ID", "147320"))
CAMB_TIMEOUT = float(os.environ.get("CAMB_TIMEOUT", "60"))
MAX_CHARS = int(os.environ.get("CAMB_MAX_CHARS", "600"))

# App language -> CAMB locale tag.
LANG_TAGS = {"en": "en-us", "si": "si-lk", "ta": "ta-in"}


def synthesize(text: str, lang: str) -> bytes:
    if not CAMB_API_KEY:
        raise PermissionError(
            "CAMB_API_KEY is not set. Add it in your Vercel project's "
            "Environment Variables to enable Sinhala/Tamil speech."
        )
    tag = LANG_TAGS.get((lang or "en").lower(), "en-us")
    body = json.dumps({
        "text": text[:MAX_CHARS],
        "voice_id": CAMB_VOICE_ID,
        "language": tag,
        "speech_model": CAMB_MODEL,
    }).encode("utf-8")
    req = urllib.request.Request(
        CAMB_URL,
        data=body,
        method="POST",
        headers={"x-api-key": CAMB_API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=CAMB_TIMEOUT) as resp:
        return resp.read()


class handler(BaseHTTPRequestHandler):
    def _error(self, code: int, message: str):
        body = json.dumps({"ok": False, "error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 (Vercel/BaseHTTPRequestHandler API)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._error(400, "Body must be JSON.")

        text = str(data.get("text") or "").strip()
        lang = str(data.get("lang") or "en").strip()
        if not text:
            return self._error(400, "Missing 'text'.")

        try:
            audio = synthesize(text, lang)
        except PermissionError as exc:
            return self._error(400, str(exc))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            return self._error(502, f"CAMB.AI error {exc.code}: {detail}")
        except Exception as exc:
            return self._error(502, str(exc))

        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)
