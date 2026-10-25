"""Release and alias workflow behavior without GitHub writes."""

import json
from pathlib import Path
import subprocess

from workflow_test_support import WorkflowFixture, workflow_run


class ReleaseWorkflowContracts(WorkflowFixture):
    def test_malformed_release_tags_are_refused(self):
        for tag in ("v01.2.3", "v1.2.3-.", "v1.2.3-beta..1", "v1.2.3-rc.01"):
            with self.subTest(tag=tag):
                self.release(tag, "")
                result = self.run_step("release.yml", "Refuse anything that is not a v* release tag")
                self.assertNotEqual(result.returncode, 0)

    def test_beta_is_marked_prerelease(self):
        self.release("v1.2.3-beta.1", "## [1.2.3-beta.1]\n\n- Change.\n")
        for name in ("Refuse anything that is not a v* release tag",
                     "Extract release notes from the CHANGELOG", "Publish the Release"):
            result = self.run_step("release.yml", name)
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in Path(self.env["PROOF_CALLS"]).read_text().splitlines()]
        create = next(call for call in calls if call[:2] == ["release", "create"])
        self.assertIn("--prerelease", create)

    def test_empty_scaffolding_falls_back_to_version(self):
        self.release("v1.2.3-rc.1", "## [Unreleased]\n\n### Added\n\n<!-- pending -->\n\n## [1.2.3]\n\n- Actual change.\n")
        result = self.run_step("release.yml", "Extract release notes from the CHANGELOG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Actual change.", (self.runtime / "notes.md").read_text())

    def test_pending_fragments_refuse_publication(self):
        self.release("v1.2.3", "## [1.2.3]\n\n- Existing change.\n")
        fragments = self.work / "changelog.d"
        fragments.mkdir()
        (fragments / "26-fix.md").write_text("---\nsection: Fixed\n---\nPending change.\n")
        result = self.run_step("release.yml", "Extract release notes from the CHANGELOG")
        self.assertNotEqual(result.returncode, 0)

    def test_empty_final_notes_are_refused(self):
        self.release("v1.2.3", "## [1.2.3]\n\n### Fixed\n\n<!-- TODO -->\n")
        result = self.run_step("release.yml", "Extract release notes from the CHANGELOG")
        self.assertNotEqual(result.returncode, 0)

    def test_rc_renders_pending_fragments_without_modifying_sources(self):
        original = "## [Unreleased]\n\n- Handwritten note.\n\n## [1.2.2]\n\n- Old.\n"
        self.release("v1.2.3-rc.1", original)
        fragments = self.work / "changelog.d"
        fragments.mkdir()
        fragment = fragments / "26-fix.md"
        fragment.write_text("---\nsection: Fixed\n---\nPending note.\n")
        renderer = self.runtime / "renderer-fixture.py"
        renderer.write_text(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['RENDER_CALLS']).write_text(json.dumps(sys.argv[1:]))\n"
            "print('## [Unreleased]\\n\\n## [1.2.3]\\n\\n- Handwritten note.\\n- Pending note.')\n"
        )
        self.env["RENDER_FIXTURE"] = str(renderer)
        self.env["PRESERVE_HISTORY"] = "true"
        self.env["ALLOW_INITIAL_RELEASE"] = "true"
        result = self.run_step("release.yml", "Extract release notes from the CHANGELOG")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pending note.", (self.runtime / "notes.md").read_text())
        self.assertEqual((self.work / "CHANGELOG.md").read_text(), original)
        self.assertTrue(fragment.exists())
        calls = [json.loads(line) for line in Path(self.env["PROOF_CALLS"]).read_text().splitlines()]
        self.assertIn("?ref=" + "a" * 40, calls[0][1])
        args = json.loads(Path(self.env["RENDER_CALLS"]).read_text())
        self.assertEqual(args[0], "render")
        self.assertIn("--preserve-history", args)
        self.assertIn("--allow-initial-release", args)
        self.assertEqual(args[args.index("--version") + 1], "1.2.3")
        self.assertEqual(args[args.index("--extra-sections") + 1], "Docs")

    def test_alias_rejects_noncommit_api_objects(self):
        curl = self.bin / "curl"
        curl.write_text("#!/bin/sh\nprintf '%s' \"$ALIAS_JSON\"\n")
        curl.chmod(0o755)
        code = workflow_run("alias-guard.yml", "Refuse an annotated alias tag")
        for expression in ("github.token", "github.api_url", "github.repository"):
            code = code.replace("${{ " + expression + " }}", "synthetic")
        self.env["TAG"] = "v1"
        for payload, expected in (({"object": {"type": "commit"}}, 0),
                                  ({"object": {"type": "tag"}}, 1), ({}, 1)):
            self.env["ALIAS_JSON"] = json.dumps(payload)
            result = subprocess.run(["bash", "-c", code], cwd=self.work,
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)

