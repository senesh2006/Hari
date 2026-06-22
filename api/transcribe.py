"""English speech-to-text via AssemblyAI (Universal model).

POST /api/transcribe
  Body: raw audio bytes (e.g. audio/webm from MediaRecorder)
  Returns: {"ok": true, "text": "..."}

Requires ASSEMBLYAI_API_KEY. AssemblyAI does not offer standalone TTS — use /api/tts
for spoken replies (CAMB.AI). See api/tts.py.

Standard library only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY")
ASSEMBLY_BASE = os.environ.get("ASSEMBLYAI_BASE_URL", "https://api.assemblyai.com/v2")
ASSEMBLY_SPEECH_MODEL = os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "universal-3-pro")
ASSEMBLY_TIMEOUT = float(os.environ.get("ASSEMBLYAI_TIMEOUT", "25"))
POLL_INTERVAL = float(os.environ.get("ASSEMBLYAI_POLL_INTERVAL", "0.4"))


def _request(method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    if not ASSEMBLYAI_API_KEY:
        raise PermissionError(
            "ASSEMBLYAI_API_KEY is not set. Add it in Vercel Environment Variables."
        )
    url = f"{ASSEMBLY_BASE.rstrip('/')}/{path.lstrip('/')}"
    hdrs = {"authorization": ASSEMBLYAI_API_KEY}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=ASSEMBLY_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:400].decode("utf-8", errors="replace")
        raise ValueError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc


def transcribe_audio(audio_bytes: bytes) -> str:
    if not audio_bytes:
        raise ValueError("Empty audio.")
    upload = _request("POST", "upload", data=audio_bytes)
    audio_url = upload.get("upload_url")
    if not audio_url:
        raise ValueError("AssemblyAI upload failed — no upload_url.")

    payload = {"audio_url": audio_url, "language_code": "en"}
    if ASSEMBLY_SPEECH_MODEL:
        payload["speech_model"] = ASSEMBLY_SPEECH_MODEL

    job = _request(
        "POST",
        "transcript",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    tid = job.get("id")
    if not tid:
        raise ValueError("AssemblyAI transcript job failed to start.")

    deadline = time.monotonic() + ASSEMBLY_TIMEOUT
    while time.monotonic() < deadline:
        result = _request("GET", f"transcript/{tid}")
        status = result.get("status")
        if status == "completed":
            return str(result.get("text") or "").strip()
        if status == "error":
            raise ValueError(result.get("error") or "AssemblyAI transcription error.")
        time.sleep(POLL_INTERVAL)

    raise TimeoutError("AssemblyAI transcription timed out.")


class handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        audio = self.rfile.read(length) if length else b""
        if not audio:
            return self._json(400, {"ok": False, "error": "Missing audio body."})
        try:
            text = transcribe_audio(audio)
            self._json(200, {"ok": True, "text": text})
        except PermissionError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(502, {"ok": False, "error": str(exc)})
