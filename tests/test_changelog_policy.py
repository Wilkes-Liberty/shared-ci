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
