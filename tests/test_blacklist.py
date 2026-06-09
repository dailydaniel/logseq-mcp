"""Tests for the blacklist filter."""

from __future__ import annotations

from mcp_server_logseq.blacklist import Blacklist, canon_page_name


def test_canon() -> None:
    assert canon_page_name("ApiKey") == "apikey"
    assert canon_page_name(" /Secret/Foo/ ") == "secret/foo"


def test_page_exclusion_and_subpages() -> None:
    bl = Blacklist(["apikey", "secret"])
    assert bl.is_page_excluded("apikey") is True
    assert bl.is_page_excluded("APIKEY") is True
    assert bl.is_page_excluded("apikey/openai") is True  # subpage
    assert bl.is_page_excluded("apikeys") is False  # not a subpage
    assert bl.is_page_excluded("public") is False


def test_inactive_blacklist_is_passthrough() -> None:
    bl = Blacklist([])
    blocks = [{"uuid": "a", "page_refs": ["apikey"], "children": []}]
    assert bl.filter_blocks(blocks) is blocks


def test_redacts_block_referencing_excluded_page() -> None:
    bl = Blacklist(["apikey"])
    blocks = [
        {"uuid": "ok", "text": "fine", "page_refs": [], "tags": [], "children": [
            {"uuid": "leak", "text": "token is X", "page_refs": ["apikey"], "tags": [], "children": []},
        ]},
        {"uuid": "leak2", "text": "secret", "page_refs": [], "tags": ["apikey"], "children": [
            {"uuid": "child", "text": "nested secret", "page_refs": [], "tags": [], "children": []},
        ]},
    ]
    out = bl.filter_blocks(blocks)
    # first block kept, its leaking child redacted
    assert out[0]["uuid"] == "ok"
    assert out[0]["children"][0]["redacted"] is True
    assert out[0]["children"][0]["text"] == "<excluded block>"
    # second block redacted via tag, subtree collapsed
    assert out[1]["redacted"] is True
    assert out[1]["children"] == []
