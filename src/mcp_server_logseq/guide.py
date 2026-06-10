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
- `find_tasks(markers?, tag?, under_tag?, page?, priority?, limit?)` — task blocks.
- `datascript_query(query, inputs?, rules?)` — raw Datalog (advanced).
- `custom_query` / `list_custom_queries` — saved queries.
- `write_note` / `set_page_properties` — write under `{prefix}/` only.
- `set_task_status(uuid, status)` — change a task's marker.

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

## Writing
- `write_note(subpath="plan")` always lands under `{prefix}/` (-> `{prefix}/plan`).
  Passing an already-prefixed subpath is fine (no double prefix). You cannot edit
  pages outside `{prefix}/` — an unprefixed subpath is rewritten into it.
"""


def render_guide(agent_prefix: str) -> str:
    """Fill the guide with the deployment's actual agent write-prefix."""
    return GUIDE.format(prefix=agent_prefix or "byAgent")
