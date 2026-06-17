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

## AI gift concierge (NVIDIA NIM)

`api/search.py` is an agentic, *reasoning* shopping endpoint — not a keyword
search. It:

1. opens an MCP session to Kapruka and **discovers its tools at runtime**,
2. gives those tools to an **NVIDIA NIM** model (OpenAI-compatible chat API),
3. **brainstorms concrete gift ideas** from the request (e.g. for "birthday gift
   for mom" it searches *bouquet, chocolate hamper, perfume, watch, saree…*
   rather than the literal words "birthday gift"),
4. **asks clarifying questions** when key details are missing (budget,
   interests, delivery city, occasion date) before choosing, and
5. returns a curated, varied answer plus the raw tool results.

When the model needs more info it returns `{"needs_input": true, "questions":
[...]}`; the client collects answers and re-POSTs with `"allow_questions":
false` so the model proceeds to search. The home page handles this flow.

Because tools are discovered dynamically, it adapts to whatever the Kapruka MCP
exposes — no tool names are hard-coded.

### Usage

```bash
# POST
curl -s -X POST https://<your-app>.vercel.app/api/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"birthday gift for mom under 5000 LKR"}'

# or GET
curl -s 'https://<your-app>.vercel.app/api/search?q=red%20roses%20for%20delivery'
```

The home page also has a search box wired to this endpoint.

### Required configuration

NVIDIA NIM needs an API key. **Add this in Vercel → Project → Settings →
Environment Variables**, then redeploy:

| Env var          | Required | Default                          | Purpose                                   |
| ---------------- | -------- | -------------------------------- | ----------------------------------------- |
| `NVIDIA_API_KEY` | **yes**  | —                                | NIM API key from <https://build.nvidia.com> |
| `NVIDIA_NIM_MODEL` | no     | `meta/llama-3.3-70b-instruct`    | Any NIM model that supports tool calling. |
| `NVIDIA_NIM_BASE_URL` | no  | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible NIM endpoint.      |
| `SEARCH_MAX_ROUNDS` | no    | `5`                              | Max tool-calling rounds per query.        |

The Kapruka MCP itself needs no key.

### Performance

Each query runs an agentic tool-calling loop, so latency is dominated by the
NIM model and the number of tool rounds. Defaults are tuned for speed:

- `NVIDIA_NIM_MODEL` defaults to `meta/llama-3.3-70b-instruct` for better
  judgement and tone (set it to `meta/llama-3.1-8b-instruct` for lower latency).
- `SEARCH_MAX_ROUNDS` defaults to `3`, and the tool catalogue is cached across
  warm invocations to skip a round-trip.

## All MCP tools (`api/tool.py`)

Beyond search, every tool the Kapruka MCP exposes is available directly:

```bash
# List all tools and their input schemas
curl -s https://<your-app>.vercel.app/api/tool

# Invoke any tool by name
curl -s -X POST https://<your-app>.vercel.app/api/tool \
  -H 'Content-Type: application/json' \
  -d '{"name":"<tool_name>","arguments":{ ... }}'
```

The catalogue is discovered at runtime — no tool names are hard-coded. The home
page renders a schema-driven form for every tool. Tools whose names imply a
side effect (order/checkout/payment/…) are flagged `writes` and require an
explicit confirmation in the UI before they run.
