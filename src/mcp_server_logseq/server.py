"""Logseq MCP server — agent-facing tool surface.

Principle: read broadly (everything blacklist-filtered), write narrowly (only the
agent namespace + a task-status carve-out). Raw low-level Logseq write methods are
not exposed. See assets/logseq-mcp-design-v0.7.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .blacklist import Blacklist
from .client import LogseqClient
from . import queries as q
from . import resolve as rsv
from . import writes as w
from .config import AppConfig, CompiledQuery
from .normalize import normalize_block

# ---------------------------------------------------------------------------
# Runtime state (set by __init__.main before the server runs)
# ---------------------------------------------------------------------------

config: dict[str, Optional[str]] = {"url": "http://localhost:12315", "token": None}
app_config: Optional[AppConfig] = None

_client: Optional[LogseqClient] = None

mcp = FastMCP("logseq")


def get_client() -> LogseqClient:
    global _client
    if _client is None:
        _client = LogseqClient(config["url"] or "http://localhost:12315", config["token"] or "")
    return _client


def _cfg() -> AppConfig:
    if app_config is None:  # pragma: no cover - always set at startup
        raise RuntimeError("config not loaded")
    return app_config


def _blacklist() -> Blacklist:
    return Blacklist(_cfg().blacklist.pages)


def _read_depth(override: Optional[int]) -> int:
    return _cfg().read.resolve_depth if override is None else max(0, override)


async def _finalize(blocks: list[dict], depth: int) -> list[dict]:
    """Resolve refs (if depth>0), then drop/redact via blacklist."""
    client = get_client()
    bl = _blacklist()
    if depth > 0:
        await rsv.resolve_refs(client, blocks, depth, bl)
    if bl.active:
        blocks = [b for b in blocks if not (b.get("page") and bl.is_page_excluded(b["page"]))]
        blocks = bl.filter_blocks(blocks)
    return blocks


# ---------------------------------------------------------------------------
# Find / query tools
# ---------------------------------------------------------------------------

_PULL = "[* {:block/page [:block/name :block/journal-day :block/original-name]}]"


@mcp.tool()
async def search(
    query: Annotated[str, Field(description="Text or regex to search block content for")],
    regex: Annotated[bool, Field(description="Treat query as a regex")] = False,
    limit: Annotated[Optional[int], Field(description="Max results")] = None,
    case_sensitive: Annotated[bool, Field(description="Case-sensitive match")] = False,
) -> dict:
    """Full-text search across block content in the graph."""
    import re as _re

    from .config import _edn_dumps

    # Logseq's datascript sandbox allows clojure.string/includes? and re-find/
    # re-pattern, but NOT lower-case — so case-insensitive uses a (?i) regex.
    # Nested function calls aren't allowed in one clause, so bind the pattern.
    if case_sensitive and not regex:
        where = f"[?b :block/content ?c] [(clojure.string/includes? ?c {_edn_dumps(query)})]"
    else:
        body = query if regex else _re.escape(query)
        pat = _edn_dumps(("" if case_sensitive else "(?i)") + body)
        where = f"[?b :block/content ?c] [(re-pattern {pat}) ?re] [(re-find ?re ?c)]"
    dq = f"[:find (pull ?b {_PULL}) :where {where}]"

    rows = await get_client().call("logseq.DB.datascriptQuery", [dq])
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows or [])]
    blocks = await _finalize(blocks, 0)
    results = [
        {"uuid": b["uuid"], "page": b["page"], "status": b["status"], "text": b["text"]}
        for b in blocks
        if not b.get("redacted")
    ]
    return {"count": len(results), "results": results[: (limit or 50)]}


@mcp.tool()
async def find_tasks(
    markers: Annotated[Optional[list[str]], Field(description="Task markers (default TODO/DOING/NOW/LATER)")] = None,
    tag: Annotated[Optional[str], Field(description="Task directly references this page/tag")] = None,
    under_tag: Annotated[Optional[str], Field(description="Task is a descendant of a block referencing a page whose name starts with this")] = None,
    page: Annotated[Optional[str], Field(description="Restrict to this page")] = None,
    priority: Annotated[Optional[str], Field(description="Priority A/B/C")] = None,
    limit: Annotated[Optional[int], Field(description="Max results")] = None,
) -> dict:
    """Find task blocks by marker, tag, priority or page."""
    from .config import _edn_dumps

    mk = markers or ["TODO", "DOING", "NOW", "LATER"]
    mset = "#{" + " ".join(_edn_dumps(m.upper()) for m in mk) + "}"
    clauses = [f"[?b :block/marker ?m] [(contains? {mset} ?m)]"]
    rules = None

    if under_tag:
        rules = (
            "[[(descendant ?b ?a) [?b :block/parent ?a]]"
            " [(descendant ?b ?a) [?b :block/parent ?p] (descendant ?p ?a)]]"
        )
        clauses.append(
            f"[?a :block/refs ?rp] [?rp :block/name ?n] "
            f"[(clojure.string/starts-with? ?n {_edn_dumps(under_tag.lower())})] (descendant ?b ?a)"
        )
    if tag:
        clauses.append(f"[?b :block/refs ?tp] [?tp :block/name {_edn_dumps(tag.lower())}]")
    if page:
        clauses.append(f"[?b :block/page ?pg] [?pg :block/name {_edn_dumps(page.lower())}]")
    if priority:
        clauses.append(f"[?b :block/priority {_edn_dumps(priority.upper())}]")

    head = "[:find (pull ?b " + _PULL + ") :in $ %" if rules else "[:find (pull ?b " + _PULL + ")"
    dq = f"{head} :where " + " ".join(clauses) + "]"
    args = [dq, rules] if rules else [dq]
    rows = await get_client().call("logseq.DB.datascriptQuery", args)
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows or [])]
    blocks = await _finalize(blocks, _read_depth(None))
    blocks = [b for b in blocks if not b.get("redacted")]
    return {"count": len(blocks), "tasks": blocks[: (limit or 100)]}


@mcp.tool()
async def list_custom_queries() -> dict:
    """List the named custom queries available from the server config."""
    return {
        "queries": [
            {"name": cq.name, "description": cq.description, "tool": cq.register_as_tool}
            for cq in _cfg().queries.values()
        ]
    }


async def _run_named_query(cq: CompiledQuery, inputs: Optional[list[Any]]) -> dict:
    rows = await q.run_compiled(get_client(), cq, override_inputs=inputs)
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows)]
    depth = _cfg().read.resolve_depth if cq.resolve_block_refs is None else (
        _cfg().read.resolve_depth if cq.resolve_block_refs else 0
    )
    blocks = await _finalize(blocks, depth)
    return {"name": cq.name, "count": len(blocks), "results": blocks}


@mcp.tool()
async def custom_query(
    name: Annotated[str, Field(description="Name of a configured query")],
    inputs: Annotated[Optional[list[Any]], Field(description="Override the query's inputs")] = None,
) -> dict:
    """Run a named custom query from the server config."""
    cq = _cfg().queries.get(name)
    if cq is None:
        raise ValueError(f"no custom query named {name!r}; see list_custom_queries")
    return await _run_named_query(cq, inputs)


@mcp.tool()
async def datascript_query(
    query: Annotated[str, Field(description="Raw Datalog query vector, e.g. [:find (pull ?b [*]) :where ...]")],
    inputs: Annotated[Optional[list[Any]], Field(description="Query inputs (after $ and rules)")] = None,
    rules: Annotated[Optional[str], Field(description="Datalog rules as an EDN string, bound to %")] = None,
) -> dict:
    """Run a raw Datalog query against the graph (advanced)."""
    rows = await q.run_datascript(get_client(), query, inputs, rules)
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows)]
    if blocks:
        blocks = await _finalize(blocks, _read_depth(None))
        return {"count": len(blocks), "results": blocks}
    return {"count": len(rows), "results": rows}  # non-pull shape: return raw rows


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def read_page(
    page: Annotated[str, Field(description="Page name or UUID")],
    depth: Annotated[Optional[int], Field(description="Block-ref resolution depth (default from config)")] = None,
) -> dict:
    """Read a page as a normalized block tree with references resolved."""
    bl = _blacklist()
    if bl.is_page_excluded(page):
        raise ValueError(f"page {page!r} is blacklisted")
    tree = await get_client().call("logseq.Editor.getPageBlocksTree", [page]) or []
    blocks = [normalize_block(b) for b in tree]
    blocks = await _finalize(blocks, _read_depth(depth))
    return {"page": page, "blocks": blocks}


@mcp.tool()
async def read_block(
    uuid: Annotated[str, Field(description="Block UUID")],
    depth: Annotated[Optional[int], Field(description="Block-ref resolution depth (default from config)")] = None,
) -> dict:
    """Read a single block (and its children) with references resolved."""
    raw = await get_client().call("logseq.Editor.getBlock", [uuid, {"includeChildren": True}])
    if not isinstance(raw, dict):
        raise ValueError(f"block {uuid!r} not found")
    blocks = await _finalize([normalize_block(raw)], _read_depth(depth))
    return {"block": blocks[0] if blocks else None}


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def write_note(
    subpath: Annotated[str, Field(description="Page path within the agent namespace")],
    content: Annotated[Optional[str], Field(description="Block content to add (markdown)")] = None,
    mode: Annotated[str, Field(description="append | replace")] = "append",
    properties: Annotated[Optional[dict], Field(description="Page properties (set on creation)")] = None,
) -> dict:
    """Create or update a page in the agent's namespace."""
    return await w.write_note(_cfg(), get_client(), subpath, content, mode, properties)


