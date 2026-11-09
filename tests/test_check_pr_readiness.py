"""Pins for PR readiness that inspects check suites as well as check runs.

Failure modes written down first:
  * a completed action_required suite with no check-run entry is reported
    as success or expected-skip (the Copilot review-event gap);
  * a successful check-run picture is reported as blocked;
  * an expected skipped assistant job is treated as a failure;
  * an actual failed check run is swallowed;
  * the diagnostic approves or reruns the waiting workflow.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from workflow_test_support import ROOT

SCRIPT = ROOT / ".github/scripts/check-pr-readiness.py"
spec = importlib.util.spec_from_file_location("check_pr_readiness", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def check_run(name, conclusion="success", status="completed", suite_id=1, **extra):
    payload = {
        "id": extra.pop("id", abs(hash(name)) % 10_000),
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example.invalid/runs/{name}",
        "check_suite": {"id": suite_id},
    }
    payload.update(extra)
    return payload


def suite(suite_id, conclusion="success", status="completed", check_runs=1, **extra):
    payload = {
        "id": suite_id,
        "status": status,
        "conclusion": conclusion,
        "latest_check_runs_count": check_runs,
        "app": {"name": extra.pop("app_name", "GitHub Actions")},
        "url": f"https://api.example.invalid/check-suites/{suite_id}",
    }
    payload.update(extra)
    return payload


def workflow(run_id, suite_id, event="pull_request", conclusion="success",
             status="completed", name="CI", actor="jmcerda", path=""):
    payload = {
        "id": run_id,
        "name": name,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "check_suite_id": suite_id,
        "html_url": f"https://github.com/example/project/actions/runs/{run_id}",
        "triggering_actor": {"login": actor},
    }
    if path:
        payload["path"] = path
    return payload


class ClassifyTests(unittest.TestCase):
    def test_prejob_action_required_suite_is_not_ready(self):
        """Check-run-only looks like expected-skip; suites name the review run."""
        check_runs = [
            check_run("lint", "success", suite_id=1),
            check_run("assistant", "skipped", suite_id=1),
        ]
        suites = [
            suite(1, "success", check_runs=2),
            suite(2, "action_required", check_runs=0),
        ]
        workflows = [
            workflow(11, 1, "pull_request", "success", name="CI"),
            workflow(
                22,
                2,
                "pull_request_review",
                "action_required",
                name="Agent perspective",
                actor="Copilot",
            ),
        ]
        runs_only = mod.classify(check_runs, [], [])
        self.assertEqual(runs_only.kind, "expected_skip", runs_only)
        verdict = mod.classify(check_runs, suites, workflows)
        self.assertEqual(verdict.kind, "action_required", verdict)
        attention = [item for item in verdict.findings if item.kind == "action_required"]
        self.assertEqual(len(attention), 1, verdict)
        self.assertEqual(attention[0].name, "Agent perspective")
        self.assertEqual(attention[0].run_id, 22)
        self.assertEqual(attention[0].event, "pull_request_review")
        self.assertIn("no check-run entry", attention[0].detail)
        self.assertIn("Copilot", attention[0].detail)
        text = mod.format_verdict(verdict)
        self.assertIn("Review-triggered run that needs operator attention", text)
        self.assertIn("run=22", text)
        self.assertIn("invocation guards", text)
        self.assertIn("Do not approve the waiting run", text)

    def test_review_comment_event_is_named(self):
        verdict = mod.classify(
            [],
            [suite(7, "action_required", check_runs=0)],
            [workflow(9, 7, "pull_request_review_comment", "action_required",
                      name="Assistant", actor="Copilot")],
        )
        self.assertEqual(verdict.kind, "action_required")
        self.assertEqual(verdict.findings[0].event, "pull_request_review_comment")
        self.assertEqual(verdict.findings[0].run_id, 9)

    def test_success(self):
        verdict = mod.classify(
            [check_run("lint", "success")],
            [suite(1, "success", check_runs=1)],
            [workflow(3, 1, "pull_request", "success", name="CI")],
        )
        self.assertEqual(verdict.kind, "success", verdict)
        self.assertEqual(verdict.findings, ())
        self.assertIn("successful", mod.format_verdict(verdict))

    def test_expected_skip(self):
        verdict = mod.classify(
            [
                check_run("lint", "success", suite_id=1),
                check_run("assistant", "skipped", suite_id=1),
            ],
            [suite(1, "success", check_runs=2)],
            [workflow(4, 1, "pull_request", "success", name="CI")],
        )
        self.assertEqual(verdict.kind, "expected_skip", verdict)
        skipped = [item.name for item in verdict.findings if item.kind == "expected_skip"]
        self.assertEqual(skipped, ["assistant"])
        self.assertIn("Expected skip: assistant", mod.format_verdict(verdict))

    def test_actual_failure(self):
        verdict = mod.classify(
            [check_run("lint", "failure")],
            [suite(1, "failure", check_runs=1)],
            [workflow(5, 1, "pull_request", "failure", name="CI")],
        )
        self.assertEqual(verdict.kind, "failure", verdict)
        self.assertTrue(any(item.name == "lint" for item in verdict.findings))
        self.assertIn("Failed checks", mod.format_verdict(verdict))

    def test_failure_outranks_action_required(self):
        verdict = mod.classify(
            [check_run("lint", "failure", suite_id=1)],
            [
                suite(1, "failure", check_runs=1),
                suite(2, "action_required", check_runs=0),
            ],
            [
                workflow(1, 1, "pull_request", "failure", name="CI"),
                workflow(2, 2, "pull_request_review", "action_required", name="Lint"),
            ],
        )
        self.assertEqual(verdict.kind, "failure")
        self.assertTrue(any(item.kind == "action_required" for item in verdict.findings))

    def test_pending_check_run(self):
        verdict = mod.classify(
            [check_run("lint", conclusion=None, status="in_progress")],
            [suite(1, conclusion=None, status="in_progress", check_runs=1)],
            [workflow(6, 1, status="in_progress", conclusion=None)],
        )
        self.assertEqual(verdict.kind, "pending", verdict)

    def test_waiting_suite_without_jobs_needs_attention(self):
        verdict = mod.classify(
            [check_run("lint", "success", suite_id=1)],
            [
                suite(1, "success", check_runs=1),
                suite(2, conclusion=None, status="waiting", check_runs=0),
            ],
            [workflow(8, 2, "pull_request_review", conclusion=None, status="waiting",
                      name="Agent perspective")],
        )
        self.assertEqual(verdict.kind, "action_required", verdict)

    def test_requested_and_pending_are_unfinished_not_approval(self):
        for status in ("pending", "requested"):
            verdict = mod.classify(
                [],
                [suite(2, conclusion=None, status=status, check_runs=0)],
                [workflow(8, 2, "pull_request", conclusion=None, status=status, name="CI")],
            )
            self.assertEqual(verdict.kind, "pending", verdict)
            self.assertFalse(any(item.kind == "action_required" for item in verdict.findings))
            self.assertNotIn("completed action_required", mod.format_verdict(verdict))

    def test_nonempty_in_progress_suite_is_not_success(self):
        """Successful check runs do not hide a suite that is still running."""
        verdict = mod.classify(
            [check_run("lint", "success", id=1, suite_id=1)],
            [suite(1, conclusion=None, status="in_progress", check_runs=1)],
            [workflow(6, 1, status="in_progress", conclusion=None, name="CI")],
        )
        self.assertEqual(verdict.kind, "pending", verdict)

    def test_nonempty_failed_suite_is_not_success(self):
        """Successful check runs do not hide a failed suite."""
        verdict = mod.classify(
            [check_run("lint", "success", id=1, suite_id=1)],
            [suite(1, "failure", status="completed", check_runs=1)],
            [workflow(6, 1, "pull_request", "failure", name="CI")],
        )
        self.assertEqual(verdict.kind, "failure", verdict)
        self.assertTrue(any(item.name == "CI" for item in verdict.findings))

    def test_ignored_check_does_not_make_its_suite_a_failure(self):
        verdict = mod.classify(
            [
                check_run("lint", "success", id=1, suite_id=1),
                check_run("readiness / PR readiness", "failure", id=2, suite_id=1),
            ],
            [suite(1, "failure", status="completed", check_runs=2)],
            [workflow(6, 1, "pull_request", "failure", name="CI")],
            ignore_names=frozenset({"PR readiness"}),
        )
        self.assertEqual(verdict.kind, "success", verdict)

    def test_ignore_own_check_name(self):
        verdict = mod.classify(
            [
                check_run("lint", "success"),
                check_run("PR readiness", conclusion=None, status="in_progress"),
            ],
            [suite(1, "success", check_runs=1)],
            [],
            ignore_names=frozenset({"PR readiness"}),
        )
        self.assertEqual(verdict.kind, "success", verdict)

    def test_skipped_check_run_id_does_not_suppress_workflow_run(self):
        """Check-run 22 and workflow-run 22 are different resources."""
        verdict = mod.classify(
            [
                check_run("lint", "success", id=1, suite_id=1),
                check_run("assistant", "skipped", id=22, suite_id=1),
            ],
            [
                suite(1, "success", check_runs=2),
                suite(2, "action_required", check_runs=0),
            ],
            [
                workflow(11, 1, "pull_request", "success", name="CI"),
                workflow(
                    22,
                    2,
                    "pull_request_review_comment",
                    "action_required",
                    name="Agent perspective",
                    actor="Copilot",
                ),
            ],
        )
        self.assertEqual(verdict.kind, "action_required", verdict)
        attention = [item for item in verdict.findings if item.kind == "action_required"]
        self.assertEqual([item.run_id for item in attention], [22])
        self.assertIn("no check-run entry", attention[0].detail)

    def test_ignores_composite_readiness_check_and_own_workflow(self):
        """`readiness / PR readiness` is this job; `readiness / lint` is not."""
        verdict = mod.classify(
            [
                check_run("lint", "success", id=1, suite_id=1),
                check_run("readiness / PR readiness", "failure", id=22, suite_id=2),
                check_run("readiness / lint", "failure", id=23, suite_id=1),
            ],
            [
                suite(1, "failure", check_runs=2),
                suite(2, "failure", check_runs=1),
            ],
            [
                workflow(11, 1, "pull_request", "failure", name="CI"),
                workflow(
                    22,
                    2,
                    "pull_request",
                    "failure",
                    name="PR readiness",
                    path=".github/workflows/readiness.yml",
                ),
            ],
            ignore_names=frozenset({"PR readiness", ".github/workflows/readiness.yml"}),
        )
        self.assertEqual(verdict.kind, "failure", verdict)
        names = [item.name for item in verdict.findings]
        self.assertIn("readiness / lint", names)
        self.assertNotIn("readiness / PR readiness", names)
        self.assertNotIn("PR readiness", names)

    def test_in_progress_composite_readiness_check_is_not_pending(self):
        verdict = mod.classify(
            [
                check_run("lint", "success", id=1, suite_id=1),
                check_run(
                    "readiness / PR readiness",
                    conclusion=None,
                    status="in_progress",
                    id=9,
                    suite_id=2,
                ),
            ],
            [
                suite(1, "success", check_runs=1),
                suite(2, conclusion=None, status="in_progress", check_runs=1),
            ],
            [
                workflow(4, 1, "pull_request", "success", name="CI"),
                workflow(5, 2, status="in_progress", conclusion=None, name="PR readiness"),
            ],
            ignore_names=frozenset({"PR readiness"}),
        )
        self.assertEqual(verdict.kind, "success", verdict)

    def test_app_name_is_kept_and_empty_workflow_does_not_hide_suite_name(self):
        unnamed = suite(4, "action_required", check_runs=0, app_name="GitHub Actions")
        self.assertEqual(mod.display_name(unnamed), "GitHub Actions")
        self.assertEqual(mod.display_name({}), "")
        named = suite(4, "action_required", check_runs=0, name="Agent perspective")
        verdict = mod.classify([], [named], [{}])
        self.assertEqual(verdict.kind, "action_required")
        self.assertEqual(verdict.findings[0].name, "Agent perspective")

    def test_ignore_suite_by_app_name(self):
        unnamed = suite(4, "action_required", check_runs=0, app_name="Dependabot")
        verdict = mod.classify(
            [],
            [unnamed],
            [],
            ignore_names=frozenset({"Dependabot"}),
        )
        self.assertEqual(verdict.kind, "success", verdict)

    def test_script_does_not_authorize_workflow_runs(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("/approve", source)
        self.assertNotIn("/rerun", source)
        self.assertNotIn("--method", source)
        self.assertNotIn("POST", source)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="readiness-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.calls.write_text("")
        self.state = {
            "pulls": {
                "head": {"sha": "abc123"},
            },
            "check-runs": {"total_count": 0, "check_runs": []},
            "check-suites": {"total_count": 0, "check_suites": []},
            "workflow-runs": {"total_count": 0, "workflow_runs": []},
        }
        (self.root / "state.json").write_text(json.dumps(self.state))
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable} -I\n" + textwrap.dedent(f'''
            import json, pathlib, sys
            args = sys.argv[1:]
            pathlib.Path({str(self.calls)!r}).open("a").write(json.dumps(args) + "\\n")
            state = json.loads(pathlib.Path({str(self.root / "state.json")!r}).read_text())
            path = args[1]
            if "/pulls/" in path:
                print(json.dumps(state["pulls"]))
            elif "/check-runs" in path:
                print(json.dumps(state["check-runs"]))
            elif "/check-suites" in path:
                print(json.dumps(state["check-suites"]))
            elif "/actions/runs" in path:
                print(json.dumps(state["workflow-runs"]))
            else:
                raise SystemExit("unexpected " + path)
        '''))
        gh.chmod(0o755)
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        self.env.pop("GITHUB_REPOSITORY", None)
        self.env.pop("PR", None)
        self.env.pop("HEAD_SHA", None)
        self.env.pop("IGNORE_CHECK", None)

    def write_state(self, **fields):
        self.state.update(fields)
        (self.root / "state.json").write_text(json.dumps(self.state))

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )

    def test_cli_prejob_action_required_exits_one(self):
        self.write_state(
            **{
                "check-runs": {
                    "total_count": 2,
                    "check_runs": [
                        check_run("lint", "success", suite_id=1),
                        check_run("assistant", "skipped", suite_id=1),
                    ],
                },
                "check-suites": {
                    "total_count": 2,
                    "check_suites": [
                        suite(1, "success", check_runs=2),
                        suite(2, "action_required", check_runs=0),
                    ],
                },
                "workflow-runs": {
                    "total_count": 2,
                    "workflow_runs": [
                        workflow(11, 1, "pull_request", "success", name="CI"),
                        workflow(22, 2, "pull_request_review", "action_required",
                                 name="Agent perspective", actor="Copilot"),
                    ],
                },
            }
        )
        result = self.run_script("--repo", "example/project", "--sha", "abc123")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("action_required", result.stdout)
        self.assertIn("Agent perspective", result.stdout)
        self.assertIn("run=22", result.stdout)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        joined = " ".join(" ".join(call) for call in calls)
        self.assertNotIn("approve", joined)
        self.assertNotIn("rerun", joined)
        self.assertNotIn("POST", joined)
        self.assertIn("check-suites", joined)
        self.assertIn("check-runs", joined)

    def test_cli_success_exits_zero(self):
        self.write_state(
            **{
                "check-runs": {
                    "total_count": 1,
                    "check_runs": [check_run("lint", "success")],
                },
                "check-suites": {
                    "total_count": 1,
                    "check_suites": [suite(1, "success", check_runs=1)],
                },
                "workflow-runs": {
                    "total_count": 1,
                    "workflow_runs": [workflow(3, 1)],
                },
            }
        )
        result = self.run_script("--repo", "example/project", "--pr", "41")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("success", result.stdout)

    def test_cli_expected_skip_exits_zero(self):
        self.write_state(
            **{
                "check-runs": {
                    "total_count": 2,
                    "check_runs": [
                        check_run("lint", "success"),
                        check_run("assistant", "skipped"),
                    ],
                },
                "check-suites": {
                    "total_count": 1,
                    "check_suites": [suite(1, "success", check_runs=2)],
                },
                "workflow-runs": {"total_count": 0, "workflow_runs": []},
            }
        )
        result = self.run_script("--repo", "example/project", "--sha", "abc123")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("expected_skip", result.stdout)

    def test_cli_failure_exits_one(self):
        self.write_state(
            **{
                "check-runs": {
                    "total_count": 1,
                    "check_runs": [check_run("lint", "failure")],
                },
                "check-suites": {
                    "total_count": 1,
                    "check_suites": [suite(1, "failure", check_runs=1)],
                },
                "workflow-runs": {"total_count": 0, "workflow_runs": []},
            }
        )
        result = self.run_script("--repo", "example/project", "--sha", "abc123")
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("failure", result.stdout)

    def test_cli_api_error_exits_two(self):
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable} -I\nraise SystemExit('boom')\n")
        result = self.run_script("--repo", "example/project", "--sha", "abc123")
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)

    def test_pending_does_not_fail_the_job(self):
        self.write_state(
            **{
                "check-runs": {
                    "total_count": 1,
                    "check_runs": [check_run("lint", conclusion=None, status="in_progress")],
                },
                "check-suites": {
                    "total_count": 1,
                    "check_suites": [suite(1, conclusion=None, status="in_progress", check_runs=1)],
                },
                "workflow-runs": {"total_count": 0, "workflow_runs": []},
            }
        )
        result = self.run_script("--repo", "example/project", "--sha", "abc123")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("pending", result.stdout)


class WorkflowContracts(unittest.TestCase):
    def test_readiness_permissions_stay_read_only(self):
        source = (ROOT / ".github/workflows/pr-readiness.yml").read_text()
        self.assertIn("contents: read", source)
        self.assertIn("checks: read", source)
        self.assertIn("actions: read", source)
        self.assertIn("pull-requests: read", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("/approve", source)
        self.assertNotIn("/rerun", source)
        self.assertIn("check-pr-readiness.py", source)
        self.assertIn("python3 -I", source)
        self.assertIn("job.workflow_sha", source)

    def test_caller_reevaluates_on_workflow_run_without_write_or_self_trigger(self):
        source = (ROOT / ".github/workflows/readiness.yml").read_text()
        self.assertIn("contents: read", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("checks: write", source)
        self.assertNotIn("check_suite:", source)
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("pr:", source)
        self.assertIn("sha:", source)
        self.assertIn("uses: ./.github/workflows/pr-readiness.yml", source)
        filter_block = source.split("workflow_run:", 1)[1].split("types:", 1)[0]
        self.assertIn("Agent perspective", filter_block)
        self.assertNotIn("PR readiness", filter_block)
        self.assertNotIn("readiness.yml", filter_block)

    def test_reusable_workflow_reads_workflow_run_head_and_stays_read_only(self):
        source = (ROOT / ".github/workflows/pr-readiness.yml").read_text()
        self.assertIn("workflow_run.head_sha", source)
        self.assertIn("workflow_run.conclusion", source)
        self.assertIn("workflow_run.name != 'PR readiness'", source)
        self.assertIn(".github/workflows/readiness.yml", source)
        self.assertIn("inputs.pr", source)
        self.assertIn("inputs.sha", source)
        self.assertIn("action_required", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("actions: write", source)
        self.assertNotIn("checks: write", source)
