"""Tests for how tool results go over the wire: compact, empty block fields dropped."""

from __future__ import annotations

import asyncio
import json

from mcp_server_logseq import server
from mcp_server_logseq.config import load_config


def test_a_block_loses_empty_fields_and_a_raw_content_equal_to_its_text() -> None:
    block = {
        "uuid": "u1", "text": "note", "raw_content": "note", "status": None,
        "page_refs": [], "properties": {}, "is_block_ref": False,
        "children": [{"uuid": "u2", "text": "x", "raw_content": "x\nid:: u2", "tags": []}],
    }
    assert server._prune(block) == {
        "uuid": "u1", "text": "note", "is_block_ref": False,
        "children": [{"uuid": "u2", "text": "x", "raw_content": "x\nid:: u2"}],
    }


def test_non_block_dicts_keep_their_empty_values() -> None:
    """`"blocks": []` is an answer ("the page has no blocks"), not noise."""
    assert server._prune({"page": "byAgent", "blocks": []}) == {"page": "byAgent", "blocks": []}
    assert server._prune({"task": None}) == {"task": None}


def test_the_wire_form_is_compact_json() -> None:
    wire = server._wire({"page": "p", "blocks": [{"uuid": "u", "text": "т", "status": None}]})
    assert wire == '{"page":"p","blocks":[{"uuid":"u","text":"т"}]}'
    assert server._wire("plain text") == "plain text"


def test_registered_tools_keep_their_name_signature_and_docs() -> None:
    server.app_config = load_config(None)

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    rp = tools["read_page"]
    assert "YYYY-MM-DD" in rp.inputSchema["properties"]["page"]["description"]
    assert rp.inputSchema["required"] == ["page"]
    assert "not an empty result" in (rp.description or "")
    assert len(tools) == 18


def test_a_called_tool_answers_in_compact_json() -> None:
    server.app_config = load_config(None)

    async def main():
        out = await server.mcp.call_tool("get_logseq_guide", {})
        blocks = out[0] if isinstance(out, tuple) else out
        return blocks[0].text

    text = asyncio.run(main())
    assert text.startswith('{"guide":"')
    assert json.loads(text)["guide"].startswith("# Logseq MCP")
