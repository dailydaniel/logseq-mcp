"""Tests for the audit log (pure parts + disabled no-op)."""

from __future__ import annotations

import asyncio
import datetime

from mcp_server_logseq.audit import default_journal_title, log_write
from mcp_server_logseq.config import load_config


def test_default_journal_title() -> None:
    assert default_journal_title(datetime.date(2026, 6, 9)) == "Jun 9th, 2026"
    assert default_journal_title(datetime.date(2026, 6, 1)) == "Jun 1st, 2026"
    assert default_journal_title(datetime.date(2026, 6, 2)) == "Jun 2nd, 2026"
    assert default_journal_title(datetime.date(2026, 6, 3)) == "Jun 3rd, 2026"
    assert default_journal_title(datetime.date(2026, 6, 11)) == "Jun 11th, 2026"  # teens -> th


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list = []

    async def call(self, method, args=None):
        self.calls.append((method, args))
        return None


def test_log_write_noop_when_disabled() -> None:
    cfg = load_config(None)  # audit_log is None by default
    client = _RecordingClient()
    asyncio.run(log_write(cfg, client, "wrote", "[[byAgent/x]]"))
    assert client.calls == []  # nothing written when audit is off
