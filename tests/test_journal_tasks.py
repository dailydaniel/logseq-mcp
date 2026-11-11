"""Tests for add_journal_tasks: new TODOs under a block in the user's journal.

The channel writes outside the agent namespace, so its checks are the point:
journal pages only, configured tag subtrees only (never archive), no duplicate of
an open task, append-only after the parent's last child, and a read-back. A fake
client stands in for Logseq; no live graph needed.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from mcp_server_logseq import writes
from mcp_server_logseq.config import load_config
from mcp_server_logseq.journal_tasks import (
    JournalTaskError, add_journal_tasks, match_roots, render_task, task_key,
)

PARENT = "6af1b6f2-350e-41ad-a5e0-ce37e3a121dd"
KID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _no_settle_delay(monkeypatch):
    monkeypatch.setattr(writes, "VERIFY_DELAY", 0)


def _cfg(tags=("_study", "_work")):
    cfg = load_config(None)
    cfg.tasks.journal_tags = list(tags)
    return cfg


class FakeClient:
    """A parent block on a journal page, its path-refs, and the open tasks."""

    def __init__(self, path_refs=("nov 9th, 2026", "_week_plan", "_study"),
                 journal_day=20261109, children=(KID,), open_tasks=(), drop=0,
                 exists=True):
        self.calls: list[tuple] = []
        self.path_refs = list(path_refs)
        self.journal_day = journal_day
        self.children = list(children)
        self.open_tasks = list(open_tasks)   # (uuid, content, path-ref name)
        self.drop = drop
        self.exists = exists

    async def call(self, method, args=None):
        self.calls.append((method, args))
        if method == "logseq.DB.datascriptQuery":
            q = args[0]
            if ":block/journal-day" in q:
                return [["Nov 9th, 2026", self.journal_day]] if self.exists else []
            if ":block/path-refs ?r" in q:
                return [[n] for n in self.path_refs]
            if ":block/marker" in q:
                root = re.search(r'starts-with\? \?n "([^"]+)"', q).group(1)
                return [[u, c, n] for u, c, n in self.open_tasks if n.startswith(root)]
            raise AssertionError(q)
        if method == "logseq.Editor.getBlock":
            return {"uuid": args[0], "children": [{"uuid": u} for u in self.children]}
        if method == "logseq.Editor.insertBatchBlock":
            target, batch, opts = args
            assert opts["keepUUID"] is True
            ids = [b["properties"]["id"] for b in batch]
            if self.drop:
                ids = ids[: -self.drop]
            if opts["sibling"]:
                i = self.children.index(target)
                self.children[i + 1: i + 1] = ids
            else:
                assert target == PARENT
                self.children[0:0] = ids
            return None
        raise AssertionError(method)


def _add(client, tasks, parent=PARENT, cfg=None):
    return asyncio.run(add_journal_tasks(cfg or _cfg(), client, parent, tasks))


def test_tasks_land_after_the_last_child_in_order() -> None:
    client = FakeClient()
    res = _add(client, [
        {"title": "MDP и уравнение Беллмана", "priority": "b", "page": "byAgent/learn/rl/mdp"},
        "value и policy iteration",
    ])
    assert [a["content"] for a in res["added"]] == [
        "TODO [#B] [[byAgent/learn/rl/mdp]] MDP и уравнение Беллмана",
        "TODO value и policy iteration",
    ]
    assert client.children == [KID] + [a["uuid"] for a in res["added"]]
    assert res["page"] == "Nov 9th, 2026" and res["tags"] == ["_study"]
    assert res["duplicates"] == []
    batches = [a for m, a in client.calls if m == "logseq.Editor.insertBatchBlock"]
    assert len(batches) == 1 and batches[0][0] == KID and batches[0][2]["sibling"] is True


def test_a_parent_without_children_gets_them_as_children() -> None:
    client = FakeClient(children=())
    res = _add(client, ["one", "two"])
    assert client.children == [a["uuid"] for a in res["added"]]


def test_an_open_duplicate_is_skipped_and_reported() -> None:
    client = FakeClient(open_tasks=[
        ("aaaa", "TODO [#A] [[RL]] MDP basics\nid:: aaaa", "_study"),
        ("bbbb", "TODO [[Evoca]] crypt", "_work/itquick"),
    ])
    res = _add(client, [
        {"title": "mdp  BASICS", "page": "rl", "priority": "C"},
        {"title": "crypt", "page": "myCouncil/app"},
        "new one",
        "New one",
    ])
    assert [a["content"] for a in res["added"]] == [
        "TODO [[myCouncil/app]] crypt", "TODO new one",
    ]
    assert res["duplicates"][0] == {"title": "mdp BASICS", "existing_uuid": "aaaa"}
    assert res["duplicates"][1]["reason"] == "repeated in this call"


def test_all_duplicates_write_nothing() -> None:
    client = FakeClient(open_tasks=[("aaaa", "TODO x", "_study")])
    res = _add(client, ["x"])
    assert res["added"] == []
    assert not any(m == "logseq.Editor.insertBatchBlock" for m, _ in client.calls)


def test_work_projects_count_but_archive_is_refused() -> None:
    res = _add(FakeClient(path_refs=("nov 9th, 2026", "_work", "_work/dynamo")), ["t"])
    assert res["tags"] == ["_work"]
    client = FakeClient(path_refs=("_work", "_work/archive", "_work/archive/frisbee"))
    with pytest.raises(JournalTaskError, match="archived"):
        _add(client, ["t"])
    assert not any(m == "logseq.Editor.insertBatchBlock" for m, _ in client.calls)


@pytest.mark.parametrize("client, msg", [
    (FakeClient(path_refs=("nov 9th, 2026", "_productivity")), "not under #_study, #_work"),
    (FakeClient(journal_day=0), "not a journal page"),
    (FakeClient(exists=False), "not found"),
])
def test_parents_outside_the_contract_are_refused(client, msg) -> None:
    with pytest.raises(JournalTaskError, match=msg):
        _add(client, ["t"])
    assert not any(m == "logseq.Editor.insertBatchBlock" for m, _ in client.calls)


@pytest.mark.parametrize("tasks, msg", [
    ([], "non-empty list"),
    (["TODO buy milk"], "task marker"),
    ([{"title": "  "}], "non-empty title"),
    ([{"title": "x", "priority": "D"}], "A, B or C"),
    ([{"title": "x", "page": "a]] [[b"}], "plain page name"),
    ([42], "title string"),
    ([f"t{i}" for i in range(51)], "at most 50"),
])
def test_bad_input_is_refused_before_any_call(tasks, msg) -> None:
    client = FakeClient()
    with pytest.raises(JournalTaskError, match=msg):
        _add(client, tasks)
    assert client.calls == []


def test_parent_must_be_a_uuid_and_may_be_a_ref() -> None:
    with pytest.raises(JournalTaskError, match="block uuid"):
        _add(FakeClient(), ["t"], parent="Nov 9th, 2026")
    assert _add(FakeClient(), ["t"], parent=f"(({PARENT}))")["parent"] == PARENT


def test_disabled_without_journal_tags() -> None:
    with pytest.raises(JournalTaskError, match="disabled"):
        _add(FakeClient(), ["t"], cfg=_cfg(tags=()))


def test_a_task_that_did_not_land_is_an_error() -> None:
    with pytest.raises(JournalTaskError, match="1 of 2 tasks are not under"):
        _add(FakeClient(drop=1), ["a", "b"])


def test_pure_helpers() -> None:
    assert task_key("LATER [#A] #[[RL]]  MDP\nid:: x\n:LOGBOOK:\nCLOCK: x\n:END:") == "rl mdp"
    assert render_task({"title": "x", "page": "[[RL]]"}) == ("x", "TODO [[RL]] x")
    assert match_roots(["_study/tasks"], ["_study"]) == ["_study"]
    assert match_roots(["_studying"], ["_study", "_studying"]) == ["_studying"]
