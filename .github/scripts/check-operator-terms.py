#!/usr/bin/env python3
"""Warn when PR text or docs use bot-perspective language.

Standing rule (DEV-1217): pull request titles, bodies and comments are
written for a developer. People are operator(s). AI bots are agent(s).
Both words are correct and are never flagged.

This check is warn-only. It prints a GitHub ``::warning`` annotation per
hit and exits 0. ``--strict`` makes hits fail the run, for a later switch
to enforcement.

What is flagged
---------------
Bot-perspective language: a bot writing about itself or other bots as if
they were the reader, narration aimed at fellow agents, product names used
as actors (``Cursor did``, ``Copilot will``, ``Bugbot here``), self-identity
(``as an AI``, ``I am Claude``), ALL-CAPS directives in the prose
(``DO NOT MERGE``, ``AGENTS: …``), and calling people ``human(s)`` or
``the user`` in agent-to-agent tone where ``operator`` is meant.

What is allowed
---------------
``operator`` / ``operators``, ``agent`` / ``agents``, the existing
people-term keep cases (``human-readable``, ``Human Resources``,
humanize*, identifiers such as ``needs-human``, ``human_*`` and
``HUMAN-CHECKPOINTS``, product copy ``human editor``, the rule statement
``human(s)``, third-party quotes, ``human Co-authored-by``), anything in
inline code, fenced code, file paths or URLs, the reserved
``<!-- agent-state … -->`` metadata block (DEV-1217 R2), and any line
carrying ``operator-terminology: allow`` or ``agent-perspective: allow``.

Legitimate product names in a technical context (a pull request that
changes Copilot config) are not actors and do not match; if one still
hits, mark that line with the allow comment.

Scanned files
-------------
Markdown (``*.md``, ``*.mdc``), prompt files (``*.prompt*``, and
anything under ``prompts/``, ``agents/``, ``skills/`` or ``rules/``), and
every file under ``.github/``. Skipped everywhere: source code (``*.py``,
``*.sh`` and similar, where a flagged word is usually an identifier),
translation files (``*.po``, ``*.pot``), ``CHANGELOG.md`` (released notes
are not rewritten) and vendored trees (``node_modules/``, ``vendor/``,
Drupal core and contrib).

PR text
-------
``--github`` reads the current issue or pull request title, body, issue
comments, review comments and reviews through ``gh api``. Surfaces can
also be passed with ``--title-file``, ``--body-file``, ``--comment-file``
or ``--text``. File scanning still runs unless every path is out of scope.

Usage: check-operator-terms.py [--strict] [--github] [PATH ...]
With no paths, scans the files ``git ls-files`` reports.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

PRAGMAS = ("operator-terminology: allow", "agent-perspective: allow")

# Keep cases for people-terms and other spans that must never warn.
# Inline code, URLs and file paths are masked here so a hit inside them
# cannot fire. Fenced blocks and the agent-state HTML comment are stripped
# from the whole document before line-by-line matching.
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
            r"human editors?",
            r"humans?\(s\)",
            r"`[^`\n]*`",
            r"https?://[^\s)>\]]+",
            r"(?:[\w.~-]+/)+\.?[\w.~-]+",
            r"\b[\w.-]+\.(?:md|mdc|yml|yaml|py|sh|js|mjs|cjs|ts|tsx|json|txt|toml|prompt)\b",
        ]
    ),
    re.I,
)

# Hidden machine-readable channel reserved by DEV-1217 R2. Bots put YAML
# (or any other payload) inside; the human-readable narrative stays outside.
METADATA_BLOCK = re.compile(r"<!--[ \t]*agent-state\b[\s\S]*?-->", re.I)
FENCED = re.compile(r"^ {0,3}(```+|~~~+)[^\n]*\n[\s\S]*?^ {0,3}\1[^\n]*$", re.M)

PRODUCTS = (
    r"Cursor|Copilot|Claude|Bugbot|ChatGPT|Gemini|Codex|Devin|Grok|"
    r"Aider|Windsurf"
)
ACTOR_VERBS = (
    r"did|will|here|says|said|thinks|wants|recommends|suggests|"
    r"posted|wrote|opened|reviewed|fixed"
)

PATTERNS = ["*.md", "*.mdc", "*.prompt", "*.prompt.*", ".github/*"]
DIRS = ("prompts/", "agents/", "skills/", "rules/")
VENDORED = (
    "node_modules/",
    "vendor/",
    "web/core/",
    "web/modules/contrib/",
    "web/themes/contrib/",
    "web/profiles/contrib/",
    "web/libraries/",
)
SKIP = (
    "*.po",
    "*.pot",
    "CHANGELOG.md",
    "*.py",
    "*.sh",
    "*.bash",
    "*.js",
    "*.mjs",
    "*.cjs",
    "*.ts",
    "*.php",
    "*.rb",
    "*.go",
)


class Hit(NamedTuple):
    line: int
    column: int
    word: str
    message: str


class Rule(NamedTuple):
    pattern: re.Pattern[str]
    template: str


def _rule(pattern: str, template: str, flags: int = re.I) -> Rule:
    return Rule(re.compile(pattern, flags), template)


# ``operator`` and ``agent`` are deliberately absent. Both are correct.
RULES: tuple[Rule, ...] = (
    _rule(
        r"\bhumans?\b",
        '"{word}": refer to people as operator(s)',
    ),
    _rule(
        r"\b(?:ask|tell|notify|inform|prompt|ping)(?:ing)? the users?\b",
        '"{word}": refer to the operator, not "the user"',
    ),
    _rule(
        r"\bwait(?:ing)? for the users?\b",
        '"{word}": refer to the operator, not "the user"',
    ),
    _rule(
        r"\bthe users? (?:should|must|needs? to) (?:merge|approve|review|decide|confirm)\b",
        '"{word}": refer to the operator, not "the user"',
    ),
    _rule(
        r"\bas an(?: AI| LLM| language model)\b",
        '"{word}": write for a developer; do not narrate as an AI',
    ),
    _rule(
        rf"\bI(?: am|'m) (?:an? )?(?:AI|language model|LLM|{PRODUCTS})\b",
        '"{word}": write for a developer; do not introduce a bot identity',
    ),
    _rule(
        rf"\b(?:{PRODUCTS}) (?:{ACTOR_VERBS})\b",
        '"{word}": product names are not actors in the narrative',
    ),
    _rule(
        r"\bDO NOT (?:MERGE|APPROVE|DEPLOY|RELEASE|PUSH|COMMIT|SQUASH|TAG)\b",
        '"{word}": put hold-state in the agent-state metadata block, not in the narrative',
        0,
    ),
    _rule(
        r"\bNEVER (?:MERGE|APPROVE|DEPLOY|RELEASE)\b",
        '"{word}": put hold-state in the agent-state metadata block, not in the narrative',
        0,
    ),
    _rule(
        r"\b(?:AGENTS?|BOTS?):\s",
        '"{word}": do not address agents in the operator-facing narrative',
        0,
    ),
    _rule(
        r"\bif you(?:'re| are) (?:an? )?(?:AI|agent|bot|LLM)\b",
        '"{word}": do not address agents in the operator-facing narrative',
    ),
    _rule(
        r"\bfellow (?:agents?|bots?)\b",
        '"{word}": do not address agents in the operator-facing narrative',
    ),
    _rule(
        r"\b(?:other|next) agents? (?:should|must|will)\b",
        '"{word}": do not address agents in the operator-facing narrative',
    ),
    _rule(
        r"\bagents? reading this\b",
        '"{word}": do not address agents in the operator-facing narrative',
    ),
)


def in_scope(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if any(p.startswith(d) or f"/{d}" in p for d in VENDORED):
        return False
    if any(fnmatch.fnmatch(name, pat) for pat in SKIP):
        return False
    if p.startswith(".github/") or any(f"/{d}" in f"/{p}" for d in DIRS):
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in PATTERNS)


def _blank_keep_newlines(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def mask_pattern(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: _blank_keep_newlines(m.group(0)), text)


def mask_document(text: str) -> str:
    return mask_pattern(mask_pattern(text, METADATA_BLOCK), FENCED)


def findings(text: str) -> list[Hit]:
    found: list[Hit] = []
    for n, line in enumerate(mask_document(text).splitlines(), 1):
        if any(marker in line for marker in PRAGMAS):
            continue
        masked = ALLOW.sub(lambda m: " " * len(m.group(0)), line)
        for rule in RULES:
            for match in rule.pattern.finditer(masked):
                word = match.group(0).rstrip()
                found.append(
                    Hit(n, match.start() + 1, word, rule.template.format(word=word))
                )
    return found


def hits(text: str) -> list[tuple[int, int, str]]:
    """Line, column, matched text. Kept for the original test names."""
    return [(item.line, item.column, item.word) for item in findings(text)]


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def safe_text(text: str) -> str:
    """Replace lone surrogates so UTF-8 writes and summaries cannot crash."""
    return text.encode("utf-8", "replace").decode("utf-8")


def emit(level: str, message: str, path: str | None = None, line: int = 0, column: int = 0) -> None:
    msg = escape(message)
    if path:
        shown = path.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
        where = escape(shown).replace(",", "%2C").replace(":", "%3A")
        print(f"::{level} file={where},line={line},col={column}::{msg}")
        return
    prefix = f"{line}:" if line else ""
    print(f"::{level}::{escape(prefix)}{msg}" if prefix else f"::{level}::{msg}")


def write_summary(total: int, notes: list[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("## Agent perspective\n\n")
            handle.write(f"{total} warning(s). The check is warn-only.\n\n")
            for note in notes:
                handle.write(f"- {safe_text(note)}\n")
    except (OSError, UnicodeError):
        return


def _gh_api(path: str) -> object | None:
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate", path],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        try:
            return json.loads(raw.replace("][", ","))
        except json.JSONDecodeError:
            return None


def _as_items(payload: object | None) -> list[dict]:
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _body_of(item: dict) -> str:
    body = item.get("body")
    return body if isinstance(body, str) else ""


def fetch_github_surfaces() -> list[tuple[str, str]]:
    """Title, body and comments for the current issue or pull request."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("ISSUE_NUMBER", "")
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1] or "/" in parts[1]:
        print("::warning::agent-perspective: GITHUB_REPOSITORY is missing; PR text skipped.")
        return []
    if not number.isdigit():
        print("::warning::agent-perspective: ISSUE_NUMBER is missing; PR text skipped.")
        return []

    surfaces: list[tuple[str, str]] = []
    pull = _gh_api(f"repos/{repo}/pulls/{number}")
    issue = None if isinstance(pull, dict) else _gh_api(f"repos/{repo}/issues/{number}")
    subject = pull if isinstance(pull, dict) else issue
    if not isinstance(subject, dict):
        print("::warning::agent-perspective: could not read the issue or pull request; PR text skipped.")
        return []

    kind = "PR" if isinstance(pull, dict) else "issue"
    title = subject.get("title")
    if isinstance(title, str) and title:
        surfaces.append((f"{kind} title", title))
    body = subject.get("body")
    if isinstance(body, str) and body:
        surfaces.append((f"{kind} body", body))

    for item in _as_items(_gh_api(f"repos/{repo}/issues/{number}/comments")):
        text = _body_of(item)
        ident = item.get("id")
        if text and isinstance(ident, int):
            surfaces.append((f"issue comment {ident}", text))

    if isinstance(pull, dict):
        for item in _as_items(_gh_api(f"repos/{repo}/pulls/{number}/comments")):
            text = _body_of(item)
            ident = item.get("id")
            if text and isinstance(ident, int):
                surfaces.append((f"review comment {ident}", text))
        for item in _as_items(_gh_api(f"repos/{repo}/pulls/{number}/reviews")):
            text = _body_of(item)
            ident = item.get("id")
            if text and isinstance(ident, int):
                surfaces.append((f"review {ident}", text))
    return surfaces


