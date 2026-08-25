#!/usr/bin/env python3
"""Rewrite PR commits to drop AI-attribution trailers and AI identities.

Wilkes & Liberty work reads as authored by the human operator. Local commit
hooks cannot see server-side commits (GitHub "Commit suggestion", Copilot
Autofix, web UI, hosted Cursor Cloud Agents). The companion
check-attribution.py *fails* a dirty PR; this script *cleans* the PR branch
when it can, so the operator does not have to hand-rebase every Copilot
trailer or Cursor Agent author stamp before merge.

Scope:
  * Rewrites **commit messages** in base..head (non-merge commits) to remove
    attribution *shapes* — credit trailers naming an AI author, "Generated
    with <AI>" footers, and the robot-emoji marker line.
  * Rewrites **author and/or committer identity** when that slot would fail
    check-attribution.py's identity scan (same patterns — keep them in sync).
    Replacement human, operator lock 2026-08-22 (DEV-414), in order:
      1. a human ``Co-authored-by:`` trailer already on the commit (name +
         email that is NOT an AI identity under the same rules). Hosted
         Cursor Cloud Agents stamp the session initiator this way.
      2. ``STRIP_AUTHOR_NAME`` + ``STRIP_AUTHOR_EMAIL`` when both are set,
         not an AI identity, and the address is ``@users.noreply.github.com``
         (the workflow passes the PR opener in that shape).
      3. if the author slot is already a human, that human (so an AI
         committer on a human-authored commit can be restamped).
      4. ``Jeremy Michael Cerda <jmcerda@users.noreply.github.com>``, but
         only when the dirty author is *not* an unambiguous Autofix /
         copilot identity. Hosted Cursor Agent commits may use this
         default. ``copilot-swe-agent[bot]`` / Copilot Autofix without a
         human trailer and without a safe ``STRIP_AUTHOR_*`` stays dirty
         and the check fails closed — do not invent Jeremy as the
         Autofix author.
    Standing-orders ``@wilkesliberty.com`` is used only when we actually
    control identity at commit time. It is not the strip rewrite default
    and is not accepted via ``STRIP_AUTHOR_*`` — those must be the
    GitHub noreply shape. A company address already on a human
    Co-authored-by trailer is still preferred (rule 1).
  * After promoting a human to author (and committer if that slot was AI),
    AI credit trailers are removed as today. ``Co-authored-by: Cursor Agent``
    is not left behind. Human Co-authored-by trailers are preserved.
  * Preserves trees and dates. When rewriting identity, GIT_AUTHOR_* is set
    to the human and GIT_AUTHOR_DATE stays the original author date. If the
    committer was AI, GIT_COMMITTER_* is set to the same human and
    GIT_COMMITTER_DATE stays the original committer date. A human committer
    is left untouched (server-side "Commit suggestion" on someone else's
    work must not overwrite that human).
  * ``clean_cursor_pr_body`` removes Cursor cloud wrapper markers from a PR
    body string. The workflow PATCHes GitHub when those markers are present;
    this script does not talk to the API.
  * Does **not** rewrite mid-subject prose like "AI-assisted refactor" —
    those still fail the check for a human reword (auto-rewriting subjects
    is too lossy).
  * Rewrites the first-parent chain, including merge commits. A merge of
    ``master`` into a dirty feature branch no longer disables the rewrite
    (that miss left Cursor Agent on the commit and failed the check).
    Second parents are remapped when they sit on the same first-parent
    walk; otherwise they are kept.

Usage:
  strip-attribution.py --base <sha> --head <sha> [--push] [--dry-run]
                       [--branch <name>] [--force-with-lease-ref <spec>]

Exits:
  0  clean already, or successfully rewrote (and pushed if --push)
  1  rewrite needed but failed, or residual dirty messages remain
  2  usage / environment error

GitHub Actions: writes `stripped` and `count` to $GITHUB_OUTPUT when set.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

# --- BEGIN SHARED PATTERNS (keep identical to check-attribution.py) ---

AI_AUTHOR = (
    r"claude|anthropic|copilot|codex|cursor|devin|aider|windsurf|"
    r"chatgpt|openai|gemini|llama|"
    r"powered\s+by\s+ai|\bai\s+(?:assistant|agent|bot)\b"
)

CREDIT_TRAILER = r"Co-?Authored-?By|Co-?Committed-?By|Assisted-?By|Generated-?By"

# Line-oriented shapes safe to auto-remove. The broader "AI-generated" mid-prose
# pattern lives only in check-attribution.py so subjects still need a human.
STRIP_LINE_PATTERNS = [
    re.compile(rf"^(?:{CREDIT_TRAILER})\s*:\s*.*(?:{AI_AUTHOR})", re.I),
    re.compile(rf"^(?i:(?:{CREDIT_TRAILER})\s*:)\s*.*\bAI\b"),
    re.compile(
        rf"^\s*(?:\U0001F916\s*)?(?:Generated|Created|Written|Authored|Produced)\s+(?:with|by)\s+"
        rf".*(?:{AI_AUTHOR})",
        re.I,
    ),
    re.compile(r"^\s*\U0001F916\s*$"),  # a line that is ONLY the robot emoji
]

# --- END SHARED PATTERNS ---

# --- BEGIN SHARED IDENTITY PATTERNS (keep identical to check-attribution.py) ---

# UNAMBIGUOUS -- a company or product, not something a person is called. Seeing
# one in an author field is the claim by itself.
IDENTITY_UNAMBIGUOUS = (
    r"anthropic|openai|copilot|chatgpt|windsurf|aider|"
    r"github\s*copilot|swe-?agent"
)

# AMBIGUOUS -- also ordinary human given names or common words. `claude` and
# `devin` are names people have; `gemini`, `cursor`, `codex` and `llama` are
# words. Flagging these on sight would block a contributor called Claude Dupont,
# which on the published projects is an outside contribution refused for being
# named wrong. They only count alongside a marker below.
IDENTITY_AMBIGUOUS = r"claude|devin|gemini|cursor|codex|llama"

# What turns an ambiguous token into a claim: a bot/agent marker, or a noreply
# address of the kind automation commits under. `Claude <noreply@anthropic.com>`
# is caught by the unambiguous list anyway; `claude-code[bot]` is caught here.
# `bot` and `agent` are bounded the same way the tokens above are, and for the
# same reason: unbounded, `agent` matches inside ordinary words that turn up in
# real addresses -- `agentur` is German for agency, and `Reagent` is a surname.
# Either would have combined with an ambiguous given name to flag a human.
# `noreply` needs no boundary; it is not a fragment of anything.
IDENTITY_BOT_MARKER = (
    r"\[bot\]|(?<![a-z0-9])(?:bot|agent)(?![a-z0-9])|noreply|no-reply"
)

# Boundaries are explicitly alphanumeric rather than `\b`, because Python's `\b`
# is built on `\w`, which counts `_` as a word character. `\bcopilot\b` does NOT
# match `copilot_swe_agent` -- the underscore is the commonest separator in bot
# handles, so the boundary intended to prevent surname false positives was also
# skipping the exact identities this check exists for.
def _bounded(alternation: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])", re.I)


AI_IDENTITY_UNAMBIGUOUS = _bounded(IDENTITY_UNAMBIGUOUS)
AI_IDENTITY_AMBIGUOUS = _bounded(IDENTITY_AMBIGUOUS)
AI_IDENTITY_MARKER = re.compile(IDENTITY_BOT_MARKER, re.I)

# Roles are checked separately so the message can say which one is wrong.
# Both matter: an agent that authors a commit lands in the author slot, while a
# server-side "Commit suggestion" can put one in the committer slot instead.
IDENTITY_ROLES = ("author", "committer")


def find_identity_attribution(identities):
    """Return a description of the first AI identity found, or None.

    `identities` is ((author_name, author_email), (committer_name, committer_email)).

    An identity counts as an attribution when it contains an unambiguous vendor
    or product name, OR an ambiguous token together with a bot/agent marker.
    Name and email are considered as one string per role, so a marker in the
    address qualifies a token in the name -- `Claude <claude-code[bot]@...>` is
    one identity, not two unrelated fields.

    Deliberately NOT a check that the address is an @wilkesliberty.com one.
    Standing order §2 does require that of the operator, but this control also
    runs on the published projects, where an external contributor's commit is
    the point rather than a defect. A rule that fails every outside pull request
    would be removed within a week, and taking the whole control with it.
    """
    for role, (name, email) in zip(IDENTITY_ROLES, identities):
        identity = " ".join(v for v in (name, email) if v)
        if not identity:
            continue

        hit = AI_IDENTITY_UNAMBIGUOUS.search(identity)
        if hit:
            return f"an AI {role} identity ({hit.group(0)} in: {identity})"

        hit = AI_IDENTITY_AMBIGUOUS.search(identity)
        if hit and AI_IDENTITY_MARKER.search(identity):
            return (f"an AI {role} identity ({hit.group(0)} with a bot/agent "
                    f"marker, in: {identity})")
    return None

# --- END SHARED IDENTITY PATTERNS ---


# ``Co-authored-by: Name <email>`` (and the usual key spelling variants).
# Name + email are both required; a trailer without an address cannot be
# promoted to an identity field.
COAUTHOR_LINE = re.compile(
    r"^Co-?Authored-?By\s*:\s*(.+?)\s*<([^<>]+)>\s*$",
    re.I | re.M,
)

# Operator lock 2026-08-22 (DEV-414): rewrite target when no human
# Co-authored-by is present. GitHub noreply, not @wilkesliberty.com —
# company mail is for identities we stamp at commit time, not for
# restamping history we did not author.
DEFAULT_REWRITE_HUMAN = (
    "Jeremy Michael Cerda",
    "jmcerda@users.noreply.github.com",
)

# STRIP_AUTHOR_* is only consumed when it matches this shape.
NOREPLY_EMAIL = re.compile(r"@users\.noreply\.github\.com\s*$", re.I)

CURSOR_PR_BODY_BEGIN = "<!-- CURSOR_AGENT_PR_BODY_BEGIN -->"
CURSOR_PR_BODY_END = "<!-- CURSOR_AGENT_PR_BODY_END -->"

# Trailing footer Cursor appends after the end marker. Only a <div> at the
# end of the body that mentions cursor.com/agents is removed, so a mention
# of that URL in the human-written summary is left alone.
CURSOR_PR_FOOTER = re.compile(
    r"(?:\r?\n)*<div\b[^>]*>[\s\S]*?cursor\.com/agents[\s\S]*?</div>[ \t]*(?:\r?\n)*\Z",
    re.I,
)


def identity_is_ai(name: str, email: str) -> bool:
    """True when this name+email pair would fail the check's identity scan."""
    return find_identity_attribution(((name, email), ("", ""))) is not None


