"""Tests for write_note(blocks=...) and edit_blocks.

`blocks` appends whole block trees: each top-level block is appended, its children
inserted under it in one insertBatchBlock call, and its uuid persisted as `id::`.
edit_blocks runs edit_block's checks per item and reports rejects instead of
failing the batch. A fake client records the Logseq calls; no live graph needed.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_logseq.config import load_config
from mcp_server_logseq.writes import WriteError, edit_blocks, write_note


def _cfg():
    cfg = load_config(None)
    cfg.write.allow_agents_write_any = False
    cfg.write.agent_write_prefix = "byAgent"
    return cfg


class FakeClient:
    """Enough of logseq.Editor.* for write_note and edit_block(s)."""

    def __init__(self, blocks: dict | None = None):
        self.calls: list[tuple] = []
        self._n = 0
        self.blocks = blocks or {}  # uuid -> {"content", "page"}

    async def call(self, method, args=None):
        self.calls.append((method, args))
        if method == "logseq.Editor.getPage":
            if isinstance(args[0], str) and args[0].startswith("byAgent/existing"):
                return {"original-name": args[0]}
            return {"original-name": "byAgent/cards/x"} if args[0] == 1 else None
        if method == "logseq.Editor.createPage":
            return {"original-name": args[0]}
        if method == "logseq.Editor.appendBlockInPage":
            self._n += 1
            return {"uuid": f"u{self._n}", "content": args[1]}
        if method == "logseq.Editor.getBlock":
            b = self.blocks.get(args[0])
            return dict(b, uuid=args[0]) if b else None
        if method == "logseq.Editor.updateBlock":
            self.blocks[args[0]]["content"] = args[1]
            return {"uuid": args[0]}
        return None

    def methods(self):
        return [m for m, _ in self.calls]


def _write(client, **kw):
    return asyncio.run(write_note(_cfg(), client, kw.pop("subpath", "cards/math"), **kw))


def test_block_trees_append_children_and_persist_ids() -> None:
    client = FakeClient()
    res = _write(client, blocks=[
        {"content": "What is SE? #card", "children": [
            "The sigma of an estimate",
            {"content": "Example", "children": ["SE = 0.049"]},
        ]},
        "A flat block",
    ])
    assert res["page"] == "byAgent/cards/math"
    assert res["block_uuids"] == ["u1", "u2"]

    batch = [a for m, a in client.calls if m == "logseq.Editor.insertBatchBlock"]
    assert batch == [["u1", [
        {"content": "The sigma of an estimate"},
        {"content": "Example", "children": [{"content": "SE = 0.049"}]},
    ], {"sibling": False}]]  # the flat block has no children -> no batch call

    ids = [a for m, a in client.calls if m == "logseq.Editor.upsertBlockProperty"]
    assert ids == [["u1", "id", "u1"], ["u2", "id", "u2"]]


def test_content_block_comes_before_trees() -> None:
    client = FakeClient()
    res = _write(client, content="intro", blocks=["q1"])
    appended = [a[1] for m, a in client.calls if m == "logseq.Editor.appendBlockInPage"]
    assert appended == ["intro", "q1"]
    assert res["appended_uuid"] == "u1" and res["block_uuids"] == ["u2"]


def test_no_block_uuids_key_without_blocks() -> None:
    res = _write(FakeClient(), content="note")
    assert "block_uuids" not in res


@pytest.mark.parametrize("blocks, msg", [
    ([], "non-empty list"),
    ([{"children": ["x"]}], "non-empty string content"),
    ([{"content": "  "}], "non-empty string content"),
    ([42], "string or an object"),
    ([{"content": "q", "children": "not a list"}], "must be a list"),
    ([{"content": "q", "children": ["TODO buy milk"]}], "task marker"),
])
def test_bad_trees_are_refused_before_any_write(blocks, msg) -> None:
    client = FakeClient()
    with pytest.raises(WriteError, match=msg):
        _write(client, blocks=blocks)
    assert client.calls == []  # validated whole, nothing written


def test_depth_and_size_limits() -> None:
    deep: object = "leaf"
    for _ in range(9):
        deep = {"content": "level", "children": [deep]}
    with pytest.raises(WriteError, match="levels deep"):
        _write(FakeClient(), blocks=[deep])
    with pytest.raises(WriteError, match="blocks per call"):
        _write(FakeClient(), blocks=[f"b{i}" for i in range(301)])


def _blocks():
    return {
        "a": {"content": "Q a #card", "page": {"original-name": "byAgent/cards/x"}},
        "b": {"content": "Q b #card", "page": {"original-name": "byAgent/cards/x"}},
        "c": {"content": "Q c #card", "page": {"original-name": "Daniel/notes"}},
    }


def test_edit_blocks_applies_matches_and_reports_rejects() -> None:
    client = FakeClient(_blocks())
    res = asyncio.run(edit_blocks(_cfg(), client, [
        {"uuid": "a", "old_content": "Q a #card", "new_content": "Q a #card\ncard-repeats:: 1"},
        {"uuid": "b", "old_content": "stale", "new_content": "Q b #card\ncard-repeats:: 1"},
        {"uuid": "c", "old_content": "Q c #card", "new_content": "hijack"},
        {"old_content": "x", "new_content": "y"},
    ]))
    assert [a["uuid"] for a in res["applied"]] == ["a"]
    reasons = {r["uuid"]: r["reason"] for r in res["rejected"]}
    assert "does not match" in reasons["b"]
    assert "outside the agent namespace" in reasons["c"]
    assert "needs a uuid" in reasons[None]
    assert client.blocks["a"]["content"].endswith("card-repeats:: 1")
    assert client.blocks["b"]["content"] == "Q b #card"   # untouched
    assert client.blocks["c"]["content"] == "Q c #card"   # outside byAgent, untouched


def test_edit_blocks_needs_a_list() -> None:
    with pytest.raises(WriteError, match="non-empty list"):
        asyncio.run(edit_blocks(_cfg(), FakeClient(), []))
