"""Journal task channel: new TODOs under an existing block in the user's journal.

The second channel that writes OUTSIDE the agent namespace (the first is the
worklog). An agent drafts tasks, the user approves the list in chat, and the tasks
land where the user keeps tasks by hand: in a journal, under a category header
(`#_study`, `#_work/dynamo`) or under a ((ref)) to a bigger task in the weekly plan.
The server cannot see that approval, so it enforces the shape instead:

  * **append-only** — new blocks become the last children of `parent`; nothing
    that exists is edited, moved or removed;
  * **journals only** — `parent` must sit on a journal page;
  * **configured subtrees only** — `parent`, or a block above it, must carry a tag
    at or under one of `[tasks].journal_tags` (`_study`, or `_work` for
    `#_work/dynamo`); a subtree tagged `…/archive/…` is refused. Logseq also puts
    the namespace parents of a tag into `:block/path-refs` (a block under
    `#_work/archive/frisbee` carries `_work` too), which is why archive is refused
    explicitly instead of simply failing to match;
  * **closed shape** — the server renders `TODO [#P] [[page]] title`; the caller
    passes no marker and no multi-line text;
  * **no duplicates** — a task whose normalized text equals an open task under the
    same tag root is skipped and reported with that task's uuid;
  * **read back** — one insertBatchBlock with minted ids, then the parent's children
    are re-read; a task that did not land is an error, not a silent success.

Gated by `[tasks].journal_tags`: empty (the default) and the tool refuses.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid as uuidlib
from typing import Any, Optional

from . import writes as w
from .blacklist import canon_page_name
from .client import LogseqClient
from .config import AppConfig
from .normalize import display_text, parse_marker


class JournalTaskError(Exception):
    pass


MAX_TASKS = 50
OPEN_MARKERS = ("TODO", "DOING", "NOW", "LATER", "WAITING", "WAIT", "IN-PROGRESS", "STARTED")
ARCHIVE_SEGMENT = "archive"

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_WS_RE = re.compile(r"\s+")
_PRIORITY_RE = re.compile(r"\[#[A-Za-z]\]")
_LINK_RE = re.compile(r"#?\[\[([^\]]+)\]\]")


def task_key(content: str) -> str:
    """Pure: a task's identity for the duplicate check.

    Marker, priority, property lines and logbook are dropped, `[[x]]` and `#[[x]]`
    become `x`, case and whitespace are folded. `TODO [#A] [[RL]] MDP basics` and
    `LATER [[rl]]  mdp basics` are the same task; `[[myCouncil/app]] crypt` and
    `[[Evoca]] crypt` are not."""
    body = display_text(content or "")
    body = _PRIORITY_RE.sub("", body)
    body = _LINK_RE.sub(r"\1", body)
    body = unicodedata.normalize("NFC", body)
    return _WS_RE.sub(" ", body).strip().lower()


def render_task(item: Any) -> tuple[str, str]:
    """Pure: one caller item -> (title, block content). Raises on a bad item.

    An item is a title string or {title, priority?, page?}."""
    if isinstance(item, str):
        title, priority, page = item, None, None
    elif isinstance(item, dict):
        title, priority, page = item.get("title"), item.get("priority"), item.get("page")
    else:
        raise JournalTaskError("each task must be a title string or an object with 'title'")
    if not isinstance(title, str) or not title.strip():
        raise JournalTaskError("every task needs a non-empty title")
    title = _WS_RE.sub(" ", title).strip()
    if parse_marker(title + " ")[0] is not None:
        raise JournalTaskError(
            f"title {title!r} starts with a task marker — pass the bare title, the "
            "server writes TODO itself"
        )
    parts = ["TODO"]
    if priority not in (None, ""):
        p = str(priority).strip().upper().lstrip("[#").rstrip("]")
        if p not in ("A", "B", "C"):
            raise JournalTaskError("priority must be A, B or C")
        parts.append(f"[#{p}]")
    if page not in (None, ""):
        if not isinstance(page, str):
            raise JournalTaskError("page must be a page name")
        name = page.strip().removeprefix("[[").removesuffix("]]").strip()
        if not name or any(s in name for s in ("[", "]", "\n")):
            raise JournalTaskError(f"page {page!r} is not a plain page name")
        parts.append(f"[[{name}]]")
    parts.append(title)
    return title, " ".join(parts)


def match_roots(ref_names: list[str], roots: list[str]) -> list[str]:
    """Pure: the configured tag roots `parent` sits under, given its path-ref names.

    Raises when it sits under none, or under an archived tag of a root."""
    names = [canon_page_name(n) for n in ref_names if isinstance(n, str)]
    hit: list[str] = []
    for root in roots:
        under = [n for n in names if n == root or n.startswith(root + "/")]
        if not under:
            continue
        archived = [n for n in under if ARCHIVE_SEGMENT in n[len(root):].split("/")]
        if archived:
            raise JournalTaskError(
                f"parent is under an archived tag (#{archived[0]}) — tasks are not "
                "added to archived projects"
            )
        hit.append(root)
    if not hit:
        tags = ", ".join(f"#{r}" for r in roots)
        raise JournalTaskError(
            f"parent is not under {tags} (itself or a block above it) — journal tasks "
            "go only into those subtrees"
        )
    return hit


def _child_uuid(child: Any) -> Optional[str]:
    if isinstance(child, dict):
        u = child.get("uuid")
        return u if isinstance(u, str) else None
    if isinstance(child, (list, tuple)) and len(child) == 2 and child[0] == "uuid":
        return child[1] if isinstance(child[1], str) else None
    return None


async def _children(client: LogseqClient, parent: str) -> list[str]:
    raw = await client.call("logseq.Editor.getBlock", [parent, {"includeChildren": True}])
    if not isinstance(raw, dict):
        raise JournalTaskError(f"block {parent!r} not found")
    return [u for u in (_child_uuid(c) for c in raw.get("children") or []) if u]


async def _open_tasks_under(client: LogseqClient, roots: list[str]) -> dict[str, str]:
    """task_key -> uuid of every open task whose path-refs reach one of `roots`."""
    markers = " ".join(f'"{m}"' for m in OPEN_MARKERS)
    found: dict[str, str] = {}
    for root in roots:
        q = (
            "[:find ?u ?c ?n :where "
            f"[?b :block/marker ?m] [(contains? #{{{markers}}} ?m)] "
            "[?b :block/path-refs ?p] [?p :block/name ?n] "
            f'[(clojure.string/starts-with? ?n "{root}")] '
            "[?b :block/content ?c] [?b :block/uuid ?u]]"
        )
        for row in await client.call("logseq.DB.datascriptQuery", [q]) or []:
            if not isinstance(row, list) or len(row) < 3:
                continue
            u, content, name = row[0], row[1], row[2]
            if name == root or (isinstance(name, str) and name.startswith(root + "/")):
                found.setdefault(task_key(content), str(u))
    return found


async def add_journal_tasks(
    config: AppConfig, client: LogseqClient, parent: str, tasks: list
) -> dict:
    roots = [canon_page_name(t) for t in config.tasks.journal_tags if (t or "").strip()]
    if not roots:
        raise JournalTaskError(
            'journal tasks are disabled (set [tasks].journal_tags, e.g. ["_study", "_work"])'
        )
    if not isinstance(tasks, list) or not tasks:
        raise JournalTaskError("tasks must be a non-empty list")
    if len(tasks) > MAX_TASKS:
        raise JournalTaskError(f"at most {MAX_TASKS} tasks per call")
    items = [render_task(t) for t in tasks]  # validated whole before anything is read

    parent = (parent or "").strip().removeprefix("((").removesuffix("))").strip()
    if not _UUID_RE.match(parent):
        raise JournalTaskError("parent must be a block uuid")

    page_q = (
        f'[:find ?name ?jd :where [?b :block/uuid #uuid "{parent}"] [?b :block/page ?p] '
        "[?p :block/original-name ?name] [(get-else $ ?p :block/journal-day 0) ?jd]]"
    )
    rows = await client.call("logseq.DB.datascriptQuery", [page_q]) or []
    if not rows or not isinstance(rows[0], list):
        raise JournalTaskError(f"block {parent!r} not found")
    page_name, journal_day = rows[0][0], rows[0][1]
    if not journal_day:
        raise JournalTaskError(
            f"parent is on {page_name!r}, not a journal page — journal tasks go into journals only"
        )

    refs_q = (
        f'[:find ?n :where [?b :block/uuid #uuid "{parent}"] '
        "[?b :block/path-refs ?r] [?r :block/name ?n]]"
    )
    ref_rows = await client.call("logseq.DB.datascriptQuery", [refs_q]) or []
    hit = match_roots([r[0] for r in ref_rows if isinstance(r, list) and r], roots)

    existing = await _open_tasks_under(client, hit)
    to_add: list[tuple[str, str]] = []
    duplicates: list[dict] = []
    for title, content in items:
        key = task_key(content)
        if key in existing:
            duplicates.append({"title": title, "existing_uuid": existing[key]})
        elif any(task_key(c) == key for _, c in to_add):
            duplicates.append({"title": title, "existing_uuid": None,
                               "reason": "repeated in this call"})
        else:
            to_add.append((title, content))

    added: list[dict] = []
    if to_add:
        ids = [str(uuidlib.uuid4()) for _ in to_add]
        batch = [{"content": c, "properties": {"id": i}} for (_, c), i in zip(to_add, ids)]
        # Append AFTER the last child (as its sibling) rather than as a child of the
        # parent: that keeps the new tasks at the end, in the order given, whatever
        # side Logseq inserts non-sibling children on.
        kids = await _children(client, parent)
        if kids:
            target, opts = kids[-1], {"sibling": True, "keepUUID": True}
        else:
            target, opts = parent, {"sibling": False, "keepUUID": True}
        await client.call("logseq.Editor.insertBatchBlock", [target, batch, opts])
        await asyncio.sleep(w.VERIFY_DELAY)
        present = set(await _children(client, parent))
        missing = [i for i in ids if i not in present]
        if missing:
            raise JournalTaskError(
                f"{len(missing)} of {len(ids)} tasks are not under the parent after the "
                f"write (first missing: {missing[0]}); re-read the parent and add what is missing"
            )
        added = [{"uuid": i, "content": c} for (_, c), i in zip(to_add, ids)]

    return {
        "parent": parent,
        "page": page_name,
        "tags": hit,
        "added": added,
        "duplicates": duplicates,
    }
