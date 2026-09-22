"""Tests for the worklog channel (pure helpers + the find-or-create structure)."""

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path

import pytest

from mcp_server_logseq.config import load_config
from mcp_server_logseq.worklog import (
    WorklogError,
    add_journal_note,
    clean_text,
    first_line,
    list_projects,
    project_tag,
    select_projects,
)

STAMP = datetime.datetime(2026, 9, 22, 17, 50)

PROJECT_ROWS = [
    ["_work/dynamo", "_work/dynamo"],
    ["_work/dynamo/sync", "_work/dynamo/sync"],
    ["_work/dynamo/tasks", "_work/dynamo/tasks"],
    ["_work/itquick", "_work/itquick"],
    ["_work/archive", "_work/archive"],
    ["_work/archive/botev", "_work/archive/botev"],
    ["_work/frisbee", "_work/frisbee"],
]

TASK_UUID = "6a9eb940-758e-4966-a31d-f91e79c35f42"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_select_projects_keeps_only_direct_children() -> None:
    got = select_projects(PROJECT_ROWS, "_work", ["archive", "frisbee"])
    assert got == ["dynamo", "itquick"]  # no /sync, /tasks, no excluded roots


def test_select_projects_ignores_malformed_rows() -> None:
    rows = [["_work/dynamo", "_work/dynamo"], ["_work/x"], "junk", [1, 2]]
    assert select_projects(rows, "_work", []) == ["dynamo"]


def test_first_line_ignores_appended_properties() -> None:
    # Once referenced, a block carries id:: on a later line.
    assert first_line("#_work/dynamo\nid:: 6a9eb940-7585") == "#_work/dynamo"
    assert first_line("  17:50 note  ") == "17:50 note"
    assert first_line("") == ""


def test_project_tag() -> None:
    assert project_tag("_work", "dynamo") == "#_work/dynamo"
    assert project_tag("_work/", "/dynamo") == "#_work/dynamo"


def test_clean_text_collapses_to_one_line() -> None:
    assert clean_text("  told   how\nto update ") == "told how to update"


# ---------------------------------------------------------------------------
# Fake client: keeps a real (mutable) journal tree so nesting can be asserted
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *, projects=None, blocks=None) -> None:
        self.calls: list = []
        self.projects = projects if projects is not None else PROJECT_ROWS
        self.blocks = blocks or {}
        self.tree: list = []
        self._n = 0

    def _uuid(self) -> str:
        self._n += 1
        return f"{self._n:08d}-0000-4000-8000-000000000000"

    def _attach_child(self, nodes, parent_uuid, node) -> bool:
        for n in nodes:
            if n.get("uuid") == parent_uuid:
                n.setdefault("children", []).append(node)
                return True
            if self._attach_child(n.get("children") or [], parent_uuid, node):
                return True
        return False

    def _attach_sibling(self, nodes, target_uuid, node) -> bool:
        for i, n in enumerate(nodes):
            if n.get("uuid") == target_uuid:
                nodes.insert(i + 1, node)
                return True
            if self._attach_sibling(n.get("children") or [], target_uuid, node):
                return True
        return False

    async def call(self, method, args=None):
        self.calls.append((method, args))
        args = args or []
        if method == "logseq.DB.datascriptQuery":
            return [["Sep 22nd, 2026"]] if "journal-day" in args[0] else self.projects
        if method == "logseq.Editor.getBlock":
            uid = args[0]
            return {"uuid": uid, "content": self.blocks[uid]} if uid in self.blocks else None
        if method == "logseq.Editor.getPageBlocksTree":
            return self.tree
        if method == "logseq.Editor.appendBlockInPage":
            node = {"uuid": self._uuid(), "content": args[1], "children": []}
            self.tree.append(node)
            return node
        if method == "logseq.Editor.insertBlock":
            target, opts = args[0], (args[2] if len(args) > 2 else {})
            node = {"uuid": self._uuid(), "content": args[1], "children": []}
            attach = self._attach_sibling if opts.get("sibling") else self._attach_child
            if not attach(self.tree, target, node):
                raise AssertionError(f"insertBlock target {target} not in the tree")
            return node
        return None

    # -- assertions helpers --
    def writes(self) -> list:
        return [c for c in self.calls if c[0].startswith("logseq.Editor.")
                and c[0] != "logseq.Editor.getBlock"
                and c[0] != "logseq.Editor.getPageBlocksTree"]


def _cfg(tmp_path: Path, body: str = "enabled = true\nexclude = [\"archive\", \"frisbee\"]\n"):
    p = tmp_path / "config.toml"
    p.write_text(f"[worklog]\n{body}", encoding="utf-8")
    return load_config(p)


def _shape(nodes) -> list:
    """Compact (content, children) view of the tree for assertions."""
    return [(n["content"], _shape(n.get("children") or [])) for n in nodes]


# ---------------------------------------------------------------------------
# Gate + input validation
# ---------------------------------------------------------------------------