def identity_is_unambiguous_ai(name: str, email: str) -> bool:
    """True for copilot / Autofix / swe-agent and other unambiguous vendors.

    These must not receive DEFAULT_REWRITE_HUMAN. Without a human
    Co-authored-by or a safe STRIP_AUTHOR_* they stay dirty.
    """
    identity = " ".join(v for v in (name, email) if v)
    if not identity:
        return False
    return AI_IDENTITY_UNAMBIGUOUS.search(identity) is not None


def human_coauthors(message: str) -> List[Tuple[str, str]]:
    """Human ``Co-authored-by`` trailers on a commit message, oldest first."""
    raw = message.replace("\r\n", "\n").replace("\r", "\n")
    found: List[Tuple[str, str]] = []
    for match in COAUTHOR_LINE.finditer(raw):
        name = match.group(1).strip()
        email = match.group(2).strip()
        if name and email and not identity_is_ai(name, email):
            found.append((name, email))
    return found


def env_strip_author() -> Optional[Tuple[str, str]]:
    """Human from STRIP_AUTHOR_NAME + STRIP_AUTHOR_EMAIL, or None.

    Both must be set, pass the identity scan, and use a
    ``@users.noreply.github.com`` address (operator lock 2026-08-22).
    An AI fallback or a company-domain address is treated as missing
    so this script cannot restamp Cursor with Cursor, and cannot
    hardcode ``@wilkesliberty.com`` as a rewrite target.
    """
    name = os.environ.get("STRIP_AUTHOR_NAME", "").strip()
    email = os.environ.get("STRIP_AUTHOR_EMAIL", "").strip()
    if (
        name
        and email
        and not identity_is_ai(name, email)
        and NOREPLY_EMAIL.search(email)
    ):
        return (name, email)
    return None


