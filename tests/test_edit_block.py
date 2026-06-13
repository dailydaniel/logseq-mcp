"""Tests for edit_block: namespace confinement + read-before-write match guard.

A uuid can point anywhere in the graph, so edit_block must gate on the block's
page (unlike subpath writes, which resolve_agent_path confines). These use a fake
client so no live Logseq is needed.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_server_logseq.config import load_config
from mcp_server_logseq.writes import WriteError, edit_block


def _cfg(allow_any: bool = False, prefix: str = "byAgent"):
    cfg = load_config(None)
    cfg.write.allow_agents_write_any = allow_any
    cfg.write.agent_write_prefix = prefix
    return cfg


class FakeClient:
    """Dispatches the handful of logseq.Editor.* calls edit_block makes."""

    def __init__(self, *, content: str, page):
        self._block = {"uuid": "u1", "content": content, "page": page}
        self._pages = {1: {"original-name": "byAgent/claude-code/plans/x"},
                       2: {"original-name": "work/secret/notes"}}
        self.updated = None  # (uuid, new_content) if updateBlock was called

    async def call(self, method, args=None):
        if method == "logseq.Editor.getBlock":
            return self._block
        if method == "logseq.Editor.getPage":
            return self._pages.get(args[0])
        if method == "logseq.Editor.updateBlock":
            self.updated = (args[0], args[1])
            return {"uuid": args[0], "content": args[1]}
        return None


def _run(cfg, client, **kw):
    return asyncio.run(edit_block(cfg, client, "u1", kw["old"], kw["new"]))


def test_edits_block_inside_namespace() -> None:
    client = FakeClient(content="hello", page={"original-name": "byAgent/claude-code/plans/x"})
    res = _run(_cfg(), client, old="hello", new="hello world")
    assert client.updated == ("u1", "hello world")
    assert res["page"] == "byAgent/claude-code/plans/x"
    assert res["new_content"] == "hello world"


def test_refuses_block_outside_namespace() -> None:
    client = FakeClient(content="hello", page={"original-name": "work/secret/notes"})
    with pytest.raises(WriteError, match="outside the agent namespace"):
        _run(_cfg(), client, old="hello", new="bye")
    assert client.updated is None  # never wrote


def test_namespace_check_is_case_insensitive() -> None:
    client = FakeClient(content="x", page={"original-name": "ByAgent/Plans/Y"})
    res = _run(_cfg(), client, old="x", new="x2")
    assert client.updated == ("u1", "x2")
    assert res["page"] == "ByAgent/Plans/Y"


def test_resolves_page_by_id_when_only_id_present() -> None:
    # getBlock often returns page as {id}; the gate must getPage to learn the name.
    client_in = FakeClient(content="hi", page={"id": 1})   # -> byAgent/...
    assert _run(_cfg(), client_in, old="hi", new="hi!")["page"].startswith("byAgent/")
    client_out = FakeClient(content="hi", page={"id": 2})  # -> work/secret
    with pytest.raises(WriteError, match="outside the agent namespace"):
        _run(_cfg(), client_out, old="hi", new="hi!")
    assert client_out.updated is None


def test_match_guard_rejects_stale_old_content() -> None:
    client = FakeClient(content="current text", page={"original-name": "byAgent/x"})
    with pytest.raises(WriteError, match="does not match"):
        _run(_cfg(), client, old="what I think it says", new="new")
    assert client.updated is None  # mismatch must not write


def test_rejects_identical_old_and_new() -> None:
    client = FakeClient(content="same", page={"original-name": "byAgent/x"})
    with pytest.raises(WriteError, match="identical"):
        _run(_cfg(), client, old="same", new="same")
    assert client.updated is None


def test_allow_any_bypasses_namespace_gate() -> None:
    client = FakeClient(content="hello", page={"original-name": "work/secret/notes"})
    res = _run(_cfg(allow_any=True), client, old="hello", new="bye")
    assert client.updated == ("u1", "bye")
    assert res["page"] == "work/secret/notes"
