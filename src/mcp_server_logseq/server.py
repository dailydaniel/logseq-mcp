"""Logseq MCP server.

A thin MCP wrapper over the Logseq local HTTP API (default
http://127.0.0.1:12315/api). Each tool proxies a single `logseq.Editor.*`
method via `POST /api` with a `{"method": ..., "args": [...]}` body and a
`Authorization: Bearer <token>` header.

Built on FastMCP (the high-level API of the official `mcp` package).
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# ---------------------------------------------------------------------------
# Configuration & HTTP client
# ---------------------------------------------------------------------------

# Mutable runtime config. Populated from the environment at import time and
# optionally overridden by the CLI (see __init__.main) before the server runs.
config: dict[str, Optional[str]] = {
    "url": "http://localhost:12315",
    "token": None,
}

# Loaded TOML/EDN configuration (set at startup by __init__.main). None until then.
app_config: Any = None

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Return a lazily-created shared async HTTP client."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def logseq_request(method: str, args: list[Any]) -> Any:
    """Call a Logseq plugin-API method over the local HTTP server.

    Raises a ValueError with a readable message on failure; FastMCP surfaces
    that to the caller as a tool error.
    """
    token = config["token"]
    if not token:
        raise ValueError("LOGSEQ_API_TOKEN is not configured")

    url = (config["url"] or "http://localhost:12315").rstrip("/")
    try:
        response = await _get_client().post(
            f"{url}/api",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"method": method, "args": args},
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error talking to Logseq: {exc}") from exc

    if response.status_code == 401:
        raise ValueError("Invalid Logseq API token")
    if response.status_code >= 400:
        raise ValueError(f"Logseq API error {response.status_code}: {response.text}")

    return response.json()


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------


def _format_block_result(result: dict) -> str:
    return (
        f"Created block in {result.get('page', {}).get('name', 'unknown page')}\n"
        f"UUID: {result.get('uuid')}\n"
        f"Content: {result.get('content')}\n"
        f"Parent: {result.get('parent', {}).get('uuid') or 'None'}"
    )


def _format_page_result(result: dict) -> str:
    properties = "".join(
        f"  {key}: {value}\n"
        for key, value in (result.get("propertiesTextValues") or {}).items()
    )
    properties_text = "\n" + properties if properties else " None"
    return (
        f"Page: {result.get('name')}\n"
        f"UUID: {result.get('uuid')}\n"
        f"Journal: {result.get('journal', False)}\n"
        f"Properties: {properties_text}"
    )


def _format_pages_list(pages: list[dict]) -> str:
    return "\n".join(
        f"{p.get('name')} ({p.get('uuid', 'alias')})"
        for p in sorted(pages, key=lambda x: x.get("name") or "")
    )


def _format_blocks_tree(blocks: list[dict]) -> str:
    def walk(block: dict, level: int = 0) -> list[str]:
        lines = ["  " * level + "- " + (block.get("content") or "")]
        for child in block.get("children", []):
            if isinstance(child, dict):
                lines.extend(walk(child, level + 1))
        return lines

    return "\n".join(line for block in blocks for line in walk(block))


# ---------------------------------------------------------------------------
# Server & tools
# ---------------------------------------------------------------------------

mcp = FastMCP("logseq")


@mcp.tool(name="logseq_insert_block")
async def insert_block(
    content: Annotated[str, Field(description="Content of the new block")],
    parent_block: Annotated[
        Optional[str],
        Field(default=None, description="UUID or name of the parent block/page"),
    ] = None,
    is_page_block: Annotated[
        bool, Field(description="Insert as a page-level block")
    ] = False,
    before: Annotated[bool, Field(description="Insert before the parent")] = False,
    custom_uuid: Annotated[
        Optional[str], Field(default=None, description="Custom UUID for the block")
    ] = None,
) -> str:
    """Insert a new block into Logseq.

    Use is_page_block=true with a page name as parent_block to create a
    page-level block, or pass a block UUID as parent_block to nest under it.
    """
    if parent_block and parent_block.startswith("((") and parent_block.endswith("))"):
        parent_block = parent_block.strip("()")
    result = await logseq_request(
        "logseq.Editor.insertBlock",
        [
            parent_block,
            content,
            {
                "isPageBlock": is_page_block,
                "before": before,
                "customUUID": custom_uuid,
            },
        ],
    )
    return _format_block_result(result)


@mcp.tool(name="logseq_create_page")
async def create_page(
    page_name: Annotated[str, Field(description="Name of the page to create")],
    properties: Annotated[
        Optional[dict], Field(default=None, description="Page properties")
    ] = None,
    journal: Annotated[bool, Field(description="Create as a journal page")] = False,
    format: Annotated[str, Field(description="Page format: markdown or org")] = "markdown",
    create_first_block: Annotated[
        bool, Field(description="Create an initial empty block")
    ] = True,
) -> str:
    """Create a new page in Logseq with optional properties."""
    result = await logseq_request(
        "logseq.Editor.createPage",
        [
            page_name,
            properties or {},
            {
                "journal": journal,
                "format": format,
                "createFirstBlock": create_first_block,
            },
        ],
    )
    return _format_page_result(result)


@mcp.tool(name="logseq_get_current_page")
async def get_current_page() -> str:
    """Get the currently active page or block in the user's workspace."""
    result = await logseq_request("logseq.Editor.getCurrentPage", [])
    if result is None:
        return "No current page"
    return (
        f"Current: {result.get('name', result.get('content', 'Untitled'))}\n"
        f"UUID: {result.get('uuid')}\n"
        f"Last updated: {result.get('updatedAt', 'N/A')}"
    )


