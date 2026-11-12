"""Run the real policy step against current metadata, not stale event labels."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ChangelogPolicyTest(unittest.TestCase):
    def test_live_labels_and_existing_policy_exceptions(self):
        source = (ROOT / ".github/workflows/changelog-fragments.yml").read_text()
        step = source.split("      - name: Decide whether a fragment is required\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        lines = step.split("        run: |\n", 1)[1].splitlines()
        script = "\n".join(line[10:] for line in lines if not line or line.startswith("          "))
        cases = [
            ("opt-in", ["changelog"], "false", "false", "true"),
            ("opt-in", [], "true", "false", "false"),
            ("opt-in", ["Changelog"], "false", "false", "true"),
            ("required", ["No-Changelog"], "false", "false", "false"),
            ("required", [], "false", "false", "true"),
            ("required", ["no-changelog"], "false", "false", "false"),
            ("required", [], "false", "true", "false"),
            ("unknown", [], "false", "false", None),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text(f"#!{sys.executable} -I\nimport os\nprint(os.environ['REMOTE'])\n")
            gh.chmod(0o755)
            for policy, labels, stale, bot, expected in cases:
                with self.subTest(policy=policy, labels=labels, bot=bot):
                    output = root / "output"
                    output.write_text("")
                    env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                               POLICY=policy, LABELLED=stale, EXEMPT="false", DEPENDABOT=bot,
                               LABEL="changelog", EXEMPTION_LABEL="no-changelog", PR="1",
                               GITHUB_REPOSITORY="example/project", GITHUB_OUTPUT=str(output),
                               REMOTE=json.dumps({"labels": [{"name": label} for label in labels]}))
                    result = subprocess.run(["bash", "-c", script], env=env, cwd=root,
                                            text=True, capture_output=True)
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("required=" + expected, output.read_text())
            for remote in ("not JSON", '{}', '{"labels": "invalid"}'):
                with self.subTest(remote=remote):
                    env.update(REMOTE=remote, POLICY="opt-in")
                    output.write_text("")
                    result = subprocess.run(["bash", "-c", script], env=env, cwd=root,
                                            text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "")


class HistoryCorrectionWorkflowTest(unittest.TestCase):
    def test_both_jobs_require_explicit_correction_opt_in_including_push(self):
        import hashlib
        import shutil
        from test_changelog_fragments import BASE, Sandbox, fragment

        source = (ROOT / ".github/workflows/changelog-fragments.yml").read_text()
        for step_name, push in (("Require one fragment", False),
                                ("Validate fragments and the changelog", False),
                                ("Validate fragments and the changelog", True)):
            with self.subTest(step=step_name, push=push):
                box = Sandbox()
                try:
                    box.write("CHANGELOG.md", BASE)
                    base = box.commit_all("initial archive")
                    updated = BASE.replace("baseline that must survive compile byte for byte", "Reviewed public wording")
                    heading = "## [0.1.0] - 2020-01-01"
                    box.write("CHANGELOG.md", updated)
                    box.write("changelog.d/44-correction.md", fragment("Fixed", "Correct earlier references."))
                    manifest = {
                        "schema": 1, "reason": "internal-reference-removal",
                        "fragment": "changelog.d/44-correction.md",
                        "releases": [{"heading": heading,
                                      "before_sha256": hashlib.sha256(BASE[BASE.index(heading):].encode()).hexdigest(),
                                      "after_sha256": hashlib.sha256(updated[updated.index(heading):].encode()).hexdigest()}],
                    }
                    box.write("changelog.corrections/44-correction.json", json.dumps(manifest))
                    box.commit_all("correction candidate")
                    runtime = box.root / "runtime"
                    (runtime / "trusted").mkdir(parents=True)
                    shutil.copy(ROOT / ".github/scripts/changelog-fragments.py", runtime / "trusted/changelog-fragments.py")
                    box.write("hashlib.py", "raise RuntimeError('checkout module must not be imported')\n")
                    block = source.split("      - name: " + step_name + "\n", 1)[1]
                    block = block.split("\n      - name:", 1)[0]
                    lines = block.split("        run: |\n", 1)[1].splitlines()
                    script = "\n".join(line[10:] for line in lines if not line or line.startswith("          "))
                    env = dict(os.environ, BASE=base, PR_BASE="" if push else base,
                               PUSH_BEFORE=base, PR_HEAD=box.git("rev-parse", "HEAD").stdout.strip(),
                               CHANGELOG="CHANGELOG.md", FRAGMENTS="changelog.d", EXTRA="",
                               FORMAT="versioned", INITIAL="false", PRESERVE="true",
                               ENFORCE="true", RUNNER_TEMP=str(runtime))
                    for enabled, expected in (("false", 1), ("true", 0)):
                        env["CORRECTIONS"] = enabled
                        result = subprocess.run(["bash", "-c", script], env=env, cwd=box.repo,
                                                text=True, capture_output=True)
                        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                finally:
                    box.cleanup()
