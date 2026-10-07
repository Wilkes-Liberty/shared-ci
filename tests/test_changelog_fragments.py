"""Tests for .github/scripts/changelog-fragments.py.

Two pull requests that each add a changelog bullet used to conflict on
adjacent lines of CHANGELOG.md. These tests are the pins for the replacement:
fragment files merge in either order, compile emits a ``## [X.Y.Z] - date``
section the release workflow already knows how to read, and the labelled-PR
rule accepts that shape instead of an edit to the shared list.

Run: python3 -m unittest discover -s tests
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "changelog-fragments.py"

BASE = """\
# Changelog

All notable changes are documented in this file.

## [Unreleased]

## [0.1.0] - 2020-01-01

### Added
- baseline that must survive compile byte for byte
"""

TAIL = """\
## [0.1.0] - 2020-01-01

### Added
- baseline that must survive compile byte for byte
"""


def fragment(section: str, body: str) -> str:
    return f"---\nsection: {section}\n---\n{body}\n"


def extract_section(text: str, heading: str) -> str:
    """The release workflow's rule: from the heading through the line before the next ##."""
    lines = []
    inside = False
    for line in text.splitlines():
        if not inside and line.startswith(heading):
            inside = True
            lines.append(line)
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            lines.append(line)
    return "\n".join(lines)


