#!/usr/bin/env python3
"""Warn when docs, prompts or .github files call people "human(s)".

Standing rule: refer to people as operator(s), never human(s). This check
is warn-only. It prints a GitHub ``::warning`` annotation per hit and exits
0. ``--strict`` makes hits fail the run, for a later switch to enforcement.

Scanned: Markdown (``*.md``, ``*.mdc``), prompt files (``*.prompt*``, and
anything under ``prompts/``, ``agents/``, ``skills/`` or ``rules/``), and
every file under ``.github/``.

Allowed (never flagged): "human-readable", "Human Resources", humanize*,
identifiers such as ``needs-human``, ``human_*`` and ``HUMAN-CHECKPOINTS``,
third-party quotes ("written by a human", "Human Action Model"), anything in
inline code, and any line carrying ``operator-terminology: allow``.

Usage: check-operator-terms.py [--strict] [PATH ...]
With no paths, scans the files ``git ls-files`` reports.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

WORD = re.compile(r"\bhumans?\b", re.I)
ALLOW = re.compile(
    "|".join(
        [
            r"human[- ]readable",
            r"Human Resources",
            r"humaniz\w*",
            r"\bhuman_\w+",
            r"\w+_human\b",
            r"needs-human",
            r"HUMAN-CHECKPOINTS",
            r"written by a human",
            r"Human Action Model",
            r"human[- ]written",
            r"human Co-authored-by",
            r"`[^`\n]*`",
        ]
    ),
    re.I,
)
PRAGMA = "operator-terminology: allow"
PATTERNS = ["*.md", "*.mdc", "*.prompt", "*.prompt.*", ".github/*"]
DIRS = ("prompts/", "agents/", "skills/", "rules/")


def in_scope(path: str) -> bool:
    p = path.replace("\\", "/")
    if p.startswith(".github/") or any(f"/{d}" in f"/{p}" for d in DIRS):
        return True
    name = p.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in PATTERNS)


def hits(text: str) -> list[tuple[int, int, str]]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if PRAGMA in line:
            continue
        masked = ALLOW.sub(lambda m: " " * len(m.group(0)), line)
        for m in WORD.finditer(masked):
            found.append((n, m.start() + 1, m.group(0)))
    return found


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        out = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        ).stdout
        paths = out.splitlines()
    total = 0
    for path in paths:
        if not in_scope(path) or not Path(path).is_file():
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line, col, word in hits(text):
            total += 1
            level = "error" if strict else "warning"
            msg = escape(f'"{word}": refer to people as operator(s), never human(s)')
            print(f"::{level} file={path},line={line},col={col}::{msg}")
    print(f"operator-terminology: {total} hit(s)" + ("" if strict else " (warn-only)"))
    return 1 if strict and total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
