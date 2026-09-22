"""Optional audit log: append a provenance line to today's journal on writes.

Gated by [audit_log].enabled. Each successful write through the agent-namespace
and task-status channels appends a new root-level block to today's journal page,
e.g. `09:15 [[byAgent]] wrote [[byAgent/research/x]]`. Time is rendered by the
server (not Logseq slash commands). Reads/searches are never logged, and neither
is the worklog channel — it already writes a visible, timestamped journal entry,
so auditing it would only duplicate that line. Best-effort: a logging failure
must not fail the underlying operation.
"""

from __future__ import annotations

import datetime
from typing import Optional

from .client import LogseqClient
from .config import AppConfig

# Re-exported: the journal primitives used to live here, and callers (including
# tests) still import `default_journal_title` from this module.
from .journal import default_journal_title, today_journal_page  # noqa: F401

__all__ = ["default_journal_title", "log_write"]


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
        page = await today_journal_page(client, today or now.date())
        agent = config.write.agent_write_prefix
        line = f"{now:%H:%M} [[{agent}]] {action} {ref}"
        await client.call("logseq.Editor.appendBlockInPage", [page, line])
    except Exception:  # pragma: no cover - best-effort, never raises
        pass
