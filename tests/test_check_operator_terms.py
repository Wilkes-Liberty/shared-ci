"""Pins for the warn-only operator-terminology lint.

Failure modes written down first:
  * the lint fails a caller's build (it must only annotate);
  * a fetch or script crash turns the check red;
  * keep cases (human-readable, Human Resources, the needs-human label,
    HUMAN-CHECKPOINTS, .po files, "human editor" product copy, inline
    ignore marker) get flagged, so people learn to ignore the annotations;
  * the rule statement "human(s)" and frozen release notes flag themselves;
  * code identifiers in scripts get flagged as prose.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workflow_test_support import WorkflowFixture, workflow_run

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/check-operator-terms.py"
spec = importlib.util.spec_from_file_location("check_operator_terms", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class HitsTest(unittest.TestCase):
    def test_flags_people(self):
        self.assertEqual(len(mod.hits("A human merges.\nHumans deploy.")), 2)

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
            "Use `a human` in code spans.",
            "{{ $value | humanize }}",
            "The human editor reviews the draft in the CMS.",
            "Refer to people as operator(s), never human(s).",
        ])
        self.assertEqual(mod.hits(text), [])

    def test_allowlist_does_not_swallow_neighbours(self):
        found = mod.hits("Keep it human-readable; a human merges it.")
        self.assertEqual([w for _, _, w in found], ["human"])


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
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)],
                               capture_output=True, text=True, cwd=d)
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


class WorkflowTest(WorkflowFixture):
    STEP = "Scan (warn-only)"

    def test_reusable_only_and_read_only(self):
        source = (ROOT / ".github/workflows/operator-terms.yml").read_text()
        on_block = source.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_call:", on_block)
        self.assertNotIn("pull_request", on_block)
        self.assertIn("permissions:\n  contents: read\n", source)
        # A failed or hung checkout must not end the check red either.
        checkout = source.split("      - name: Check out\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("continue-on-error: true", checkout)
        self.assertIn("timeout-minutes:", source)
        step = workflow_run("operator-terms.yml", self.STEP)
        self.assertNotIn("--strict", step)
        # The runner's default shell is `bash -e`; the step must turn it off.
        self.assertTrue(step.lstrip().splitlines()[2].startswith("set +e"), step)

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
