"""Tests for .github/scripts/check-attribution.py.

These build a real throwaway git repository and run the script end-to-end as a
subprocess, because the thing under test is partly the `git log --format` string
-- a unit test that called `find_identity_attribution` directly would pass while
the script fed it nothing.

The identity cases are the regression pins for the gap that prompted this file:
on 2026-08-01 a pull request merged carrying `copilot-swe-agent[bot]` in its
author field, and this check passed it. Trailers had been stripped; nothing ever
looked at who the commit claimed wrote it.

Run: python3 -m unittest discover -s tests
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parent.parent
          / ".github" / "scripts" / "check-attribution.py")

CLEAN = 0
DIRTY = 1

HUMAN = ("Jeremy Michael Cerda", "jmcerda@wilkesliberty.com")


def trailer(author: str) -> str:
    """Assemble the trailer at runtime rather than writing it literally.

    The operators' local commit guard blocks any shell command containing
    `git commit` alongside an attribution pattern, and this file contains both.
    A literal trailer would make the file itself unreadable from a shell.
    """
    return "Co-" + "authored-by: " + author


class Repo:
    """A throwaway git repository with one base commit."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="attr-test-")
        self._git("init", "-q", "-b", "master")
        self._git("config", "user.name", HUMAN[0])
        self._git("config", "user.email", HUMAN[1])
        self.base = self.commit("base", identity=HUMAN)

    def _git(self, *args, env=None):
        return subprocess.run(["git", "-C", self.dir, *args],
                              capture_output=True, text=True, check=True, env=env)

    def commit(self, message, identity=HUMAN, committer=None, filename="f.txt"):
        """Add a commit with an explicit author (and optionally committer).

        `filename` exists so the merge tests can touch separate files on the two
        branches; appending every commit to one file makes any merge conflict.
        """
        import os
        name, email = identity
        cname, cemail = committer or identity
        path = Path(self.dir) / filename
        path.write_text((path.read_text() if path.exists() else "") + message + "\n")
        self._git("add", filename)
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": cname, "GIT_COMMITTER_EMAIL": cemail,
        })
        self._git("commit", "-q", "-m", message, env=env)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def check(self, title=None, body=None):
        argv = [str(SCRIPT), "--base", self.base, "--head", "HEAD"]
        for flag, text in (("--title-file", title), ("--body-file", body)):
            if text is not None:
                f = Path(self.dir) / (flag.strip("-") + ".txt")
                f.write_text(text)
                argv += [flag, str(f)]
        return subprocess.run(argv, cwd=self.dir, capture_output=True, text=True)

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class BaselineTest(unittest.TestCase):
    """The clean case must pass, or every negative test below is meaningless."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_human_commit_passes(self):
        self.repo.commit("a normal change")
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_trailer_in_message_still_fails(self):
        """The pre-existing behaviour must survive the identity change."""
        self.repo.commit("a change\n\n" + trailer("Claude <noreply@anthropic.com>"))
        self.assertEqual(self.repo.check().returncode, DIRTY)


class IdentityTest(unittest.TestCase):
    """An AI name in the author or committer slot is credit, and must fail.

    Every case here passed the check before the identity scan existed.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_ai_author_name_fails(self):
        self.repo.commit("fix: correct a count",
                         identity=("copilot-swe-agent[bot]",
                                   "1989@users.noreply.github.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("AI author identity", result.stdout)

    def test_ai_author_email_fails(self):
        """The name can look human while the address gives it away."""
        self.repo.commit("fix: correct a count",
                         identity=("J. Doe", "198982749+Copilot@users.noreply.github.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("AI author identity", result.stdout)

    def test_ai_committer_fails_even_with_human_author(self):
        """Server-side suggestions can leave the AI in the committer slot only."""
        self.repo.commit("fix: apply a suggestion", identity=HUMAN,
                         committer=("Claude", "noreply@anthropic.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("AI committer identity", result.stdout)

    def test_clean_message_does_not_excuse_a_dirty_identity(self):
        """The exact shape of the miss: trailers stripped, author still a bot."""
        self.repo.commit("a perfectly ordinary subject line",
                         identity=("copilot-swe-agent[bot]", "x@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)


class IdentityFalsePositiveTest(unittest.TestCase):
    """Identity matching is word-bounded, and must not fail legitimate committers.

    Names are far more varied than commit prose. The bare alternation the prose
    patterns use matches substrings, and `llama` sits inside real surnames.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_dependabot_passes(self):
        """Not every bot is an authorship claim, and these repos rely on this one."""
        self.repo.commit("build(deps): bump a dependency",
                         identity=("dependabot[bot]",
                                   "49699333+dependabot[bot]@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_github_actions_bot_passes(self):
        self.repo.commit("ci: regenerate",
                         identity=("github-actions[bot]",
                                   "41898282+github-actions[bot]@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_surname_containing_an_ai_vendor_substring_passes(self):
        """`Guillamas` contains `llama`; `Codexis` contains `codex`."""
        for name, email in (("Ana Guillamas", "ana@example.com"),
                            ("Rui Codexis", "rui@example.com")):
            with self.subTest(name=name):
                repo = Repo()
                try:
                    repo.commit("a normal change", identity=(name, email))
                    self.assertEqual(repo.check().returncode, CLEAN)
                finally:
                    repo.cleanup()

    def test_external_contributor_address_passes(self):
        """The control runs on the published projects too.

        Company policy requires the operator's own company address, but
        enforcing that here would fail every outside pull request on the
        published repositories -- so the identity scan deliberately checks for
        AI names and not for the domain.
        """
        self.repo.commit("fix: upstream contribution",
                         identity=("Outside Contributor", "someone@example.org"))
        self.assertEqual(self.repo.check().returncode, CLEAN)


class MergeCommitTest(unittest.TestCase):
    """Merge commits are in scope, and the identity scan is why that matters.

    `commits_in_range` carried a three-way contradiction before this: the
    docstring said merges were excluded, the comment beside the git invocation
    said they were included on purpose, and the implementation passed no
    `--no-merges`. These pin the behaviour so prose and code cannot drift apart
    again without something going red.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def _merge(self, identity):
        """Create a side branch and merge it back with an explicit identity."""
        import os
        self.repo._git("checkout", "-q", "-b", "side")
        self.repo.commit("side work", filename="side.txt")
        self.repo._git("checkout", "-q", "master")
        self.repo.commit("master work", filename="master.txt")
        env = os.environ.copy()
        name, email = identity
        env.update({
            "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
        })
        self.repo._git("merge", "--no-ff", "-q", "side", "-m",
                       "Merge pull request #1 from side", env=env)

    def test_merge_commits_are_scanned(self):
        self._merge(HUMAN)
        result = self.repo.check()
        self.assertEqual(result.returncode, CLEAN)
        # 3 = side work + master work + the merge itself.
        self.assertIn("Scanned 3 commit(s)", result.stdout)

    def test_ai_authored_merge_is_caught(self):
        """The case skipping merges would have missed entirely.

        A merge is authored by whoever performed it, so an agent that merges a
        pull request signs that commit with its own identity.
        """
        self._merge(("copilot-swe-agent[bot]", "x@users.noreply.github.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("AI author identity", result.stdout)


class SeparatorInjectionTest(unittest.TestCase):
    """Identity fields are attacker-controlled; the parser must not trust them.

    The first version split `git log` output on \\x1e and assumed only the
    message could contain it. An author name is written by whoever made the
    commit, so on a fork pull request that assumption is an attacker's choice.
    A name carrying the separator produced the wrong field count, and the parser
    then skipped the record — craft a name, skip the scan.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_separator_in_author_name_does_not_hide_the_commit(self):
        hostile = "Bad\x1eActor"
        self.repo.commit("a change", identity=(hostile, "bad@example.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, CLEAN)
        self.assertIn("Scanned 1 commit(s)", result.stdout)

    def test_separator_in_name_does_not_hide_an_ai_identity(self):
        """The bypass proper: hide a bot commit behind a crafted separator."""
        self.repo.commit("a change",
                         identity=("copilot-swe-agent[bot]\x1epadding",
                                   "x@users.noreply.github.com"))
        result = self.repo.check()
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("AI author identity", result.stdout)

    def test_multiline_message_is_one_commit(self):
        """A multi-line BODY must not be read as several commits.

        Named for the body, not the subject: git takes the subject as the first
        line only, so a subject containing a newline is not a case that exists.
        The record boundary is what is under test here.
        """
        self.repo.commit("subject\n\nbody line\n\nmore body")
        result = self.repo.check()
        self.assertIn("Scanned 1 commit(s)", result.stdout)


class IdentityBoundaryTest(unittest.TestCase):
    """`\\b` is built on `\\w`, which counts `_` — the commonest bot separator.

    `\\bcopilot\\b` does not match `copilot_swe_agent`, so the boundary added to
    prevent surname false positives was also skipping the identities this check
    exists to catch.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_underscore_delimited_bot_is_caught(self):
        self.repo.commit("a change",
                         identity=("copilot_swe_agent", "x@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)

    def test_underscore_delimited_vendor_in_email_is_caught(self):
        self.repo.commit("a change", identity=("J. Doe", "openai_bot@example.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)


class AmbiguousNameTest(unittest.TestCase):
    """Some vendor tokens are also ordinary human given names.

    `claude` and `devin` are names people have. Flagging them on sight would
    refuse an outside contribution for being named wrong — on the published
    projects, that is the contribution this control is supposed to welcome.
    They count only alongside a bot/agent marker.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_human_named_claude_passes(self):
        self.repo.commit("fix: upstream contribution",
                         identity=("Claude Dupont", "claude.dupont@example.fr"))
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_human_named_devin_passes(self):
        self.repo.commit("fix: upstream contribution",
                         identity=("Devin O'Brien", "devin@example.com"))
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_claude_with_a_bot_marker_is_caught(self):
        self.repo.commit("a change",
                         identity=("claude-code[bot]", "x@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)

    def test_ambiguous_name_with_vendor_domain_is_caught(self):
        """`anthropic` is unambiguous, so the address alone settles it."""
        self.repo.commit("a change", identity=("Claude", "noreply@anthropic.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)

    def test_marker_words_are_bounded_too(self):
        """`agent` unbounded matches inside ordinary words in real addresses.

        `agentur` is German for agency and turns up in agency domains;
        `Reagent` is a surname. Either one, beside an ambiguous given name,
        would have flagged a human — the marker has to be bounded exactly like
        the tokens it qualifies.
        """
        for name, email in (("Claude Weber", "claude@agentur-berlin.de"),
                            ("Devin Reagent", "devin.reagent@example.com")):
            with self.subTest(name=name):
                repo = Repo()
                try:
                    repo.commit("fix: upstream contribution", identity=(name, email))
                    self.assertEqual(repo.check().returncode, CLEAN)
                finally:
                    repo.cleanup()

    def test_real_agent_marker_still_matches(self):
        """Bounding the marker must not disarm it."""
        self.repo.commit("a change",
                         identity=("claude-code-agent", "x@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, DIRTY)


class HarnessFailureTest(unittest.TestCase):
    """A gate that cannot read the commits must say so, legibly.

    Unparseable output and undecodable commits already failed closed by
    crashing — an uncaught traceback exits non-zero, which is the safe
    direction. But a traceback is not a finding: nothing in it says what to do,
    and its exit code is the same one a genuine attribution produces, so the two
    are indistinguishable to whoever reads the check.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_unreadable_range_reports_a_finding_not_a_traceback(self):
        """The pre-existing path: git itself fails (usually a shallow clone)."""
        result = subprocess.run(
            [str(SCRIPT), "--base", "does-not-exist", "--head", "HEAD"],
            cwd=self.repo.dir, capture_output=True, text=True)
        self.assertEqual(result.returncode, DIRTY)
        self.assertIn("::error::", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_unparseable_output_reports_a_finding_not_a_traceback(self):
        """The new path: git succeeds but its output does not divide evenly.

        Driven through a `git` shim on PATH rather than by patching internals,
        so it exercises the same code the workflow runs. Without the handler
        this exits on an uncaught ValueError — still non-zero, so still failing
        closed, but indistinguishable in the check list from a real finding.
        """
        import os, stat
        shim_dir = Path(self.repo.dir) / "shim"
        shim_dir.mkdir()
        shim = shim_dir / "git"
        # Three NUL-separated fields; the parser expects a multiple of seven.
        shim.write_text('#!/bin/sh\nprintf "a\\000b\\000c\\000"\n')
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

        env = os.environ.copy()
        env["PATH"] = f"{shim_dir}:{env['PATH']}"
        result = subprocess.run(
            [str(SCRIPT), "--base", "BASE", "--head", "HEAD"],
            cwd=self.repo.dir, capture_output=True, text=True, env=env)

        self.assertEqual(result.returncode, DIRTY, "must fail closed")
        self.assertIn("::error::", result.stdout,
                      "must emit a workflow annotation, not just crash")
        self.assertNotIn("Traceback", result.stderr,
                         "a traceback is not a finding")


class CursorSummaryBodyTest(unittest.TestCase):
    """Bugbot CURSOR_SUMMARY prose must not fail the body scan (shared-ci#16).

    The stripper removes the block before the scan; this pins that the raw
    block would be dirty and the cleaned body is clean.
    """

    def setUp(self):
        self.repo = Repo()
        import importlib.util
        strip_path = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "strip-attribution.py"
        spec = importlib.util.spec_from_file_location("strip_attribution", strip_path)
        self.strip = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.strip)

    def tearDown(self):
        self.repo.cleanup()

    def test_raw_cursor_summary_with_ai_generated_fails(self):
        self.repo.commit("a normal change")
        body = (
            "Human-written PR summary with no authorship claim.\n"
            "\n"
            "<!-- CURSOR_SUMMARY -->\n"
            "> Changes fail-closed behavior for AI-generated public files.\n"
            "<!-- /CURSOR_SUMMARY -->\n"
        )
        self.assertEqual(self.repo.check(body=body).returncode, DIRTY)

    def test_cleaned_cursor_summary_body_passes(self):
        self.repo.commit("a normal change")
        body = (
            "Human-written PR summary with no authorship claim.\n"
            "\n"
            "<!-- CURSOR_SUMMARY -->\n"
            "> Changes fail-closed behavior for AI-generated public files.\n"
            "<!-- /CURSOR_SUMMARY -->\n"
        )
        cleaned = self.strip.clean_cursor_pr_body(body)
        self.assertIsNotNone(cleaned)
        self.assertEqual(self.repo.check(body=cleaned).returncode, CLEAN)


class AnnotationEscapingTest(unittest.TestCase):
    """Findings quote attacker-controlled text into `::error::` annotations.

    A raw newline ends the annotation and begins a new line of workflow output,
    so a crafted identity could emit its own workflow commands or forge a
    reassuring log line.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_percent_in_a_reported_subject_is_encoded(self):
        r"""The reachable vector, and it is not a literal newline.

        Git will not put a raw newline into a subject or an identity, so the
        obvious injection cannot be built that way. `%` can: GitHub Actions
        DECODES percent-escapes in annotation text, so a subject containing the
        four characters `%0A` becomes a real line break in the workflow log, and
        whatever follows it starts a new line -- `::notice::` included.

        Escaping `%` first turns it into `%250A`, which renders as the literal
        text the author actually wrote.
        """
        self.repo.commit("fix: 100%0A::notice::forged and 50% done",
                         identity=("copilot-swe-agent[bot]", "x@users.noreply.github.com"))
        out = self.repo.check().stdout
        errors = [l for l in out.splitlines() if l.startswith("::error::")]
        self.assertTrue(errors, "expected a finding to quote the subject")
        joined = "\n".join(errors)
        self.assertIn("%250A", joined, "the percent was not encoded")
        self.assertNotIn("%0A::notice::", joined,
                         "a raw %0A survives and Actions would decode it to a newline")
        self.assertIn("50%25 done", joined, "an ordinary percent was not encoded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
