"""render_guide must fill {prefix} without choking on literal braces in the text.

Regression: 0.3.0 added a `{"project": "[[X]]"}` example and rendered via
str.format(), which parsed the literal braces as a format field -> KeyError.
"""

from __future__ import annotations

from mcp_server_logseq.guide import render_guide


def test_render_guide_does_not_raise_and_fills_prefix() -> None:
    out = render_guide("byAgent")
    assert "byAgent/" in out
    assert "{prefix}" not in out  # every placeholder substituted


def test_render_guide_preserves_literal_braces() -> None:
    # a literal {"project": ...} example must survive verbatim (the 0.3.0 regression)
    out = render_guide("byAgent")
    assert '{"project"' in out


def test_render_guide_custom_prefix() -> None:
    out = render_guide("agentX")
    assert "agentX/" in out


def test_render_guide_empty_prefix_defaults() -> None:
    out = render_guide("")
    assert "byAgent/" in out