def resolve_replacement_human(message: str, meta: dict) -> Optional[Tuple[str, str]]:
    """Human to stamp when author and/or committer is an AI identity.

    Operator lock 2026-08-22 (DEV-414): prefer a human Co-authored-by
    already on the commit; else a noreply-shaped STRIP_AUTHOR_* (PR
    opener); else an already-human author slot; else
    DEFAULT_REWRITE_HUMAN (Jeremy noreply) for hosted Cursor-like
    identities. Unambiguous Autofix / copilot without a human trailer
    or safe env is left alone so the check fails closed. Never default
    to @wilkesliberty.com.
    """
    humans = human_coauthors(message)
    if humans:
        return humans[0]
    env = env_strip_author()
    if env:
        return env
    if (
        meta.get("author_name")
        and meta.get("author_email")
        and not identity_is_ai(meta["author_name"], meta["author_email"])
    ):
        return (meta["author_name"], meta["author_email"])
    if identity_is_unambiguous_ai(
        meta.get("author_name") or "", meta.get("author_email") or ""
    ):
        return None
    return DEFAULT_REWRITE_HUMAN


def rewrite_identity(meta: dict, human: Tuple[str, str]) -> Tuple[dict, List[str]]:
    """Return (new_meta, notes). Dates and tree are always preserved.

    Author is restamped only when the current author is an AI identity.
    Committer is restamped only when the current committer is an AI
    identity, and then to the same human. A human in either slot is kept.
    """
    notes: List[str] = []
    new_meta = dict(meta)
    name, email = human
    if identity_is_ai(meta["author_name"], meta["author_email"]):
        new_meta["author_name"] = name
        new_meta["author_email"] = email
        notes.append(
            f"author {meta['author_name']} <{meta['author_email']}> → {name} <{email}>"
        )
    if identity_is_ai(meta["committer_name"], meta["committer_email"]):
        new_meta["committer_name"] = name
        new_meta["committer_email"] = email
        notes.append(
            f"committer {meta['committer_name']} <{meta['committer_email']}> "
            f"→ {name} <{email}>"
        )
    return new_meta, notes


