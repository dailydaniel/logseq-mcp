"""Normalize raw Logseq blocks into a flat, agent-friendly JSON shape.

Pure functions only (no I/O). Block references are *parsed* but not resolved
here — resolution (which needs API calls) happens in resolve.py and fills in
`resolved_refs`. Refs/tags/markers are parsed from the block's raw `content`
rather than the API's `:block/refs` field, because content carries the literal
`[[Page]]` / `#tag` / `((uuid))` syntax and is identical across the
getPageBlocksTree (camelCase) and datascriptQuery (kebab-case) endpoints.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Task markers recognized by Logseq's parser (file version). Uppercase only,
# must be the first token. Source: mldoc heading parser + frontend marker.cljs.
MARKERS = (
    "NOW", "LATER", "TODO", "DOING", "DONE",
    "WAITING", "WAIT", "CANCELED", "CANCELLED", "IN-PROGRESS", "STARTED",
)

# Leading marker (+ optional heading prefix and [#A] priority). Group 2 = marker,
# group 3 = priority bracket (kept when rewriting status).
_MARKER_RE = re.compile(
    r"^(\s*#+\s+)?(" + "|".join(MARKERS) + r")(\s+\[#[A-Za-z]\])?\s+"
)
_PRIORITY_RE = re.compile(r"\[#([A-Za-z])\]")
_BLOCK_REF_RE = re.compile(r"\(\(([0-9a-fA-F-]{36})\)\)")
_PAGE_REF_RE = re.compile(r"\[\[([^\]]+)\]\]")
_TAG_BRACKET_RE = re.compile(r"#\[\[([^\]]+)\]\]")
_TAG_WORD_RE = re.compile(r"#([\w/_.-]+)")
_PROP_LINE_RE = re.compile(r"^\s*[\w][\w.-]*::\s?.*$", re.MULTILINE)
_LOGBOOK_RE = re.compile(r":LOGBOOK:.*?:END:", re.DOTALL | re.IGNORECASE)


def parse_marker(content: str) -> tuple[Optional[str], Optional[str]]:
    """Return (status, priority) parsed from a block's leading marker."""
    m = _MARKER_RE.match(content or "")
    if not m:
        return None, None
    status = m.group(2)
    prio_match = _PRIORITY_RE.search(m.group(3) or "")
    priority = prio_match.group(1).upper() if prio_match else None
    return status, priority


def _strip_drawers_and_props(text: str) -> str:
    text = _LOGBOOK_RE.sub("", text)
    text = _PROP_LINE_RE.sub("", text)
    return text


def display_text(content: str) -> str:
    """Human-readable text: marker/priority/property-lines/logbook removed."""
    body = _MARKER_RE.sub("", content or "", count=1)
    body = _strip_drawers_and_props(body)
    return body.strip()


def extract_refs(content: str) -> tuple[list[str], list[str], list[str]]:
    """Return (page_refs, tags, block_ref_uuids) parsed from content.

    `#tag` and `[[Page]]` are kept separate (per spec) even though Logseq treats
    them as the same page reference. A `#[[multi word]]` counts as a tag; a
    bare `[[Page]]` not preceded by `#` counts as a page ref.
    """
    content = content or ""
    block_refs = _BLOCK_REF_RE.findall(content)

    # Strip [#A] priority brackets first so their '#A' isn't read as a #tag.
    tag_src = _PRIORITY_RE.sub("", content)
    tags: list[str] = list(_TAG_BRACKET_RE.findall(tag_src))
    tags += _TAG_WORD_RE.findall(tag_src)

    page_refs: list[str] = []
    for m in _PAGE_REF_RE.finditer(content or ""):
        start = m.start()
        if start > 0 and content[start - 1] == "#":
            continue  # part of a #[[...]] tag, already captured
        page_refs.append(m.group(1))

    return _dedup(page_refs), _dedup(tags), _dedup(block_refs)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _field(block: dict, *names: str) -> Any:
    for n in names:
        if n in block:
            return block[n]
    return None


def _page_info(block: dict) -> tuple[Optional[str], Optional[int]]:
    page = _field(block, "page")
    if not isinstance(page, dict):
        return None, None
    name = _field(page, "original-name", "originalName", "name")
    journal = _field(page, "journal-day", "journalDay")
    return name, journal


def _user_properties(block: dict) -> dict[str, Any]:
    props = _field(block, "properties")
    if not isinstance(props, dict):
        return {}
    return {k: v for k, v in props.items() if k != "id"}


def is_block_ref_only(content: str) -> bool:
    """True if the block's content is solely a single ((uuid)) embed."""
    stripped = _strip_drawers_and_props(content or "").strip()
    m = _BLOCK_REF_RE.fullmatch(stripped)
    return m is not None


def normalize_block(block: dict, *, _depth: int = 0) -> dict:
    """Convert a raw Logseq block (either endpoint shape) into flat JSON.

    Children are normalized recursively. `resolved_refs` is left empty; it is
    populated later by resolve.py if ref resolution is enabled.
    """
    content = _field(block, "content") or ""
    status, priority = parse_marker(content)
    page_refs, tags, block_refs = extract_refs(content)
    page_name, journal_day = _page_info(block)

    children = _field(block, "children") or []
    norm_children = [
        normalize_block(c, _depth=_depth + 1)
        for c in children
        if isinstance(c, dict)
    ]

    result: dict[str, Any] = {
        "uuid": _field(block, "uuid"),
        "text": display_text(content),
        "status": status,
        "priority": priority,
        "page_refs": page_refs,
        "tags": tags,
        "block_refs": block_refs,
        "is_block_ref": is_block_ref_only(content),
        "resolved_refs": [],
        "page": page_name,
        "journal_day": journal_day,
        "properties": _user_properties(block),
        "children": norm_children,
        "raw_content": content,
    }
    return result


def rewrite_marker(content: str, new_status: str) -> str:
    """Replace only the leading marker, preserving heading/priority/text.

    If the block has no marker, prepend the new one.
    """
    m = _MARKER_RE.match(content or "")
    if m:
        prefix = m.group(1) or ""
        priority = m.group(3) or ""
        rest = content[m.end():]
        return f"{prefix}{new_status}{priority} {rest}"
    return f"{new_status} {content}"


def current_marker(content: str) -> Optional[str]:
    status, _ = parse_marker(content)
    return status