class Sandbox:
    """A throwaway repository. Hooks are pointed at an empty directory.

    Operator commit-msg hooks rewrite commit messages (issue 19). These tests
    do not plant attribution trailers, and they still must not depend on the
    machine's hooks.
    """

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="changelog-frag-"))
        self.hooks = self.root / "hooks"
        self.hooks.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")

    def git(self, *args, check=True):
        result = subprocess.run(
            ["git", "-c", f"core.hooksPath={self.hooks}", "-C", self.repo, *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class StructureTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.box.write("CHANGELOG.md", BASE)

    def tearDown(self):
        self.box.cleanup()

    def test_valid_changelog_with_no_fragments_passes(self):
        result = self.box.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_unreleased_fails(self):
        self.box.write("CHANGELOG.md", "# Changelog\n\n## [0.1.0] - 2020-01-01\n\n- x\n")
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("[Unreleased]", result.stdout)

    def test_duplicate_section_under_unreleased_fails(self):
        self.box.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- one\n\n### Fixed\n- two\n\n"
            "## [0.1.0] - 2020-01-01\n\n- x\n",
        )
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("duplicate '### Fixed'", result.stdout)

    def test_unknown_and_misordered_headings_fail(self):
        self.box.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- a\n\n### Added\n- b\n\n"
            "## [0.1.0] - 2020-01-01\n\n- x\n",
        )
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must come before", result.stdout)

        self.box.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\n### Fixes\n- a\n\n"
            "## [0.1.0] - 2020-01-01\n\n- x\n",
        )
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown heading", result.stdout)

    def test_extra_section_is_opt_in(self):
        text = (
            "# Changelog\n\n## [Unreleased]\n\n### Docs\n- a note\n\n"
            "## [0.1.0] - 2020-01-01\n\n- x\n"
        )
        self.box.write("CHANGELOG.md", text)
        rejected = self.box.run("check")
        self.assertEqual(rejected.returncode, 1)
        accepted = self.box.run("check", "--extra-sections", "Docs")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_legacy_version_heading_is_not_a_structure_error(self):
        """shared-ci's older sections use `## v1.2.0 — date` and must keep passing."""
        self.box.write(
            "CHANGELOG.md",
            BASE + "\n## v1.2.0 — 2020-02-02\n\n- an older note\n",
        )
        result = self.box.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_deleted_release_heading_fails(self):
        base = self.box.commit_all("base")
        self.box.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\n## [9.9.9] - 2024-01-01\n\n- replaced\n",
        )
        self.box.commit_all("drop the shipped heading")
        result = self.box.run("check", "--base", base)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("## [0.1.0] - 2020-01-01", result.stdout)
        self.assertIn("deleted", result.stdout)

    def test_bad_fragment_is_a_finding(self):
        cases = {
            "readme.md": fragment("Fixed", "A note."),
            "20-ok.md": "not front matter\n",
            "21-empty.md": "---\nsection: Fixed\n---\n\n",
            "22-typo.md": "---\nsection: Fixes\n---\nA note.\n",
            "23-extra-key.md": "---\nsection: Fixed\nissue: 20\n---\nA note.\n",
            "24-heading.md": fragment("Fixed", "## [1.2.3] - 2020-01-01\nnope"),
        }
        for name, text in cases.items():
            directory = self.box.repo / "changelog.d"
            if directory.exists():
                shutil.rmtree(directory)
            self.box.write(f"changelog.d/{name}", text)
            with self.subTest(name=name):
                result = self.box.run("check")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("::error::", result.stdout)

    def test_dotfile_in_fragment_directory_is_ignored(self):
        self.box.write("changelog.d/.gitkeep", "")
        self.box.write("changelog.d/20-note.md", fragment("Security", "A note."))
        result = self.box.run("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_this_repository_changelog_passes(self):
        """The checker must accept the file release.yml already publishes."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class CompileTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.box.write("CHANGELOG.md", BASE)

    def tearDown(self):
        self.box.cleanup()

    def test_fragments_become_a_version_section_and_are_deleted(self):
        # Filename order and section order disagree on purpose. Added must
        # still precede Fixed, and within Fixed the lower filename comes first.
        self.box.write(
            "changelog.d/90-later.md",
            fragment("Added", "Later filename, earlier section."),
        )
        self.box.write(
            "changelog.d/30-second.md",
            fragment("Fixed", "Second fix, named so it sorts after the first."),
        )
        self.box.write(
            "changelog.d/10-first.md",
            fragment("Fixed", "First fix.\nIt continues on a second line."),
        )
        result = self.box.run(
            "compile", "--version", "1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertTrue(text.endswith(TAIL), "prior release bytes changed:\n" + text)
        section = extract_section(text, "## [1.4.0]")
        self.assertTrue(section.startswith("## [1.4.0] - 2026-10-07"), section)
        body = "\n".join(section.splitlines()[1:]).strip()
        self.assertNotIn("baseline", body)
        self.assertLess(body.index("### Added"), body.index("### Fixed"))
        self.assertLess(body.index("First fix."), body.index("Second fix"))
        self.assertIn("  It continues on a second line.", body)
        self.assertIn("## [Unreleased]\n\n## [1.4.0] - 2026-10-07\n", text)
        self.assertFalse((self.box.repo / "changelog.d" / "10-first.md").exists())
        self.assertFalse((self.box.repo / "changelog.d" / "30-second.md").exists())
        self.assertFalse((self.box.repo / "changelog.d" / "90-later.md").exists())

        again = self.box.run(
            "compile", "--version", "1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(again.returncode, 1, again.stderr)
        self.assertIn("already has", again.stderr)

    def test_handwritten_unreleased_notes_move_into_the_version(self):
        self.box.write(
            "CHANGELOG.md",
            "# Changelog\n\n## [Unreleased]\n\n- shipped by hand before fragments\n\n"
            "### Fixed\n- already grouped\n\n## [0.1.0] - 2020-01-01\n\n- baseline\n",
        )
        self.box.write(
            "changelog.d/20-new.md",
            fragment("Fixed", "From a fragment."),
        )
        result = self.box.run(
            "compile", "--version", "1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        section = extract_section(text, "## [1.4.0]")
        self.assertIn("- shipped by hand before fragments", section)
        self.assertIn("- already grouped", section)
        self.assertIn("- From a fragment.", section)
        self.assertLess(
            section.index("already grouped"),
            section.index("From a fragment."),
        )
        unreleased = extract_section(text, "## [Unreleased]")
        self.assertNotIn("shipped by hand", unreleased)
        self.assertNotIn("already grouped", unreleased)

    def test_compile_refuses_a_bad_version_or_date(self):
        self.box.write("changelog.d/20-note.md", fragment("Added", "A note."))
        bad_version = self.box.run(
            "compile", "--version", "v1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(bad_version.returncode, 2, bad_version.stderr)
        bad_date = self.box.run(
            "compile", "--version", "1.4.0", "--date", "07-10-2026",
        )
        self.assertEqual(bad_date.returncode, 2, bad_date.stderr)
        self.assertTrue((self.box.repo / "changelog.d" / "20-note.md").exists())

    def test_compile_refuses_when_there_is_nothing_to_fold_in(self):
        result = self.box.run(
            "compile", "--version", "1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("no fragments", result.stderr)


class MergeAndEntryTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_two_fragment_branches_merge_in_either_order(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.commit_all("base")
        self.box.git("branch", "left")
        self.box.git("branch", "right")

        self.box.git("checkout", "-q", "left")
        self.box.write(
            "changelog.d/20-alpha.md",
            fragment("Added", "Alpha entry from the left branch."),
        )
        self.box.commit_all("alpha")

        self.box.git("checkout", "-q", "right")
        self.box.write(
            "changelog.d/21-beta.md",
            fragment("Fixed", "Beta entry from the right branch."),
        )
        self.box.commit_all("beta")

        for into, other in (("left", "right"), ("right", "left")):
            with self.subTest(into=into, other=other):
                self.box.git("checkout", "-q", "-B", f"merge-{into}-{other}", into)
                merged = self.box.git("merge", "--no-edit", other, check=False)
                self.assertEqual(
                    merged.returncode, 0, merged.stdout + merged.stderr,
                )
                self.assertTrue((self.box.repo / "changelog.d" / "20-alpha.md").is_file())
                self.assertTrue((self.box.repo / "changelog.d" / "21-beta.md").is_file())
                self.assertEqual(
                    (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8"),
                    BASE,
                )

    def test_labelled_pr_must_add_exactly_one_fragment(self):
        self.box.write("CHANGELOG.md", BASE)
        base = self.box.commit_all("base")

        missing = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(missing.returncode, 1, missing.stdout)
        self.assertIn("exactly one", missing.stdout)

        self.box.write("changelog.d/20-one.md", fragment("Changed", "One entry."))
        self.box.commit_all("one fragment")
        one = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(one.returncode, 0, one.stdout + one.stderr)

        self.box.write("changelog.d/21-two.md", fragment("Changed", "A second entry."))
        self.box.commit_all("two fragments")
        two = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(two.returncode, 1, two.stdout)

    def test_hidden_or_misnamed_file_does_not_count_as_the_required_fragment(self):
        self.box.write("CHANGELOG.md", BASE)
        base = self.box.commit_all("base")
        for name in (".bypass", "readme.md"):
            with self.subTest(name=name):
                directory = self.box.repo / "changelog.d"
                if directory.exists():
                    shutil.rmtree(directory)
                self.box.write(f"changelog.d/{name}", "not a fragment\n")
                self.box.commit_all(f"only {name}")
                result = self.box.run("check", "--require-entry", "--base", base)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("added 0 fragment", result.stdout)

        directory = self.box.repo / "changelog.d"
        if directory.exists():
            shutil.rmtree(directory)
        self.box.write("changelog.d/.gitkeep", "")
        self.box.write("changelog.d/20-one.md", fragment("Changed", "One entry."))
        self.box.commit_all("real fragment beside a hidden file")
        kept = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(kept.returncode, 0, kept.stdout + kept.stderr)

    def test_deleting_a_hidden_file_is_not_a_release_compile(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/.bypass", "not a fragment\n")
        self.box.write("changelog.d/20-keep.md", fragment("Added", "Still waiting."))
        base = self.box.commit_all("base")
        (self.box.repo / "changelog.d" / ".bypass").unlink()
        text = (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        self.box.write(
            "CHANGELOG.md",
            text.replace(
                "## [Unreleased]\n",
                "## [Unreleased]\n\n## [1.4.0] - 2026-10-07\n\n- not from a fragment\n",
            ),
        )
        self.box.commit_all("heading without consuming fragments")
        result = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("deleted 0", result.stdout)
        self.assertTrue((self.box.repo / "changelog.d" / "20-keep.md").is_file())

    def test_fragment_pr_that_also_edits_the_changelog_fails(self):
        self.box.write("CHANGELOG.md", BASE)
        base = self.box.commit_all("base")
        self.box.write("changelog.d/20-one.md", fragment("Changed", "One entry."))
        text = (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        self.box.write(
            "CHANGELOG.md",
            text.replace("## [Unreleased]\n", "## [Unreleased]\n\n- also hand edited\n"),
        )
        self.box.commit_all("fragment and a bullet")
        result = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("CHANGELOG.md changed", result.stdout)

    def test_release_compile_satisfies_the_entry_check(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/20-alpha.md", fragment("Added", "Alpha."))
        self.box.write("changelog.d/21-beta.md", fragment("Fixed", "Beta."))
        base = self.box.commit_all("fragments waiting")
        compiled = self.box.run(
            "compile", "--version", "1.4.0", "--date", "2026-10-07",
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.box.commit_all("release: compile changelog fragments")
        result = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.box.repo / "CHANGELOG.md").read_text(encoding="utf-8")
        section = extract_section(text, "## [1.4.0]")
        self.assertIn("Alpha.", section)
        self.assertIn("Beta.", section)
        self.assertNotIn("baseline", section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
