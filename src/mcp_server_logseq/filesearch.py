"""File-backed search: ripgrep finds candidate pages, the API supplies content.

ripgrep over the graph's .md files is a fast way to get *candidate page names*
(Logseq has no real full-text API). The actual matched content and snippets are
then re-fetched through the API and blacklist-redacted, so blacklisted text never
leaks even though it exists on disk. Filename → page identity:
  - `a___b.md` (triple-lowbar) → page `a/b`, percent-decoded;
  - files under a `journals/` dir → resolved by journal-day (yyyy_MM_dd).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Optional


class FileSearchError(Exception):
    pass


def ripgrep_path() -> Optional[str]:
    return shutil.which("rg")


def find_candidate_files(
    files_path: str, query: str, regex: bool, case_sensitive: bool, timeout: float = 30.0
) -> list[str]:
    rg = ripgrep_path()
    if not rg:
        raise FileSearchError("ripgrep (rg) not found on PATH")
    if not os.path.isdir(files_path):
        raise FileSearchError(f"files_path is not a directory: {files_path}")

    args = [rg, "--files-with-matches", "--no-messages", "-g", "*.md"]
    if not case_sensitive:
        args.append("-i")
    if not regex:
        args.append("-F")  # fixed-string (literal) match
    args += ["--", query, files_path]

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FileSearchError("ripgrep timed out") from exc
    if proc.returncode == 2:
        raise FileSearchError(proc.stderr.strip() or "ripgrep error")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def decode_candidate(path_str: str, files_path: str) -> Optional[tuple[str, object]]:
    """Map a file path to ('page', name) or ('journal', journal_day:int)."""
    p = Path(path_str)
    try:
        rel = p.relative_to(files_path)
    except ValueError:
        rel = Path(os.path.relpath(path_str, files_path))
    parts = [part.lower() for part in rel.parts]
    stem = p.stem

    if "journals" in parts:
        digits = re.sub(r"\D", "", stem)
        if len(digits) == 8 and digits.isdigit():
            return ("journal", int(digits))
        return None

    name = urllib.parse.unquote(stem.replace("___", "/"))
    return ("page", name)


def build_matcher(query: str, regex: bool, case_sensitive: bool):
    """A Python-side predicate matching the same query against block text."""
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(query, flags)
        return lambda text: rx.search(text or "") is not None
    if case_sensitive:
        return lambda text: query in (text or "")
    needle = query.lower()
    return lambda text: needle in (text or "").lower()