def identity_changed(old: dict, new: dict) -> bool:
    return (
        old["author_name"] != new["author_name"]
        or old["author_email"] != new["author_email"]
        or old["committer_name"] != new["committer_name"]
        or old["committer_email"] != new["committer_email"]
    )


def clean_cursor_pr_body(body: str) -> Optional[str]:
    """Return a cleaned PR body if Cursor cloud wrappers are present, else None.

    Removes the BEGIN/END HTML comments and a trailing
    ``<div>…cursor.com/agents…</div>`` footer. The human-written summary
    between the markers is preserved. Returns None when no wrapper is
    present so callers can no-op without a GitHub write.
    """
    if not body:
        return None
    has_begin = CURSOR_PR_BODY_BEGIN in body
    has_end = CURSOR_PR_BODY_END in body
    has_footer = CURSOR_PR_FOOTER.search(body) is not None
    if not (has_begin or has_end or has_footer):
        return None
    cleaned = body.replace(CURSOR_PR_BODY_BEGIN, "").replace(CURSOR_PR_BODY_END, "")
    cleaned = CURSOR_PR_FOOTER.sub("", cleaned)
    cleaned = re.sub(r"^\s*\n", "", cleaned)
    cleaned = re.sub(r"\n[ \t]*\Z", "\n", cleaned)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def git(args: List[str], *, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=text,
        check=check,
    )


def short(rev: str) -> str:
    return rev[:12] if re.fullmatch(r"[0-9a-f]{40}", rev) else rev


def should_strip_line(line: str) -> bool:
    # Ignore pure whitespace; keep blank lines for structure until we collapse.
    if not line.strip():
        return False
    return any(p.search(line) for p in STRIP_LINE_PATTERNS)


