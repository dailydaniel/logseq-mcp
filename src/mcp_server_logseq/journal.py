"""Journal-page primitives shared by the channels that read and write the journal.

The audit log and the worklog need "today's journal page, creating it if Logseq
has not materialized it yet"; read_page resolves "the journal of 2026-09-23". A
page is located by `:block/journal-day` (an int `YYYYMMDD`) rather than by title,
so a graph using a custom title format still resolves — and no caller has to know
that the default title is `Sep 23rd, 2026`, not `Sep 23, 2026`. The default title
is only used when the page must be created.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from .client import LogseqClient

# A page argument that names a day rather than a page: `2026-09-23`.
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def default_journal_title(d: datetime.date) -> str:
    """Logseq's default journal title format, e.g. 'Jun 9th, 2026'."""
    return f"{d.strftime('%b')} {_ordinal(d.day)}, {d.year}"


async def journal_page(client: LogseqClient, day: datetime.date) -> Optional[str]:
    """The journal page for `day` (any title format), or None if the day has none."""
    jd = int(day.strftime("%Y%m%d"))
    dq = f"[:find ?name :where [?p :block/journal-day {jd}] [?p :block/original-name ?name]]"
    rows = await client.call("logseq.DB.datascriptQuery", [dq])
    return rows[0][0] if rows and rows[0] else None


async def today_journal_page(client: LogseqClient, today: datetime.date) -> str:
    """Resolve today's journal page name (any title format), creating if absent."""
    name = await journal_page(client, today)
    if name is not None:
        return name
    name = default_journal_title(today)
    await client.call(
        "logseq.Editor.createPage",
        [name, {}, {"journal": True, "redirect": False, "createFirstBlock": False}],
    )
    return name
