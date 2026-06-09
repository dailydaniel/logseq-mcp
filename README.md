# Logseq MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives an
LLM **configurable, safety-scoped** access to a [Logseq](https://logseq.com)
graph: query and search broadly (all output filtered by a blacklist), but write
only inside the agent's own namespace plus a narrow task-status channel.

Built on **FastMCP** (the high-level API of the official `mcp` package).

> Targets the **file/Markdown ("OG") version** of Logseq. The newer DB (SQLite)
> version changed the underlying schema; some methods may behave differently
> there.

## Requirements

- A running Logseq with the **local HTTP API server enabled**
  (Settings → Features → *HTTP APIs server*, then start it from the 🔌 menu).
- An **authorization token** created in the HTTP API server settings.

## Usage with Claude Desktop

```json
{
  "mcpServers": {
    "logseq": {
      "command": "uvx",
      "args": ["mcp-server-logseq"],
      "env": {
        "LOGSEQ_API_TOKEN": "<YOUR_TOKEN>",
        "LOGSEQ_API_URL": "http://127.0.0.1:12315"
      }
    }
  }
}
```

## Configuration

| Source | Token | URL |
| --- | --- | --- |
| Environment | `LOGSEQ_API_TOKEN` | `LOGSEQ_API_URL` (default `http://localhost:12315`) |
| CLI flag | `--api-key` | `--url` |

The token is read from the environment or `--api-key`; it is never stored in
code. A `.env` file is supported (see `.env.example`).

### Config file (optional)

Behaviour beyond the defaults is set in a TOML file — path from
`LOGSEQ_MCP_CONFIG` (default `~/.config/logseq-mcp/config.toml`). Custom queries
live in EDN files next to it. **The server runs fine with no config file** (safe
read-mostly defaults); see [`examples/config.toml`](examples/config.toml) for a
full annotated example.

| Section | Key options |
| --- | --- |
| `[read]` | `resolve_depth` — how deep to expand `((block refs))` |
| `[write]` | `agent_write_prefix` (default `byAgent`), `allow_agents_write_any` |
| `[search]` | `files_path` — graph folder; set it to use the ripgrep backend |
| `[blacklist]` | `pages` — pages (and subpages) to hide and redact everywhere |
| `[tasks]` | `allow_status_change` — gate for `set_task_status` |
| `[audit_log]` | `enabled` — log writes to today's journal |
| `[queries.<name>]` | a named query: `file`/inline `query`, `register_as_tool`, … |

Secrets and the API URL stay in the environment, never in this file.

## Transports

By default the server runs over **stdio** (for Claude Desktop and other local
clients). A **Streamable HTTP** transport is also available for remote/networked
use (e.g. a phone client):

```bash
LOGSEQ_MCP_HTTP_TOKEN=<client-secret> \
  mcp-server-logseq --transport streamable-http --host 0.0.0.0 --port 8000
# MCP endpoint: http://<host>:8000/mcp
```

Env vars: `LOGSEQ_MCP_TRANSPORT`, `LOGSEQ_MCP_HOST`, `LOGSEQ_MCP_PORT`,
`LOGSEQ_MCP_HTTP_TOKEN` (or `--http-token`).

### Authentication

The Streamable HTTP transport **requires** a bearer token: every request must
send `Authorization: Bearer <LOGSEQ_MCP_HTTP_TOKEN>`, or it gets `401`. The
server refuses to start in this mode without a token set. Note this is a
distinct secret from `LOGSEQ_API_TOKEN`:

| Secret | Direction |
| --- | --- |
| `LOGSEQ_API_TOKEN` | this server → Logseq |
| `LOGSEQ_MCP_HTTP_TOKEN` | client (phone) → this server |

> ⚠️ A bearer token over **plain HTTP** is only safe on an already-encrypted
> channel. Don't expose the raw port to the open internet. The easy path for a
> home/headless host is **Tailscale**: install it on the host and the client,
> and reach `http://<host>.<tailnet>.ts.net:8000/mcp` over the encrypted
> tunnel — no domains, nginx, or certificates. (`tailscale serve` can add TLS
> if you want `https://`.)

## Docker

Build once:

```bash
docker build -t logseq-mcp .
```

Quick try (ephemeral — `--rm` removes the container on stop):

```bash
docker run --rm -p 8000:8000 \
  -e LOGSEQ_API_TOKEN=<logseq-token> \
  -e LOGSEQ_MCP_HTTP_TOKEN=<client-secret> \
  -e TZ=Europe/Moscow \
  logseq-mcp
```

Persistent deploy (e.g. a headless Mac mini) — run once; `--restart` brings it
back after reboots:

```bash
docker run -d --name logseq-mcp --restart unless-stopped -p 8000:8000 \
  -e LOGSEQ_API_TOKEN=<logseq-token> \
  -e LOGSEQ_MCP_HTTP_TOKEN=<client-secret> \
  -e TZ=Europe/Moscow \
  -e LOGSEQ_MCP_CONFIG=/cfg/config.toml \
  -v /path/to/config-dir:/cfg:ro \
  -v "/path/to/your/graph:/graph:ro" \
  logseq-mcp
```

- `-v .../config-dir:/cfg` — folder holding your `config.toml` (+ `queries/`,
  `rules/`); set `files_path = "/graph"` in it to enable file search. Omit both
  the mount and `LOGSEQ_MCP_CONFIG` to run on defaults.
- `-v .../graph:/graph` — your Logseq graph folder (read-only), for file search.
- `-e TZ=<zone>` — local time for audit-log timestamps (image bundles `tzdata`;
  the clock is UTC otherwise).

The container serves Streamable HTTP on port 8000 and talks to a Logseq running
on the **host**. On Docker Desktop (macOS/Windows) the default
`LOGSEQ_API_URL=http://host.docker.internal:12315` already points at the host;
on Linux add `--add-host=host.docker.internal:host-gateway` (or set
`LOGSEQ_API_URL` to the host IP). Make sure Logseq's HTTP API server is running
and listening.

## Tools

All read output is normalized to a flat JSON shape and passed through the
blacklist. Reads resolve `((block refs))` non-lossily (the resolved block's
`uuid`/`status` is kept so you can act on it).

### Find
- **search** — full-text search over block content (`query`, `regex?`, `limit?`,
  `case_sensitive?`, `exclude_journals?`). Uses ripgrep over `files_path` when
  set, else a datascript content match.
- **find_tasks** — task blocks by `markers?`, `tag?`, `under_tag?` (descendant),
  `page?`, `priority?`, `limit?`.
- **custom_query** — run a named query from the config (`name`, `inputs?`).
- **list_custom_queries** — list the configured queries.
- **datascript_query** — run a raw Datalog query (`query`, `inputs?`, `rules?`).

### Read
- **read_page** — a page as a normalized block tree (`page`, `depth?`).
- **read_block** — a block and its children (`uuid`, `depth?`).

### Write (agent namespace only)
- **write_note** — create/append/replace a page under `agent_write_prefix`
  (`subpath`, `content?`, `mode?`, `properties?`).
- **set_page_properties** — set/remove page properties (`subpath`, `properties`;
  a `null` value removes one).

### Tasks
- **set_task_status** — change only a task's marker (`uuid`, `status`); gated by
  `[tasks].allow_status_change`.

### Dynamic
- **query_&lt;name&gt;** — each config query with `register_as_tool = true` is
  exposed as its own tool.

## Development

```bash
git clone https://github.com/dailydaniel/logseq-mcp.git
cd logseq-mcp
cp .env.example .env   # fill in LOGSEQ_API_TOKEN
uv sync
uv run mcp-server-logseq
```

Inspect with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv --directory . run mcp-server-logseq
```

## License

MIT