@mcp.tool(name="logseq_get_page")
async def get_page(
    src_page: Annotated[str, Field(description="Page name, UUID or database ID")],
    include_children: Annotated[
        bool, Field(description="Include child blocks in the response")
    ] = False,
) -> str:
    """Retrieve metadata about a specific page."""
    result = await logseq_request(
        "logseq.Editor.getPage",
        [src_page, {"includeChildren": include_children}],
    )
    if result is None:
        return f"Page not found: {src_page}"
    return _format_page_result(result)


@mcp.tool(name="logseq_get_all_pages")
async def get_all_pages(
    repo: Annotated[
        Optional[str], Field(default=None, description="Repository / graph name")
    ] = None,
) -> str:
    """List all pages in the graph with basic metadata."""
    result = await logseq_request(
        "logseq.Editor.getAllPages", [repo] if repo else []
    )
    return _format_pages_list(result or [])


@mcp.tool(name="logseq_edit_block")
async def edit_block(
    src_block: Annotated[str, Field(description="Block UUID or reference")],
    pos: Annotated[int, Field(description="Cursor position", ge=0, le=10000)] = 0,
) -> str:
    """Enter editing mode for a specific block (UI side effect)."""
    await logseq_request("logseq.Editor.editBlock", [src_block, {"pos": pos}])
    return f"Editing block {src_block} at position {pos}"


@mcp.tool(name="logseq_exit_editing_mode")
async def exit_editing_mode(
    select_block: Annotated[
        bool, Field(description="Keep the block selected after exiting")
    ] = False,
) -> str:
    """Exit the current block editing mode (UI side effect)."""
    await logseq_request("logseq.Editor.exitEditingMode", [select_block])
    return "Exited editing mode" + (" with block selected" if select_block else "")


@mcp.tool(name="logseq_get_current_page_content")
async def get_current_page_content() -> str:
    """Get the hierarchical block structure of the current page."""
    result = await logseq_request("logseq.Editor.getCurrentPageBlocksTree", [])
    return _format_blocks_tree(result or [])


@mcp.tool(name="logseq_get_editing_block_content")
async def get_editing_block_content() -> str:
    """Get the content of the block currently being edited."""
    result = await logseq_request("logseq.Editor.getEditingBlockContent", [])
    return f"Current editing block content:\n{result}"


@mcp.tool(name="logseq_get_page_content")
async def get_page_content(
    src_page: Annotated[str, Field(description="Page name or UUID")],
) -> str:
    """Get the block hierarchy for a specific page."""
    result = await logseq_request("logseq.Editor.getPageBlocksTree", [src_page])
    return _format_blocks_tree(result or [])
