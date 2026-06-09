"""Tests for the pure parts of writes/queries (no Logseq needed)."""

from __future__ import annotations

import datetime

import pytest

from mcp_server_logseq.config import load_config
from mcp_server_logseq.queries import _encode_inputs, resolve_input_token
from mcp_server_logseq.writes import WriteError, resolve_agent_path


def _cfg(allow_any: bool = False, prefix: str = "byAgent"):
    cfg = load_config(None)
    cfg.write.allow_agents_write_any = allow_any
    cfg.write.agent_write_prefix = prefix
    return cfg


def test_resolve_agent_path_forces_prefix() -> None:
    cfg = _cfg()
    assert resolve_agent_path(cfg, "research/x") == "byAgent/research/x"
    assert resolve_agent_path(cfg, "/research/x/") == "byAgent/research/x"
    assert resolve_agent_path(cfg, "byAgent/y") == "byAgent/y"  # no double prefix
    assert resolve_agent_path(cfg, "byAgent") == "byAgent"


def test_resolve_agent_path_rejects_traversal() -> None:
    cfg = _cfg()
    with pytest.raises(WriteError):
        resolve_agent_path(cfg, "../etc/passwd")
    with pytest.raises(WriteError):
        resolve_agent_path(cfg, "")


def test_resolve_agent_path_allow_any() -> None:
    cfg = _cfg(allow_any=True)
    assert resolve_agent_path(cfg, "anywhere/x") == "anywhere/x"


def test_resolve_input_token() -> None:
    today = int(datetime.date.today().strftime("%Y%m%d"))
    assert resolve_input_token("@today-journal-day") == today
    assert resolve_input_token("@today-minus:0") == today
    assert isinstance(resolve_input_token("@now-iso"), str)
    assert resolve_input_token("plain") == "plain"
    assert resolve_input_token(20250603) == 20250603


def test_encode_inputs_edn() -> None:
    # strings get quoted (so Logseq's edn-read yields a string, not a symbol)
    assert _encode_inputs(["DONE", 5]) == ['"DONE"', "5"]
