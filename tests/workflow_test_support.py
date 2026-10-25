"""Synthetic process fixtures for the shared workflow contract tests."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


def workflow_run(filename, name):
    source = (ROOT / ".github/workflows" / filename).read_text()
    step = source.split("      - name: " + name + "\n", 1)[1]
    step = step.split("\n      - name:", 1)[0]
    lines = []
    for line in step.split("        run: |\n", 1)[1].splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "checkout"
        self.work.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        shutil.copytree(ROOT / ".github/scripts", self.runtime / "trusted")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "RUNNER_TEMP": str(self.runtime),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "GH_TOKEN": "synthetic-token",
            "GITHUB_REPOSITORY": "example/project",
            "PR": "1",
            "ALLOW_CLEAN": "true",
            "PROOF_STATE": str(self.runtime / "remote.json"),
            "PROOF_CALLS": str(self.runtime / "calls.jsonl"),
            "SCRIPTS_REF": "a" * 40,
            "EXTRA_SECTIONS": "Docs",
            "ALLOW_INITIAL_RELEASE": "false",
            "RENDER_CALLS": str(self.runtime / "render.json"),
        })
        self.env.pop("PYTHONPATH", None)
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable} -I\n" + textwrap.dedent('''
            import json, os, pathlib, sys
            args = sys.argv[1:]
            with open(os.environ["PROOF_CALLS"], "a") as f:
                f.write(json.dumps(args) + "\\n")
            state = pathlib.Path(os.environ["PROOF_STATE"])
            if args[0] == "api":
                if "/contents/" in args[1]:
                    print(pathlib.Path(os.environ["RENDER_FIXTURE"]).read_text())
                    raise SystemExit(0)
                if os.environ.get("FAIL_GET") == "1" and "PATCH" not in args:
                    raise SystemExit(1)
                if "PATCH" in args:
                    if os.environ.get("FAIL_PATCH") == "1":
                        raise SystemExit(1)
                    payload = json.loads(pathlib.Path(args[args.index("--input") + 1]).read_text())
                    current = json.loads(state.read_text())
                    current.update(payload)
                    state.write_text(json.dumps(current))
                print(state.read_text())
            elif args[:2] == ["release", "view"]:
                if "--json" in args:
                    print("https://example.invalid/release")
                else:
                    raise SystemExit(1)
        '''))
        gh.chmod(0o755)

    def run_step(self, workflow, name):
        code = workflow_run(workflow, name)
        code = code.replace("/tmp/release-notes.md", str(self.runtime / "notes.md"))
        return subprocess.run(["bash", "-c", code], cwd=self.work,
                              env=self.env, text=True, capture_output=True)

    def body(self, staged, remote=None):
        (self.runtime / "pr-body.txt").write_text(staged)
        (self.runtime / "pr-title.txt").write_text("old title")
        Path(self.env["PROOF_STATE"]).write_text(json.dumps({
            "title": "current title", "body": staged if remote is None else remote,
        }))

    def release(self, tag, changelog):
        self.env.update(GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=tag,
                        CHANGELOG=str(self.work / "CHANGELOG.md"),
                        FRAGMENTS_DIR="changelog.d")
        (self.work / "CHANGELOG.md").write_text(changelog)

