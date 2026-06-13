"""Logseq MCP server — agent-facing tool surface.

Principle: read broadly (everything blacklist-filtered), write narrowly (only the
agent namespace + a task-status carve-out). Raw low-level Logseq write methods are
not exposed. See assets/logseq-mcp-design-v0.7.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .blacklist import Blacklist, canon_page_name
from .client import LogseqClient
from . import audit
from . import filesearch as fs
from . import queries as q
from . import resolve as rsv
from . import writes as w
from .config import AppConfig, CompiledQuery
from .guide import render_guide
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


def _flatten(blocks: list[dict]):
    for b in blocks:
        yield b
        yield from _flatten(b.get("children") or [])


async def _search_datascript(query: str, regex: bool, case_sensitive: bool, exclude_journals: bool) -> list[dict]:
    import re as _re

    from .config import _edn_dumps

    # Logseq's datascript sandbox allows includes?/re-find/re-pattern but NOT
    # lower-case; case-insensitive uses a (?i) regex. No nested calls per clause.
    if case_sensitive and not regex:
        match = f"[(clojure.string/includes? ?c {_edn_dumps(query)})]"
    else:
        body = query if regex else _re.escape(query)
        pat = _edn_dumps(("" if case_sensitive else "(?i)") + body)
        match = f"[(re-pattern {pat}) ?re] [(re-find ?re ?c)]"
    clauses = f"[?b :block/content ?c] {match}"
    if exclude_journals:
        clauses += " [?b :block/page ?pg] (not-join [?pg] [?pg :block/journal-day ?jd])"
    dq = f"[:find (pull ?b {_PULL}) :where {clauses}]"
    rows = await get_client().call("logseq.DB.datascriptQuery", [dq])
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows or [])]
    return await _finalize(blocks, 0)


async def _resolve_journal_pages(days: list[int]) -> list[str]:
    if not days:
        return []
    dset = "#{" + " ".join(str(d) for d in days) + "}"
    dq = (
        "[:find ?name :where [?p :block/journal-day ?d] "
        f"[(contains? {dset} ?d)] [?p :block/original-name ?name]]"
    )
    rows = await get_client().call("logseq.DB.datascriptQuery", [dq])
    return [r[0] for r in (rows or []) if r]


async def _search_files(query: str, regex: bool, case_sensitive: bool, exclude_journals: bool, top_k: int = 50) -> list[dict]:
    files_path = _cfg().search.files_path
    bl = _blacklist()
    candidate_files = fs.find_candidate_files(files_path, query, regex, case_sensitive)

    pages: list[str] = []
    journal_days: list[int] = []
    for f in candidate_files:
        decoded = fs.decode_candidate(f, files_path)
        if not decoded:
            continue
        kind, value = decoded
        if kind == "journal":
            if not exclude_journals:
                journal_days.append(int(value))  # type: ignore[arg-type]
        elif not bl.is_page_excluded(str(value)):
            pages.append(str(value))

    pages += await _resolve_journal_pages(journal_days)
    seen: set[str] = set()
    ordered = [p for p in pages if not (p in seen or seen.add(p))][:top_k]

    matcher = fs.build_matcher(query, regex, case_sensitive)
    results: list[dict] = []
    for page in ordered:
        tree = await get_client().call("logseq.Editor.getPageBlocksTree", [page]) or []
        blocks = await _finalize([normalize_block(b) for b in tree], 0)
        for b in _flatten(blocks):
            if not b.get("redacted") and matcher(b.get("text") or ""):
                results.append({"uuid": b["uuid"], "page": page, "status": b["status"], "text": b["text"]})
    return results


@mcp.tool()
async def search(
    query: Annotated[str, Field(description="Text or regex to search block content for")],
    regex: Annotated[bool, Field(description="Treat query as a regex")] = False,
    limit: Annotated[Optional[int], Field(description="Max results")] = None,
    case_sensitive: Annotated[bool, Field(description="Case-sensitive match")] = False,
    exclude_journals: Annotated[bool, Field(description="Omit journal/daily pages from results")] = False,
) -> dict:
    """Full-text search across block content in the graph."""
    use_files = bool(_cfg().search.files_path) and fs.ripgrep_path() is not None
    if use_files:
        blocks_or_results = await _search_files(query, regex, case_sensitive, exclude_journals)
        results = blocks_or_results
    else:
        blocks = await _search_datascript(query, regex, case_sensitive, exclude_journals)
        results = [
            {"uuid": b["uuid"], "page": b["page"], "status": b["status"], "text": b["text"]}
            for b in blocks
            if not b.get("redacted")
        ]
    backend = "files" if use_files else "datascript"
    return {"backend": backend, "count": len(results), "results": results[: (limit or 50)]}


@mcp.tool()
async def find_tasks(
    markers: Annotated[Optional[list[str]], Field(description="Task markers (default TODO/DOING/NOW/LATER)")] = None,
    tag: Annotated[Optional[str], Field(description="Task directly references this page/tag")] = None,
    under_tag: Annotated[Optional[str], Field(description="Task is a descendant of a block referencing a page whose name starts with this")] = None,
    page: Annotated[Optional[str], Field(description="Restrict to this page")] = None,
    priority: Annotated[Optional[str], Field(description="Priority A/B/C")] = None,
    limit: Annotated[Optional[int], Field(description="Max results")] = None,
    scope: Annotated[str, Field(description="all | agent | human — agent tasks reference the agent namespace; 'human' excludes them")] = "all",
    agent: Annotated[Optional[str], Field(description="With scope=agent, narrow to this agent (tasks referencing [[<prefix>/<agent>]])")] = None,
) -> dict:
    """Find task blocks by marker, tag, priority, page or agent-ownership scope."""
    from .config import _edn_dumps

    if scope not in ("all", "agent", "human"):
        raise ValueError("scope must be 'all', 'agent' or 'human'")

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

    if scope != "all":
        pre = canon_page_name(_cfg().write.agent_write_prefix)
        if scope == "agent" and agent:
            target = _edn_dumps(f"{pre}/{canon_page_name(agent)}")
            clauses.append(f"[?b :block/refs ?arp] [?arp :block/name {target}]")
        elif scope == "agent":
            clauses.append(
                f"[?b :block/refs ?arp] [?arp :block/name ?arn] "
                f"[(clojure.string/starts-with? ?arn {_edn_dumps(pre + '/')})]"
            )
        else:  # human — exclude agent-owned tasks
            clauses.append(
                f"(not-join [?b] [?b :block/refs ?arp] [?arp :block/name ?arn] "
                f"[(clojure.string/starts-with? ?arn {_edn_dumps(pre + '/')})])"
            )

    head = "[:find (pull ?b " + _PULL + ") :in $ %" if rules else "[:find (pull ?b " + _PULL + ")"
    dq = f"{head} :where " + " ".join(clauses) + "]"
    args = [dq, rules] if rules else [dq]
    rows = await get_client().call("logseq.DB.datascriptQuery", args)
    blocks = [normalize_block(b) for b in q.flatten_pull_rows(rows or [])]
    blocks = await _finalize(blocks, _read_depth(None))
    blocks = [b for b in blocks if not b.get("redacted")]
    return {"count": len(blocks), "tasks": blocks[: (limit or 100)]}


def _filter_page_rows(rows: list[Any], pre: str, depth: Optional[int], bl: Blacklist) -> list[dict]:
    """Pure: turn raw [name, orig] rows into blacklist/depth-filtered page entries."""
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name, orig = row[0], row[1]
        if not isinstance(name, str) or not isinstance(orig, str):
            continue
        if bl.is_page_excluded(orig):
            continue
        remainder = name[len(pre) + 1:] if pre else name
        segs = [s for s in remainder.split("/") if s]
        if pre and not segs:
            continue  # the bare prefix page itself is not one of its descendants
        if depth is not None and len(segs) > max(0, depth):
            continue
        out.append({"name": orig})
    out.sort(key=lambda e: e["name"].lower())
    return out


@mcp.tool()
async def list_pages(
    prefix: Annotated[str, Field(description="Namespace prefix; returns its descendant pages. Empty = all pages.")] = "",
    depth: Annotated[Optional[int], Field(description="Limit namespace levels below the prefix (1 = direct children). Default: unlimited.")] = None,
) -> dict:
    """List page names under a namespace prefix (structure, not block content).

    Namespace children are separate pages, so read_page on a parent shows none of
    them; use this to discover them. Matches the full lowercased ``:block/name``.
    """
    from .config import _edn_dumps

    pre = canon_page_name(prefix)
    where = ["[?p :block/name ?name]", "[?p :block/original-name ?orig]"]
    if pre:
        where.append(f"[(clojure.string/starts-with? ?name {_edn_dumps(pre + '/')})]")
    dq = "[:find ?name ?orig :where " + " ".join(where) + "]"
    rows = await q.run_datascript(get_client(), dq)
    pages = _filter_page_rows(rows, pre, depth, _blacklist())
    return {"prefix": pre, "count": len(pages), "pages": pages}


@mcp.tool()
async def get_logseq_guide() -> dict:
    """Return the authoritative guide for querying and writing this Logseq graph.

    Read this once before using the other tools — it covers the verified Datalog
    gotchas (lowercase names, prefix descendants, marker/journal-day types, tags vs
    refs, read/write scoping) so you don't have to rediscover them.
    """
    return {"guide": render_guide(_cfg().write.agent_write_prefix)}


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
    mode: Annotated[str, Field(description="append | replace (swaps page content, keeps page properties; remove a property via set_page_properties null)")] = "append",
    properties: Annotated[Optional[dict], Field(description="Page properties (set on creation)")] = None,
) -> dict:
    """Create or update a page in the agent's namespace."""
    res = await w.write_note(_cfg(), get_client(), subpath, content, mode, properties)
    await audit.log_write(_cfg(), get_client(), "wrote", f"[[{res['page']}]]")
    return res


