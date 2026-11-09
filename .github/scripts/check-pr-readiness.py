#!/usr/bin/env python3
"""Classify pull-request readiness from check runs and check suites.

A check-run-only view misses a completed ``action_required`` check suite
that has no check-run entry. Review and review-comment events from Copilot
create that shape before any jobs start, so lint can look successful or
expected-skip while GitHub still reports an unstable merge state.

This script inspects check-suite conclusions as well as check runs and
names the review-triggered workflow run that needs operator attention.
It does not approve or rerun anything: an operator rerun of that named
run keeps existing invocation guards (lint jobs run; an explicitly
invoked assistant job stays skipped when no invocation is present).

Usage:
  check-pr-readiness.py [--repo owner/name] [--pr N] [--sha SHA]

Exits 0 when the completed picture is success or expected skip, 1 when a
failure or ``action_required`` suite blocks readiness, 2 on a usage or
API error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import NamedTuple

REVIEW_EVENTS = frozenset({"pull_request_review", "pull_request_review_comment"})
BLOCKING_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "startup_failure", "stale"}
)
OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
ATTENTION_CONCLUSIONS = frozenset({"action_required"})
WAITING_STATUSES = frozenset({"waiting", "waiting_for_review", "requested", "pending"})
INCOMPLETE_STATUSES = frozenset({"queued", "in_progress"}) | WAITING_STATUSES

RERUN_NOTE = (
    "An operator rerun keeps existing invocation guards: lint jobs run, "
    "and an explicitly invoked assistant job stays skipped when no "
    "invocation is present. Do not approve the waiting run from this "
    "diagnostic; that would authorize workflow code from an untrusted "
    "review event."
)


def fail(message: str, code: int = 2) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


class Finding(NamedTuple):
    kind: str
    name: str
    detail: str
    url: str
    event: str
    run_id: int | None


class Verdict(NamedTuple):
    kind: str
    findings: tuple[Finding, ...]


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def check_suite_id(item: dict) -> int | None:
    suite = item.get("check_suite")
    if isinstance(suite, dict):
        return _as_int(suite.get("id"))
    return _as_int(item.get("check_suite_id"))


def run_count_for_suite(suite: dict, check_runs: list[dict]) -> int:
    reported = suite.get("latest_check_runs_count")
    if isinstance(reported, int):
        return reported
    suite_id = _as_int(suite.get("id"))
    if suite_id is None:
        return 0
    return sum(1 for run in check_runs if check_suite_id(run) == suite_id)


def workflow_for_suite(suite: dict, workflow_runs: list[dict]) -> dict:
    suite_id = _as_int(suite.get("id"))
    if suite_id is None:
        return {}
    for run in workflow_runs:
        if _as_int(run.get("check_suite_id")) == suite_id:
            return run
    return {}


def actor_login(item: dict) -> str:
    for key in ("triggering_actor", "actor"):
        login = _as_str(_as_dict(item.get(key)).get("login"))
        if login:
            return login
    return ""


def app_name(item: dict) -> str:
    app = item.get("app")
    if isinstance(app, dict):
        return _as_str(app.get("name"))
    return _as_str(app)


def display_name(item: dict) -> str:
    """Return the item's own name, or "" when it has none.

    Callers apply a generic label only after choosing between the workflow
    and the suite. ``app.name`` is a string; wrapping it in ``_as_dict``
    drops it and makes every unnamed suite look like "check suite".
    """
    for key in ("name", "display_title"):
        name = _as_str(item.get(key))
        if name:
            return name
    return app_name(item)


def item_url(item: dict) -> str:
    for key in ("html_url", "url"):
        url = _as_str(item.get(key))
        if url:
            return url
    return ""


def is_ignored(name: str, ignore_names: frozenset[str]) -> bool:
    """Match a check name, a workflow path, or the called-job suffix.

    Reusable-workflow checks are recorded as ``{caller job} / {called job}``.
    An ignore token of ``PR readiness`` therefore matches
    ``readiness / PR readiness`` and does not match ``readiness / lint``.
    """
    folded = name.casefold().strip()
    if not folded:
        return False
    tokens = {token.casefold().strip() for token in ignore_names if token and token.strip()}
    if folded in tokens:
        return True
    if " / " not in folded:
        return False
    return folded.rsplit(" / ", 1)[-1].strip() in tokens


def matches_ignore(item: dict, ignore_names: frozenset[str]) -> bool:
    return is_ignored(display_name(item), ignore_names) or is_ignored(
        _as_str(item.get("path")), ignore_names
    )


def classify(
    check_runs: list[dict],
    check_suites: list[dict],
    workflow_runs: list[dict] | None = None,
    ignore_names: frozenset[str] | None = None,
) -> Verdict:
    """Return the worst completed readiness kind for this snapshot.

    Kind priority: failure, action_required, pending, expected_skip, success.
    ``action_required`` suites are inspected even when they have no check-run
    entry; a check-run-only call (empty ``check_suites``) cannot see them.
    """
    workflow_runs = list(workflow_runs or [])
    ignore_names = frozenset(ignore_names or ())
    findings: list[Finding] = []
    # Check-run ids and workflow-run ids are different GitHub resources.
    # A shared integer set lets check run 22 suppress workflow run 22.
    seen_runs: set[tuple[str, int]] = set()
    seen_suites: set[int] = set()
    suite_runs = [run for run in check_runs if isinstance(run, dict)]

    visible_runs = [
        run
        for run in suite_runs
        if not matches_ignore(run, ignore_names)
    ]

    def add(finding: Finding, namespace: str) -> None:
        run_id = finding.run_id
        if run_id is not None:
            key = (namespace, run_id)
            if key in seen_runs:
                return
            seen_runs.add(key)
        findings.append(finding)

    for run in visible_runs:
        name = display_name(run) or "check run"
        status = _as_str(run.get("status"))
        conclusion = _as_str(run.get("conclusion"))
        url = item_url(run)
        run_id = _as_int(run.get("id"))
        if status in INCOMPLETE_STATUSES or status not in {"", "completed"}:
            if conclusion not in ATTENTION_CONCLUSIONS and status in WAITING_STATUSES:
                add(Finding("action_required", name, f"status={status}", url, "", run_id), "check-run")
            elif conclusion not in ATTENTION_CONCLUSIONS:
                add(Finding("pending", name, f"status={status or 'unknown'}", url, "", run_id), "check-run")
            continue
        if conclusion in BLOCKING_CONCLUSIONS:
            add(Finding("failure", name, f"conclusion={conclusion}", url, "", run_id), "check-run")
        elif conclusion in ATTENTION_CONCLUSIONS:
            add(Finding("action_required", name, f"conclusion={conclusion}", url, "", run_id), "check-run")
        elif conclusion == "skipped":
            add(Finding("expected_skip", name, "conclusion=skipped", url, "", run_id), "check-run")
        elif conclusion not in OK_CONCLUSIONS:
            add(Finding("failure", name, f"conclusion={conclusion or 'missing'}", url, "", run_id), "check-run")

    for suite in check_suites:
        if not isinstance(suite, dict):
            continue
        suite_id = _as_int(suite.get("id"))
        workflow = workflow_for_suite(suite, workflow_runs)
        if matches_ignore(suite, ignore_names) or matches_ignore(workflow, ignore_names):
            if suite_id is not None:
                seen_suites.add(suite_id)
            continue
        if suite_id is not None:
            seen_suites.add(suite_id)
        event = _as_str(workflow.get("event"))
        url = item_url(workflow) or item_url(suite)
        run_id = _as_int(workflow.get("id"))
        name = display_name(workflow) or display_name(suite) or "check suite"
        status = _as_str(suite.get("status")) or _as_str(workflow.get("status"))
        conclusion = _as_str(suite.get("conclusion")) or _as_str(workflow.get("conclusion"))
        run_count = run_count_for_suite(suite, suite_runs)
        empty = run_count == 0
        review = event in REVIEW_EVENTS
        actor = actor_login(workflow)
        empty_note = "no check-run entry" if empty else f"check-runs={run_count}"
        who = f", actor={actor}" if actor else ""
        event_note = f", event={event}" if event else ""
        detail = f"conclusion={conclusion or 'none'}, {empty_note}{event_note}{who}"

        if conclusion in ATTENTION_CONCLUSIONS or status in WAITING_STATUSES:
            if empty:
                detail = (
                    "completed action_required suite with no check-run entry"
                    f"{event_note}{who}"
                )
            add(Finding("action_required", name, detail, url, event, run_id), "workflow-run")
            continue
        if empty and (status in INCOMPLETE_STATUSES or status not in {"", "completed"}):
            add(Finding("pending", name, f"status={status or 'unknown'}{event_note}", url, event, run_id), "workflow-run")
            continue
        if empty and conclusion in BLOCKING_CONCLUSIONS:
            add(Finding("failure", name, detail, url, event, run_id), "workflow-run")
            continue
        if empty and conclusion == "skipped":
            add(Finding("expected_skip", name, detail, url, event, run_id), "workflow-run")
        elif empty and conclusion not in OK_CONCLUSIONS and conclusion:
            add(Finding("failure", name, detail, url, event, run_id), "workflow-run")
        elif review and empty and not conclusion:
            add(Finding("pending", name, f"review-triggered suite with no jobs{event_note}", url, event, run_id), "workflow-run")

    for workflow in workflow_runs:
        if not isinstance(workflow, dict):
            continue
        if matches_ignore(workflow, ignore_names):
            continue
        suite_id = _as_int(workflow.get("check_suite_id"))
        if suite_id is not None and suite_id in seen_suites:
            continue
        status = _as_str(workflow.get("status"))
        conclusion = _as_str(workflow.get("conclusion"))
        event = _as_str(workflow.get("event"))
        name = display_name(workflow) or "workflow run"
        url = item_url(workflow)
        run_id = _as_int(workflow.get("id"))
        actor = actor_login(workflow)
        who = f", actor={actor}" if actor else ""
        event_note = f", event={event}" if event else ""
        if conclusion in ATTENTION_CONCLUSIONS or status in WAITING_STATUSES:
            add(
                Finding(
                    "action_required",
                    name,
                    f"workflow conclusion={conclusion or 'none'}, status={status or 'none'}{event_note}{who}",
                    url,
                    event,
                    run_id,
                ),
                "workflow-run",
            )
        elif conclusion in BLOCKING_CONCLUSIONS:
            add(Finding("failure", name, f"conclusion={conclusion}{event_note}", url, event, run_id), "workflow-run")

    kinds = {item.kind for item in findings}
    if "failure" in kinds:
        kind = "failure"
    elif "action_required" in kinds:
        kind = "action_required"
    elif "pending" in kinds:
        kind = "pending"
    elif "expected_skip" in kinds:
        kind = "expected_skip"
    else:
        kind = "success"
    return Verdict(kind, tuple(findings))


def format_verdict(verdict: Verdict) -> str:
    lines = [f"PR readiness: {verdict.kind}"]
    attention = [item for item in verdict.findings if item.kind == "action_required"]
    if attention:
        lines.append("Review-triggered run that needs operator attention:" if any(
            item.event in REVIEW_EVENTS for item in attention
        ) else "Run that needs operator attention:")
        for item in attention:
            run = f" run={item.run_id}" if item.run_id is not None else ""
            lines.append(f"- {item.name}{run}: {item.detail}")
            if item.url:
                lines.append(f"  {item.url}")
        lines.append(RERUN_NOTE)
    elif verdict.kind == "failure":
        lines.append("Failed checks:")
        for item in verdict.findings:
            if item.kind == "failure":
                lines.append(f"- {item.name}: {item.detail}")
    elif verdict.kind == "expected_skip":
        skipped = [item.name for item in verdict.findings if item.kind == "expected_skip"]
        lines.append("Expected skip: " + ", ".join(skipped))
    elif verdict.kind == "pending":
        lines.append("Checks are still running.")
    else:
        lines.append("Completed check runs and check suites are successful.")
    return "\n".join(lines) + "\n"


def _gh_json(path: str) -> object:
    try:
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        fail(f"check-pr-readiness: cannot run gh ({exc})")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "gh api failed").strip()
        fail(f"check-pr-readiness: {err}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("check-pr-readiness: GitHub returned invalid JSON")


def _list(path: str, key: str) -> list[dict]:
    sep = "&" if "?" in path else "?"
    items: list[dict] = []
    page = 1
    while page <= 50:
        payload = _gh_json(f"{path}{sep}per_page=100&page={page}")
        if not isinstance(payload, dict):
            fail(f"check-pr-readiness: {key} response was not an object")
        batch = payload.get(key)
        if not isinstance(batch, list):
            fail(f"check-pr-readiness: missing {key} list")
        items.extend(item for item in batch if isinstance(item, dict))
        total = payload.get("total_count")
        if not isinstance(total, int) or len(items) >= total or not batch:
            return items
        page += 1
    return items


def load_snapshot(repo: str, sha: str) -> tuple[list[dict], list[dict], list[dict]]:
    check_runs = _list(f"repos/{repo}/commits/{sha}/check-runs", "check_runs")
    check_suites = _list(f"repos/{repo}/commits/{sha}/check-suites", "check_suites")
    workflow_runs = _list(f"repos/{repo}/actions/runs?head_sha={sha}", "workflow_runs")
    return check_runs, check_suites, workflow_runs


def resolve_sha(repo: str, pr: str, sha: str) -> str:
    if sha:
        return sha
    if not pr.isdigit():
        fail("check-pr-readiness: provide --pr or --sha")
    payload = _gh_json(f"repos/{repo}/pulls/{pr}")
    head = _as_dict(_as_dict(payload).get("head")).get("sha")
    if not isinstance(head, str) or not head:
        fail("check-pr-readiness: pull request head SHA missing")
    return head


def parse_args(argv: list[str]) -> tuple[str, str, str, frozenset[str]]:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr = os.environ.get("PR") or os.environ.get("ISSUE_NUMBER") or ""
    sha = os.environ.get("HEAD_SHA") or ""
    ignore = [name for name in os.environ.get("IGNORE_CHECK", "").split(",") if name]
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--repo" and index + 1 < len(argv):
            index += 1
            repo = argv[index]
        elif arg == "--pr" and index + 1 < len(argv):
            index += 1
            pr = argv[index]
        elif arg == "--sha" and index + 1 < len(argv):
            index += 1
            sha = argv[index]
        elif arg == "--ignore-name" and index + 1 < len(argv):
            index += 1
            ignore.append(argv[index])
        elif arg in {"-h", "--help"}:
            print(__doc__)
            raise SystemExit(0)
        else:
            fail(f"check-pr-readiness: unknown argument {arg}")
        index += 1
    parts = repo.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1] or "/" in parts[1]:
        fail("check-pr-readiness: --repo owner/name is required")
    return repo, pr, sha, frozenset(ignore)


def main(argv: list[str]) -> int:
    repo, pr, sha, ignore_names = parse_args(argv)
    sha = resolve_sha(repo, pr, sha)
    check_runs, check_suites, workflow_runs = load_snapshot(repo, sha)
    verdict = classify(check_runs, check_suites, workflow_runs, ignore_names)
    text = format_verdict(verdict)
    print(text, end="")
    if verdict.kind == "action_required":
        for item in verdict.findings:
            if item.kind == "action_required":
                print(f"::error::{item.name}: {item.detail}")
        return 1
    if verdict.kind == "failure":
        for item in verdict.findings:
            if item.kind == "failure":
                print(f"::error::{item.name}: {item.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
