"""Tests for config loading (TOML + EDN queries)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_server_logseq.config import ConfigError, load_config


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- defaults -------------------------------------------------------------


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.toml")
    assert cfg.read.resolve_depth == 2
    assert cfg.write.allow_agents_write_any is False
    assert cfg.write.agent_write_prefix == "agent"
    assert cfg.search.files_path == ""
    assert cfg.blacklist.pages == []
    assert cfg.tasks.allow_status_change is False
    assert cfg.reading_list is None  # optional sections off when absent
    assert cfg.audit_log is None
    assert cfg.queries == {}


def test_none_path_uses_defaults() -> None:
    cfg = load_config(None)
    assert cfg.read.resolve_depth == 2
    assert cfg.queries == {}


# --- sections -------------------------------------------------------------


def test_full_sections(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "config.toml",
        """
        [read]
        resolve_depth = 0

        [write]
        allow_agents_write_any = true
        agent_write_prefix = "bot"

        [search]
        files_path = "/graph"

        [blacklist]
        pages = ["api-key", "secret"]

        [tasks]
        allow_status_change = true

        [reading_list]
        namespace = "read"

        [audit_log]
        enabled = true
        """,
    )
    cfg = load_config(p)
    assert cfg.read.resolve_depth == 0
    assert cfg.write.allow_agents_write_any is True
    assert cfg.write.agent_write_prefix == "bot"
    assert cfg.search.files_path == "/graph"
    assert cfg.blacklist.pages == ["api-key", "secret"]
    assert cfg.tasks.allow_status_change is True
    assert cfg.reading_list is not None and cfg.reading_list.namespace == "read"
    assert cfg.audit_log is not None and cfg.audit_log.enabled is True


def test_empty_optional_section_enables_defaults(tmp_path: Path) -> None:
    p = _write(tmp_path, "config.toml", "[reading_list]\n")
    cfg = load_config(p)
    assert cfg.reading_list is not None
    assert cfg.reading_list.namespace == "read"


def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "config.toml", "[read]\nresolve_dpeth = 3\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_toml(tmp_path: Path) -> None:
    p = _write(tmp_path, "config.toml", "[read\n")
    with pytest.raises(ConfigError):
        load_config(p)


# --- queries --------------------------------------------------------------


def test_inline_simple_query(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "config.toml",
        """
        [queries.quick]
        query = "(and (todo NOW) (priority A))"
        """,
    )
    cfg = load_config(p)
    q = cfg.queries["quick"]
    assert q.kind == "simple"
    assert q.query == "(and (todo NOW) (priority A))"
    assert q.rules is None and q.inputs == []


def test_inline_datalog_vector(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "config.toml",
        """
        [queries.tasks]
        query = "[:find (pull ?b [*]) :where [?b :block/marker _]]"
        """,
    )
    cfg = load_config(p)
    q = cfg.queries["tasks"]
    assert q.kind == "datalog"
    assert q.query.startswith("[:find")


def test_query_from_edn_map_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "queries/week_plan.edn",
        """
        {:query [:find (pull ?b [*]) :in $ % :where (item ?b)]
         :rules [[(item ?b) [?b :block/refs ?p] [?p :block/name "_week_plan"]]]
         :inputs ["@today-journal-day"]}
        """,
    )
    p = _write(
        tmp_path,
        "config.toml",
        """
        [queries.week_plan]
        description = "Week plan"
        file = "queries/week_plan.edn"
        register_as_tool = true
        resolve_block_refs = true
        """,
    )
    cfg = load_config(p)
    q = cfg.queries["week_plan"]
    assert q.kind == "datalog"
    assert q.description == "Week plan"
    assert q.register_as_tool is True
    assert q.resolve_block_refs is True
    assert "(item ?b)" in q.query
    assert q.rules is not None and "_week_plan" in q.rules
    assert q.inputs == ["@today-journal-day"]


def test_shared_rules_file(tmp_path: Path) -> None:
    _write(tmp_path, "rules/common.edn", "[[(item ?b) [?b :a ?c]]]")
    _write(tmp_path, "queries/q.edn", "{:query [:find ?b :in $ % :where (item ?b)]}")
    p = _write(
        tmp_path,
        "config.toml",
        """
        [queries.q]
        file = "queries/q.edn"
        rules = "rules/common.edn"
        """,
    )
    cfg = load_config(p)
    q = cfg.queries["q"]
    assert q.rules is not None and "(item ?b)" in q.rules


def test_query_requires_exactly_one_source(tmp_path: Path) -> None:
    both = _write(
        tmp_path,
        "config.toml",
        '[queries.x]\nfile = "a.edn"\nquery = "(todo NOW)"\n',
    )
    with pytest.raises(ConfigError):
        load_config(both)

    neither = _write(tmp_path, "config2.toml", "[queries.x]\ndescription = \"x\"\n")
    with pytest.raises(ConfigError):
        load_config(neither)


def test_missing_query_file(tmp_path: Path) -> None:
    p = _write(tmp_path, "config.toml", '[queries.x]\nfile = "missing.edn"\n')
    with pytest.raises(ConfigError):
        load_config(p)


def test_map_without_query_key_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "queries/bad.edn", "{:rules [[(r ?b) [?b :a ?c]]]}")
    p = _write(tmp_path, "config.toml", '[queries.bad]\nfile = "queries/bad.edn"\n')
    with pytest.raises(ConfigError):
        load_config(p)