@mcp.tool()
async def create_task(
    title: Annotated[str, Field(description="Task text")],
    agent: Annotated[str, Field(description="Executor agent name, e.g. 'hermes' -> link [[<prefix>/hermes]]")],
    project: Annotated[Optional[str], Field(description="Project page the task belongs to, e.g. 'Frisbee/Tech Support Bot'")] = None,
    marker: Annotated[str, Field(description="Task marker (TODO/DOING/NOW/LATER/WAITING/...)")] = "TODO",
    priority: Annotated[Optional[str], Field(description="Priority A/B/C")] = None,
    tags: Annotated[Optional[list[str]], Field(description="Extra category tags, e.g. ['plan']")] = None,
    plan_page: Annotated[Optional[str], Field(description="Detailed plan page, e.g. 'byAgent/hermes/proj/plan'")] = None,
    blocks_on: Annotated[Optional[str], Field(description="UUID of a blocking/parent task block -> ((uuid))")] = None,
    on_page: Annotated[Optional[str], Field(description="Agent-namespace page to hold the task (default <agent>/tasks)")] = None,
) -> dict:
    """Create a structured task. This is the ONLY way to make a task — write_note
    rejects task-marker content. The block lives in the agent namespace, carries a
    `#task` tag and a link to the executor agent, and optionally links a project,
    a detail page, and a blocking block."""
    res = await w.create_task(
        _cfg(), get_client(), title, agent, project, marker, priority, tags,
        plan_page, blocks_on, on_page,
    )
    await audit.log_write(_cfg(), get_client(), "created task", f"(({res['uuid']})) on [[{res['page']}]]")
    return res


