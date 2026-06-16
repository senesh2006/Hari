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
