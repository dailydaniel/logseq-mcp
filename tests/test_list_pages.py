"""Tests for list_pages' pure row-filtering helper."""

from __future__ import annotations

from mcp_server_logseq.blacklist import Blacklist
from mcp_server_logseq.server import _filter_page_rows

ROWS = [
    ["byagent", "byAgent"],
    ["byagent/audit", "byAgent/audit"],
    ["byagent/audit/demo", "byAgent/audit/demo"],
    ["byagent/test", "byAgent/test"],
    ["byagent/test/hello", "byAgent/test/hello"],
    ["byagent/apikey", "byAgent/apikey"],
]

NONE = Blacklist([])


def test_returns_original_case_names_sorted() -> None:
    out = _filter_page_rows(ROWS, "byagent", None, NONE)
    names = [p["name"] for p in out]
    assert names == [
        "byAgent/apikey",
        "byAgent/audit",
        "byAgent/audit/demo",
        "byAgent/test",
        "byAgent/test/hello",
    ]
    # the bare prefix page itself is not a descendant of "byagent/"
    assert "byAgent" not in names


def test_depth_limits_namespace_levels() -> None:
    out = _filter_page_rows(ROWS, "byagent", 1, NONE)
    assert [p["name"] for p in out] == ["byAgent/apikey", "byAgent/audit", "byAgent/test"]


def test_blacklist_excludes_pages() -> None:
    out = _filter_page_rows(ROWS, "byagent", None, Blacklist(["byagent/apikey"]))
    assert "byAgent/apikey" not in [p["name"] for p in out]


def test_empty_prefix_counts_absolute_depth() -> None:
    out = _filter_page_rows(ROWS, "", 1, NONE)
    # only top-level (1-segment) names survive
    assert [p["name"] for p in out] == ["byAgent"]


def test_malformed_rows_skipped() -> None:
    rows = [["byagent/x", "byAgent/x"], ["bad"], [1, 2], "nope", []]
    out = _filter_page_rows(rows, "byagent", None, NONE)
    assert [p["name"] for p in out] == ["byAgent/x"]