@mcp.tool()
async def set_page_properties(
    subpath: Annotated[str, Field(description="Page path within the agent namespace")],
    properties: Annotated[dict, Field(description="Properties to set; value null removes one")],
) -> dict:
    """Set or remove properties on an agent-namespace page."""
    res = await w.set_page_properties(_cfg(), get_client(), subpath, properties)
    await audit.log_write(_cfg(), get_client(), "set properties on", f"[[{res['page']}]]")
    return res


@mcp.tool()
async def edit_block(
    uuid: Annotated[str, Field(description="Block UUID — read it first (read_block/read_page)")],
    old_content: Annotated[str, Field(description="The block's EXACT current full content; the edit is rejected unless it matches")],
    new_content: Annotated[str, Field(description="Replacement full content for the block")],
) -> dict:
    """Replace a block's content, confined to the agent namespace.

    Read the block first: you must pass its exact current content as `old_content`.
    The edit is rejected if it doesn't match (you didn't read it, or it changed) —
    this enforces read-before-write and guards a concurrent edit. The block must
    live under the agent namespace. To change a task's status use set_task_status."""
    res = await w.edit_block(_cfg(), get_client(), uuid, old_content, new_content)
    await audit.log_write(_cfg(), get_client(), "edited block", f"(({uuid})) on [[{res['page']}]]")
    return res


@mcp.tool()
async def set_task_status(
    uuid: Annotated[str, Field(description="Task block UUID")],
    status: Annotated[str, Field(description="New marker, e.g. TODO/DOING/DONE")],
) -> dict:
    """Change only a task block's status marker (text untouched)."""
    res = await w.set_task_status(_cfg(), get_client(), uuid, status)
    await audit.log_write(_cfg(), get_client(), "moved task", f"(({uuid})) to {res['new_status']}")
    return res


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
