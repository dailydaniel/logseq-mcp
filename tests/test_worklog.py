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
    list_agents,
    list_projects,
    project_tag,
    resolve_agent,
    select_agents,
    select_projects,
    tag_ref,
)

STAMP = datetime.datetime(2026, 9, 22, 17, 50)
AGENT = "work-scout"
SIG = "#_agents/claude/work-scout"

PROJECT_ROWS = [
    ["_work/dynamo", "_work/dynamo"],
    ["_work/dynamo/sync", "_work/dynamo/sync"],
    ["_work/dynamo/tasks", "_work/dynamo/tasks"],
    ["_work/itquick", "_work/itquick"],
    ["_work/archive", "_work/archive"],
    ["_work/archive/botev", "_work/archive/botev"],
    ["_work/frisbee", "_work/frisbee"],
]

AGENT_ROWS = [
    ["_agents/claude/work-scout", "_agents/claude/work-scout"],
    ["_agents/claude/logseq-factory-admin", "_agents/claude/logseq-factory-admin"],
    ["_agents/codex/macbook", "_agents/codex/macbook"],
    ["_agents/claude/work-cost/notes", "_agents/claude/work-cost/notes"],  # too deep
    ["_agents/archive/old-bot", "_agents/archive/old-bot"],                # excluded runtime
    ["_agents/readme", "_agents/readme"],                                  # too shallow
    ["_agents/claude/ghost", "_agents/claude/ghost"],                      # mentioned, no file
]
# Pages Logseq knows only because a note mentions them: no :block/file.
FILELESS = {"_agents/claude/ghost"}

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
    def __init__(self, *, projects=None, agents=None, blocks=None) -> None:
        self.calls: list = []
        self.projects = projects if projects is not None else PROJECT_ROWS
        self.agents = agents if agents is not None else AGENT_ROWS
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
            q = args[0]
            if "journal-day" in q:
                return [["Sep 22nd, 2026"]]
            if '"_agents/' in q:
                if ":block/file" in q:
                    return [r for r in self.agents if r[0] not in FILELESS]
                return self.agents
            return self.projects
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

    def writes(self) -> list:
        return [c for c in self.calls if c[0].startswith("logseq.Editor.")
                and c[0] not in ("logseq.Editor.getBlock", "logseq.Editor.getPageBlocksTree")]


def _cfg(tmp_path: Path, body: str = (
    "enabled = true\nexclude = [\"archive\", \"frisbee\"]\n"
    "agent_exclude = [\"archive\"]\n"
)):
    p = tmp_path / "config.toml"
    p.write_text(f"[worklog]\n{body}", encoding="utf-8")
    return load_config(p)


def _shape(nodes) -> list:
    """Compact (content, children) view of the tree for assertions."""
    return [(n["content"], _shape(n.get("children") or [])) for n in nodes]


# ---------------------------------------------------------------------------
# Gate + input validation
# ---------------------------------------------------------------------------


def test_disabled_by_default_writes_nothing() -> None:
    cfg = load_config(None)  # no [worklog] section at all
    client = _FakeClient()
    with pytest.raises(WorklogError, match="disabled"):
        asyncio.run(add_journal_note(cfg, client, "note", "dynamo", AGENT))
    assert client.calls == []


def test_unknown_project_is_rejected_and_lists_options(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="dynamo, itquick"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "frisbee", AGENT))
    assert client.writes() == []


def test_unknown_agent_is_rejected_and_lists_options(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="claude/logseq-factory-admin, claude/work-scout, codex/macbook"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", "hermes"))
    assert client.writes() == []


def test_empty_text_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="text is required"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "   ", "dynamo", AGENT))
    assert client.calls == []


def test_task_marker_text_is_rejected(tmp_path: Path) -> None:
    """A note starting with a marker would silently become a journal task."""
    client = _FakeClient()
    with pytest.raises(WorklogError, match="task marker"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "TODO fix it", "dynamo", AGENT))
    assert client.calls == []


def test_missing_task_block_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient(blocks={})
    with pytest.raises(WorklogError, match="not found"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "n", "dynamo", AGENT, TASK_UUID))
    assert client.writes() == []


def test_non_task_block_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient(blocks={TASK_UUID: "just a paragraph"})
    with pytest.raises(WorklogError, match="is not a task"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "n", "dynamo", AGENT, TASK_UUID))
    assert client.writes() == []


