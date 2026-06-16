#!/usr/bin/env python3
"""Connectivity / health test for the Kapruka MCP server.

Target: https://mcp.kapruka.com/mcp
Transport: Streamable HTTP (no auth required)

The test performs a real Model Context Protocol handshake against the live
server and verifies that it behaves like a working MCP endpoint:

  1. ``initialize``                -> server returns protocolVersion + serverInfo
  2. ``notifications/initialized`` -> acknowledge the handshake
  3. ``tools/list``               -> server advertises its tool catalogue

It depends only on the Python standard library, so it can run anywhere with
``python3`` and outbound network access to ``mcp.kapruka.com``.

Run standalone:
    python3 test_kapruka_mcp.py

Run under pytest (if available):
    pytest test_kapruka_mcp.py -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

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


class MCPError(AssertionError):
    """Raised when the server replies but not in a valid MCP shape."""


def _parse_body(content_type: str, raw: bytes) -> dict:
    """Decode an MCP response body.

    Streamable HTTP servers may answer with either a plain JSON object or a
    Server-Sent Events stream (``text/event-stream``) whose ``data:`` lines
    carry the JSON-RPC payload. Handle both.
    """
    text = raw.decode("utf-8", errors="replace").strip()
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload and payload != "[DONE]":
                    return _loads(payload)
        raise MCPError(f"No JSON data found in SSE stream:\n{text}")
    return _loads(text)


def _loads(text: str) -> dict:
    """json.loads with a legible error when the body is not JSON.

    Proxies, firewalls and egress allowlists often answer with a plain-text
    page; surface that body instead of a bare JSONDecodeError.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:300] if text else "<empty>"
        raise MCPError(
            f"Expected a JSON-RPC body but got non-JSON content "
            f"(is the host reachable?): {snippet}"
        ) from exc


def mcp_request(method: str, params: dict | None, request_id, session_id: str | None):
    """Send one JSON-RPC message to the MCP server.

    Returns ``(parsed_body_or_None, session_id, status_code)``. ``body`` is
    ``None`` for notifications, which the server answers with ``202 Accepted``
    and an empty payload.
    """
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
        MCP_URL,
        data=json.dumps(message).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            new_session = resp.headers.get("Mcp-Session-Id") or session_id
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        status = exc.code
        new_session = exc.headers.get("Mcp-Session-Id") or session_id
        content_type = exc.headers.get("Content-Type", "")
        raw = exc.read()

    body = _parse_body(content_type, raw) if raw.strip() else None
    return body, new_session, status


class KaprukaMCPTest(unittest.TestCase):
    """End-to-end smoke test for the Kapruka MCP server."""

    def test_mcp_handshake_and_tools(self) -> None:
        # 1. initialize -------------------------------------------------------
        init_body, session_id, status = mcp_request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kapruka-mcp-test", "version": "1.0.0"},
            },
            request_id=1,
            session_id=None,
        )

        self.assertEqual(status, 200, f"initialize returned HTTP {status}")
        self.assertIsNotNone(init_body, "initialize returned an empty body")
        self.assertEqual(init_body.get("jsonrpc"), "2.0")
        self.assertNotIn("error", init_body, f"initialize error: {init_body.get('error')}")

        result = init_body.get("result", {})
        self.assertIn("protocolVersion", result, "no protocolVersion in initialize result")
        self.assertIn("serverInfo", result, "no serverInfo in initialize result")
        self.assertIn("capabilities", result, "no capabilities in initialize result")
        print(f"[ok] connected to {result.get('serverInfo')} "
              f"(protocol {result.get('protocolVersion')})")

        # 2. notifications/initialized ---------------------------------------
        _, session_id, ack_status = mcp_request(
            "notifications/initialized", {}, request_id=None, session_id=session_id
        )
        self.assertIn(ack_status, (200, 202),
                      f"initialized notification returned HTTP {ack_status}")

        # 3. tools/list -------------------------------------------------------
        tools_body, session_id, tools_status = mcp_request(
            "tools/list", {}, request_id=2, session_id=session_id
        )
        self.assertEqual(tools_status, 200, f"tools/list returned HTTP {tools_status}")
        self.assertIsNotNone(tools_body, "tools/list returned an empty body")
        self.assertNotIn("error", tools_body, f"tools/list error: {tools_body.get('error')}")

        tools = tools_body.get("result", {}).get("tools")
        self.assertIsInstance(tools, list, "tools/list result.tools is not a list")
        print(f"[ok] server advertises {len(tools)} tool(s): "
              f"{[t.get('name') for t in tools]}")


def main() -> int:
    """Run the smoke test and print a clear pass/fail summary."""
    print(f"Testing Kapruka MCP server at: {MCP_URL}\n")
    try:
        suite = unittest.TestLoader().loadTestsFromTestCase(KaprukaMCPTest)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    except urllib.error.URLError as exc:
        print(f"\n[FAIL] could not reach the server: {exc}", file=sys.stderr)
        return 2

    if result.wasSuccessful():
        print("\n[PASS] Kapruka MCP server is working.")
        return 0
    print("\n[FAIL] Kapruka MCP server check failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
