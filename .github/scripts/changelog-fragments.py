#!/usr/bin/env python3
"""Changelog fragments: one file per pull request, compiled at release.

Concurrent pull requests used to each insert a bullet under the same
``## [Unreleased]`` list. Adjacent lines conflict, each conflict is another
push, and each push reruns the repository's CI. GitHub's merge button does
not honor a custom merge driver, so ``merge=union`` does not help.

A labelled pull request adds ``changelog.d/<key>-<slug>.md`` instead of
editing that list. The file names the Keep a Changelog section in its front
matter. ``compile`` folds every fragment into one ``## [X.Y.Z] - YYYY-MM-DD``
section and deletes the fragments. Notes already under ``[Unreleased]`` are
carried into that section ahead of the fragment groups, and ``[Unreleased]``
is left empty. The shared release workflow keeps reading the versioned
heading, so the notes it publishes stay the section it already extracts.

``check`` is the pull-request gate:

* always — fragment syntax, and the structural rules that used to live in
  each repository's changelog script (one ``[Unreleased]``, a release heading
  after it, known and ordered ``###`` headings, no released heading deleted);
* with ``--require-entry`` — a changelog-labelled pull request added exactly
  one new fragment and did not touch ``CHANGELOG.md``, or it is the release
  compile (the version heading appeared and the fragments were deleted).

Stdlib only. The workflow fetches this file from Wilkes-Liberty/shared-ci;
callers do not vendor it.

Exit 0 when clean, 1 when a finding is printed, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Keep a Changelog 1.1.0 order. Callers append their own headings after these
# with --extra-sections; they do not get to reorder the six.
SECTIONS = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

UNRELEASED = "## [Unreleased]"
CALENDAR_HEADING = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]$")
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")


def unreleased_heading(style: str) -> str:
    return "## Unreleased" if style == "rolling" else UNRELEASED

VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]")
DATED_VERSION_HEADING = re.compile(
    r"^## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$"
)
LEGACY_HEADING = re.compile(r"^## v\d+\.\d+\.\d+")
FRAGMENT_NAME = re.compile(
    r"^(?=.*-)[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]\.md$"
)
CONFLICT_MARKER = re.compile(r"^(?:<{7,}|={7,}|>{7,}|\|{7,})(?:[ \t].*)?$", re.M)
H3 = re.compile(r"^### (.+?)\s*$", re.M)
HEADING_LINE = re.compile(r"^#{2,3} ", re.M)
EXTRA_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")


def escape(text: str) -> str:
    """Percent-encode annotation text.

    GitHub Actions decodes ``%0A`` inside ``::error::`` into a real newline,
    which ends the annotation. Fragment text is pull-request input.
    """
    return (
        text.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def emit(errors: list[str], ok: str) -> int:
    if not errors:
        print(ok)
        return 0
    for error in errors:
        print(f"::error::{escape(error)}")
    return 1


def section_order(extra: list[str]) -> list[str]:
    order = list(SECTIONS)
    for name in extra:
        if name in order:
            raise ValueError(
                f"{name} is already a Keep a Changelog section; "
                "do not repeat it in --extra-sections"
            )
        if not EXTRA_NAME.fullmatch(name):
            raise ValueError(
                f"extra section {name!r} must look like 'Docs' "
                "(one capital letter, then letters or digits)"
            )
        order.append(name)
    return order


def parse_extra(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def fragment_filename_error(name: str) -> str | None:
    if name.startswith("."):
        return None
    if not FRAGMENT_NAME.fullmatch(name):
        return (
            f"changelog fragment {name} must be named "
            "<key>-<slug>.md (letters, digits, '.', '_' or '-'; "
            "at least one hyphen; no directories)"
        )
    return None


def parse_fragment(path: Path, allowed: list[str]) -> tuple[str, str] | str:
    """Return ``(section, body)`` or an error string."""
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return f"{path.name} is not UTF-8"
    return parse_fragment_text(text, path.name, allowed)


def parse_fragment_text(text: str, name: str, allowed: list[str]) -> tuple[str, str] | str:
    if CONFLICT_MARKER.search(text):
        return f"{name} contains an unresolved conflict marker"
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return f"{name} must open with a '---' front matter block"
    lines = text.splitlines()
    close_at = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            close_at = index
            break
    if close_at is None:
        return f"{name} front matter is not closed with '---'"
    raw = "\n".join(lines[1:close_at])
    body = "\n".join(lines[close_at + 1 :])
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            return f"{name} front matter line {line!r} is not 'key: value'"
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            return f"{name} has an empty front matter key"
        if key in fields:
            return f"{name} repeats front matter key {key}"
        fields[key] = value
    unknown = sorted(set(fields) - {"section"})
    if unknown:
        return (
            f"{name} front matter has unknown "
            f"key{'s' if len(unknown) != 1 else ''} {', '.join(unknown)}; "
            "only 'section' is read"
        )
    section = fields.get("section", "")
    if section not in allowed:
        listed = ", ".join(allowed)
        return (
            f"{name} section {section!r} is not one of: {listed}"
        )
    if not body.strip():
        return f"{name} has an empty body"
    if HEADING_LINE.search(body):
        return (
            f"{name} body contains a markdown heading; "
            "the compile step writes the headings"
        )
    return section, body.strip("\n")


def render_bullet(body: str) -> str:
    """One fragment body becomes one or more markdown list items.

    Authors may write the prose bare (a dash is added) or start with ``- ``.
    Continuation lines are indented so they stay inside the item. A later
    line that already starts with ``- `` is a further item in the same entry.
    """
    lines = body.split("\n")
    out: list[str] = []
    first = True
    for line in lines:
        if first:
            out.append(line if line.startswith("- ") else "- " + line)
            first = False
            continue
        if line.strip() == "":
            out.append("")
        elif line.startswith(("  ", "\t", "- ")):
            out.append(line)
        else:
            out.append("  " + line)
    return "\n".join(out)


def load_fragments(
    directory: Path, allowed: list[str]
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return ``(filename, section, body)`` sorted by filename, plus errors."""
    errors: list[str] = []
    found: list[tuple[str, str, str]] = []
    if directory.is_symlink():
        return found, [f"{directory} must not be a symlink"]
    if not directory.exists():
        return found, errors
    if not directory.is_dir():
        return found, [f"{directory} is not a directory"]
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            errors.append(f"{path.name} must not be a symlink")
            continue
        if path.name.startswith("."):
            continue
        if not path.is_file():
            errors.append(
                f"{directory.name}/{path.name} is a directory; "
                "fragments are files, one per pull request"
            )
            continue
        name_error = fragment_filename_error(path.name)
        if name_error:
            errors.append(name_error)
            continue
        parsed = parse_fragment(path, allowed)
        if isinstance(parsed, str):
            errors.append(parsed)
            continue
        section, body = parsed
        found.append((path.name, section, body))
    return found, errors


