"""Pins for the warn-only agent-perspective lint.

Failure modes written down first:
  * the lint fails a caller's build (it must only annotate);
  * a fetch or script crash turns the check red;
  * ``operator`` or ``agent`` is flagged — both words are correct;
  * keep cases (human-readable, Human Resources, the needs-human label,
    HUMAN-CHECKPOINTS, .po files, "human editor" product copy, inline
    ignore marker, fenced code, file paths, the agent-state block)
    get flagged, so people learn to ignore the annotations;
  * the rule statement "human(s)" and frozen release notes flag themselves;
  * code identifiers in scripts get flagged as prose;
  * a technical mention of a product name ("Copilot configuration") warns;
  * PR title, body and comment surfaces are not scanned.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from workflow_test_support import WorkflowFixture, workflow_run

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/check-operator-terms.py"
spec = importlib.util.spec_from_file_location("check_operator_terms", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def words(text: str) -> list[str]:
    return [word for _, _, word in mod.hits(text)]


class HitsTest(unittest.TestCase):
    def test_flags_people(self):
        self.assertEqual(len(mod.hits("A human merges.\nHumans deploy.")), 2)

    def test_operator_and_agent_pass(self):
        text = "\n".join([
            "An operator merges.",
            "Operators review the diff.",
            "An agent opened this pull request.",
            "Agents may comment; the narrative is still for the operator.",
        ])
        self.assertEqual(mod.hits(text), [])

    def test_flags_bot_perspective_examples(self):
        cases = [
            ("Cursor did the refactor.", "Cursor did"),
            ("Copilot will update the tests.", "Copilot will"),
            ("Ship it as an AI reviewer.", "as an AI"),
            ("I am Claude and I reviewed this.", "I am Claude"),
            ("Bugbot here with a pass.", "Bugbot here"),
            ("DO NOT MERGE until the token arrives.", "DO NOT MERGE"),
            ("AGENTS: skip the changelog check.", "AGENTS:"),
            ("Please ask the user before merging.", "ask the user"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertIn(expected, words(text), words(text))

    def test_technical_product_name_is_not_an_actor(self):
        text = "\n".join([
            "This pull request updates the Copilot configuration.",
            "Pin the Cursor workflow to v1.",
            "Claude review is documented in the vault.",
        ])
        self.assertEqual(mod.hits(text), [])

    def test_lowercase_do_not_merge_is_prose(self):
        self.assertEqual(mod.hits("Please do not merge until review finishes."), [])

    def test_allowlist(self):
        text = "\n".join([
            "Keep notes human-readable.",
            "Industry: Human Resources",
            "Apply the needs-human label.",
            "Use human_coauthors() here.",
            "See HUMAN-CHECKPOINTS.md.",
            'Templates say "must be written by a human".',
            "The Human Action Model.",
            "a human merges <!-- operator-terminology: allow -->",
            "a human merges  # operator-terminology: allow",
            "Cursor did this  # agent-perspective: allow",
            "Use `a human` in code spans.",
            "Use `Cursor did this` in code spans.",
            "{{ $value | humanize }}",
            "The human editor reviews the draft in the CMS.",
            "Refer to people as operator(s), never human(s).",
        ])
        self.assertEqual(mod.hits(text), [])

    def test_allowlist_does_not_swallow_neighbours(self):
        found = mod.hits("Keep it human-readable; a human merges it.")
        self.assertEqual([w for _, _, w in found], ["human"])

    def test_skips_inline_code_and_fenced_blocks(self):
        text = "\n".join([
            "See `I am Claude` in the prompt fixture.",
            "```",
            "Cursor did the refactor.",
            "DO NOT MERGE",
            "```",
            "A human merges.",
        ])
        self.assertEqual(words(text), ["human"])

    def test_skips_tilde_fences_and_file_paths(self):
        text = "\n".join([
            "~~~markdown",
            "as an AI",
            "~~~",
            "See AGENTS.md and .github/workflows/copilot.yml for setup.",
            "https://example.test/I-am-Claude",
            "ask the user before merging.",
        ])
        self.assertEqual(words(text), ["ask the user"])

    def test_skips_agent_state_metadata_block(self):
        text = "\n".join([
            "Ready for review.",
            "<!-- agent-state",
            "do-not-merge: true",
            "note: DO NOT MERGE",
            "actor: Cursor did this",
            "-->",
            "Cursor did the extra commit.",
        ])
        self.assertEqual(words(text), ["Cursor did"])

    def test_other_html_comments_are_still_scanned(self):
        text = "<!-- leftover: DO NOT MERGE this narrative -->"
        self.assertIn("DO NOT MERGE", words(text))


class ScopeTest(unittest.TestCase):
    def test_in_scope(self):
        for path in ("docs/README.md", ".github/workflows/ci.yml",
                     ".github/PULL_REQUEST_TEMPLATE.md", "prompts/review.txt",
                     "skills/x/SKILL.md", "rules/STANDING-ORDERS.md",
                     "a.prompt.md", "x.mdc"):
            with self.subTest(path=path):
                self.assertTrue(mod.in_scope(path))

    def test_out_of_scope(self):
        for path in ("src/app.py", ".github/scripts/strip.py",
                     "skills/x/run.sh", "translations/es.po",
                     ".github/locale/messages.pot", "CHANGELOG.md",
                     "docs/CHANGELOG.md", "node_modules/pkg/README.md",
                     "vendor/acme/skills/x/SKILL.md", "web/core/README.md",
                     "web/modules/contrib/foo/README.md"):
            with self.subTest(path=path):
                self.assertFalse(mod.in_scope(path))


class CliTest(unittest.TestCase):
    def run_on(self, body, *flags, cwd=None):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "doc.md"
            p.write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), *flags, str(p)],
                capture_output=True, text=True, cwd=cwd,
            )

    def test_warn_only_exits_zero(self):
        r = self.run_on("a human merges\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("::warning file=", r.stdout)
        self.assertNotIn("::error", r.stdout)

    def test_strict_fails(self):
        r = self.run_on("a human merges\n", "--strict")
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error", r.stdout)

    def test_clean_passes_strict(self):
        self.assertEqual(self.run_on("an operator merges\n", "--strict").returncode, 0)
        self.assertEqual(self.run_on("an agent opened this\n", "--strict").returncode, 0)

    def test_text_surface_warns_without_a_file(self):
        r = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--text", "I am Claude"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("::warning::", r.stdout)
        self.assertIn("I am Claude", r.stdout)

    def test_title_and_comment_files(self):
        with tempfile.TemporaryDirectory() as d:
            title = Path(d) / "title.txt"
            comment = Path(d) / "comment.txt"
            title.write_text("Cursor did the work\n", encoding="utf-8")
            comment.write_text("ask the user to merge\n", encoding="utf-8")
            r = subprocess.run(
                [sys.executable, "-I", str(SCRIPT),
                 "--title-file", str(title), "--comment-file", str(comment)],
                capture_output=True, text=True,
            )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("title:", r.stdout)
        self.assertIn("comment:", r.stdout)
        self.assertIn("2 hit(s)", r.stdout)

    def test_job_summary_lists_hits(self):
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "summary.md"
            env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
            r = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--text", "DO NOT MERGE"],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Agent perspective", summary.read_text())
            self.assertIn("DO NOT MERGE", summary.read_text())

    def test_outside_a_git_tree_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                               capture_output=True, text=True, cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("::warning", r.stdout)

    def test_undecodable_file_name_does_not_lose_the_scan(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            (Path(d) / "a.md").write_text("a human merges\n")
            try:
                with open(os.fsencode(d) + b"/b\xff.md", "w") as f:
                    f.write("a human merges\n")
            except OSError:
                self.skipTest("file system rejects non-UTF-8 names (runs on Linux CI)")
            subprocess.run(["git", "-C", d, "add", "."], check=True)
            subprocess.run(["git", "-C", d, "config", "core.quotePath", "false"], check=True)
            env = dict(os.environ, GITHUB_STEP_SUMMARY=str(Path(d) / "summary.md"))
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                               capture_output=True, text=True, cwd=d, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("file=a.md,line=1", r.stdout)

    def test_file_names_with_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            (Path(d) / "my notes.md").write_text("a human merges\n")
            subprocess.run(["git", "-C", d, "add", "."], check=True)
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                               capture_output=True, text=True, cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("file=my notes.md,line=1", r.stdout)

    def test_this_repository_is_clean(self):
        r = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--strict"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 hit(s)", r.stdout)


class GitHubSurfaceTest(unittest.TestCase):
    def run_github(self, handler: str, extra_env: dict | None = None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gh = root / "gh"
            gh.write_text("#!/usr/bin/env python3\n" + handler)
            gh.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = str(root) + os.pathsep + env["PATH"]
            env["GITHUB_REPOSITORY"] = "example/project"
            env["ISSUE_NUMBER"] = "12"
            env.pop("GITHUB_STEP_SUMMARY", None)
            if extra_env:
                env.update(extra_env)
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--github"],
                capture_output=True, text=True, cwd=d, env=env,
            )

    def test_scans_title_body_and_comments(self):
        handler = textwrap.dedent("""\
            import json, sys
            path = sys.argv[-1]
            if path.endswith("/pulls/12"):
                print(json.dumps({"title": "Cursor did the work", "body": "Ready."}))
            elif path.endswith("/issues/12/comments"):
                print(json.dumps([{"id": 1, "body": "ask the user to merge"}]))
            elif path.endswith("/pulls/12/comments"):
                print(json.dumps([{"id": 2, "body": "I am Claude"}]))
            elif path.endswith("/pulls/12/reviews"):
                print(json.dumps([{"id": 3, "body": "DO NOT MERGE"}]))
            else:
                raise SystemExit(1)
        """)
        r = self.run_github(handler)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PR title:", r.stdout)
        self.assertIn("issue comment 1:", r.stdout)
        self.assertIn("review comment 2:", r.stdout)
        self.assertIn("review 3:", r.stdout)
        self.assertIn("4 hit(s)", r.stdout)

    def test_issue_without_a_pull_request(self):
        handler = textwrap.dedent("""\
            import json, sys
            path = sys.argv[-1]
            if "/pulls/12" in path:
                raise SystemExit(1)
            if path.endswith("/issues/12"):
                print(json.dumps({"title": "Operators only", "body": "as an AI"}))
            elif path.endswith("/issues/12/comments"):
                print(json.dumps([]))
            else:
                raise SystemExit(1)
        """)
        r = self.run_github(handler)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("issue body:", r.stdout)
        self.assertIn("1 hit(s)", r.stdout)

    def test_failed_github_read_warns_and_passes(self):
        r = self.run_github("import sys\nraise SystemExit(1)\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("::warning::", r.stdout)

    def test_missing_issue_number_warns_and_passes(self):
        r = self.run_github("import sys\nraise SystemExit(1)\n",
                            extra_env={"ISSUE_NUMBER": ""})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ISSUE_NUMBER is missing", r.stdout)


class WorkflowTest(WorkflowFixture):
    STEP = "Scan (warn-only)"

    def test_reusable_only_and_read_only(self):
        source = (ROOT / ".github/workflows/operator-terms.yml").read_text()
        on_block = source.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_call:", on_block)
        self.assertNotIn("pull_request", on_block)
        self.assertIn("contents: read", source)
        self.assertIn("pull-requests: read", source)
        self.assertIn("issues: read", source)
        self.assertIn("name: Agent perspective (warn-only)", source)
        # A failed or hung checkout must not end the check red either.
        checkout = source.split("      - name: Check out\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("continue-on-error: true", checkout)
        self.assertIn("timeout-minutes:", source)
        step = workflow_run("operator-terms.yml", self.STEP)
        self.assertNotIn("--strict", step)
        # The runner's default shell is `bash -e`; the step must turn it off.
        self.assertTrue(step.lstrip().splitlines()[2].startswith("set +e"), step)

    def test_reference_caller_covers_comment_events(self):
        source = (ROOT / ".github/workflows/terminology.yml").read_text()
        self.assertIn("issue_comment:", source)
        self.assertIn("pull_request_review_comment:", source)
        self.assertIn("pull_request_review:", source)
        self.assertIn("edited", source)
        self.assertIn("./.github/workflows/operator-terms.yml", source)

    def scan(self):
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        (self.work / "README.md").write_text("A human merges this.\n")
        subprocess.run(["git", "-C", str(self.work), "add", "."], check=True)
        code = workflow_run("operator-terms.yml", self.STEP)
        # Run the way the runner does: bash -e -o pipefail.
        return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", code],
                              cwd=self.work, env=self.env, text=True,
                              capture_output=True)

    def test_hits_annotate_and_pass(self):
        self.env["RENDER_FIXTURE"] = str(SCRIPT)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning file=README.md,line=1", result.stdout)

    def test_failed_fetch_warns_and_passes(self):
        (self.bin / "gh").write_text("#!/bin/sh\nexit 1\n")
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_crashing_script_warns_and_passes(self):
        broken = self.runtime / "broken.py"
        broken.write_text("raise SystemExit(3)\n")
        self.env["RENDER_FIXTURE"] = str(broken)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)

    def test_missing_workflow_sha_warns_and_passes(self):
        self.env["SCRIPTS_REF"] = ""
        self.env["RENDER_FIXTURE"] = str(SCRIPT)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::", result.stdout)


if __name__ == "__main__":
    unittest.main()
