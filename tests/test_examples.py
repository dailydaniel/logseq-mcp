"""The shipped examples/config.toml must always load and compile cleanly."""

from __future__ import annotations

from pathlib import Path

from mcp_server_logseq.config import load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "config.toml"


def test_example_config_loads() -> None:
    cfg = load_config(EXAMPLE)
    assert set(cfg.queries) == {"week_plan", "frisbee_tasks", "now"}

    week = cfg.queries["week_plan"]
    assert week.kind == "datalog"
    assert week.register_as_tool is True
    assert "_week_plan" in (week.rules or "")

    frisbee = cfg.queries["frisbee_tasks"]
    assert frisbee.kind == "datalog"
    assert "descendant" in (frisbee.rules or "")  # pulled from shared rules file

    now = cfg.queries["now"]
    assert now.kind == "simple"
