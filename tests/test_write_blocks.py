"""Tests for write_note(blocks=...) and edit_blocks.

`blocks` appends whole block trees in ONE insertBatchBlock (one Logseq transaction):
top-level blocks get uuids we mint, kept by Logseq and written as `id::`, and the
page is read back before success is reported. edit_blocks runs edit_block's checks
per item, reads every edit back, and reports rejects instead of failing the batch.
A fake client stands in for Logseq; no live graph needed.
"""

from __future__ import annotations

import asyncio
import uuid as uuidlib

import pytest

from mcp_server_logseq import writes
from mcp_server_logseq.config import load_config
from mcp_server_logseq.writes import WriteError, edit_blocks, write_note


@pytest.fixture(autouse=True)
def _no_settle_delay(monkeypatch):
    monkeypatch.setattr(writes, "VERIFY_DELAY", 0)


def _cfg():
    cfg = load_config(None)
    cfg.write.allow_agents_write_any = False
    cfg.write.agent_write_prefix = "byAgent"
    return cfg


class FakeClient:
    """Enough of logseq.Editor.* for write_note and edit_block(s)."""

    def __init__(self, blocks: dict | None = None, pages: dict | None = None,
                 drop_batch_after: int | None = None, drop_updates: set | None = None):
        self.calls: list[tuple] = []
        self.blocks = blocks or {}            # uuid -> {"content", "page"} (edit_block tests)
        self.pages = pages if pages is not None else {}   # page -> [top-level block dicts]
        self.drop_batch_after = drop_batch_after
        self.drop_updates = drop_updates or set()

    async def call(self, method, args=None):
        self.calls.append((method, args))
        if method == "logseq.Editor.getPage":
            if isinstance(args[0], str):
                return {"original-name": args[0]} if args[0] in self.pages else None
            return {"original-name": "byAgent/cards/x"} if args[0] == 1 else None
        if method == "logseq.Editor.createPage":
            self.pages.setdefault(args[0], [])
            return {"original-name": args[0]}
        if method == "logseq.Editor.appendBlockInPage":
            b = {"uuid": str(uuidlib.uuid4()), "content": args[1]}
            self.pages.setdefault(args[0], []).append(b)
            return b
        if method == "logseq.Editor.insertBatchBlock":
            anchor, batch, opts = args
            assert opts == {"sibling": True, "keepUUID": True}
            page = next(p for p, bs in self.pages.items() if any(b["uuid"] == anchor for b in bs))
            new = [{"uuid": item["properties"]["id"], "content": item["content"],
                    "children": item.get("children", [])} for item in batch]
            if self.drop_batch_after is not None:
                new = new[: self.drop_batch_after]
            idx = next(i for i, b in enumerate(self.pages[page]) if b["uuid"] == anchor)
            self.pages[page][idx + 1: idx + 1] = new
            return None
        if method == "logseq.Editor.removeBlock":
            for bs in self.pages.values():
                bs[:] = [b for b in bs if b["uuid"] != args[0]]
            return None
        if method == "logseq.Editor.getPageBlocksTree":
            return [dict(b) for b in self.pages.get(args[0], [])]
        if method == "logseq.Editor.getBlock":
            b = self.blocks.get(args[0])
            return dict(b, uuid=args[0]) if b else None
        if method == "logseq.Editor.updateBlock":
            if args[0] not in self.drop_updates:
                self.blocks[args[0]]["content"] = args[1]
            return {"uuid": args[0]}
        return None


def _write(client, **kw):
    return asyncio.run(write_note(_cfg(), client, kw.pop("subpath", "cards/math"), **kw))


def test_block_trees_go_in_one_batch_with_minted_ids() -> None:
    client = FakeClient()
    res = _write(client, blocks=[
        {"content": "What is SE? #card", "children": [
            "The sigma of an estimate",
            {"content": "Example", "children": ["SE = 0.049"]},
        ]},
        "A flat block",
    ])
    assert res["page"] == "byAgent/cards/math"
    ids = res["block_uuids"]
    assert len(ids) == 2 and all(uuidlib.UUID(u) for u in ids)

    batches = [a for m, a in client.calls if m == "logseq.Editor.insertBatchBlock"]
    assert len(batches) == 1                       # one transaction for everything
    assert batches[0][1] == [
        {"content": "What is SE? #card", "properties": {"id": ids[0]}, "children": [
            {"content": "The sigma of an estimate"},
            {"content": "Example", "children": [{"content": "SE = 0.049"}]},
        ]},
        {"content": "A flat block", "properties": {"id": ids[1]}},
    ]
    # the placeholder used as the anchor on an empty page is gone
    assert [b["uuid"] for b in client.pages["byAgent/cards/math"]] == ids


def test_appends_after_the_existing_blocks() -> None:
    client = FakeClient(pages={"byAgent/cards/math": [{"uuid": "old", "content": "Q0 #card"}]})
    res = _write(client, blocks=["Q1 #card"])
    assert [b["uuid"] for b in client.pages["byAgent/cards/math"]] == ["old"] + res["block_uuids"]
    assert not any(m == "logseq.Editor.removeBlock" for m, _ in client.calls)


def test_content_block_comes_before_trees() -> None:
    client = FakeClient()
    res = _write(client, content="intro", blocks=["q1"])
    page = client.pages["byAgent/cards/math"]
    assert [b["content"] for b in page] == ["intro", "q1"]
    assert res["appended_uuid"] == page[0]["uuid"] and res["block_uuids"] == [page[1]["uuid"]]


def test_a_dropped_block_is_an_error_not_a_silent_success() -> None:
    client = FakeClient(drop_batch_after=1)
    with pytest.raises(WriteError, match="1 of 2 blocks are not on"):
        _write(client, blocks=["q1", "q2"])


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
        _write(FakeClient(), blocks=[f"b{i}" for i in range(1001)])


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
    assert res["applied"] == [{"uuid": "a", "page": "byAgent/cards/x"}]
    reasons = {r["uuid"]: r["reason"] for r in res["rejected"]}
    assert "does not match" in reasons["b"]
    assert "outside the agent namespace" in reasons["c"]
    assert "needs a uuid" in reasons[None]
    assert client.blocks["a"]["content"].endswith("card-repeats:: 1")
    assert client.blocks["b"]["content"] == "Q b #card"   # untouched
    assert client.blocks["c"]["content"] == "Q c #card"   # outside byAgent, untouched


def test_edit_blocks_reports_an_edit_logseq_dropped() -> None:
    client = FakeClient(_blocks(), drop_updates={"b"})
    res = asyncio.run(edit_blocks(_cfg(), client, [
        {"uuid": "a", "old_content": "Q a #card", "new_content": "Q a #card\ncard-repeats:: 1"},
        {"uuid": "b", "old_content": "Q b #card", "new_content": "Q b #card\ncard-repeats:: 1"},
    ]))
    assert [a["uuid"] for a in res["applied"]] == ["a"]
    assert res["rejected"][0]["uuid"] == "b"
    assert "did not persist" in res["rejected"][0]["reason"]


def test_edit_blocks_needs_a_list() -> None:
    with pytest.raises(WriteError, match="non-empty list"):
        asyncio.run(edit_blocks(_cfg(), FakeClient(), []))
