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
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")
DATED_VERSION_HEADING = re.compile(
    r"^## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$"
)
LEGACY_HEADING = re.compile(r"^## v\d+\.\d+\.\d+")
FRAGMENT_NAME = re.compile(
    r"^(?=.*-)[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]\.md$"
)
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
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return f"{path.name} must open with a '---' front matter block"
    lines = text.splitlines()
    close_at = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            close_at = index
            break
    if close_at is None:
        return f"{path.name} front matter is not closed with '---'"
    raw = "\n".join(lines[1:close_at])
    body = "\n".join(lines[close_at + 1 :])
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            return f"{path.name} front matter line {line!r} is not 'key: value'"
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            return f"{path.name} has an empty front matter key"
        if key in fields:
            return f"{path.name} repeats front matter key {key}"
        fields[key] = value
    unknown = sorted(set(fields) - {"section"})
    if unknown:
        return (
            f"{path.name} front matter has unknown "
            f"key{'s' if len(unknown) != 1 else ''} {', '.join(unknown)}; "
            "only 'section' is read"
        )
    section = fields.get("section", "")
    if section not in allowed:
        listed = ", ".join(allowed)
        return (
            f"{path.name} section {section!r} is not one of: {listed}"
        )
    if not body.strip():
        return f"{path.name} has an empty body"
    if HEADING_LINE.search(body):
        return (
            f"{path.name} body contains a markdown heading; "
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
    if not directory.exists():
        return found, errors
    if not directory.is_dir():
        return found, [f"{directory} is not a directory"]
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            continue
        if path.is_dir():
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
        if VERSION_HEADING.match(stripped) or LEGACY_HEADING.match(stripped):
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


def structure_errors(text: str, allowed: list[str]) -> list[str]:
    errors: list[str] = []
    unreleased = [
        line for line in text.splitlines() if line.rstrip() == UNRELEASED
    ]
    if len(unreleased) != 1:
        errors.append(
            f"expected exactly one '{UNRELEASED}' heading, found {len(unreleased)}"
        )
        return errors

    sections = h2_sections(text)
    seen_unreleased = False
    followed = False
    for title, body in sections:
        if title == UNRELEASED:
            seen_unreleased = True
            errors.extend(heading_order_errors(title, body, allowed))
            continue
        if seen_unreleased and not followed and VERSION_HEADING.match(title):
            followed = True
        if title.startswith("## "):
            errors.extend(heading_order_errors(title, body, allowed))
    if not followed:
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
    if fragment_only:
        return []

    release = False
    if changelog_changed and deleted and not added and not other:
        shown = git(["diff", "-U0", f"{base}...{head}", "--", changelog], cwd)
        if shown.returncode != 0:
            return ["could not read the CHANGELOG diff for the release compile"]
        for line in shown.stdout.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            if DATED_VERSION_HEADING.match(line[1:]):
                release = True
                break
    if release:
        return []

    detail = (
        f"added {len(added)} fragment(s), deleted {len(deleted)}, "
        f"other fragment changes {len(other)}, "
        f"CHANGELOG.md {'changed' if changelog_changed else 'unchanged'}"
    )
    return [
        "a changelog-labelled pull request must add exactly one "
        "changelog.d/<key>-<slug>.md fragment and leave CHANGELOG.md "
        "untouched, or be the release compile (CHANGELOG.md gains a "
        "'## [X.Y.Z] - YYYY-MM-DD' heading and the consumed fragments "
        f"are deleted). This diff did neither ({detail})"
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


def splice_version(text: str, version: str, date: str, body: str) -> str:
    """Insert the version section after ``[Unreleased]`` and empty that section.

    The bytes from the following release heading to the end of the file are
    copied unchanged. Raises ``ValueError`` when ``[Unreleased]`` is missing
    or repeated — the structural check reports that case first, and compile
    refuses to guess which block to replace.
    """
    lines = text.splitlines(keepends=True)
    start = None
    end = None
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == UNRELEASED:
            if start is not None:
                raise ValueError(f"more than one '{UNRELEASED}' heading")
            start = index
        elif start is not None and end is None and line.startswith("## "):
            end = index
            break
    if start is None:
        raise ValueError(f"no '{UNRELEASED}' heading to compile into")
    tail = "".join(lines[end:]) if end is not None else ""
    if not body.endswith("\n"):
        body += "\n"
    # A blank line between the new section and the previous release heading.
    spacer = "\n" if tail else ""
    insertion = (
        f"{UNRELEASED}\n"
        f"\n"
        f"## [{version}] - {date}\n"
        f"\n"
        f"{body}"
        f"{spacer}"
    )
    prefix = "".join(lines[:start])
    return prefix + insertion + tail


def cmd_check(args: argparse.Namespace) -> int:
    try:
        allowed = section_order(parse_extra(args.extra_sections))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    changelog = Path(args.changelog)
    fragments = Path(args.fragments)
    if not changelog.is_file():
        print(f"::error::cannot read {changelog}")
        return 1
    try:
        text = read_text(changelog)
    except UnicodeDecodeError:
        print(f"::error::{escape(str(changelog))} is not UTF-8")
        return 1

    errors = structure_errors(text, allowed)
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
        if args.require_entry:
            head = args.head or "HEAD"
            errors.extend(
                entry_errors(cwd, args.base, head, args.changelog, args.fragments)
            )
    elif args.require_entry:
        print("error: --require-entry needs --base", file=sys.stderr)
        return 2

    return emit(errors, "Changelog fragments OK.")


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        allowed = section_order(parse_extra(args.extra_sections))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print("error: --version must be MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        print("error: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2

    changelog = Path(args.changelog)
    fragments = Path(args.fragments)
    if not changelog.is_file():
        print(f"error: cannot read {changelog}", file=sys.stderr)
        return 2
    try:
        text = read_text(changelog)
    except UnicodeDecodeError:
        print(f"error: {changelog} is not UTF-8", file=sys.stderr)
        return 1

    if any(
        line.rstrip() == f"## [{args.version}]"
        or line.startswith(f"## [{args.version}] ")
        or line.startswith(f"## [{args.version}] -")
        for line in text.splitlines()
    ):
        print(
            f"error: {changelog} already has a '## [{args.version}]' heading",
            file=sys.stderr,
        )
        return 1

    found, errors = load_fragments(fragments, allowed)
    if errors:
        for error in errors:
            print(f"::error::{escape(error)}")
        return 1
    if not found:
        print(
            f"error: no fragments in {fragments} to compile",
            file=sys.stderr,
        )
        return 1

    errors = structure_errors(text, allowed)
    if errors:
        for error in errors:
            print(f"::error::{escape(error)}")
        return 1

    sections = h2_sections(text)
    unreleased_body = ""
    for title, body in sections:
        if title == UNRELEASED:
            unreleased_body = body
            break
    partitioned = partition_unreleased(unreleased_body, allowed)
    if isinstance(partitioned, str):
        print(f"::error::{escape(partitioned)}")
        return 1
    preamble, existing = partitioned
    rendered = render_release(preamble, existing, found, allowed)
    try:
        spliced = splice_version(text, args.version, args.date, rendered)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    temporary = changelog.with_name(changelog.name + ".tmp")
    temporary.write_text(spliced, encoding="utf-8")
    os.replace(temporary, changelog)
    for name, _section, _body in found:
        (fragments / name).unlink()
    print(
        f"Compiled {len(found)} fragment(s) into ## [{args.version}] - {args.date}."
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
    check.set_defaults(func=cmd_check)

    compile_cmd = sub.add_parser(
        "compile",
        help="fold fragments into a versioned CHANGELOG section and delete them",
    )
    compile_cmd.add_argument("--changelog", default="CHANGELOG.md")
    compile_cmd.add_argument("--fragments", default="changelog.d")
    compile_cmd.add_argument("--version", required=True)
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
