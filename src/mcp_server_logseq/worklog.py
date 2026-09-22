"""Worklog channel: append a timestamped work note to today's journal.

This is the only write channel that lands OUTSIDE the agent namespace, so the
path confinement of `writes.resolve_agent_path` does not protect it. Its safety
rests instead on a deliberately narrow contract:

  * **append-only** — an existing block is never edited or removed;
  * **today's journal only** — the page is resolved from `:block/journal-day`
    and can never be named by the caller;
  * **one root** — every block it creates lives under a single dedicated root
    block, so human-authored journal structure is never touched;
  * **closed inputs** — the project must come from a server-built enum, the task
    must be an existing task block, and the caller supplies only free text;
  * **the server stamps the time** — callers cannot pass one, which also keeps a
    resumed agent from writing a remembered (stale) clock value.

The structure maintained in today's journal — find-or-create at every level, so
repeated notes nest under the existing headers instead of piling up duplicates:

    #_worklog                         <- root_block
      #_work/dynamo                   <- project group
        ((6a9eb940-...))              <- task anchor (only when `task` is given)
          17:50 told how to update    <- the note

Gated by [worklog].enabled, off by default.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Optional

from .blacklist import canon_page_name
from .client import LogseqClient
from .config import AppConfig, WorklogCfg, _edn_dumps
from .journal import today_journal_page
from .normalize import parse_marker


class WorklogError(Exception):
    pass


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_WHITESPACE_RE = re.compile(r"\s+")


def _enabled_cfg(config: AppConfig) -> WorklogCfg:
    wl = config.worklog
    if not (wl and wl.enabled):
        raise WorklogError("the worklog channel is disabled (set [worklog].enabled=true)")
    return wl


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def first_line(content: str) -> str:
    """A block's first line, stripped — what structural blocks are matched on.

    Logseq appends `id::` (and a `:LOGBOOK:` drawer) on later lines once a block
    is referenced or clocked, so matching whole content would fail to find the
    same group on the next call.
    """
    return (content or "").split("\n", 1)[0].strip()


def project_tag(namespace: str, project: str) -> str:
    """Render a project group's tag: ('_work', 'dynamo') -> '#_work/dynamo'."""
    return f"#{namespace.strip('/')}/{project.strip('/')}"


def select_projects(rows: list[Any], namespace: str, exclude: list[str]) -> list[str]:
    """Pure: `[name, original-name]` rows -> the closed set of project slugs.

    Only DIRECT children of the namespace qualify (`_work/dynamo` yes,
    `_work/dynamo/sync` no), minus the configured exclusions — a depth filter
    alone would also admit archive roots and retired projects.
    """
    pre = canon_page_name(namespace)
    denied = {canon_page_name(e) for e in exclude if (e or "").strip()}
    out: list[str] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name, orig = row[0], row[1]
        if not isinstance(name, str) or not isinstance(orig, str):
            continue
        remainder = name[len(pre) + 1:] if pre else name
        segs = [s for s in remainder.split("/") if s]
        if len(segs) != 1:
            continue  # the namespace page itself, or a deeper descendant
        if segs[0] in denied:
            continue
        out.append(orig.split("/")[-1])
    return sorted(set(out), key=str.lower)


def find_node(nodes: list[Any], line: str) -> Optional[dict]:
    """The first child whose first line matches `line`, if any."""
    for node in nodes or []:
        if isinstance(node, dict) and first_line(node.get("content") or "") == line:
            return node
    return None


def clean_text(text: str) -> str:
    """Collapse whitespace so a note is always exactly one journal line."""
    return _WHITESPACE_RE.sub(" ", text or "").strip()


# ---------------------------------------------------------------------------
# Graph access
# ---------------------------------------------------------------------------


async def list_projects(config: AppConfig, client: LogseqClient) -> list[str]:
    """The closed set of projects a note may be filed under."""
    wl = _enabled_cfg(config)
    pre = canon_page_name(wl.namespace)
    if not pre:
        raise WorklogError("[worklog].namespace is empty")
    dq = (
        "[:find ?name ?orig :where [?p :block/name ?name] [?p :block/original-name ?orig] "
        f"[(clojure.string/starts-with? ?name {_edn_dumps(pre + '/')})]]"
    )
    rows = await client.call("logseq.DB.datascriptQuery", [dq]) or []
    return select_projects(rows, wl.namespace, wl.exclude)


