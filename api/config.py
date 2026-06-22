"""Public runtime config for the static frontend (no secrets beyond Supabase anon key).

GET /api/config -> { supabaseUrl, supabaseAnonKey, assemblyAiEnabled }
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._respond(
            200,
            {
                "supabaseUrl": os.environ.get("SUPABASE_URL", "").rstrip("/"),
                "supabaseAnonKey": os.environ.get("SUPABASE_ANON_KEY", ""),
                "assemblyAiEnabled": bool(os.environ.get("ASSEMBLYAI_API_KEY")),
            },
        )
