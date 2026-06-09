"""Non-lossy block-reference resolution over normalized blocks.

Walks normalized blocks and, for each ((uuid)) embed, fetches the referenced
block and records {uuid, status, priority, text} in `resolved_refs` (so the
agent keeps the address for read->uuid->write), while substituting readable text
into the display. Depth-limited and cycle-safe; respects the blacklist.
"""

from __future__ import annotations

from typing import Optional

from .blacklist import Blacklist
from .client import LogseqClient
from .normalize import normalize_block


async def resolve_refs(
    client: LogseqClient,
    blocks: list[dict],
    depth: int,
    blacklist: Optional[Blacklist] = None,
) -> list[dict]:
    """Resolve block-refs in-place (and return the same list)."""
    if depth <= 0:
        return blocks
    visited: set[str] = set()
    for b in blocks:
        await _resolve_node(client, b, depth, blacklist, visited)
    return blocks


async def _resolve_node(
    client: LogseqClient,
    block: dict,
    depth: int,
    blacklist: Optional[Blacklist],
    visited: set[str],
) -> None:
    if block.get("redacted"):
        return

    for uuid in block.get("block_refs") or []:
        if depth <= 0 or uuid in visited:
            continue
        visited.add(uuid)
        summary = await _resolve_one(client, uuid, depth, blacklist, visited)
        if summary is None:
            continue
        block.setdefault("resolved_refs", []).append(summary)
        if block.get("is_block_ref"):
            block["text"] = summary["text"]
        else:
            block["text"] = (block.get("text") or "").replace(
                f"(({uuid}))", summary["text"]
            )

    for child in block.get("children") or []:
        await _resolve_node(client, child, depth, blacklist, visited)


async def _resolve_one(
    client: LogseqClient,
    uuid: str,
    depth: int,
    blacklist: Optional[Blacklist],
    visited: set[str],
) -> Optional[dict]:
    raw = await client.call("logseq.Editor.getBlock", [uuid, {"includeChildren": False}])
    if not isinstance(raw, dict):
        return None
    rn = normalize_block(raw)

    if blacklist and blacklist.active and blacklist._block_references_excluded(rn):
        return {
            "uuid": uuid,
            "status": None,
            "priority": None,
            "text": blacklist.placeholder,
            "redacted": True,
        }

    # follow nested embeds one level shallower
    await _resolve_node(client, rn, depth - 1, blacklist, visited)
    return {
        "uuid": rn.get("uuid"),
        "status": rn.get("status"),
        "priority": rn.get("priority"),
        "text": rn.get("text"),
    }
