"""Mint a short-lived AssemblyAI Voice Agent token for browser WebSocket auth.

GET /api/voice_token -> { token, expires_in_seconds }

The browser uses this for wss://agents.assemblyai.com/v1/ws?token=...
Requires ASSEMBLYAI_API_KEY (never exposed to the client).

Standard library only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
TOKEN_URL = os.environ.get(
    "ASSEMBLYAI_TOKEN_URL", "https://agents.assemblyai.com/v1/token"
)
TOKEN_EXPIRES = int(os.environ.get("ASSEMBLYAI_TOKEN_EXPIRES", "120"))
MAX_SESSION = int(os.environ.get("ASSEMBLYAI_MAX_SESSION_SECONDS", "90"))


def mint_token() -> dict:
    if not ASSEMBLYAI_API_KEY:
        raise PermissionError(
            "ASSEMBLYAI_API_KEY is not set. Add it in Vercel Environment Variables."
        )
    url = (
        f"{TOKEN_URL}?expires_in_seconds={TOKEN_EXPIRES}"
        f"&max_session_duration_seconds={MAX_SESSION}"
    )
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {ASSEMBLYAI_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", errors="replace")
        raise ValueError(f"AssemblyAI token HTTP {exc.code}: {detail}") from exc


class handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        try:
            data = mint_token()
            self._json(200, {"ok": True, **data})
        except PermissionError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(502, {"ok": False, "error": str(exc)})
