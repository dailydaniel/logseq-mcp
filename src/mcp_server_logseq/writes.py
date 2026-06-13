"""Write operations: the agent's namespace channel + task-status channel.

All page writes are confined to the agent's namespace (`agent_write_prefix`,
default `byAgent`) unless `allow_agents_write_any`. Task status changes touch
only the leading marker.
"""

from __future__ import annotations

from typing import Any, Optional

from .blacklist import canon_page_name
from .client import LogseqClient
from .config import AppConfig
from .normalize import normalize_block, parse_marker, rewrite_marker, MARKERS


class WriteError(Exception):
    pass


def resolve_agent_path(config: AppConfig, subpath: str) -> str:
    """Map a caller subpath to a full page name, enforcing the agent namespace."""
    sub = (subpath or "").strip().strip("/")
    if not sub:
        raise WriteError("empty page path")
    if ".." in sub.split("/"):
        raise WriteError("path may not contain '..'")

    if config.write.allow_agents_write_any:
        return sub

    prefix = config.write.agent_write_prefix
    if sub == prefix or sub.startswith(prefix + "/"):
        return sub
    return f"{prefix}/{sub}"


async def _get_page(client: LogseqClient, name: str) -> Optional[dict]:
    page = await client.call("logseq.Editor.getPage", [name, {"includeChildren": False}])
    return page if isinstance(page, dict) else None


async def _ensure_page(
    client: LogseqClient, name: str, properties: Optional[dict], create_first_block: bool
) -> tuple[dict, bool]:
    existing = await _get_page(client, name)
    if existing is not None:
        return existing, True
    created = await client.call(
        "logseq.Editor.createPage",
        [name, properties or {}, {"redirect": False, "createFirstBlock": create_first_block}],
    )
    if not isinstance(created, dict):
        raise WriteError(f"could not create page {name!r}")
    return created, False


async def write_note(
    config: AppConfig,
    client: LogseqClient,
    subpath: str,
    content: Optional[str] = None,
    mode: str = "append",
    properties: Optional[dict] = None,
) -> dict:
    if mode not in ("append", "replace"):
        raise WriteError("mode must be 'append' or 'replace'")
    if content and parse_marker(content)[0] is not None:
        raise WriteError(
            "content starts with a task marker — create tasks via create_task, not "
            "write_note (write_note is for notes; create_task enforces task structure)"
        )
    name = resolve_agent_path(config, subpath)

    page, existed = await _ensure_page(
        client, name, properties, create_first_block=bool(properties)
    )

    preserved: dict = {}
    if mode == "replace" and existed:
        tree = await client.call("logseq.Editor.getPageBlocksTree", [name]) or []
        # Replace swaps content but keeps page properties (they live on the first
        # block); to remove a property, use set_page_properties(..., {prop: null}).
        if tree:
            preserved = tree[0].get("properties") or {}
        for blk in tree:
            uuid = blk.get("uuid")
            if uuid:
                await client.call("logseq.Editor.removeBlock", [uuid])

    appended_uuid = None
    if content:
        block = await client.call("logseq.Editor.appendBlockInPage", [name, content])
        if isinstance(block, dict):
            appended_uuid = block.get("uuid")

    # Re-attach preserved page properties to the new first block (mode="replace").
    if preserved:
        holder = appended_uuid
        if holder is None:
            block = await client.call("logseq.Editor.appendBlockInPage", [name, ""])
            holder = block.get("uuid") if isinstance(block, dict) else None
        if holder:
            for key, value in preserved.items():
                await client.call("logseq.Editor.upsertBlockProperty", [holder, key, value])

    return {
        "page": name,
        "already_existed": existed,
        "appended_uuid": appended_uuid,
        "kept_properties": list(preserved.keys()),
    }


def _tag_token(tag: str) -> str:
    """Render a tag, using #[[...]] form when it contains spaces."""
    t = tag.strip().lstrip("#")
    return f"#[[{t}]]" if " " in t else f"#{t}"


