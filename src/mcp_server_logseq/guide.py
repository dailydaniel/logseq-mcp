"""Authoritative, in-sync usage guide returned by the `get_logseq_guide` tool.

Co-located with the server so it stays correct as tool semantics evolve — agents
should fetch it instead of re-deriving Logseq/Datalog behaviour (which is easy to
get plausibly wrong). All facts below are verified against the live graph.
"""

from __future__ import annotations

GUIDE = """\
# Logseq MCP — query & write guide

This server (**dailydaniel/logseq-mcp**, file/Markdown "OG" Logseq) gives scoped
access to a Logseq graph: **read broadly, write only inside `{prefix}/`.**

## Tools
- `search(query, regex?, case_sensitive?, exclude_journals?, limit?)` — full-text
  (ripgrep). Fastest for "find notes about X".
- `list_pages(prefix?, depth?)` — list page names by namespace prefix (structure,
  NOT block content). Use to discover the children of a namespace.
- `read_page(page, depth?)` / `read_block(uuid, depth?)` — content of one page/block.
  For a journal pass the date, `read_page("2026-09-23")` — never guess its title
  (`Sep 23rd, 2026`). A page that does not exist is an error, not `[]`.
- `find_tasks(markers?, tag?, under_tag?, page?, priority?, scope?, agent?, limit?)`
  — task blocks. `scope`: `all` | `agent` (only agent-owned) | `human` (exclude them).
- `datascript_query(query, inputs?, rules?)` — raw Datalog (advanced).
- `custom_query` / `list_custom_queries` — saved queries.
- `write_note` / `set_page_properties` — write notes under `{prefix}/` only.
  `write_note(blocks=[...])` appends whole block trees (a block with its children).
- `create_task(...)` — the ONLY way to make a task (see Tasks below).
- `set_task_status(uuid, status)` — change a task's marker.
- `add_journal_tasks(parent, tasks)` — add tasks the USER approved to their journal,
  under an existing block. See "Tasks in the user's journal".
- `edit_block(uuid, old_content, new_content)` — replace ONE block's content
  in place (read it first; namespace-confined). See "Editing one block".
  `edit_blocks(edits=[...])` applies many such edits in one call.
- `list_work_projects()` / `list_agents()` / `add_journal_note(text, work, agent,
  task?)` — log what you did to TODAY's journal. Present only when `[worklog]` is
  enabled. See "Worklog". `get_worklog_setup_template()` — only when the user asks
  to put worklog instructions into CLAUDE.md / AGENTS.md.

## Block fields
A block comes with only the fields it actually has: a null, `[]` or `{}` field is
left out (no `status` = not a task, no `children` = a leaf), and `raw_content` is
present only when it differs from `text` — i.e. when the block has a task marker,
properties such as `id::`, or a logbook.

## Discovering what's in a namespace
A namespace parent page (e.g. `{prefix}`) usually has **no blocks of its own** —
its children are SEPARATE pages. `read_page("{prefix}")` returns `[]`; that does
NOT mean it is empty. Use `list_pages(prefix="{prefix}")` to see the child pages.

## Datalog facts (verified on the live graph)
- Page names are stored **lowercase** in `:block/name`; original casing lives in
  `:block/original-name`. `[?p :block/name "ByName"]` -> 0 results; lowercase it,
  or pull both.
- **Descendants at ANY depth = ONE clause**, no manual namespace walking:
  `[(clojure.string/starts-with? ?name "prefix/")]` on the lowercased `:block/name`.
  (`:block/namespace` is the DIRECT parent only — do not chain it N times.)
  Java interop `(.startsWith ...)` does NOT work — use `clojure.string/starts-with?`.
- `:block/marker` is an **UPPERCASE STRING** ("TODO"/"DOING"/"NOW"/"LATER"/"DONE"...),
  not a page. Match `[?b :block/marker "TODO"]`, not `[?m :block/name "todo"]`.
- `:block/journal-day` is an **INT `YYYYMMDD`** (e.g. 20260610). Use `[(= ?d 20260610)]`
  or numeric ranges.
- `:block/tags` (page-level `tags` property) is NOT the same relation as
  `:block/refs` (inline #tag / wiki-links inside a block).
- **Reads are GLOBAL** (whole graph). Only **writes** are confined to `{prefix}/`.
  A blacklist additionally redacts specific named pages.
- Datalog sandbox: `clojure.string/includes?`, `starts-with?`, `re-find`,
  `re-pattern` work; `clojure.string/lower-case` does NOT (use a `(?i)` regex).
  No nested function calls in one `:where` clause — bind intermediates separately.

## find_tasks notes
- `tag` / `under_tag` match task BLOCKS that reference the page via `:block/refs`.
  If no task references it, you get 0 — that is a correct answer, not a bug.
- `page` matches blocks DIRECTLY on that page, not its namespace descendants.
- `scope="agent"` returns only tasks owned by an agent (they reference `{prefix}/...`);
  `agent="hermes"` narrows to one. `scope="human"` excludes all agent tasks.

## Tasks — always via create_task (write_note refuses task markers)
`write_note` rejects any content that starts with a task marker, so the ONLY way to
make a task is `create_task`, which enforces the structure:

    create_task(title, agent, project?, marker="TODO", priority?, tags?,
                plan_page?, blocks_on?, on_page?)

- It produces a block like:
  `TODO [#A] #task <title> [[{prefix}/<agent>]] [[<project>]] [[<plan_page>]] ((<blocks_on>))`
- The block lives in the agent namespace (default page `{prefix}/<agent>/tasks`);
  it *references* the project page (read-only) — it does not write there.
- `agent` is required — it is the ownership link that makes `find_tasks(scope=...)` work.
- `blocks_on` is a block UUID rendered as `((uuid))` — a lightweight dependency edge.
- Markers must be real Logseq markers (TODO/DOING/NOW/LATER/DONE/WAITING/...). There
  is no `PENDING` — use `WAITING` or `LATER`.

## Tasks in the user's journal (only when `[tasks].journal_tags` is set)
The user keeps tasks in journals, under category headers, and plans the week with
`((refs))` to them:

    plans for week 9/11/26 #_week_plan
      #_study
        ((начать изучать RL))
          TODO [#B] [[byAgent/learn/rl/mdp]] MDP и уравнение Беллмана   <- added

`add_journal_tasks(parent, tasks=[{"title", "priority"?, "page"?}, ...])` adds new
TODOs as the last children of `parent`, in order, as `TODO [#P] [[page]] title`.

- **Only a list the user approved in this conversation, word for word.** Show the
  list first; the server cannot see the approval, so this is on you.
- `parent` is a block uuid on a journal page, under a configured tag (e.g.
  `#_study`, `#_work/dynamo`; never `…/archive/…`): the header itself, or a
  `((ref))` to the bigger task the new ones break down. Find it with read_page on
  the journal or `query_week_plan`.
- Pass the bare title: no marker (the server writes TODO), one line.
- A task already open under the same tag with the same text (ignoring case, link
  brackets and priority) is not added; it comes back in `duplicates` with its uuid.
  The server only catches exact repeats — search for similar tasks yourself before
  proposing a list.
- `added` returns each new task's uuid: keep them for `set_task_status`.
- Append-only: it never edits, moves or removes anything. Tasks for an agent to
  execute go in the agent namespace via `create_task` instead.

## Writing notes
- `write_note(subpath="plan")` always lands under `{prefix}/` (-> `{prefix}/plan`).
  Passing an already-prefixed subpath is fine (no double prefix). You cannot edit
  pages outside `{prefix}/` — an unprefixed subpath is rewritten into it. Content
  starting with a task marker is rejected — use `create_task` for tasks.
- `mode`: `append` adds a block; `replace` swaps the page content but **keeps page
  properties** (to remove a property use `set_page_properties` with a null value).
  write_note appends a block to a page; to change ONE existing block in place use
  `edit_block` (below).
- `blocks` appends block TREES in one call: each item is a string or
  `{"content": "...", "children": [ ...same shape... ]}`, nested up to 8 levels,
  1000 blocks per call. `content` alone can only add one flat block; use `blocks`
  whenever a block needs children (e.g. a flashcard: the question block with the
  answer as its children). Every top-level block gets a persisted `id::` — its uuid
  is stable across re-indexes — and the uuids come back in `block_uuids`, in order.
  Task markers are refused at any depth, as for `content`. Send many blocks in ONE
  call rather than many calls: the server writes them as one transaction and reads
  the page back; a burst of single writes can lose blocks in Logseq.
- `write_note`'s `properties` argument only applies when the page is first CREATED;
  on an existing page it is **ignored**. To add or change properties on an existing
  page use `set_page_properties` (it upserts; a null value removes a property).
- A property **value** containing `[[Page]]` or `#tag` becomes a real graph ref:
  `set_page_properties(.., {"project": "[[Some Page]]"})` shows up in that page's
  linked references — use it for structured links between pages. Only bare values
  (`status:: unread`, a word-list `tags:: a, b`) stay plain strings.

## Editing one block
- `edit_block(uuid, old_content, new_content)` replaces a block's whole content.
  Read the block first (`read_page`/`read_block`) and pass its EXACT current content
  as `old_content` — the edit is rejected if it doesn't match (you never read it, or
  it changed meanwhile). This is the block analogue of a file edit's old/new match and
  guards against clobbering a concurrent change.
- `old_content` must be the block's **`raw_content`** (what read_block returns under
  that key), NOT the cleaned `text`: raw_content keeps the leading task marker
  (`TODO`/`DONE`) and any `key:: value` property lines. Passing the marker-stripped
  `text` will fail the match. A block without `raw_content` has none because it
  equals `text` — then `text` IS the exact content (see "Block fields").
- Like other content writes it is confined to `{prefix}/`: a uuid on a page outside
  the agent namespace is refused. To change a task's status marker use
  `set_task_status`, not `edit_block`.
- `edit_blocks(edits=[{"uuid", "old_content", "new_content"}, ...])` runs the same
  checks per item, up to 300 per call, and is NOT all-or-nothing: edits that pass
  apply, and the rest come back in `rejected` with the reason (usually: the block
  changed since you read it, or the edit did not persist when read back). Re-read
  and retry those. One audit line per batch,
  where single edit_block calls would add one line each.

## Worklog — recording what you did (only when `[worklog]` is enabled)
`add_journal_note(text, work, agent, task?)` appends `HH:MM #<agent> <text>` to
**today's journal**, nested under its project group and, when `task` is given,
under a `((uuid))` reference to that task — the same shape a human keeps by hand:

    #_worklog
      #_work/dynamo
        ((6a9eb940-758e-4966-a31d-f91e79c35f42))
          17:50 #_agents/claude/work-scout told how to update

- `agent` must be one of `list_agents()`, which returns `<runtime>/<name>`
  (`claude/work-scout`, `codex/macbook`, …) — **your own name**, the one this
  session runs as. A bare name is accepted when only one runtime has it. Agents are
  registered by a human creating their page under `_agents/`; you cannot add
  yourself, and mentioning a name in a note does not register it. The
  signature is written inline on the note rather than as a grouping level, so
  several agents can log against one project without splitting it. The server
  cannot tell who is calling: signing honestly is on you.
- `work` must be one of `list_work_projects()` — read that first, do not guess a
  project name; anything else is rejected.
- `task` must be an EXISTING task block (any marker, `DOING` included). It is not
  checked against the project — file the note under the project you are working on.
- The **server stamps the time**. Never put a clock value in `text`, and never one
  recalled from earlier in a resumed session.
- `text` may not start with a task marker — that would create a task in the journal;
  use `create_task` for that.
- It is one of the two channels that write OUTSIDE `{prefix}/` (the other is
  `add_journal_tasks`), so it is deliberately narrow: append-only, today's journal
  only, and everything it creates lives under the single worklog root block. It can
  never edit or delete anything.
"""


def render_guide(agent_prefix: str) -> str:
    """Fill the guide with the deployment's actual agent write-prefix.

    Uses str.replace (not .format) so literal braces in the guide text — e.g. a
    `{"project": "[[X]]"}` example — don't get parsed as format fields.
    """
    return GUIDE.replace("{prefix}", agent_prefix or "byAgent")
