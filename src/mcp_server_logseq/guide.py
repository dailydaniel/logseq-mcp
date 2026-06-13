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
- `find_tasks(markers?, tag?, under_tag?, page?, priority?, scope?, agent?, limit?)`
  — task blocks. `scope`: `all` | `agent` (only agent-owned) | `human` (exclude them).
- `datascript_query(query, inputs?, rules?)` — raw Datalog (advanced).
- `custom_query` / `list_custom_queries` — saved queries.
- `write_note` / `set_page_properties` — write notes under `{prefix}/` only.
- `create_task(...)` — the ONLY way to make a task (see Tasks below).
- `set_task_status(uuid, status)` — change a task's marker.
- `edit_block(uuid, old_content, new_content)` — replace ONE block's content
  in place (read it first; namespace-confined). See "Editing one block".

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

## Writing notes
- `write_note(subpath="plan")` always lands under `{prefix}/` (-> `{prefix}/plan`).
  Passing an already-prefixed subpath is fine (no double prefix). You cannot edit
  pages outside `{prefix}/` — an unprefixed subpath is rewritten into it. Content
  starting with a task marker is rejected — use `create_task` for tasks.
- `mode`: `append` adds a block; `replace` swaps the page content but **keeps page
  properties** (to remove a property use `set_page_properties` with a null value).
  write_note appends a block to a page; to change ONE existing block in place use
  `edit_block` (below).
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
- Like other content writes it is confined to `{prefix}/`: a uuid on a page outside
  the agent namespace is refused. To change a task's status marker use
  `set_task_status`, not `edit_block`.
"""


def render_guide(agent_prefix: str) -> str:
    """Fill the guide with the deployment's actual agent write-prefix."""
    return GUIDE.format(prefix=agent_prefix or "byAgent")