def build_task_content(
    *,
    title: str,
    marker: str,
    priority: Optional[str],
    agent_ref: str,
    base_tag: Optional[str],
    tags: Optional[list[str]],
    project: Optional[str],
    plan_page: Optional[str],
    blocks_on: Optional[str],
) -> str:
    """Assemble the canonical task block string (pure)."""
    parts = [marker]
    if priority:
        parts.append(f"[#{priority}]")
    for t in ([base_tag] if base_tag else []) + (tags or []):
        if t and t.strip():
            parts.append(_tag_token(t))
    parts.append(title.strip())
    parts.append(f"[[{agent_ref}]]")
    if project:
        parts.append(f"[[{project.strip().strip('[]')}]]")
    if plan_page:
        parts.append(f"[[{plan_page.strip().strip('[]')}]]")
    if blocks_on:
        parts.append(f"(({blocks_on.strip('()')}))")
    return " ".join(parts)


async def create_task(
    config: AppConfig,
    client: LogseqClient,
    title: str,
    agent: str,
    project: Optional[str] = None,
    marker: str = "TODO",
    priority: Optional[str] = None,
    tags: Optional[list[str]] = None,
    plan_page: Optional[str] = None,
    blocks_on: Optional[str] = None,
    on_page: Optional[str] = None,
) -> dict:
    """Create a structured task block inside the agent namespace.

    The block lives under the agent prefix (write-confined) but references the
    executor agent's page and, optionally, a project / plan page / blocking block.
    """
    if not (title or "").strip():
        raise WriteError("title is required")
    marker = (marker or "TODO").strip().upper()
    if marker not in MARKERS:
        raise WriteError(f"invalid marker {marker!r}; one of {', '.join(MARKERS)}")
    if priority:
        priority = priority.strip().upper().lstrip("[#").rstrip("]")
        if priority not in ("A", "B", "C"):
            raise WriteError("priority must be A, B or C")
    agent = (agent or "").strip().strip("/")
    if not agent:
        raise WriteError("agent (executor) is required")

    prefix = config.write.agent_write_prefix
    agent_ref = f"{prefix}/{agent}"
    page = resolve_agent_path(config, on_page or f"{agent}/tasks")

    content = build_task_content(
        title=title, marker=marker, priority=priority, agent_ref=agent_ref,
        base_tag="task", tags=tags, project=project, plan_page=plan_page,
        blocks_on=blocks_on,
    )

    await _ensure_page(client, page, None, create_first_block=False)
    block = await client.call("logseq.Editor.appendBlockInPage", [page, content])
    uuid = block.get("uuid") if isinstance(block, dict) else None
    return {"page": page, "uuid": uuid, "marker": marker, "content": content}


async def set_page_properties(
    config: AppConfig, client: LogseqClient, subpath: str, properties: dict
) -> dict:
    """Upsert page properties (value `None` removes the property)."""
    if not isinstance(properties, dict) or not properties:
        raise WriteError("properties must be a non-empty object")
    name = resolve_agent_path(config, subpath)
    page, existed = await _ensure_page(client, name, None, create_first_block=True)

    tree = await client.call("logseq.Editor.getPageBlocksTree", [name]) or []
    if not tree:
        raise WriteError(f"page {name!r} has no block to hold properties")
    first_uuid = tree[0].get("uuid")
    if not first_uuid:
        raise WriteError("could not locate the page's first block")

    changed: dict[str, Any] = {}
    for key, value in properties.items():
        if value is None:
            await client.call("logseq.Editor.removeBlockProperty", [first_uuid, key])
            changed[key] = None
        else:
            await client.call("logseq.Editor.upsertBlockProperty", [first_uuid, key, value])
            changed[key] = value

    return {"page": name, "already_existed": existed, "properties": changed}


