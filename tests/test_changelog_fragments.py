"""Tests for .github/scripts/changelog-fragments.py.

Two pull requests that each add a changelog bullet used to conflict on
adjacent lines of CHANGELOG.md. These tests are the pins for the replacement:
fragment files merge in either order, compile emits a ``## [X.Y.Z] - date``
section the release workflow already knows how to read, and the labelled-PR
rule accepts that shape instead of an edit to the shared list.

Run: python3 -m unittest discover -s tests
"""

import hashlib
import json
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
        self.assertIn("no substantive pending notes", result.stderr)


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
        self.assertIn("consume every pending fragment", result.stdout)
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

    def _concurrent_base_advance(self, extra_pr_fragment=False):
        """GitHub merge of a one-fragment PR after another fragment landed on the target.

        Returns ``(event_base, pr_head)``. HEAD is left on the regenerated
        merge commit: first parent is the newer target, second parent is the
        PR tip. ``event_base`` is the older SHA a pull_request event can keep.
        """
        self.box.write("CHANGELOG.md", BASE)
        event_base = self.box.commit_all("base")
        self.box.git("branch", "pr-a")
        self.box.git("branch", "pr-b")

        self.box.git("checkout", "-q", "pr-a")
        self.box.write(
            "changelog.d/40-one.md",
            fragment("Fixed", "This pull request's entry."),
        )
        if extra_pr_fragment:
            self.box.write(
                "changelog.d/40-two.md",
                fragment("Fixed", "A second entry on the same pull request."),
            )
        pr_head = self.box.commit_all("this pull request")

        self.box.git("checkout", "-q", "pr-b")
        self.box.write(
            "changelog.d/41-other.md",
            fragment("Added", "Already merged elsewhere."),
        )
        self.box.commit_all("other pull request")
        self.box.git("checkout", "-q", "master")
        merged_other = self.box.git("merge", "--no-edit", "pr-b", check=False)
        self.assertEqual(
            merged_other.returncode, 0, merged_other.stdout + merged_other.stderr,
        )

        regenerated = self.box.git("merge", "--no-ff", "--no-edit", "pr-a", check=False)
        self.assertEqual(
            regenerated.returncode, 0, regenerated.stdout + regenerated.stderr,
        )
        parents = self.box.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 3, "GitHub merge commits have two parents")
        self.assertEqual(parents[2], pr_head)
        self.assertNotEqual(parents[1], event_base)
        return event_base, pr_head

    def test_concurrent_base_advance_counts_only_this_pull_request(self):
        event_base, pr_head = self._concurrent_base_advance()
        stale = self.box.run(
            "check", "--enforce-fragments", "--base", event_base,
        )
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertIn("added 2 fragment", stale.stdout)

        rebound = self.box.run(
            "check", "--enforce-fragments", "--base", event_base,
            "--pr-head", pr_head,
        )
        self.assertEqual(rebound.returncode, 0, rebound.stdout + rebound.stderr)
        labelled = self.box.run(
            "check", "--require-entry", "--base", event_base,
            "--pr-head", pr_head,
        )
        self.assertEqual(labelled.returncode, 0, labelled.stdout + labelled.stderr)

    def test_concurrent_base_advance_still_rejects_two_fragments(self):
        event_base, pr_head = self._concurrent_base_advance(extra_pr_fragment=True)
        result = self.box.run(
            "check", "--enforce-fragments", "--base", event_base,
            "--pr-head", pr_head,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("added 2 fragment", result.stdout)

    def test_merge_of_target_into_pr_is_not_rebound(self):
        self.box.write("CHANGELOG.md", BASE)
        event_base = self.box.commit_all("base")
        self.box.git("branch", "pr-a")
        self.box.git("branch", "pr-b")

        self.box.git("checkout", "-q", "pr-a")
        self.box.write(
            "changelog.d/40-one.md",
            fragment("Fixed", "This pull request's entry."),
        )
        self.box.commit_all("this pull request")

        self.box.git("checkout", "-q", "pr-b")
        self.box.write(
            "changelog.d/41-other.md",
            fragment("Added", "Already merged elsewhere."),
        )
        self.box.commit_all("other pull request")
        self.box.git("checkout", "-q", "master")
        self.box.git("merge", "--no-edit", "pr-b")

        self.box.git("checkout", "-q", "pr-a")
        self.box.git("merge", "--no-edit", "master")
        pr_head = self.box.git("rev-parse", "HEAD").stdout.strip()
        parents = self.box.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 3)
        self.assertNotEqual(parents[2], pr_head)

        result = self.box.run(
            "check", "--require-entry", "--base", event_base,
            "--pr-head", pr_head,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("added 2 fragment", result.stdout)


class AdoptionSafetyTest(unittest.TestCase):
    """Failure modes: lost notes, unsafe paths, policy bypass and new formats."""

    def setUp(self):
        self.box = Sandbox()

    def tearDown(self):
        self.box.cleanup()

    def test_lossy_release_compile_is_rejected(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/25-required.md", fragment("Fixed", "Never lose this entry."))
        base = self.box.commit_all("base")
        (self.box.repo / "changelog.d/25-required.md").unlink()
        self.box.write("CHANGELOG.md", BASE.replace("## [Unreleased]", "## [Unreleased]\n\n## [1.4.0] - 2026-10-25\n\n- unrelated"))
        self.box.commit_all("drop entry disguised as release")
        result = self.box.run("check", "--require-entry", "--base", base)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("compile", result.stdout)

    def test_unlabelled_shared_changelog_edit_is_rejected(self):
        self.box.write("CHANGELOG.md", BASE)
        base = self.box.commit_all("base")
        self.box.write("CHANGELOG.md", BASE.replace("## [Unreleased]", "## [Unreleased]\n\n- shared edit"))
        self.box.commit_all("shared edit")
        result = self.box.run("check", "--enforce-fragments", "--base", base)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_unlabelled_change_without_entry_is_allowed(self):
        self.box.write("CHANGELOG.md", BASE)
        base = self.box.commit_all("base")
        self.box.write("ordinary.txt", "change\n")
        self.box.commit_all("ordinary")
        result = self.box.run("check", "--enforce-fragments", "--base", base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_symlink_fragment_and_directory_are_rejected(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("outside/25-secret.md", fragment("Fixed", "outside content"))
        directory = self.box.repo / "changelog.d"
        directory.mkdir()
        (directory / "25-link.md").symlink_to("../outside/25-secret.md")
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        (directory / "25-link.md").unlink()
        directory.rmdir()
        directory.symlink_to("outside", target_is_directory=True)
        result = self.box.run("check")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_calendar_and_rolling_compile_preserve_history(self):
        for style, heading, history in (
            ("calendar", "## [Unreleased]", "## [2026-10-24]\n\n- old calendar entry\n"),
            ("rolling", "## Unreleased", ""),
        ):
            with self.subTest(style=style):
                original = "# Changelog\n\n" + heading + "\n\n- existing note\n\n" + history
                self.box.write("CHANGELOG.md", original)
                self.box.write("changelog.d/25-note.md", fragment("Fixed", "new fragment"))
                base = self.box.commit_all("format base")
                result = self.box.run("compile", "--format", style, "--date", "2026-10-25")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                text = (self.box.repo / "CHANGELOG.md").read_text()
                self.assertEqual(text.count("new fragment"), 1)
                self.assertEqual(text.count("existing note"), 1)
                self.assertIn("## [2026-10-25]", text)
                self.assertTrue(text.endswith(history) if history else True)
                self.box.commit_all("compile")
                checked = self.box.run("check", "--format", style, "--enforce-fragments", "--base", base)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_preview_is_read_only_and_matches_compile(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/25-preview.md", fragment("Added", "Candidate entry."))
        preview = self.box.run("render", "--version", "1.4.0", "--date", "2026-10-25")
        self.assertEqual(preview.returncode, 0, preview.stdout + preview.stderr)
        self.assertEqual((self.box.repo / "CHANGELOG.md").read_text(), BASE)
        self.assertTrue((self.box.repo / "changelog.d/25-preview.md").exists())
        compiled = self.box.run("compile", "--version", "1.4.0", "--date", "2026-10-25")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.assertEqual(preview.stdout, (self.box.repo / "CHANGELOG.md").read_text())

    def test_preserved_legacy_sections_do_not_weaken_pending_validation(self):
        legacy = BASE.replace("### Added", "### Historical category")
        self.box.write("CHANGELOG.md", legacy)
        self.box.write("changelog.d/25-history.md", fragment("Fixed", "new note"))
        base = self.box.commit_all("legacy history")
        self.assertEqual(self.box.run("check").returncode, 1)
        compiled = self.box.run("compile", "--version", "1.4.0", "--date", "2026-10-25", "--preserve-history")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.assertTrue((self.box.repo / "CHANGELOG.md").read_text().endswith(legacy.split("## [0.1.0]")[1]))
        self.box.commit_all("compile preserves history")
        checked = self.box.run("check", "--base", base, "--enforce-fragments", "--preserve-history")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        current = (self.box.repo / "CHANGELOG.md").read_text()
        self.box.write("CHANGELOG.md", current.replace("## [Unreleased]", "## [Unreleased]\n\n### Unknown\n- invalid"))
        self.assertEqual(self.box.run("check", "--preserve-history").returncode, 1)

    def test_handwritten_only_release_compiles_and_empty_release_fails(self):
        written = BASE.replace("## [Unreleased]", "## [Unreleased]\n\n### Fixed\n- inherited note")
        self.box.write("CHANGELOG.md", written)
        base = self.box.commit_all("handwritten backlog")
        rendered = self.box.run("render", "--version", "1.4.0", "--date", "2026-10-25")
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        compiled = self.box.run("compile", "--version", "1.4.0", "--date", "2026-10-25")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.box.commit_all("compile handwritten notes")
        checked = self.box.run("check", "--base", base, "--enforce-fragments")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertEqual(self.box.run("compile", "--version", "1.4.1", "--date", "2026-10-26").returncode, 1)

    def test_compile_does_not_follow_predictable_temporary_symlink(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/25-safe.md", fragment("Fixed", "new note"))
        self.box.write("unrelated.txt", "must survive")
        (self.box.repo / "CHANGELOG.md.tmp").symlink_to("unrelated.txt")
        compiled = self.box.run("compile", "--version", "1.4.0", "--date", "2026-10-25")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.assertEqual((self.box.repo / "unrelated.txt").read_text(), "must survive")

    def test_noncanonical_dates_are_rejected_without_mutation(self):
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/25-date.md", fragment("Fixed", "new note"))
        for date in ("20261025", "2026-W43-7", "2026-02-30"):
            with self.subTest(date=date):
                result = self.box.run("compile", "--version", "1.4.0", "--date", date)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.box.repo / "CHANGELOG.md").read_text(), BASE)
                self.assertTrue((self.box.repo / "changelog.d/25-date.md").exists())

    def test_conflict_markers_fail_in_archives_and_fragments(self):
        markers = "<<<<<<< HEAD\n- ours\n=======\n- theirs\n>>>>>>> branch\n"
        self.box.write("CHANGELOG.md", BASE + markers)
        self.assertEqual(self.box.run("check", "--preserve-history").returncode, 1)
        self.box.write("CHANGELOG.md", BASE)
        self.box.write("changelog.d/25-conflict.md", fragment("Fixed", markers))
        self.assertEqual(self.box.run("check").returncode, 1)
        self.assertEqual(self.box.run("compile", "--version", "1.4.0", "--date", "2026-10-25").returncode, 1)
        self.assertEqual((self.box.repo / "CHANGELOG.md").read_text(), BASE)

    def test_initial_release_requires_explicit_configuration(self):
        self.box.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
        self.assertEqual(self.box.run("check").returncode, 1)
        result = self.box.run("check", "--allow-initial-release")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HistoryCorrectionTest(unittest.TestCase):
    """Audited exceptions must not permit unlisted historical changes."""

    heading = "## [0.1.0] - 2020-01-01"
    canary = "PRIVATE_CANARY_MUST_NOT_LEAK"
    manifest_path = "changelog.corrections/44-history-correction.json"
    fragment_path = "changelog.d/44-history-correction.md"

    def setUp(self):
        self.box = Sandbox()
        self.before = BASE.replace(
            "## [Unreleased]", "## [Unreleased]\n\n### Fixed\n- inherited pending note"
        ).replace("baseline that must survive compile byte for byte", self.canary)
        self.before += "\n## [0.0.1] - 2019-01-01\n\n### Added\n- older archive\n"
        self.box.write("CHANGELOG.md", self.before)
        self.box.write("changelog.d/43-existing.md", fragment("Fixed", "Existing fragment."))
        self.base = self.box.commit_all("initial history")

    def tearDown(self):
        self.box.cleanup()

    def archive(self, raw, heading=None):
        start = raw.index((heading or self.heading).encode())
        end = raw.find(b"\n## ", start)
        return raw[start:] if end == -1 else raw[start:end + 1]

    def prepare(self, key="44", raw=None):
        before = (self.box.repo / "CHANGELOG.md").read_bytes()
        after = raw if raw is not None else before.replace(self.canary.encode(), b"Reviewed public wording")
        manifest = {
            "schema": 1,
            "reason": "internal-reference-removal",
            "fragment": f"changelog.d/{key}-history-correction.md",
            "releases": [{
                "heading": self.heading,
                "before_sha256": hashlib.sha256(self.archive(before)).hexdigest(),
                "after_sha256": hashlib.sha256(self.archive(after)).hexdigest(),
            }],
        }
        (self.box.repo / "CHANGELOG.md").write_bytes(after)
        self.box.write(f"changelog.corrections/{key}-history-correction.json", json.dumps(manifest))
        self.box.write(manifest["fragment"], fragment("Fixed", "Correct internal references in earlier notes."))
        return manifest

    def check(self, enabled=True, *extra):
        args = ["check", "--base", self.base, "--enforce-fragments", "--preserve-history"]
        if enabled:
            args.append("--allow-history-corrections")
        return self.box.run(*args, *extra)

    def assertRejected(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn(self.canary, result.stdout + result.stderr)

    def test_explicit_correction_passes_both_entry_and_structure_checks(self):
        self.prepare()
        self.box.commit_all("reviewed correction")
        for extra in ((), ("--require-entry",)):
            result = self.check(True, *extra)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_manifest_can_correct_two_archives_without_moving_pending_content(self):
        manifest = self.prepare()
        path = self.box.repo / "CHANGELOG.md"
        raw = path.read_bytes().replace(b"older archive", b"Reviewed older wording")
        heading = "## [0.0.1] - 2019-01-01"
        manifest["releases"].append({
            "heading": heading,
            "before_sha256": hashlib.sha256(self.archive(self.before.encode(), heading)).hexdigest(),
            "after_sha256": hashlib.sha256(self.archive(raw, heading)).hexdigest(),
        })
        path.write_bytes(raw)
        self.box.write(self.manifest_path, json.dumps(manifest))
        self.box.commit_all("two reviewed archive corrections")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_later_compile_preserves_corrected_crlf_archive_bytes(self):
        raw = self.before.replace("\n", "\r\n").encode()
        (self.box.repo / "CHANGELOG.md").write_bytes(raw)
        self.base = self.box.commit_all("CRLF history")
        self.prepare()
        self.base = self.box.commit_all("accepted byte exact correction")
        archive = self.archive((self.box.repo / "CHANGELOG.md").read_bytes())
        compiled = self.box.run("compile", "--version", "0.2.0", "--date", "2026-11-12", "--preserve-history")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.box.commit_all("ordinary release compile")
        self.assertEqual(self.archive((self.box.repo / "CHANGELOG.md").read_bytes()), archive)
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_default_off_and_missing_transaction_parts_fail(self):
        self.prepare()
        self.box.commit_all("correction without opt in")
        self.assertRejected(self.check(False))
        for missing in (self.manifest_path, self.fragment_path):
            with self.subTest(missing=missing):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                self.prepare()
                (self.box.repo / missing).unlink()
                self.box.commit_all("missing correction part")
                self.assertRejected(self.check())

    def test_unlisted_preamble_pending_and_archive_bytes_are_immutable(self):
        for old, new in (("# Changelog", "# Changed"),
                         ("inherited pending note", "rewritten pending"),
                         ("older archive", "rewritten old archive")):
            with self.subTest(old=old):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                self.prepare()
                path = self.box.repo / "CHANGELOG.md"
                path.write_bytes(path.read_bytes().replace(old.encode(), new.encode()))
                self.box.commit_all("unlisted edit")
                self.assertRejected(self.check())

    def test_hash_schema_duplicate_and_unused_entries_fail_without_echoing_content(self):
        for fault in ("before", "after", "unknown", "noop", "duplicate", "reason"):
            with self.subTest(fault=fault):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                manifest = self.prepare()
                entry = manifest["releases"][0]
                if fault in ("before", "after"):
                    entry[fault + "_sha256"] = "0" * 64
                elif fault == "unknown":
                    manifest["unexpected"] = self.canary
                elif fault == "noop":
                    entry["after_sha256"] = entry["before_sha256"]
                elif fault == "duplicate":
                    manifest["releases"].append(dict(entry))
                else:
                    manifest["reason"] = self.canary
                self.box.write(self.manifest_path, json.dumps(manifest))
                self.box.commit_all("invalid manifest")
                self.assertRejected(self.check())

    def test_unused_manifest_and_multiple_manifests_fail(self):
        self.prepare()
        self.box.write("CHANGELOG.md", self.before)
        self.box.commit_all("unused intent")
        self.assertRejected(self.check())
        self.box.git("reset", "--hard", self.base)
        self.box.git("clean", "-fd")
        manifest = self.prepare()
        self.box.write("changelog.corrections/45-another.json", json.dumps(manifest))
        self.box.commit_all("ambiguous intent")
        self.assertRejected(self.check())

    def test_release_and_section_headings_cannot_be_removed_or_changed(self):
        for old, new in ((self.heading, "## [0.1.1] - 2020-01-01"),
                         ("### Added", "### Changed")):
            with self.subTest(old=old):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                manifest = self.prepare()
                path = self.box.repo / "CHANGELOG.md"
                raw = path.read_bytes().replace(old.encode(), new.encode())
                path.write_bytes(raw)
                if old == self.heading:
                    manifest["releases"][0]["heading"] = new
                manifest["releases"][0]["after_sha256"] = hashlib.sha256(self.archive(raw, new if old == self.heading else self.heading)).hexdigest()
                self.box.write(self.manifest_path, json.dumps(manifest))
                self.box.commit_all("renamed heading")
                self.assertRejected(self.check())

    def test_symlinks_traversal_and_duplicate_json_keys_fail(self):
        for fault in ("manifest", "directory", "fragment", "traversal", "duplicate_json"):
            with self.subTest(fault=fault):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                manifest = self.prepare()
                path = self.box.repo / self.manifest_path
                if fault == "manifest":
                    path.unlink()
                    path.symlink_to("../outside.json")
                elif fault == "directory":
                    shutil.rmtree(path.parent)
                    path.parent.symlink_to("../outside")
                elif fault == "fragment":
                    path = self.box.repo / self.fragment_path
                    path.unlink()
                    path.symlink_to("../outside.md")
                elif fault == "traversal":
                    manifest["fragment"] = "../outside.md"
                    self.box.write(self.manifest_path, json.dumps(manifest))
                else:
                    self.box.write(self.manifest_path, json.dumps(manifest).replace('"schema": 1', '"schema": 1, "schema": 1'))
                self.box.commit_all("unsafe correction input")
                self.assertRejected(self.check())

    def test_existing_manifests_cannot_be_changed_or_deleted(self):
        self.prepare()
        self.base = self.box.commit_all("accepted correction")
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                self.box.git("reset", "--hard", self.base)
                path = self.box.repo / self.manifest_path
                if deleted:
                    path.unlink()
                else:
                    path.write_text("{}")
                self.box.commit_all("alter previous audit record")
                self.assertRejected(self.check())

    def test_later_compile_consumes_fragment_and_retains_corrected_history(self):
        self.prepare()
        self.base = self.box.commit_all("accepted correction")
        archive = self.archive((self.box.repo / "CHANGELOG.md").read_bytes())
        compiled = self.box.run("compile", "--version", "0.2.0", "--date", "2026-11-12", "--preserve-history")
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        self.box.commit_all("ordinary release compile")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.archive((self.box.repo / "CHANGELOG.md").read_bytes()), archive)
        self.assertTrue((self.box.repo / self.manifest_path).is_file())
        self.assertFalse((self.box.repo / self.fragment_path).exists())

    def test_correction_cannot_be_combined_with_release_compilation(self):
        self.prepare()
        self.box.commit_all("correction")
        result = self.box.run("compile", "--version", "0.2.0", "--date", "2026-11-12", "--preserve-history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.box.commit_all("mixed correction and compile")
        self.assertRejected(self.check())

    def test_second_correction_chains_from_latest_archive_hash(self):
        self.prepare()
        self.base = self.box.commit_all("first accepted correction")
        raw = (self.box.repo / "CHANGELOG.md").read_bytes().replace(b"Reviewed public wording", b"Second reviewed wording")
        self.prepare(key="45", raw=raw)
        self.box.commit_all("second correction")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_raw_crlf_unicode_and_final_newline_are_preserved(self):
        raw = self.before.replace("older archive", "older résumé").replace("\n", "\r\n").encode()
        (self.box.repo / "CHANGELOG.md").write_bytes(raw)
        self.base = self.box.commit_all("CRLF history")
        self.prepare()
        self.box.commit_all("byte exact correction")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = self.box.repo / "CHANGELOG.md"
        path.write_bytes(path.read_bytes().removesuffix(b"\r\n"))
        self.box.commit_all("unlisted final newline change")
        self.assertRejected(self.check())

    def test_default_branch_push_still_requires_manifest_for_archive_changes(self):
        self.prepare()
        self.box.commit_all("valid correction")
        result = self.box.run("check", "--base", self.base, "--allow-history-corrections", "--preserve-history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.box.repo / self.manifest_path).unlink()
        self.box.commit_all("remove intent")
        self.assertRejected(self.box.run("check", "--base", self.base, "--allow-history-corrections", "--preserve-history"))

    def test_calendar_and_rolling_archives_use_the_same_correction_contract(self):
        for style in ("calendar", "rolling"):
            with self.subTest(style=style):
                self.box.git("reset", "--hard", self.base)
                self.box.git("clean", "-fd")
                self.heading = "## [2020-01-01]"
                text = "# Changelog\n\n## " + ("Unreleased" if style == "rolling" else "[Unreleased]")
                text += "\n\n" + self.heading + "\n\n### Added\n- " + self.canary + "\n"
                self.box.write("CHANGELOG.md", text)
                self.base = self.box.commit_all("calendar shaped history")
                self.prepare()
                self.box.commit_all("reviewed correction")
                result = self.check(True, "--format", style)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_push_cannot_reorder_unmanifested_archive_spans(self):
        raw = self.before.encode()
        first = raw.index(self.heading.encode())
        second = raw.index(b"## [0.0.1]")
        (self.box.repo / "CHANGELOG.md").write_bytes(raw[:first] + raw[second:] + raw[first:second])
        self.box.commit_all("unmanifested archive reorder")
        self.assertRejected(self.box.run("check", "--base", self.base, "--allow-history-corrections", "--preserve-history"))

    def test_historical_manifest_does_not_freeze_the_fragment_directory(self):
        self.prepare()
        self.box.commit_all("accepted correction")
        self.assertEqual(self.box.run("compile", "--version", "0.2.0", "--date", "2026-11-12", "--preserve-history").returncode, 0)
        self.base = self.box.commit_all("ordinary compile")
        self.box.write("notes/45-new-note.md", fragment("Fixed", "A normal entry after changing the configured directory."))
        self.box.commit_all("ordinary fragment in configured directory")
        result = self.box.run("check", "--base", self.base, "--fragments", "notes", "--enforce-fragments", "--preserve-history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verified_merge_parent_excludes_unrelated_fragments(self):
        self.box.git("checkout", "-b", "correction")
        self.prepare()
        pr_head = self.box.commit_all("correction PR")
        self.box.git("checkout", "master")
        self.box.write("changelog.d/46-unrelated.md", fragment("Added", "Unrelated committed fragment."))
        self.box.commit_all("base advanced")
        self.box.git("merge", "--no-edit", "correction")
        result = self.check(True, "--pr-head", pr_head)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
