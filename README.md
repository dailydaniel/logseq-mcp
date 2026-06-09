# Logseq MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that connects
LLMs to a [Logseq](https://logseq.com) graph. It proxies the Logseq local HTTP
API (`logseq.Editor.*` methods) so an agent can create pages, manage blocks and
read content from your graph.

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

```bash
docker build -t logseq-mcp .
docker run --rm -p 8000:8000 \
  -e LOGSEQ_API_TOKEN=<logseq-token> \
  -e LOGSEQ_MCP_HTTP_TOKEN=<client-secret> \
  -e TZ=Europe/Moscow \
  logseq-mcp
```

The container clock is UTC by default; set `-e TZ=<zone>` so audit-log timestamps
use your local time (the image bundles `tzdata`).

The container serves Streamable HTTP on port 8000 and talks to a Logseq running
on the **host**. On Docker Desktop (macOS/Windows) the default
`LOGSEQ_API_URL=http://host.docker.internal:12315` already points at the host;
on Linux add `--add-host=host.docker.internal:host-gateway` (or set
`LOGSEQ_API_URL` to the host IP). Make sure Logseq's HTTP API server is running
and listening.

## Available Tools

### Blocks
- **logseq_insert_block** — insert a new block (`content`, `parent_block?`,
  `is_page_block?`, `before?`, `custom_uuid?`)
- **logseq_edit_block** — enter editing mode for a block (`src_block`, `pos?`)
- **logseq_exit_editing_mode** — exit editing mode (`select_block?`)

### Pages
- **logseq_create_page** — create a page (`page_name`, `properties?`,
  `journal?`, `format?`, `create_first_block?`)
- **logseq_get_page** — page metadata (`src_page`, `include_children?`)
- **logseq_get_all_pages** — list all pages (`repo?`)

### Content
- **logseq_get_current_page** — active page/block — *no args*
- **logseq_get_current_page_content** — current page block tree — *no args*
- **logseq_get_editing_block_content** — content of the active block — *no args*
- **logseq_get_page_content** — block tree of a page (`src_page`)

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
