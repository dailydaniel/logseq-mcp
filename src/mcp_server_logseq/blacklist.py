"""Cross-cutting blacklist: exclude pages and redact blocks that reference them.

Two levels (applied to the output of every tool):
  (A) page exclusion — a blacklisted page and its subpages are never returned.
  (B) block redaction — a block whose page-refs/tags point at a blacklisted page
      (or subpage) collapses, with its whole subtree, into a placeholder; this
      prevents leaks via mentions or block embeds.

Pure functions over normalized blocks (see normalize.py).
"""

from __future__ import annotations

import unicodedata
from typing import Any

DEFAULT_PLACEHOLDER = "<excluded block>"


def canon_page_name(name: str) -> str:
    """Match Logseq's page-name identity: NFC, lowercased, slashes trimmed."""
    s = unicodedata.normalize("NFC", name or "").strip().lower()
    return s.strip("/")


class Blacklist:
    def __init__(self, pages: list[str], placeholder: str = DEFAULT_PLACEHOLDER) -> None:
        self._pages = [canon_page_name(p) for p in pages if p.strip()]
        self.placeholder = placeholder

    @property
    def active(self) -> bool:
        return bool(self._pages)

    def is_page_excluded(self, name: str) -> bool:
        """True if `name` equals or is a subpage of a blacklisted page."""
        c = canon_page_name(name)
        if not c:
            return False
        return any(c == p or c.startswith(p + "/") for p in self._pages)

    def _block_references_excluded(self, block: dict) -> bool:
        refs = list(block.get("page_refs") or []) + list(block.get("tags") or [])
        return any(self.is_page_excluded(r) for r in refs)

    def redact_block(self, block: dict) -> dict:
        """Return a collapsed placeholder for a redacted block (subtree dropped)."""
        return {
            "uuid": block.get("uuid"),
            "text": self.placeholder,
            "redacted": True,
            "status": None,
            "priority": None,
            "page_refs": [],
            "tags": [],
            "block_refs": [],
            "is_block_ref": False,
            "resolved_refs": [],
            "children": [],
        }

    def filter_blocks(self, blocks: list[dict]) -> list[dict]:
        """Recursively redact blocks that reference blacklisted pages."""
        if not self.active:
            return blocks
        out: list[dict] = []
        for b in blocks:
            if self._block_references_excluded(b):
                out.append(self.redact_block(b))
            else:
                b = dict(b)
                b["children"] = self.filter_blocks(b.get("children") or [])
                out.append(b)
        return out