def test_malformed_task_uuid_is_rejected(tmp_path: Path) -> None:
    client = _FakeClient()
    with pytest.raises(WorklogError, match="not a block uuid"):
        asyncio.run(add_journal_note(_cfg(tmp_path), client, "n", "dynamo", AGENT, "nope"))
    assert client.writes() == []


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_builds_the_full_nesting(tmp_path: Path) -> None:
    client = _FakeClient(blocks={TASK_UUID: "DOING [#A] [[dynamo stats/capology]] вес фичи"})
    res = asyncio.run(
        add_journal_note(_cfg(tmp_path), client, "told how to update", "dynamo",
                         AGENT, TASK_UUID, now=STAMP)
    )

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [
                (f"(({TASK_UUID}))", [
                    (f"17:50 {SIG} told how to update", []),
                ]),
            ]),
        ]),
    ]
    assert res["project"] == "dynamo"
    assert res["agent"] == "claude/work-scout"  # the resolved, runtime-qualified name
    assert res["task"] == TASK_UUID


def test_the_signature_is_inline_not_a_grouping_level(tmp_path: Path) -> None:
    """Two agents on one project share the group; only the lines differ."""
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "a", "dynamo", "work-scout", now=STAMP))
    asyncio.run(add_journal_note(cfg, client, "b", "dynamo", "logseq-factory-admin",
                                 now=STAMP.replace(minute=55)))

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [
                ("17:50 #_agents/claude/work-scout a", []),
                ("17:55 #_agents/claude/logseq-factory-admin b", []),
            ]),
        ]),
    ]


def test_without_task_the_note_hangs_off_the_project(tmp_path: Path) -> None:
    client = _FakeClient()
    asyncio.run(add_journal_note(_cfg(tmp_path), client, "login", "itquick", AGENT, now=STAMP))
    assert _shape(client.tree) == [
        ("#_worklog", [("#_work/itquick", [(f"17:50 {SIG} login", [])])]),
    ]


def test_repeated_notes_reuse_root_group_and_anchor(tmp_path: Path) -> None:
    """The whole point of find-or-create: no duplicated headers over a day."""
    cfg = _cfg(tmp_path)
    client = _FakeClient(blocks={TASK_UUID: "DOING [[dynamo stats/capology]] x"})
    for text, t in [("continue", STAMP), ("still going", STAMP.replace(minute=59))]:
        asyncio.run(add_journal_note(cfg, client, text, "dynamo", AGENT, TASK_UUID, now=t))

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [
                (f"(({TASK_UUID}))", [
                    (f"17:50 {SIG} continue", []),
                    (f"17:59 {SIG} still going", []),
                ]),
            ]),
        ]),
    ]