def scan_text(label: str, text: str, level: str, path: str | None = None) -> list[Hit]:
    found = findings(text)
    for item in found:
        if path:
            emit(level, item.message, path=path, line=item.line, column=item.column)
        else:
            emit(level, f"{label}:{item.line}: {item.message}")
    return found


def listed_files() -> list[str] | None:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"::warning::agent-perspective: could not list files ({exc})")
        return None
    # fsdecode keeps a non-UTF-8 file name readable instead of crashing.
    return [os.fsdecode(part) for part in out.split(b"\0") if part]


def main(argv: list[str]) -> int:
    strict = False
    use_github = False
    paths: list[str] = []
    supplied: list[tuple[str, str]] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--strict":
            strict = True
        elif arg == "--github":
            use_github = True
        elif arg == "--text" and index + 1 < len(argv):
            index += 1
            supplied.append(("text", argv[index]))
        elif arg == "--title-file" and index + 1 < len(argv):
            index += 1
            supplied.append(("title", Path(argv[index]).read_text(encoding="utf-8")))
        elif arg == "--body-file" and index + 1 < len(argv):
            index += 1
            supplied.append(("body", Path(argv[index]).read_text(encoding="utf-8")))
        elif arg == "--comment-file" and index + 1 < len(argv):
            index += 1
            supplied.append(("comment", Path(argv[index]).read_text(encoding="utf-8")))
        elif not arg.startswith("--"):
            paths.append(arg)
        index += 1

    if use_github:
        supplied.extend(fetch_github_surfaces())

    if not paths and not supplied:
        listed = listed_files()
        if listed is None:
            return 1 if strict else 0
        paths = listed

    level = "error" if strict else "warning"
    total = 0
    notes: list[str] = []
    for label, text in supplied:
        found = scan_text(label, text, level)
        total += len(found)
        notes.extend(f"{label}:{item.line}: {item.message}" for item in found)
    for path in paths:
        if not in_scope(path) or not Path(path).is_file():
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = scan_text(path, text, level, path=path)
        total += len(found)
        notes.extend(f"{path}:{item.line}: {item.message}" for item in found)

    write_summary(total, notes)
    print(f"agent-perspective: {total} hit(s)" + ("" if strict else " (warn-only)"))
    return 1 if strict and total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
