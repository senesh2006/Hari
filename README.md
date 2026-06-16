# Kapruka MCP test

A standalone smoke test that verifies the [Kapruka MCP](https://mcp.kapruka.com/mcp)
server is reachable and behaving like a working Model Context Protocol endpoint.

- **Endpoint:** `https://mcp.kapruka.com/mcp`
- **Transport:** Streamable HTTP (no auth required)

## What it checks

1. `initialize` — server returns a `protocolVersion`, `serverInfo` and `capabilities`.
2. `notifications/initialized` — handshake is acknowledged.
3. `tools/list` — server advertises its tool catalogue.

## Running

Requires only Python 3 (standard library only — no `pip install` needed) and
outbound network access to `mcp.kapruka.com`.

```bash
python3 test_kapruka_mcp.py
```

Or, if `pytest` is installed:

```bash
pytest test_kapruka_mcp.py -v
```

### Configuration

| Env var               | Default                        | Purpose                     |
| --------------------- | ------------------------------ | --------------------------- |
| `KAPRUKA_MCP_URL`     | `https://mcp.kapruka.com/mcp`  | Override the target URL.    |
| `KAPRUKA_MCP_TIMEOUT` | `30`                           | Per-request timeout (secs). |

> **Note:** the test must run from a network that allows egress to
> `mcp.kapruka.com`. Some sandboxed/CI environments block this host by default.

## Deploying to Vercel

This repo also ships a tiny status page so the deployment serves real content
(no more `/favicon.ico` or `(index)` 404s):

- `public/index.html` — a status page that calls the health-check endpoint.
- `api/check.py` — a Python serverless function that runs the MCP handshake
  server-side and returns the result as JSON at `GET /api/check`.

Vercel serves these with **zero configuration**: static files come from
`public/`, and any `.py` file under `api/` becomes a serverless function. No
build step and **no environment variables are required** — the Kapruka MCP
needs no auth. The endpoint URL/timeout can optionally be overridden with the
`KAPRUKA_MCP_URL` and `KAPRUKA_MCP_TIMEOUT` env vars.

After deploying, open the site and you'll see a live ✅/❌ status; the raw
check is available at `/api/check`.
