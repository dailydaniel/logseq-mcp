"""Tests for the pure task-block assembler."""

from __future__ import annotations

from mcp_server_logseq.normalize import parse_marker
from mcp_server_logseq.writes import build_task_content


def _build(**kw):
    base = dict(
        title="ship it", marker="TODO", priority=None, agent_ref="byAgent/hermes",
        base_tag="task", tags=None, project=None, plan_page=None, blocks_on=None,
    )
    base.update(kw)
    return build_task_content(**base)


def test_minimal_has_marker_tag_and_agent_link() -> None:
    out = _build()
    assert out == "TODO #task ship it [[byAgent/hermes]]"
    # the leading marker is real so Logseq parses it as a task
    assert parse_marker(out)[0] == "TODO"


def test_full_block_order() -> None:
    out = _build(
        priority="A", tags=["plan"], project="Frisbee/Tech Support Bot",
        plan_page="byAgent/hermes/proj/plan", blocks_on="6a27-uuid",
    )
    assert out == (
        "TODO [#A] #task #plan ship it [[byAgent/hermes]] "
        "[[Frisbee/Tech Support Bot]] [[byAgent/hermes/proj/plan]] ((6a27-uuid))"
    )


def test_multiword_tag_uses_bracket_form() -> None:
    out = _build(tags=["high risk"])
    assert "#[[high risk]]" in out
    assert "#task" in out


def test_strips_stray_brackets_and_parens() -> None:
    out = _build(project="[[Proj]]", blocks_on="((abc))")
    assert "[[Proj]]" in out and "[[[[" not in out
    assert "((abc))" in out and "((((" not in out
