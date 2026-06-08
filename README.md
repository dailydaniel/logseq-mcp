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
clients). An experimental **Streamable HTTP** transport is also available for
remote/networked use (e.g. a phone client):

```bash
mcp-server-logseq --transport streamable-http --host 0.0.0.0 --port 8000
# MCP endpoint: http://<host>:8000/mcp
```

Equivalent env vars: `LOGSEQ_MCP_TRANSPORT`, `LOGSEQ_MCP_HOST`, `LOGSEQ_MCP_PORT`.

> ⚠️ The Streamable HTTP transport does **not** yet enforce authentication on
> the MCP endpoint itself. Do not expose it directly to an untrusted network
> without a reverse proxy + bearer-token/OAuth layer. See the project notes for
> the planned auth design.

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