async def _block_page_name(client: LogseqClient, block: dict) -> Optional[str]:
    """Best-effort page name for a block from getBlock (page is usually `{id}`)."""
    page = block.get("page")
    pid: Optional[int] = None
    if isinstance(page, dict):
        name = page.get("original-name") or page.get("originalName") or page.get("name")
        if name:
            return name
        raw_id = page.get("id")
        if isinstance(raw_id, int):
            pid = raw_id
    elif isinstance(page, int):
        pid = page
    if pid is not None:
        pg = await client.call("logseq.Editor.getPage", [pid])
        if isinstance(pg, dict):
            return pg.get("original-name") or pg.get("originalName") or pg.get("name")
    return None


async def assert_block_in_agent_ns(
    config: AppConfig, client: LogseqClient, block: dict
) -> str:
    """Return the block's page name, raising if it is outside the agent namespace.

    The path-confinement that `resolve_agent_path` gives subpath-addressed writes
    does NOT apply to uuid-addressed writes (a uuid can point anywhere in the
    graph). Any uuid-addressed *content* write must call this gate so it cannot
    rewrite blocks outside `byAgent`. (The marker-only `set_task_status` channel is
    deliberately exempt — it can only touch a leading task marker.)
    """
    name = await _block_page_name(client, block)
    if not name:
        raise WriteError("could not determine the block's page; refusing to write")
    if config.write.allow_agents_write_any:
        return name
    prefix = canon_page_name(config.write.agent_write_prefix)
    canon = canon_page_name(name)
    if canon == prefix or canon.startswith(prefix + "/"):
        return name
    raise WriteError(
        f"block is on page {name!r}, outside the agent namespace "
        f"{config.write.agent_write_prefix!r}/ — edit_block is namespace-confined"
    )


async def edit_block(
    config: AppConfig,
    client: LogseqClient,
    uuid: str,
    old_content: str,
    new_content: str,
) -> dict:
    """Replace a block's whole content, confined to the agent namespace.

    Read-before-write is enforced by an exact match: `old_content` must equal the
    block's current full content, or the edit is rejected (the block changed, or
    the caller never read it). This is the block analogue of a file edit's
    old/new string match, and it also guards against clobbering a concurrent edit.
    """
    if old_content is None or new_content is None:
        raise WriteError("old_content and new_content are required")
    if old_content == new_content:
        raise WriteError("old_content and new_content are identical (no change)")

    raw = await client.call("logseq.Editor.getBlock", [uuid, {"includeChildren": False}])
    if not isinstance(raw, dict):
        raise WriteError(f"block {uuid!r} not found")

    page_name = await assert_block_in_agent_ns(config, client, raw)

    current = raw.get("content") or ""
    if current != old_content:
        raise WriteError(
            "old_content does not match the block's current content — read the "
            "block first (read_block/read_page); it may have changed. Supply the "
            "block's exact current content as old_content."
        )

    await client.call("logseq.Editor.updateBlock", [uuid, new_content])
    return {
        "uuid": uuid,
        "page": page_name,
        "old_content": current,
        "new_content": new_content,
    }


async def set_task_status(
    config: AppConfig, client: LogseqClient, uuid: str, status: str
) -> dict:
    if not config.tasks.allow_status_change:
        raise WriteError("task status changes are disabled (set [tasks].allow_status_change=true)")
    status = (status or "").strip().upper()
    if status not in MARKERS:
        raise WriteError(f"invalid status {status!r}; one of {', '.join(MARKERS)}")

    raw = await client.call("logseq.Editor.getBlock", [uuid, {"includeChildren": False}])
    if not isinstance(raw, dict):
        raise WriteError(f"block {uuid!r} not found")
    content = raw.get("content") or ""
    old = normalize_block(raw)["status"]

    new_content = rewrite_marker(content, status)
    await client.call("logseq.Editor.updateBlock", [uuid, new_content])

    return {"uuid": uuid, "old_status": old, "new_status": status}
