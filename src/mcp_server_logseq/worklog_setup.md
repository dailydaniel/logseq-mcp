## Logseq worklog

- Log work to the Logseq journal with `add_journal_note`: `work` = `<project>`,
  `agent` = `<runtime>/<name>`. Never sign with another agent's name.
- One entry per notable event (started, tested, finished), not per step or tool
  call, a few words each, in the language the rest of the journal uses. A key
  result may go in ("backtest: logloss 0.97 → 0.95"); discussion and reasoning go
  to the chat.
- No time and no task marker in the text: the server stamps the time and rejects
  a leading TODO/DOING.
- Link with `[[...]]` only pages that already exist: a link to a missing page
  creates an empty one. Don't repeat the project or the task, the entry is
  already nested under both.

### Which task to log under

1. At the start of a piece of work, read the agent page `{agents}/<runtime>/<name>`.
   Its `tags` include `{work}/<project>` and the project page, the page its tasks
   reference.
2. List the open tasks (`TODO`, `DOING`) that reference the project page:
   `find_tasks` with `tag` (`under_tag` if tasks sit under a block that references
   it). Show them; the user picks the one we work on. Don't guess and don't create
   tasks.
3. If the chosen task is `TODO`, set it to `DOING` (`set_task_status`). Set `DONE`
   only after the user explicitly confirms the task is finished.
4. Log every entry under that task (`task` = its uuid). One task per session; if
   the work moves to another task, ask first. The choice holds for the whole
   session, also after the context is compacted or the session resumed: don't
   ask again.
5. Status changes are already recorded: the marker itself, plus an audit line in
   the journal when the server's audit log is on. Don't log them, and don't add a
   "started" entry for a TODO → DOING switch.

If the Logseq server is unreachable, keep working and say so in the chat instead
of retrying.
