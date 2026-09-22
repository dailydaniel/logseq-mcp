"""Journal-page primitives shared by the channels that write to the journal.

Both the audit log and the worklog need "today's journal page, creating it if
Logseq has not materialized it yet". The page is located by `:block/journal-day`
(an int `YYYYMMDD`) rather than by title, so a graph using a custom title format
still resolves; the default title is only used when the page must be created.
"""

from __future__ import annotations

import datetime

from .client import LogseqClient


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def default_journal_title(d: datetime.date) -> str:
    """Logseq's default journal title format, e.g. 'Jun 9th, 2026'."""
    return f"{d.strftime('%b')} {_ordinal(d.day)}, {d.year}"


async def today_journal_page(client: LogseqClient, today: datetime.date) -> str:
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