async def _validate_task(client: LogseqClient, task: str) -> str:
    """Return the task's uuid, raising unless it is an existing task block.

    Existence keeps `((uuid))` from rendering as a broken ref; the marker check
    keeps a note from being pinned to an arbitrary paragraph. Which project the
    task belongs to is deliberately NOT checked — that relation is defined by a
    hand-written dashboard query whose marker filter excludes DOING, i.e. the
    exact tasks work is logged against.
    """
    uid = (task or "").strip().strip("()")
    if not _UUID_RE.match(uid):
        raise WorklogError(f"task {task!r} is not a block uuid")
    raw = await client.call("logseq.Editor.getBlock", [uid, {"includeChildren": False}])
    if not isinstance(raw, dict):
        raise WorklogError(f"task block {uid} not found")
    marker, _ = parse_marker(raw.get("content") or "")
    if marker is None:
        raise WorklogError(
            f"block {uid} is not a task (no TODO/DOING/... marker) — a worklog "
            "note may only be attached to a task block"
        )
    return uid


async def _append_child(
    client: LogseqClient, parent_uuid: str, children: list[Any], content: str
) -> str:
    """Append `content` as the LAST child of `parent_uuid`.

    Inserting with `sibling: false` reliably makes a child, but its position
    among existing children is not pinned across Logseq versions — and this log
    must stay chronological. So when the parent already has children we anchor
    on the last one and insert as *its* sibling, which is unambiguous; only an
    empty parent takes the child path, where position cannot be wrong.
    """
    last_uuid: Optional[str] = None
    for node in children or []:
        if isinstance(node, dict) and isinstance(node.get("uuid"), str):
            last_uuid = node["uuid"]

    if last_uuid is not None:
        target, opts = last_uuid, {"sibling": True, "before": False}
    else:
        target, opts = parent_uuid, {"sibling": False}

    block = await client.call("logseq.Editor.insertBlock", [target, content, opts])
    uuid = block.get("uuid") if isinstance(block, dict) else None
    if not isinstance(uuid, str):
        raise WorklogError(f"could not create the worklog block {content!r}")
    return uuid


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


async def add_journal_note(
    config: AppConfig,
    client: LogseqClient,
    text: str,
    work: str,
    task: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """Append a timestamped note to today's journal under work (and task)."""
    wl = _enabled_cfg(config)

    body = clean_text(text)
    if not body:
        raise WorklogError("text is required")
    if parse_marker(body)[0] is not None:
        raise WorklogError(
            "a worklog note may not start with a task marker — it would become a "
            "task in the journal; use create_task for that"
        )

    projects = await list_projects(config, client)
    wanted = canon_page_name(work or "")
    chosen = next((p for p in projects if canon_page_name(p) == wanted), None)
    if chosen is None:
        raise WorklogError(
            f"unknown project {work!r}; pick one of: {', '.join(projects) or '(none)'}"
        )

    task_uuid = await _validate_task(client, task) if task else None

    stamp = now or datetime.datetime.now()
    page = await today_journal_page(client, stamp.date())
    tree = await client.call("logseq.Editor.getPageBlocksTree", [page]) or []

    # Level 1: the worklog root. Created at the journal's root level (the only
    # place this channel ever appends outside its own subtree).
    root_line = wl.root_block.strip()
    if not root_line:
        raise WorklogError("[worklog].root_block is empty")
    root = find_node(tree, root_line)
    if root is not None:
        root_uuid = root.get("uuid")
        root_children = root.get("children") or []
        if not isinstance(root_uuid, str):
            raise WorklogError("the worklog root block has no uuid")
    else:
        created = await client.call("logseq.Editor.appendBlockInPage", [page, root_line])
        root_uuid = created.get("uuid") if isinstance(created, dict) else None
        if not isinstance(root_uuid, str):
            raise WorklogError("could not create the worklog root block")
        root_children = []

    # Level 2: the project group.
    group_line = project_tag(wl.namespace, chosen)
    group = find_node(root_children, group_line)
    if group is not None:
        group_uuid = group.get("uuid")
        group_children = group.get("children") or []
        if not isinstance(group_uuid, str):
            raise WorklogError(f"the {group_line} group has no uuid")
    else:
        group_uuid = await _append_child(client, root_uuid, root_children, group_line)
        group_children = []

    # Level 3 (optional): the task anchor. Without a task the note hangs
    # directly off the project group.
    parent_uuid, parent_children = group_uuid, group_children
    if task_uuid:
        anchor_line = f"(({task_uuid}))"
        anchor = find_node(group_children, anchor_line)
        anchor_uuid = anchor.get("uuid") if isinstance(anchor, dict) else None
        if isinstance(anchor_uuid, str):
            parent_children = anchor.get("children") or []
        else:
            anchor_uuid = await _append_child(client, group_uuid, group_children, anchor_line)
            parent_children = []
        parent_uuid = anchor_uuid

    # Level 4: the note itself.
    note_line = f"{stamp:%H:%M} {body}"
    note_uuid = await _append_child(client, parent_uuid, parent_children, note_line)

    return {
        "page": page,
        "project": chosen,
        "task": task_uuid,
        "uuid": note_uuid,
        "content": note_line,
    }