@mcp.tool()
async def set_page_properties(
    subpath: Annotated[str, Field(description="Page path within the agent namespace")],
    properties: Annotated[dict, Field(description="Properties to set; value null removes one")],
) -> dict:
    """Set or remove properties on an agent-namespace page."""
    return await w.set_page_properties(_cfg(), get_client(), subpath, properties)


@mcp.tool()
async def set_task_status(
    uuid: Annotated[str, Field(description="Task block UUID")],
    status: Annotated[str, Field(description="New marker, e.g. TODO/DOING/DONE")],
) -> dict:
    """Change only a task block's status marker (text untouched)."""
    return await w.set_task_status(_cfg(), get_client(), uuid, status)


# ---------------------------------------------------------------------------
# Dynamic per-query tools (register_as_tool)
# ---------------------------------------------------------------------------


def register_dynamic_tools() -> None:
    """Register a query_<name> tool for each config query with register_as_tool."""
    for cq in _cfg().queries.values():
        if not cq.register_as_tool:
            continue

        def make(query_obj: CompiledQuery):
            async def run(
                inputs: Annotated[Optional[list[Any]], Field(description="Override inputs")] = None,
            ) -> dict:
                return await _run_named_query(query_obj, inputs)

            return run

        mcp.tool(name=f"query_{cq.name}", description=cq.description or f"Run the '{cq.name}' query")(
            make(cq)
        )