def test_second_project_joins_the_same_root(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "a", "dynamo", AGENT, now=STAMP))
    asyncio.run(add_journal_note(cfg, client, "b", "itquick", AGENT, now=STAMP))

    assert _shape(client.tree) == [
        ("#_worklog", [
            ("#_work/dynamo", [(f"17:50 {SIG} a", [])]),
            ("#_work/itquick", [(f"17:50 {SIG} b", [])]),
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
    asyncio.run(add_journal_note(_cfg(tmp_path), client, "note", "dynamo", AGENT, now=STAMP))

    assert _shape(client.tree) == [
        ("#_work/dynamo", [("((deadbeef))", [])]),
        ("#_worklog", [("#_work/dynamo", [(f"17:50 {SIG} note", [])])]),
    ]
    assert not [c for c in client.calls if "updateBlock" in c[0] or "removeBlock" in c[0]]


def test_a_note_after_something_else_opens_a_fresh_root(tmp_path: Path) -> None:
    """The journal stays chronological: a morning root must not swallow the evening."""
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "утро", "dynamo", AGENT, now=STAMP))

    # the audit log (or Daniel himself) appends a root-level block in between
    client.tree.append(
        {"uuid": "audit-1", "content": "18:20 [[byAgent]] wrote [[byAgent/brief]]", "children": []}
    )

    asyncio.run(add_journal_note(cfg, client, "вечер", "dynamo", AGENT,
                                 now=STAMP.replace(hour=21)))

    assert _shape(client.tree) == [
        ("#_worklog", [("#_work/dynamo", [(f"17:50 {SIG} утро", [])])]),
        ("18:20 [[byAgent]] wrote [[byAgent/brief]]", []),
        ("#_worklog", [("#_work/dynamo", [(f"21:50 {SIG} вечер", [])])]),
    ]


def test_a_later_note_anchors_on_the_previous_one(tmp_path: Path) -> None:
    """Order must not depend on how Logseq positions a `sibling: false` insert."""
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    asyncio.run(add_journal_note(cfg, client, "first", "dynamo", AGENT, now=STAMP))
    first_uuid = client.tree[0]["children"][0]["children"][0]["uuid"]

    asyncio.run(add_journal_note(cfg, client, "second", "dynamo", AGENT,
                                 now=STAMP.replace(minute=55)))
    target, content, opts = [c for c in client.calls if c[0] == "logseq.Editor.insertBlock"][-1][1]

    assert content == f"17:55 {SIG} second"
    assert target == first_uuid and opts["sibling"] is True


def test_list_projects_and_agents_use_their_namespaces(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    assert asyncio.run(list_projects(cfg, client)) == ["dynamo", "itquick"]
    assert asyncio.run(list_agents(cfg, client)) == [
        "claude/logseq-factory-admin", "claude/work-scout", "codex/macbook",
    ]
    queries = [c[1][0] for c in client.calls if c[0] == "logseq.DB.datascriptQuery"]
    assert '"_work/"' in queries[0]          # EDN-quoted prefix, not a bare symbol
    assert '"_agents/"' in queries[1] and ":block/file" in queries[1]


# ---------------------------------------------------------------------------
# Agents across runtimes
# ---------------------------------------------------------------------------


def test_select_agents_takes_runtime_slash_name_from_any_runtime() -> None:
    got = select_agents(AGENT_ROWS, "_agents", ["archive"])
    assert got == ["claude/ghost", "claude/logseq-factory-admin", "claude/work-scout", "codex/macbook"]


def test_an_excluded_runtime_is_retired_from_the_list() -> None:
    assert "archive/old-bot" in select_agents(AGENT_ROWS, "_agents", [])
    assert "archive/old-bot" not in select_agents(AGENT_ROWS, "_agents", ["archive"])


def test_resolve_agent_accepts_qualified_full_and_unique_bare_names() -> None:
    agents = ["claude/work-scout", "codex/macbook"]
    assert resolve_agent(agents, "claude/work-scout", "_agents") == "claude/work-scout"
    assert resolve_agent(agents, "_agents/codex/macbook", "_agents") == "codex/macbook"
    assert resolve_agent(agents, "Work-Scout", "_agents") == "claude/work-scout"


def test_resolve_agent_refuses_a_bare_name_two_runtimes_share() -> None:
    agents = ["claude/reviewer", "codex/reviewer"]
    with pytest.raises(WorklogError, match="ambiguous"):
        resolve_agent(agents, "reviewer", "_agents")
    assert resolve_agent(agents, "codex/reviewer", "_agents") == "codex/reviewer"


def test_a_codex_agent_signs_with_its_own_runtime(tmp_path: Path) -> None:
    client = _FakeClient()
    res = asyncio.run(add_journal_note(_cfg(tmp_path), client, "linear sync", "itquick",
                                       "codex/macbook", now=STAMP))
    assert res["agent"] == "codex/macbook"
    assert _shape(client.tree) == [
        ("#_worklog", [("#_work/itquick", [("17:50 #_agents/codex/macbook linear sync", [])])]),
    ]


def test_a_mentioned_but_never_created_agent_page_does_not_register(tmp_path: Path) -> None:
    """Writing [[_agents/claude/ghost]] in any note makes a reference-only page.
    Counting those would let an agent mint itself a name; only real pages count."""
    cfg = _cfg(tmp_path)
    client = _FakeClient()
    assert "claude/ghost" not in asyncio.run(list_agents(cfg, client))
    with pytest.raises(WorklogError, match="unknown agent"):
        asyncio.run(add_journal_note(cfg, client, "x", "dynamo", "claude/ghost", now=STAMP))
    assert client.writes() == []


def test_tag_ref_brackets_a_name_with_a_space() -> None:
    assert tag_ref("_agents/claude/work-scout") == "#_agents/claude/work-scout"
    assert tag_ref("_agents/claude/my agent") == "#[[_agents/claude/my agent]]"
