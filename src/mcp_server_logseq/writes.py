"""Write operations: the agent's namespace channel + task-status channel.

All page writes are confined to the agent's namespace (`agent_write_prefix`,
default `byAgent`) unless `allow_agents_write_any`. Task status changes touch
only the leading marker.
"""

from __future__ import annotations

from typing import Any, Optional

from .client import LogseqClient
from .config import AppConfig
from .normalize import normalize_block, rewrite_marker, MARKERS


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
    name = resolve_agent_path(config, subpath)

    page, existed = await _ensure_page(
        client, name, properties, create_first_block=bool(properties)
    )

    if mode == "replace" and existed:
        tree = await client.call("logseq.Editor.getPageBlocksTree", [name]) or []
        for blk in tree:
            uuid = blk.get("uuid")
            if uuid:
                await client.call("logseq.Editor.removeBlock", [uuid])

    appended_uuid = None
    if content:
        block = await client.call("logseq.Editor.appendBlockInPage", [name, content])
        if isinstance(block, dict):
            appended_uuid = block.get("uuid")

    return {
        "page": name,
        "already_existed": existed,
        "appended_uuid": appended_uuid,
    }


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
