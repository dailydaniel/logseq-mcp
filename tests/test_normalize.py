"""Tests for the pure block normalizer."""

from __future__ import annotations

from mcp_server_logseq.normalize import (
    display_text,
    extract_refs,
    is_block_ref_only,
    normalize_block,
    parse_marker,
    rewrite_marker,
)


def test_parse_marker() -> None:
    assert parse_marker("TODO buy milk") == ("TODO", None)
    assert parse_marker("DONE [#A] buy milk") == ("DONE", "A")
    assert parse_marker("NOW [#b] x") == ("NOW", "B")
    assert parse_marker("just text") == (None, None)
    assert parse_marker("TODOsomething") == (None, None)  # marker needs a space
    assert parse_marker("") == (None, None)


def test_rewrite_marker() -> None:
    assert rewrite_marker("TODO [#A] buy milk", "DONE") == "DONE [#A] buy milk"
    assert rewrite_marker("TODO buy milk", "DONE") == "DONE buy milk"
    assert rewrite_marker("buy milk", "TODO") == "TODO buy milk"  # promote plain block
    assert rewrite_marker("NOW x", "LATER") == "LATER x"


def test_extract_refs() -> None:
    content = "TODO [[Project X]] and #urgent and #[[multi word]] see ((11111111-2222-3333-4444-555555555555))"
    page_refs, tags, block_refs = extract_refs(content)
    assert page_refs == ["Project X"]
    assert "urgent" in tags and "multi word" in tags
    assert block_refs == ["11111111-2222-3333-4444-555555555555"]


def test_priority_not_parsed_as_tag() -> None:
    page_refs, tags, _ = extract_refs("TODO [#A] do the thing #real")
    assert tags == ["real"]  # the '#A' from [#A] must not become a tag
    assert page_refs == []


def test_display_text_strips_props_and_logbook() -> None:
    content = (
        "DONE [[Frisbee Messenger/Tech Support Bot]] add sample tool\n"
        "id:: 6841c2f8-fddb-4c23-b044-3a9e96660e86\n"
        ":LOGBOOK:\nCLOCK: [2025-06-10 Tue 10:24:09]--[2025-06-10 Tue 11:15:09] =>  00:51:00\n:END:"
    )
    assert display_text(content) == "[[Frisbee Messenger/Tech Support Bot]] add sample tool"


def test_is_block_ref_only() -> None:
    assert is_block_ref_only("((683d4e0e-61c4-4f52-a2b4-5ee0eaf6951d))") is True
    assert is_block_ref_only("see ((683d4e0e-61c4-4f52-a2b4-5ee0eaf6951d))") is False
    assert is_block_ref_only("plain text") is False


def test_normalize_block_datascript_shape() -> None:
    # kebab-case keys, refs/page with names (datascriptQuery pull shape)
    block = {
        "uuid": "u1",
        "content": "DONE [[Frisbee Messenger/Tech Support Bot]] add tool\nid:: u1",
        "marker": "DONE",
        "page": {"name": "jun 3rd, 2025", "journal-day": 20250603, "original-name": "Jun 3rd, 2025"},
        "properties": {"id": "u1"},
    }
    n = normalize_block(block)
    assert n["uuid"] == "u1"
    assert n["status"] == "DONE"
    assert n["page"] == "Jun 3rd, 2025"
    assert n["journal_day"] == 20250603
    assert n["page_refs"] == ["Frisbee Messenger/Tech Support Bot"]
    assert n["properties"] == {}  # id excluded
    assert n["children"] == []


def test_normalize_block_tree_shape() -> None:
    # camelCase keys, nested children (getPageBlocksTree shape)
    block = {
        "uuid": "p1",
        "content": "TODO [#A] parent task",
        "children": [
            {"uuid": "c1", "content": "child", "children": []},
            {"uuid": "c2", "content": "((11111111-2222-3333-4444-555555555555))"},
        ],
    }
    n = normalize_block(block)
    assert n["status"] == "TODO" and n["priority"] == "A"
    assert len(n["children"]) == 2
    assert n["children"][0]["text"] == "child"
    assert n["children"][1]["is_block_ref"] is True
    assert n["children"][1]["block_refs"] == ["11111111-2222-3333-4444-555555555555"]