def clean_message(message: str) -> Tuple[str, List[str]]:
    """Return (cleaned_message, removed_lines).

    Preserves the subject line even if it would match a strip pattern, so we
    never produce a subject-less commit. Residual subject-level attribution is
    the check's job.

    Returns the message with its content unchanged when nothing matched, so a
    clean commit is never rewritten. Not byte-for-byte identical: the result is
    always newline-terminated, because `git log` supplies records with the
    trailing newline stripped and `rewrite_range` compares against
    `message + "\n"`. Callers should treat a missing final newline as
    equivalent rather than relying on identity.

    Known limitation, stated because the patterns cannot express the
    difference: matching is on the author *name* as well as the address, so a
    human co-author who happens to be called Claude, Devin or Gemini is
    stripped along with the machines. Erring that way is deliberate -- §1 is
    absolute and a missed trailer is unrecoverable once tagged -- but it is a
    real false positive. Every removed line is reported, so a wrongly dropped
    human co-author is visible in the run and can be restored by hand.
    """
    # Normalize to lines without a guaranteed trailing newline for processing.
    raw = message.replace("\r\n", "\n").replace("\r", "\n")
    if raw.endswith("\n"):
        raw = raw[:-1]
    lines = raw.split("\n")
    if not lines:
        return "\n", []

    removed: List[str] = []
    out: List[str] = [lines[0]]  # subject always kept
    for line in lines[1:]:
        if should_strip_line(line):
            removed.append(line)
            continue
        out.append(line)

    # Whitespace normalization is repair for the gap a removed trailer leaves,
    # so it only runs when something was removed. Doing it unconditionally
    # rewrites and force-pushes commits that were already clean, purely for
    # formatting -- which is outside this tool's stated scope, and would report
    # those commits as having carried attribution when they did not.
    if not removed:
        # Newline-terminated, but otherwise untouched. rewrite_range compares
        # against `message + "\n"`, and git log records arrive stripped of their
        # trailing newline -- so returning the message verbatim made every clean
        # commit compare unequal and get rewritten. That is the exact opposite
        # of what this early return is for.
        return (message if message.endswith("\n") else message + "\n"), []

    # Drop trailing blank lines in the body (keep subject even if alone).
    while len(out) > 1 and out[-1].strip() == "":
        out.pop()
    # Collapse runs of blank lines left by trailer removal (body only).
    collapsed: List[str] = [out[0]]
    blank_run = False
    for line in out[1:]:
        if line.strip() == "":
            if blank_run:
                continue
            blank_run = True
            collapsed.append("")
        else:
            blank_run = False
            collapsed.append(line)

    cleaned = "\n".join(collapsed) + "\n"
    return cleaned, removed


