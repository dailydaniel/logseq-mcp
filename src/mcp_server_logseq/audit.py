"""Optional audit log: append a provenance line to today's journal on writes.

Gated by [audit_log].enabled. Each successful write (channels 1-3) appends a new
root-level block to today's journal page, e.g. `09:15 [[byAgent]] wrote
[[byAgent/research/x]]`. Time is rendered by the server (not Logseq slash
commands). Reads/searches are never logged. Best-effort: a logging failure must
not fail the underlying operation.
"""

from __future__ import annotations

import datetime
from typing import Optional

from .client import LogseqClient
from .config import AppConfig


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def default_journal_title(d: datetime.date) -> str:
    """Logseq's default journal title format, e.g. 'Jun 9th, 2026'."""
    return f"{d.strftime('%b')} {_ordinal(d.day)}, {d.year}"


async def _today_journal_page(client: LogseqClient, today: datetime.date) -> str:
    """Resolve today's journal page name (any title format), creating if absent."""
    jd = int(today.strftime("%Y%m%d"))
    dq = f"[:find ?name :where [?p :block/journal-day {jd}] [?p :block/original-name ?name]]"
    rows = await client.call("logseq.DB.datascriptQuery", [dq])
    if rows and rows[0]:
        return rows[0][0]
    name = default_journal_title(today)
    await client.call(
        "logseq.Editor.createPage",
        [name, {}, {"journal": True, "redirect": False, "createFirstBlock": False}],
    )
    return name


async def log_write(
    config: AppConfig,
    client: LogseqClient,
    action: str,
    ref: str,
    today: Optional[datetime.date] = None,
) -> None:
    if not (config.audit_log and config.audit_log.enabled):
        return
    try:
        now = datetime.datetime.now()
        page = await _today_journal_page(client, today or now.date())
        agent = config.write.agent_write_prefix
        line = f"{now:%H:%M} [[{agent}]] {action} {ref}"
        await client.call("logseq.Editor.appendBlockInPage", [page, line])
    except Exception:  # pragma: no cover - best-effort, never raises
        pass