def test_disabled_by_default_writes_nothing(tmp_path: Path) -> None:
    cfg = load_config(None)  # no [worklog] section at all
    client = _FakeClient()
    with pytest.raises(WorklogError, match="disabled"):
        asyncio.run(add_journal_note(cfg, client, "note", "dynamo"))
    assert client.calls == []


def test_unknown_project_is_rejected_and_lists_options(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="dynamo, itquick"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "frisbee"))
    assert client.writes() == []


def test_empty_text_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="text is required"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "   ", "dynamo"))
    assert client.calls == []


def test_task_marker_text_is_rejected(tmp_path: Path) -> None:
    """A note starting with a marker would silently become a journal task."""
    client = _FakeClient()
    with pytest.raises(WorklogError, match="task marker"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "TODO fix the thing", "dynamo"))
    assert client.calls == []


def test_missing_task_block_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient(blocks={})
    with pytest.raises(WorklogError, match="not found"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", TASK_UUID))
    assert client.writes() == []


def test_non_task_block_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient(blocks={TASK_UUID: "just a paragraph"})
    with pytest.raises(WorklogError, match="is not a task"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", TASK_UUID))
    assert client.writes() == []


def test_malformed_task_uuid_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="not a block uuid"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", "nope"))
    assert client.writes() == []


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_builds_the_full_nesting(tmp_path: Path) -> None:
    client = _FakeClient(blocks={TASK_UUID: "DOING [#A] [[dynamo stats/capology]] вес фичи"})
    res = asyncio.run(
        add_journal_note(_cfg(tmp_path), client, "told how to update", "dynamo", TASK_UUID, now=STAMP)
    )

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [
                (f"(({TASK_UUID}))", [
                    ("17:50 told how to update", []),
                ]),
            ]),
        ]),
    ]
    assert res["page"] == "Sep 22nd, 2026"
    assert res["project"] == "dynamo"
    assert res["task"] == TASK_UUID
    assert res["content"] == "17:50 told how to update"


def test_without_task_the_note_hangs_off_the_project(tmp_path: Path) -> None:
    client = _FakeClient()
    asyncio.run(add_journal_note(_cfg(tmp_path), client, "login", "itquick", now=STAMP))
    assert _shape(client.tree) == [
        ("#_worklog", [("#_work/itquick", [("17:50 login", [])])]),
    ]


def test_repeated_notes_reuse_root_group_and_anchor(tmp_path: Path) -> None:
    """The whole point of find-or-create: no duplicated headers over a day."""
    cfg = _cfg(tmp_path)
    client = _FakeClient(blocks={TASK_UUID: "DOING [[dynamo stats/capology]] x"})
    for text, t in [("continue", STAMP), ("still going", STAMP.replace(minute=59))]:
        asyncio.run(add_journal_note(cfg, client, text, "dynamo", TASK_UUID, now=t))

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [
                (f"(({TASK_UUID}))", [
                    ("17:50 continue", []),
                    ("17:59 still going", []),
                ]),
            ]),
        ]),
    ]


def test_second_project_joins_the_same_root(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "a", "dynamo", now=STAMP))
    asyncio.run(add_journal_note(cfg, client, "b", "itquick", now=STAMP))

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [("17:50 a", [])]),
            ("#_work/itquick", [("17:50 b", [])]),
        ]),
    ]


def test_existing_human_journal_blocks_are_never_touched(tmp_path: Path) -> None:
    """A hand-written #_work/dynamo (e.g. in the week plan) must not be reused."""
    client = _FakeClient()
    client.tree = [
        {"uuid": "human-1", "content": "#_work/dynamo", "children": [
            {"uuid": "human-2", "content": "((deadbeef))", "children": []},
        ]},
    ]
    asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", now=STAMP))

    # the human block is untouched, and the worklog built its own subtree
    assert _shape(client.tree) == [
        ("#_work/dynamo", [("((deadbeef))", [])]),
        ("#_worklog", [("#_work/dynamo", [("17:50 note", [])])]),
    ]
    # nothing was updated or removed — append-only
    assert not [c for c in client.calls if "updateBlock" in c[0] or "removeBlock" in c[0]]


def test_a_later_note_anchors_on_the_previous_one(tmp_path: Path) -> None:
    """Order must not depend on how Logseq positions a `sibling: false` insert."""
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "first", "dynamo", now=STAMP))
    first_uuid = client.tree[0]["children"][0]["children"][0]["uuid"]

    asyncio.run(add_journal_note(cfg, client, "second", "dynamo", now=STAMP.replace(minute=55)))
    target, content, opts = [c for c in client.calls if c[0] == "logseq.Editor.insertBlock"][-1][1]

    assert content == "17:55 second"
    assert target == first_uuid and opts["sibling"] is True


def test_list_projects_uses_the_configured_namespace(tmp_path: Path) -> None:
    client = _FakeClient()
    got = asyncio.run(list_projects(_cfg(tmp_path), client))
    assert got == ["dynamo", "itquick"]
    dq = client.calls[0][1][0]
    assert '"_work/"' in dq  # EDN-quoted prefix, not a bare symbol