def commits_oldest_first(base: str, head: str) -> List[Tuple[str, str]]:
    """(sha, full message) for each first-parent commit in base..head, oldest first.

    Includes merge commits so a merge of the base branch cannot hide a dirty
    non-merge sitting under it.
    """
    sep = "\x1e"
    out = git(
        [
            "log", "--reverse", "--first-parent",
            f"--format=%H{sep}%B%x00", f"{base}..{head}",
        ]
    ).stdout
    commits: List[Tuple[str, str]] = []
    for record in out.split("\x00"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(sep, 1)
        if len(parts) == 2:
            commits.append((parts[0], parts[1]))
    return commits


def commit_meta(sha: str) -> dict:
    """Author/committer identity and dates for a commit (raw %aN etc.)."""
    fmt = "%aN%x1f%aE%x1f%aD%x1f%cN%x1f%cE%x1f%cD%x1f%T"
    line = git(["log", "-1", f"--format={fmt}", sha]).stdout.rstrip("\n")
    parts = line.split("\x1f")
    if len(parts) != 7:
        raise RuntimeError(f"unexpected meta for {sha}: {line!r}")
    return {
        "author_name": parts[0],
        "author_email": parts[1],
        "author_date": parts[2],
        "committer_name": parts[3],
        "committer_email": parts[4],
        "committer_date": parts[5],
        "tree": parts[6],
    }


def commit_parents(sha: str) -> List[str]:
    """All parent shas, first parent first."""
    out = git(["rev-list", "--parents", "-n", "1", sha]).stdout.strip().split()
    return out[1:]


def first_parent(sha: str) -> Optional[str]:
    parents = commit_parents(sha)
    return parents[0] if parents else None


def write_commit(tree: str, parents: List[str], message: str, meta: dict) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": meta["author_name"],
            "GIT_AUTHOR_EMAIL": meta["author_email"],
            "GIT_AUTHOR_DATE": meta["author_date"],
            "GIT_COMMITTER_NAME": meta["committer_name"],
            "GIT_COMMITTER_EMAIL": meta["committer_email"],
            "GIT_COMMITTER_DATE": meta["committer_date"],
        }
    )
    args = ["commit-tree", tree]
    for parent in parents:
        if parent:
            args.extend(["-p", parent])
    proc = subprocess.run(
        ["git", *args],
        input=message,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return proc.stdout.strip()


def plan_commit(sha: str, message: str) -> dict:
    """Decide whether this commit can be cleaned, and how.

    Returns a dict with cleaned message, removed trailer lines, original
    meta, replacement human (or None), and whether identity / message
    will change. Identity is only marked changeable when a replacement
    human exists — otherwise the check fails closed.
    """
    cleaned, removed = clean_message(message)
    original_norm = message if message.endswith("\n") else message + "\n"
    meta = commit_meta(sha)
    author_ai = identity_is_ai(meta["author_name"], meta["author_email"])
    committer_ai = identity_is_ai(meta["committer_name"], meta["committer_email"])
    identity_dirty = author_ai or committer_ai
    human = resolve_replacement_human(message, meta) if identity_dirty else None
    new_meta = meta
    identity_notes: List[str] = []
    if identity_dirty and human:
        new_meta, identity_notes = rewrite_identity(meta, human)
    return {
        "cleaned": cleaned,
        "removed": removed,
        "original_norm": original_norm,
        "meta": meta,
        "new_meta": new_meta,
        "human": human,
        "identity_dirty": identity_dirty,
        "identity_notes": identity_notes,
        "message_changed": bool(removed) or cleaned != original_norm,
        "identity_changed": identity_changed(meta, new_meta),
    }


def rewrite_range(base: str, head: str) -> Tuple[str, int, List[str]]:
    """Rewrite dirty messages and AI identities on the first-parent chain.

    Merge commits are recreated with remapped parents so a merge of the
    base branch cannot hide a dirty child. Returns (new_head, n_stripped,
    notes).
    """
    commits = commits_oldest_first(base, head)
    if not commits:
        return head, 0, []

    # Map old sha -> new sha for parent retargeting along the first-parent
    # chain. Merge second-parents are remapped when they appear in this walk.
    mapping: dict = {}
    notes: List[str] = []
    stripped = 0
    new_tip = head

    # Walk oldest → newest so parents exist before children.
    for sha, message in commits:
        plan = plan_commit(sha, message)
        old_parents = commit_parents(sha)
        new_parents = [mapping.get(parent, parent) for parent in old_parents]
        meta = plan["meta"]
        new_meta = plan["new_meta"]
        cleaned = plan["cleaned"]
        parents_moved = new_parents != old_parents

        if not plan["message_changed"] and not plan["identity_changed"]:
            # Message and identity unchanged. Still may need a new commit if
            # a parent moved (typical: merge sitting on a rewritten child).
            if parents_moved:
                new_sha = write_commit(meta["tree"], new_parents, cleaned, meta)
                mapping[sha] = new_sha
                new_tip = new_sha
            else:
                mapping[sha] = sha
                new_tip = sha
            continue

        new_sha = write_commit(new_meta["tree"], new_parents, cleaned, new_meta)
        mapping[sha] = new_sha
        new_tip = new_sha
        stripped += 1
        notes.append(
            f"{short(sha)} → {short(new_sha)}: removed {len(plan['removed'])} line(s)"
        )
        for line in plan["removed"]:
            notes.append(f"    - {line.rstrip()}")
        for line in plan["identity_notes"]:
            notes.append(f"    - {line}")

    return new_tip, stripped, notes


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def resolve_branch(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    # Detached HEAD is common in Actions; prefer GITHUB_HEAD_REF.
    env_branch = os.environ.get("GITHUB_HEAD_REF") or os.environ.get("BRANCH")
    if env_branch:
        return env_branch
    ref = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if ref and ref != "HEAD":
        return ref
    raise RuntimeError(
        "cannot determine branch name; pass --branch or set GITHUB_HEAD_REF"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="PR base sha")
    parser.add_argument("--head", required=True, help="PR head sha or ref")
    parser.add_argument(
        "--push",
        action="store_true",
        help="force-with-lease push the rewritten tip to origin",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be stripped without rewriting",
    )
    parser.add_argument("--branch", help="branch name to update / push")
    parser.add_argument(
        "--force-with-lease-ref",
        help="lease spec BRANCH:EXPECTED (default: <branch>:<resolved-head>)",
    )
    args = parser.parse_args()

    try:
        head_sha = git(["rev-parse", args.head]).stdout.strip()
        base_sha = git(["rev-parse", args.base]).stdout.strip()
    except subprocess.CalledProcessError as exc:
        print(
            "::error::could not resolve base/head. Does the checkout use fetch-depth: 0?",
            file=sys.stderr,
        )
        print(exc.stderr or "", file=sys.stderr)
        return 2

    commits = commits_oldest_first(base_sha, head_sha)
    dirty = []
    leftover_identity = []
    for sha, message in commits:
        plan = plan_commit(sha, message)
        if plan["message_changed"] or plan["identity_changed"]:
            dirty.append((sha, plan))
        elif plan["identity_dirty"]:
            leftover_identity.append((sha, plan["meta"]))

    if leftover_identity:
        print(
            "strip-attribution: AI author/committer identity on "
            f"{len(leftover_identity)} commit(s) could not be rewritten; "
            "leaving identity alone (fail closed)"
        )
        for sha, meta in leftover_identity:
            print(
                f"  {short(sha)}: {meta['author_name']} <{meta['author_email']}> "
                f"/ {meta['committer_name']} <{meta['committer_email']}>"
            )

    if not dirty:
        print(f"strip-attribution: clean ({short(base_sha)}..{short(head_sha)}, "
              f"{len(commits)} commit(s))")
        set_output("stripped", "false")
        set_output("count", "0")
        return 0

    print(
        f"strip-attribution: {len(dirty)} commit(s) carry removable AI attribution "
        f"in {short(base_sha)}..{short(head_sha)}"
    )
    for sha, plan in dirty:
        print(f"  {short(sha)}:")
        for line in plan["removed"]:
            print(f"    - {line.rstrip()}")
        for line in plan["identity_notes"]:
            print(f"    - {line}")

    if args.dry_run:
        set_output("stripped", "false")
        set_output("count", str(len(dirty)))
        print("strip-attribution: dry-run only; no rewrite")
        return 0

    try:
        new_tip, count, notes = rewrite_range(base_sha, head_sha)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"::error::rewrite failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr or "", file=sys.stderr)
        set_output("stripped", "false")
        set_output("count", "0")
        return 1

    for note in notes:
        print(note)

    if count == 0:
        # Dirty lines reported but rewrite found nothing (shouldn't happen).
        print("::warning::reported dirty commits but rewrite made no changes")
        set_output("stripped", "false")
        set_output("count", "0")
        return 1

    if new_tip == head_sha:
        print("::error::strip count > 0 but tip unchanged", file=sys.stderr)
        return 1

    # Point HEAD / branch at the rewritten tip locally.
    try:
        branch = resolve_branch(args.branch)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    # Update the local branch ref. Works for attached and for explicit branch.
    git(["update-ref", f"refs/heads/{branch}", new_tip])
    # Move HEAD if we are on a detached checkout of the old tip.
    try:
        git(["checkout", "-B", branch, new_tip])
    except subprocess.CalledProcessError as exc:
        print(f"::warning::checkout -B failed (ref updated anyway): {exc.stderr}")

    if args.push:
        lease = args.force_with_lease_ref or f"{branch}:{head_sha}"
        # Accept either "branch:sha" or full "refs/heads/branch:sha".
        if ":" in lease and not lease.startswith("refs/"):
            b, expected = lease.split(":", 1)
            lease_arg = f"refs/heads/{b}:{expected}"
        elif lease.startswith("refs/"):
            lease_arg = lease
        else:
            lease_arg = f"refs/heads/{branch}:{head_sha}"

        push = subprocess.run(
            [
                "git",
                "push",
                "--force-with-lease=" + lease_arg,
                "origin",
                f"refs/heads/{branch}:{branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if push.returncode != 0:
            print("::error::force-with-lease push failed", file=sys.stderr)
            print(push.stderr or push.stdout or "", file=sys.stderr)
            set_output("stripped", "false")
            set_output("count", "0")
            return 1
        print(f"strip-attribution: pushed {branch} → {short(new_tip)} "
              f"(lease {lease_arg})")

    set_output("stripped", "true")
    set_output("count", str(count))
    print(f"strip-attribution: rewrote {count} commit(s); tip {short(new_tip)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