def release_heading_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped == UNRELEASED:
            continue
        if VERSION_HEADING.match(stripped) or LEGACY_HEADING.match(stripped) or CALENDAR_HEADING.match(stripped):
            lines.append(stripped)
    return lines


def h2_sections(text: str) -> list[tuple[str, str]]:
    """Return ``(heading line, body)`` for every ``## `` section."""
    sections: list[tuple[str, str]] = []
    current: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections.append((current, "\n".join(body)))
            current = line.rstrip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections.append((current, "\n".join(body)))
    return sections


def heading_order_errors(title: str, body: str, allowed: list[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    previous = -1
    previous_name = ""
    for match in H3.finditer(body):
        name = match.group(1).strip()
        if name not in allowed:
            listed = ", ".join(allowed)
            errors.append(
                f"unknown heading '### {name}' under {title} — "
                f"expected one of: {listed}"
            )
            continue
        if name in seen:
            errors.append(
                f"duplicate '### {name}' under {title} — "
                "merge both blocks into one"
            )
        rank = allowed.index(name)
        if previous >= 0 and rank < previous:
            errors.append(
                f"'### {name}' must come before '### {previous_name}' "
                f"under {title} (order: {', '.join(allowed)})"
            )
        previous = rank
        previous_name = name
        seen.add(name)
    return errors


def structure_errors(text: str, allowed: list[str], style: str = "versioned",
                     allow_initial: bool = False, preserve_history: bool = False) -> list[str]:
    pending = unreleased_heading(style)
    errors: list[str] = []
    if CONFLICT_MARKER.search(text):
        errors.append("changelog contains an unresolved conflict marker")
    unreleased = [
        line for line in text.splitlines() if line.rstrip() == pending
    ]
    if len(unreleased) != 1:
        errors.append(
            f"expected exactly one '{pending}' heading, found {len(unreleased)}"
        )
        return errors

    sections = h2_sections(text)
    seen_unreleased = False
    followed = False
    for title, body in sections:
        if title == pending:
            seen_unreleased = True
            errors.extend(heading_order_errors(title, body, allowed))
            continue
        if seen_unreleased and not followed and VERSION_HEADING.match(title):
            followed = True
        if style == "versioned" and not preserve_history and title.startswith("## "):
            errors.extend(heading_order_errors(title, body, allowed))
    if not followed and style == "versioned" and not allow_initial:
        errors.append(
            "no '## [x.y.z]' release heading follows [Unreleased] — "
            "a release heading was probably deleted"
        )
    return errors


def deleted_heading_errors(base_text: str, head_text: str, base_name: str) -> list[str]:
    base = Counter(release_heading_lines(base_text))
    head = Counter(release_heading_lines(head_text))
    errors = []
    for heading, count in base.items():
        missing = count - head[heading]
        if missing > 0:
            errors.append(
                f"release heading '{heading}' exists on {base_name} "
                f"but not here ({missing} deleted) — a shipped release was dropped"
            )
    return errors


def parse_name_status(blob: str) -> list[tuple[str, str, str | None]]:
    """Parse ``git diff --name-status -z`` records.

    Rename and copy records carry two paths. A trailing empty field from the
    final NUL is dropped by ``split``.
    """
    parts = blob.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    records: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if not status:
            continue
        if status[0] in ("R", "C"):
            if index + 1 >= len(parts):
                raise ValueError("truncated rename record in git diff output")
            old = parts[index]
            new = parts[index + 1]
            index += 2
            records.append((status, old, new))
        else:
            if index >= len(parts):
                raise ValueError("truncated name-status record in git diff output")
            records.append((status, parts[index], None))
            index += 1
    return records


def compilable_fragment_name(name: str) -> bool:
    """True when ``load_fragments`` would fold this filename into a release.

    Hidden names are skipped, and any other name that is not
    ``<key>-<slug>.md`` is a structure error rather than an entry. The
    labelled-PR rule has to use that same set. Counting a hidden file as
    the one required fragment lets the pull request pass with nothing the
    compiler keeps, and counting a hidden deletion as a consumed fragment
    lets a release heading pass while real entries stay uncompiled.
    """
    if not name or name.startswith(".") or "/" in name:
        return False
    return fragment_filename_error(name) is None


def is_fragment_path(path: str, fragments: str) -> bool:
    prefix = fragments.strip("/") + "/"
    if not path.startswith(prefix):
        return False
    return compilable_fragment_name(path[len(prefix) :])


def entry_errors(
    cwd: Path,
    base: str,
    head: str,
    changelog: str,
    fragments: str,
    allowed: list[str] | None = None,
    style: str = "versioned",
    required: bool = True,
    allow_initial: bool = False,
    preserve_history: bool = False,
) -> list[str]:
    diff = git(
        ["diff", "--name-status", "--find-renames", "-z", f"{base}...{head}"],
        cwd,
    )
    if diff.returncode != 0:
        detail = (diff.stderr or diff.stdout or "git diff failed").strip()
        return [f"could not read the pull request diff ({detail})"]
    try:
        records = parse_name_status(diff.stdout)
    except ValueError as exc:
        return [str(exc)]

    added: list[str] = []
    deleted: list[str] = []
    other: list[str] = []
    changelog_changed = False
    for status, path, extra in records:
        paths = [path] if extra is None else [path, extra]
        if changelog in paths:
            changelog_changed = True
        kind = status[0]
        if kind == "A" and is_fragment_path(path, fragments):
            added.append(path)
        elif kind == "D" and is_fragment_path(path, fragments):
            deleted.append(path)
        elif kind in ("R", "C"):
            if any(is_fragment_path(item, fragments) for item in paths):
                other.append(f"{status} {path} -> {extra}")
        elif is_fragment_path(path, fragments):
            other.append(f"{status} {path}")

    fragment_only = (
        len(added) == 1 and not deleted and not other and not changelog_changed
    )
    if fragment_only or (not required and not added and not deleted and not other and not changelog_changed):
        return []

    if changelog_changed and not added and not other:
        # Reproduce compilation from the merge base. A heading and deleted
        # files alone do not prove their contents survived in the release.
        merged = git(["merge-base", base, head], cwd)
        if merged.returncode:
            return ["could not find the merge base for compile verification"]
        revision = merged.stdout.strip()
        previous = show_file(cwd, revision, changelog)
        current = show_file(cwd, head, changelog)
        if previous is None or current is None:
            return ["could not read changelog snapshots for compile verification"]
        names = git(["ls-tree", "-r", "--name-only", revision, "--", fragments], cwd)
        expected_paths = [name for name in names.stdout.splitlines()
                          if is_fragment_path(name, fragments)]
        if names.returncode or set(expected_paths) != set(deleted):
            return ["release compile must consume every pending fragment"]
        entries = []
        for path in sorted(expected_paths):
            raw = show_file(cwd, revision, path)
            if raw is None:
                return ["could not read a consumed fragment"]
            parsed = parse_fragment_text(raw, Path(path).name, allowed or list(SECTIONS))
            if isinstance(parsed, str):
                return [parsed]
            entries.append((Path(path).name, *parsed))
        old_headings = {title for title, _ in h2_sections(previous)}
        new_headings = [title for title, _ in h2_sections(current)
                        if title not in old_headings]
        if len(new_headings) != 1:
            return ["release compile must add exactly one archive heading"]
        title = new_headings[0]
        match = (re.fullmatch(r"## \[(.+)\] - (\d{4}-\d{2}-\d{2})", title)
                 if style == "versioned" else CALENDAR_HEADING.fullmatch(title))
        if match is None:
            return ["release compile has an invalid archive heading"]
        version = match[1] if style == "versioned" else None
        date = match[2] if style == "versioned" else match[1]
        try:
            expected = compile_text(previous, entries, allowed or list(SECTIONS),
                                    style, version, date, allow_initial, preserve_history)
        except ValueError as exc:
            return [f"release compile: {exc}"]
        if current == expected:
            return []
        return ["release compile differs from the lossless compiler output; notes or history changed"]

    detail = (
        f"added {len(added)} fragment(s), deleted {len(deleted)}, "
        f"other fragment changes {len(other)}, "
        f"CHANGELOG.md {'changed' if changelog_changed else 'unchanged'}"
    )
    return [
        "a pull request requiring an entry must add exactly one "
        "changelog.d/<key>-<slug>.md fragment and leave CHANGELOG.md "
        "untouched, or be the release compile (CHANGELOG.md gains a "
        "'## [X.Y.Z] - YYYY-MM-DD' heading and the consumed fragments "
        f"are deleted). Optional entries must follow the same storage rules ({detail})"
    ]


def show_file(cwd: Path, rev: str, path: str) -> str | None:
    shown = git(["show", f"{rev}:{path}"], cwd)
    if shown.returncode != 0:
        return None
    return shown.stdout


def partition_unreleased(
    body: str, allowed: list[str]
) -> tuple[str, dict[str, str]] | str:
    """Split an ``[Unreleased]`` body into a preamble and ``###`` blocks.

    Blocks keep the author's bytes. Compile appends new bullets under the
    same heading instead of rewriting the hand-written ones, and it refuses
    an unknown or duplicate heading rather than dropping notes.
    """
    if not H3.search(body):
        return body.strip("\n"), {}
    parts = re.split(r"(?m)^### ", body)
    preamble = parts[0].strip("\n")
    blocks: dict[str, str] = {}
    for part in parts[1:]:
        heading, _sep, rest = part.partition("\n")
        heading = heading.strip()
        if heading not in allowed:
            listed = ", ".join(allowed)
            return (
                f"[Unreleased] contains unknown heading '### {heading}' "
                f"— expected one of: {listed}"
            )
        if heading in blocks:
            return (
                f"[Unreleased] contains duplicate '### {heading}' — "
                "merge both blocks before compiling"
            )
        blocks[heading] = rest.strip("\n")
    return preamble, blocks


def render_release(preamble: str, existing: dict[str, str],
                   fragments: list[tuple[str, str, str]],
                   allowed: list[str]) -> str:
    grouped: dict[str, list[str]] = {name: [] for name in allowed}
    for _name, section, body in fragments:
        grouped[section].append(render_bullet(body))
    chunks: list[str] = []
    if preamble.strip():
        chunks.append(preamble.strip("\n"))
    for name in allowed:
        raw = existing.get(name, "")
        bullets = grouped[name]
        if not raw and not bullets:
            continue
        lines = [f"### {name}"]
        if raw:
            lines.append(raw)
        lines.extend(bullets)
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks).rstrip() + "\n"


def splice_version(text: str, version: str | None, date: str, body: str,
                   style: str = "versioned") -> str:
    """Insert the version section after ``[Unreleased]`` and empty that section.

    The bytes from the following release heading to the end of the file are
    copied unchanged. Raises ``ValueError`` when ``[Unreleased]`` is missing
    or repeated — the structural check reports that case first, and compile
    refuses to guess which block to replace.
    """
    pending = unreleased_heading(style)
    archive = f"## [{version}] - {date}" if style == "versioned" else f"## [{date}]"
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == pending:
            if start is not None:
                raise ValueError(f"more than one '{pending}' heading")
            start = index
        elif start is not None and end is None and line.startswith("## "):
            end = index
            break
    if start is None:
        raise ValueError(f"no '{pending}' heading to compile into")
    tail = "".join(lines[end:]) if end is not None else ""
    if not body.endswith("\n"):
        body += "\n"
    # A blank line between the new section and the previous release heading.
    spacer = "\n" if tail else ""
    insertion = (
        f"{pending}\n"
        f"\n"
        f"{archive}\n"
        f"\n"
        f"{body}"
        f"{spacer}"
    )
    prefix = "".join(lines[:start])
    return prefix + insertion + tail


def compile_text(
    text: str,
    found: list[tuple[str, str, str]],
    allowed: list[str],
    style: str,
    version: str | None,
    date: str,
    allow_initial: bool = False,
    preserve_history: bool = False,
) -> str:
    if style == "versioned" and (version is None or not SEMVER.fullmatch(version)):
        raise ValueError("--version must be MAJOR.MINOR.PATCH[-suffix]")
    if style != "versioned" and version is not None:
        raise ValueError("--version is only valid for versioned changelogs")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError("date shape")
        dt.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("--date must be YYYY-MM-DD") from exc
    archive = f"## [{version}]" if style == "versioned" else f"## [{date}]"
    if any(line == archive or line.startswith(archive + " ")
           for line in text.splitlines()):
        raise ValueError(f"changelog already has a '{archive}' heading")
    errors = structure_errors(text, allowed, style, allow_initial, preserve_history)
    if errors:
        raise ValueError("; ".join(errors))
    body = next(body for title, body in h2_sections(text)
                if title == unreleased_heading(style))
    partitioned = partition_unreleased(body, allowed)
    if isinstance(partitioned, str):
        raise ValueError(partitioned)
    preamble, existing = partitioned
    rendered = render_release(preamble, existing, found, allowed)
    substantive = re.sub(r"<!--[\s\S]*?-->", "", rendered)
    if not any(line.strip() and not re.match(r"^\s*(?:#{1,6}(?:\s|$)|\[[^]]+\]:)", line)
               for line in substantive.splitlines()):
        raise ValueError("no substantive pending notes to compile")
    return splice_version(text, version, date, rendered, style)


def cmd_check(args: argparse.Namespace) -> int:
    try:
        allowed = section_order(parse_extra(args.extra_sections))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    changelog = Path(args.changelog)
    fragments = Path(args.fragments)
    if changelog.is_symlink() or not changelog.is_file():
        print(f"::error::cannot read {changelog}")
        return 1
    try:
        text = read_text(changelog)
    except UnicodeDecodeError:
        print(f"::error::{escape(str(changelog))} is not UTF-8")
        return 1

    errors = structure_errors(text, allowed, args.format, args.allow_initial_release, args.preserve_history)
    _found, fragment_errors = load_fragments(fragments, allowed)
    errors.extend(fragment_errors)

    if args.base:
        cwd = Path.cwd()
        previous = show_file(cwd, args.base, args.changelog)
        if previous is None:
            print(
                f"note: {args.changelog} is not on {args.base}; "
                "skipping the deleted-release check"
            )
        else:
            errors.extend(deleted_heading_errors(previous, text, args.base))
        if args.require_entry or args.enforce_fragments:
            head = args.head or "HEAD"
            errors.extend(
                entry_errors(cwd, args.base, head, args.changelog, args.fragments,
                             allowed, args.format, args.require_entry, args.allow_initial_release, args.preserve_history)
            )
    elif args.require_entry or args.enforce_fragments:
        print("error: --require-entry needs --base", file=sys.stderr)
        return 2

    return emit(errors, "Changelog fragments OK.")


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        allowed = section_order(parse_extra(args.extra_sections))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "versioned" and (args.version is None or not SEMVER.fullmatch(args.version)):
        print("error: --version must be MAJOR.MINOR.PATCH[-suffix]", file=sys.stderr)
        return 2
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            raise ValueError("date shape")
        dt.date.fromisoformat(args.date)
    except ValueError:
        print("error: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2

    changelog = Path(args.changelog)
    fragments = Path(args.fragments)
    if changelog.is_symlink() or not changelog.is_file():
        print(f"error: cannot read {changelog}", file=sys.stderr)
        return 2
    try:
        text = read_text(changelog)
    except UnicodeDecodeError:
        print(f"error: {changelog} is not UTF-8", file=sys.stderr)
        return 1

    archive = f"## [{args.version}]" if args.format == "versioned" else f"## [{args.date}]"
    if any(line == archive or line.startswith(archive + " ") for line in text.splitlines()):
        print(f"error: changelog already has a '{archive}' heading", file=sys.stderr)
        return 1

    found, errors = load_fragments(fragments, allowed)
    if errors:
        for error in errors:
            print(f"::error::{escape(error)}")
        return 1
    try:
        spliced = compile_text(text, found, allowed, args.format, args.version,
                               args.date, args.allow_initial_release, args.preserve_history)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "render":
        print(spliced, end="")
        return 0

    # Exclusive creation prevents a pre-existing temporary symlink from being
    # followed. Keep the temporary beside the destination for atomic replace.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=changelog.parent,
            prefix=f".{changelog.name}.", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(spliced)
        os.chmod(temporary, changelog.stat().st_mode & 0o777)
        os.replace(temporary, changelog)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    for name, _section, _body in found:
        (fragments / name).unlink()
    print(
        f"Compiled {len(found)} fragment(s) into {args.version or args.date}."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate fragments and CHANGELOG.md")
    check.add_argument("--changelog", default="CHANGELOG.md")
    check.add_argument("--fragments", default="changelog.d")
    check.add_argument(
        "--base",
        help="git rev whose release headings must still be present",
    )
    check.add_argument("--head", default="HEAD")
    check.add_argument(
        "--require-entry",
        action="store_true",
        help="enforce the changelog-label rule against base...head",
    )
    check.add_argument(
        "--extra-sections",
        default="",
        help="comma-separated ### headings allowed after the Keep a Changelog six",
    )
    check.add_argument("--enforce-fragments", action="store_true",
                       help="reject shared changelog edits even when no entry is required")
    check.add_argument("--format", choices=("versioned", "calendar", "rolling"), default="versioned")
    check.add_argument("--allow-initial-release", action="store_true")
    check.add_argument("--preserve-history", action="store_true",
                       help="validate pending sections without reformatting existing archives")
    check.set_defaults(func=cmd_check)

    for command in ("compile", "render"):
        compile_cmd = sub.add_parser(
            command,
            help="compile fragments into an archive; render previews without writes",
        )
        compile_cmd.add_argument("--changelog", default="CHANGELOG.md")
        compile_cmd.add_argument("--fragments", default="changelog.d")
        compile_cmd.add_argument("--version")
        compile_cmd.add_argument("--format", choices=("versioned", "calendar", "rolling"), default="versioned")
        compile_cmd.add_argument("--allow-initial-release", action="store_true")
        compile_cmd.add_argument("--preserve-history", action="store_true")
        compile_cmd.add_argument("--date", required=True)
        compile_cmd.add_argument("--extra-sections", default="")
        compile_cmd.set_defaults(func=cmd_compile)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
