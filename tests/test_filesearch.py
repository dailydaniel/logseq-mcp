"""Tests for the pure parts of file-backed search (no ripgrep needed)."""

from __future__ import annotations

from mcp_server_logseq.filesearch import build_matcher, decode_candidate


def test_decode_page_namespace() -> None:
    assert decode_candidate("/graph/notes/byAgent___test___hello.md", "/graph") == (
        "page",
        "byAgent/test/hello",
    )
    assert decode_candidate("/graph/notes/Frisbee.md", "/graph") == ("page", "Frisbee")


def test_decode_journal() -> None:
    assert decode_candidate("/graph/journals/2026_02_25.md", "/graph") == ("journal", 20260225)
    # non-date file under journals is skipped
    assert decode_candidate("/graph/journals/notes.md", "/graph") is None


def test_decode_percent_encoding() -> None:
    assert decode_candidate("/graph/notes/foo%3Abar.md", "/graph") == ("page", "foo:bar")


def test_build_matcher() -> None:
    assert build_matcher("apple", regex=False, case_sensitive=False)("an Apple a day")
    assert not build_matcher("apple", regex=False, case_sensitive=True)("an Apple")
    assert build_matcher(r"a\d+", regex=True, case_sensitive=False)("a42")
    assert not build_matcher("x", regex=False, case_sensitive=False)("")
