"""Vercel Python serverless function: Kapruka MCP health check.

GET /api/check -> runs an MCP Streamable HTTP handshake against the Kapruka
server and returns the result as JSON. Reuses the same handshake logic as the
standalone test, but runs server-side (where outbound network access exists).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

MCP_URL = os.environ.get("KAPRUKA_MCP_URL", "https://mcp.kapruka.com/mcp")
PROTOCOL_VERSION = "2025-03-26"
TIMEOUT = float(os.environ.get("KAPRUKA_MCP_TIMEOUT", "30"))
# Cloudflare bans the default "Python-urllib" agent (error 1010), so present a
# normal browser signature.
USER_AGENT = os.environ.get(
    "KAPRUKA_MCP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)


def _loads(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:300] if text else "<empty>"
        raise ValueError(f"Non-JSON response (host reachable?): {snippet}") from exc


def _parse_body(content_type: str, raw: bytes) -> dict | None:
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


def _mcp(method, params, request_id, session_id):
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
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    req = urllib.request.Request(
        MCP_URL, data=json.dumps(message).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, hdrs, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        status, hdrs, raw = exc.code, exc.headers, exc.read()

    body = _parse_body(hdrs.get("Content-Type", ""), raw)
    return body, hdrs.get("Mcp-Session-Id") or session_id, status


def run_check() -> dict:
    init, session, status = _mcp(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kapruka-mcp-status", "version": "1.0.0"},
        },
        1,
        None,
    )
    if status != 200 or not init or "error" in init:
        return {"ok": False, "stage": "initialize", "status": status, "response": init}

    result = init.get("result", {})
    _mcp("notifications/initialized", {}, None, session)
    tools_body, _, tstatus = _mcp("tools/list", {}, 2, session)
    tools = (tools_body or {}).get("result", {}).get("tools", []) if tstatus == 200 else []

    return {
        "ok": True,
        "url": MCP_URL,
        "protocolVersion": result.get("protocolVersion"),
        "serverInfo": result.get("serverInfo"),
        "capabilities": result.get("capabilities"),
        "tools": [t.get("name") for t in tools],
        "toolCount": len(tools),
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (Vercel/BaseHTTPRequestHandler API)
        try:
            payload = run_check()
            code = 200 if payload.get("ok") else 502
        except Exception as exc:  # surface any failure as JSON
            payload = {"ok": False, "error": str(exc)}
            code = 502

        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
