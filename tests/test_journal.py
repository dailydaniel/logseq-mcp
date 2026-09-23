"""Tests for journal lookup by date and read_page's page resolution."""

from __future__ import annotations

import asyncio
import datetime

import pytest

from mcp_server_logseq import server
from mcp_server_logseq.config import load_config
from mcp_server_logseq.journal import journal_page

JOURNALS = {20260923: "Sep 23rd, 2026"}
PAGES = {"sep 23rd, 2026", "byagent", "_work/dynamo"}


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list = []

    async def call(self, method, args=None):
        self.calls.append((method, args))
        if method == "logseq.DB.datascriptQuery":
            day = int(args[0].split(":block/journal-day ")[1].split("]")[0])
            return [[JOURNALS[day]]] if day in JOURNALS else []
        if method == "logseq.Editor.getPage":
            return {"name": args[0].lower()} if args[0].lower() in PAGES else None
        if method == "logseq.Editor.getPageBlocksTree":
            return []
        return None


@pytest.fixture
def client(monkeypatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", fake)
    monkeypatch.setattr(server, "app_config", load_config(None))
    return fake


def test_journal_page_finds_the_day_by_journal_day(client: _FakeClient) -> None:
    assert asyncio.run(journal_page(client, datetime.date(2026, 9, 23))) == "Sep 23rd, 2026"
    assert asyncio.run(journal_page(client, datetime.date(2026, 9, 24))) is None


def test_a_date_reads_that_days_journal(client: _FakeClient) -> None:
    res = asyncio.run(server.read_page("2026-09-23"))
    assert res["page"] == "Sep 23rd, 2026"
    assert ("logseq.Editor.getPageBlocksTree", ["Sep 23rd, 2026"]) in client.calls


def test_a_day_without_a_journal_is_an_error(client: _FakeClient) -> None:
    with pytest.raises(ValueError, match="no journal for 2026-09-24"):
        asyncio.run(server.read_page("2026-09-24"))


def test_an_impossible_date_is_an_error(client: _FakeClient) -> None:
    with pytest.raises(ValueError, match="not a valid date"):
        asyncio.run(server.read_page("2026-13-40"))


def test_a_missing_page_is_an_error_not_an_empty_page(client: _FakeClient) -> None:
    """The graph assistant read `Sep 23, 2026` (no ordinal), got [] and reported an
    empty journal. A page that does not exist must say so."""
    with pytest.raises(ValueError, match="not found .* YYYY-MM-DD"):
        asyncio.run(server.read_page("Sep 23, 2026"))
    assert not [c for c in client.calls if c[0] == "logseq.Editor.getPageBlocksTree"]


def test_an_existing_page_without_blocks_still_reads_empty(client: _FakeClient) -> None:
    """A namespace parent has no blocks of its own; that is not an error."""
    assert asyncio.run(server.read_page("byAgent")) == {"page": "byAgent", "blocks": []}
