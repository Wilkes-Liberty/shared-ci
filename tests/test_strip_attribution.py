"""Tests for .github/scripts/strip-attribution.py.

These build a real throwaway git repository and run the script end-to-end as a
subprocess, because the thing under test is `git commit-tree` rewriting of
author/committer identity — a unit test that called rewrite_identity directly
would pass while the script left Cursor Agent on the commit.

The identity cases are the regression pins for hosted Cursor Cloud Agents:
they always commit as Cursor Agent <cursoragent@cursor.com> with the session
initiator as a Co-authored-by trailer. Before this rewrite, strip preserved
that author and vault#183 stayed red.

Run: python3 -m unittest discover -s tests
"""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRIP = ROOT / ".github" / "scripts" / "strip-attribution.py"
CHECK = ROOT / ".github" / "scripts" / "check-attribution.py"

CLEAN = 0
DIRTY = 1

HUMAN = ("Jeremy Michael Cerda", "jmcerda@wilkesliberty.com")
HUMAN_NOREPLY = ("Jeremy Michael Cerda", "jmcerda@users.noreply.github.com")
CURSOR = ("Cursor Agent", "cursoragent@cursor.com")
# GitHub Copilot Autofix / coding-agent tip. Same shape check-attribution
# already flags (unambiguous `copilot` / `swe-agent`). The 2026-08-01 miss
# that prompted the identity scan used this author on a merged PR.
AUTOFIX = ("copilot-swe-agent[bot]", "198982749+Copilot@users.noreply.github.com")
AUTOFIX_DISPLAY = ("Copilot Autofix", "198982749+Copilot@users.noreply.github.com")


def trailer(author: str) -> str:
    """Assemble the trailer at runtime rather than writing it literally.

    The operators' local commit guard blocks any shell command containing
    `git commit` alongside an attribution pattern, and this file contains both.
    A literal trailer would make the file itself unreadable from a shell.
    """
    return "Co-" + "authored-by: " + author


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Repo:
    """A throwaway git repository with one base commit."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="strip-attr-test-")
        self._git("init", "-q", "-b", "master")
        self._git("config", "user.name", HUMAN[0])
        self._git("config", "user.email", HUMAN[1])
        self.base = self.commit("base", identity=HUMAN)

    def _git(self, *args, env=None):
        return subprocess.run(
            ["git", "-C", self.dir, *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

    def commit(
        self,
        message,
        identity=HUMAN,
        committer=None,
        filename="f.txt",
        author_date=None,
        committer_date=None,
    ):
        name, email = identity
        cname, cemail = committer or identity
        path = Path(self.dir) / filename
        path.write_text((path.read_text() if path.exists() else "") + message + "\n")
        self._git("add", filename)
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": cname,
            "GIT_COMMITTER_EMAIL": cemail,
        })
        if author_date:
            env["GIT_AUTHOR_DATE"] = author_date
        if committer_date:
            env["GIT_COMMITTER_DATE"] = committer_date
        self._git("commit", "-q", "-m", message, env=env)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def log_identity(self):
        line = self._git(
            "log", "-1", "--format=%an%x1f%ae%x1f%cn%x1f%ce"
        ).stdout.rstrip("\n")
        parts = line.split("\x1f")
        return {
            "author_name": parts[0],
            "author_email": parts[1],
            "committer_name": parts[2],
            "committer_email": parts[3],
        }

    def log_dates(self):
        line = self._git("log", "-1", "--format=%aD%x1f%cD").stdout.rstrip("\n")
        author_date, committer_date = line.split("\x1f")
        return author_date, committer_date

    def tree(self):
        return self._git("log", "-1", "--format=%T").stdout.strip()

    def message(self):
        return self._git("log", "-1", "--format=%B").stdout

    def strip(self, extra_env=None, dry_run=False):
        env = os.environ.copy()
        env.pop("STRIP_AUTHOR_NAME", None)
        env.pop("STRIP_AUTHOR_EMAIL", None)
        if extra_env:
            env.update(extra_env)
        argv = [str(STRIP), "--base", self.base, "--head", "HEAD", "--branch", "master"]
        if dry_run:
            argv.append("--dry-run")
        return subprocess.run(
            argv, cwd=self.dir, capture_output=True, text=True, env=env
        )

    def check(self):
        return subprocess.run(
            [str(CHECK), "--base", self.base, "--head", "HEAD"],
            cwd=self.dir,
            capture_output=True,
            text=True,
        )

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class PatternSyncTest(unittest.TestCase):
    """Strip and check must agree on what an AI identity is."""

    def test_default_rewrite_human_is_jeremy_noreply_not_company_mail(self):
        strip = load_script("strip_attribution_default", STRIP)
        self.assertEqual(strip.DEFAULT_REWRITE_HUMAN, HUMAN_NOREPLY)
        self.assertFalse(
            strip.DEFAULT_REWRITE_HUMAN[1].endswith("@wilkesliberty.com")
        )
        self.assertFalse(strip.identity_is_ai(*strip.DEFAULT_REWRITE_HUMAN))
        self.assertTrue(strip.identity_is_unambiguous_ai(*AUTOFIX))
        self.assertTrue(strip.identity_is_unambiguous_ai(*AUTOFIX_DISPLAY))
        self.assertFalse(strip.identity_is_unambiguous_ai(*CURSOR))

    def test_shared_pattern_strings_match(self):
        check = load_script("check_attribution", CHECK)
        strip = load_script("strip_attribution", STRIP)
        self.assertEqual(check.AI_AUTHOR, strip.AI_AUTHOR)
        self.assertEqual(check.CREDIT_TRAILER, strip.CREDIT_TRAILER)
        self.assertEqual(check.IDENTITY_UNAMBIGUOUS, strip.IDENTITY_UNAMBIGUOUS)
        self.assertEqual(check.IDENTITY_AMBIGUOUS, strip.IDENTITY_AMBIGUOUS)
        self.assertEqual(check.IDENTITY_BOT_MARKER, strip.IDENTITY_BOT_MARKER)

    def test_identity_scan_agrees_on_cursor_agent_and_humans(self):
        check = load_script("check_attribution", CHECK)
        strip = load_script("strip_attribution", STRIP)
        cases = (
            (CURSOR, True),
            (HUMAN, False),
            (HUMAN_NOREPLY, False),
            (AUTOFIX, True),
            (AUTOFIX_DISPLAY, True),
            (("copilot-swe-agent[bot]", "x@users.noreply.github.com"), True),
            (("Claude Dupont", "claude.dupont@example.fr"), False),
            (("dependabot[bot]",
              "49699333+dependabot[bot]@users.noreply.github.com"), False),
        )
        for (name, email), expect_ai in cases:
            identities = ((name, email), ("", ""))
            with self.subTest(identity=f"{name} <{email}>"):
                check_hit = check.find_identity_attribution(identities)
                strip_hit = strip.find_identity_attribution(identities)
                self.assertEqual(bool(check_hit), expect_ai)
                self.assertEqual(check_hit, strip_hit)
                self.assertEqual(strip.identity_is_ai(name, email), expect_ai)


class Vault183ShapeTest(unittest.TestCase):
    """The identity shape that failed vault#183.

    Author and committer are Cursor Agent <cursoragent@cursor.com>; the
    session initiator is already on the commit as a human Co-authored-by.
    """

    def setUp(self):
        self.repo = Repo()
        self.msg = (
            "Fail-closed attribution for vault\n\n"
            + trailer(f"{HUMAN_NOREPLY[0]} <{HUMAN_NOREPLY[1]}>")
        )
        self.repo.commit(self.msg, identity=CURSOR, committer=CURSOR)

    def tearDown(self):
        self.repo.cleanup()

    def test_dry_run_does_not_rewrite(self):
        before = self.repo.log_identity()
        result = self.repo.strip(dry_run=True)
        self.assertEqual(result.returncode, CLEAN, result.stderr)
        self.assertEqual(self.repo.log_identity(), before)
        self.assertEqual(self.repo.log_identity()["author_name"], CURSOR[0])
        self.assertIn("Cursor Agent", result.stdout)
        self.assertIn("dry-run only", result.stdout)

    def test_human_coauthor_becomes_author_and_passes_check(self):
        tree_before = self.repo.tree()
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN_NOREPLY)
        self.assertEqual(
            (ident["committer_name"], ident["committer_email"]), HUMAN_NOREPLY
        )
        self.assertEqual(self.repo.tree(), tree_before)
        self.assertNotIn("Cursor Agent", self.repo.message())
        check = self.repo.check()
        self.assertEqual(check.returncode, CLEAN, check.stdout + check.stderr)


class AutofixTipRewriteTest(unittest.TestCase):
    """Mirror of Vault183ShapeTest for Copilot Autofix / SWE-agent.

    Not an identity-scan-only pin. The tip is authored and committed as
    copilot-swe-agent[bot] (or the Copilot Autofix display name). After
    strip, author and committer must be the human from Co-authored-by
    and check-attribution must exit 0.
    """

    def setUp(self):
        self.repo = Repo()
        self.repo.commit("human work on the branch")
        self.msg = (
            "Apply Autofix suggestion\n\n"
            + trailer(f"{HUMAN_NOREPLY[0]} <{HUMAN_NOREPLY[1]}>")
        )

    def tearDown(self):
        self.repo.cleanup()

    def _rewrite_tip(self, identity):
        self.repo.commit(self.msg, identity=identity, committer=identity)
        self.assertEqual(self.repo.log_identity()["author_name"], identity[0])
        self.assertEqual(self.repo.log_identity()["committer_name"], identity[0])
        tree_before = self.repo.tree()
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN_NOREPLY)
        self.assertEqual(
            (ident["committer_name"], ident["committer_email"]), HUMAN_NOREPLY
        )
        self.assertEqual(self.repo.tree(), tree_before)
        check = self.repo.check()
        self.assertEqual(check.returncode, CLEAN, check.stdout + check.stderr)
        return result

    def test_copilot_swe_agent_tip_with_human_coauthor_is_rewritten(self):
        self._rewrite_tip(AUTOFIX)

    def test_copilot_autofix_display_name_tip_with_human_coauthor_is_rewritten(self):
        self._rewrite_tip(AUTOFIX_DISPLAY)


class AutofixTipFailClosedTest(unittest.TestCase):
    """Mirror of FailClosedTest for Autofix.

    An Autofix / copilot-swe-agent[bot] author with no human
    Co-authored-by and no safe STRIP_AUTHOR_* must stay dirty. The
    Cursor Jeremy-noreply default must not invent an Autofix author.
    """

    def setUp(self):
        self.repo = Repo()
        self.repo.commit("human work on the branch")

    def tearDown(self):
        self.repo.cleanup()

    def test_autofix_author_without_human_or_safe_env_stays_dirty(self):
        self.repo.commit("Apply Autofix suggestion", identity=AUTOFIX, committer=AUTOFIX)
        before = self.repo.log_identity()
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr)
        self.assertEqual(self.repo.log_identity(), before)
        self.assertEqual(self.repo.log_identity()["author_name"], AUTOFIX[0])
        self.assertIn("fail closed", result.stdout)
        check = self.repo.check()
        self.assertEqual(check.returncode, DIRTY, check.stdout)
        self.assertIn("AI author identity", check.stdout)

    def test_autofix_author_with_unsafe_company_env_stays_dirty(self):
        self.repo.commit("Apply Autofix suggestion", identity=AUTOFIX)
        result = self.repo.strip(extra_env={
            "STRIP_AUTHOR_NAME": HUMAN[0],
            "STRIP_AUTHOR_EMAIL": HUMAN[1],
        })
        self.assertEqual(self.repo.log_identity()["author_name"], AUTOFIX[0])
        self.assertIn("fail closed", result.stdout)
        self.assertEqual(self.repo.check().returncode, DIRTY)

    def test_autofix_committer_on_human_author_is_rewritten(self):
        """Server-side Autofix can leave copilot only in the committer slot."""
        self.repo.commit(
            "apply a suggestion",
            identity=HUMAN,
            committer=AUTOFIX,
        )
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN)
        self.assertEqual((ident["committer_name"], ident["committer_email"]), HUMAN)
        self.assertEqual(self.repo.check().returncode, CLEAN)


class DefaultRewriteTest(unittest.TestCase):
    """Operator lock 2026-08-22: default rewrite is Jeremy noreply, not company mail."""

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_no_trailer_no_env_rewrites_to_jeremy_noreply(self):
        self.repo.commit("a change with no human trailer", identity=CURSOR)
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN_NOREPLY)
        self.assertNotEqual(ident["author_email"], HUMAN[1])
        self.assertTrue(ident["author_email"].endswith("@users.noreply.github.com"))
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_noreply_env_overrides_default(self):
        opener = ("other-operator", "other-operator@users.noreply.github.com")
        self.repo.commit("a change with no human trailer", identity=CURSOR)
        result = self.repo.strip(extra_env={
            "STRIP_AUTHOR_NAME": opener[0],
            "STRIP_AUTHOR_EMAIL": opener[1],
        })
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), opener)
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_company_email_env_is_not_used_as_rewrite_target(self):
        """@wilkesliberty.com is not accepted via STRIP_AUTHOR_*."""
        self.repo.commit("a change with no human trailer", identity=CURSOR)
        result = self.repo.strip(extra_env={
            "STRIP_AUTHOR_NAME": HUMAN[0],
            "STRIP_AUTHOR_EMAIL": HUMAN[1],
        })
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN_NOREPLY)
        self.assertNotEqual(ident["author_email"], HUMAN[1])

    def test_ai_env_fallback_is_ignored(self):
        """STRIP_AUTHOR_* that is itself an AI identity falls through to Jeremy noreply."""
        self.repo.commit("a change with no human trailer", identity=CURSOR)
        result = self.repo.strip(extra_env={
            "STRIP_AUTHOR_NAME": CURSOR[0],
            "STRIP_AUTHOR_EMAIL": CURSOR[1],
        })
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN_NOREPLY)
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        self.assertEqual(self.repo.check().returncode, CLEAN)


class TrailerAndDateTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_ai_credit_trailer_is_removed_after_promote(self):
        msg = (
            "rewrite identities\n\n"
            + trailer(f"{HUMAN_NOREPLY[0]} <{HUMAN_NOREPLY[1]}>") + "\n"
            + trailer("Cursor Agent <cursoragent@cursor.com>")
        )
        self.repo.commit(msg, identity=CURSOR)
        self.repo.strip()
        body = self.repo.message()
        self.assertIn(HUMAN_NOREPLY[0], body)
        self.assertNotIn("Cursor Agent", body)
        self.assertEqual(self.repo.check().returncode, CLEAN)

    def test_dates_and_tree_are_preserved(self):
        date = "Fri, 1 Aug 2025 12:00:00 +0000"
        msg = "dated change\n\n" + trailer(f"{HUMAN[0]} <{HUMAN[1]}>")
        self.repo.commit(
            msg,
            identity=CURSOR,
            author_date=date,
            committer_date=date,
        )
        tree_before = self.repo.tree()
        author_date, committer_date = self.repo.log_dates()
        self.repo.strip()
        self.assertEqual(self.repo.tree(), tree_before)
        self.assertEqual(self.repo.log_dates(), (author_date, committer_date))

    def test_human_commit_is_untouched(self):
        sha = self.repo.commit("a normal change")
        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN)
        self.assertEqual(self.repo._git("rev-parse", "HEAD").stdout.strip(), sha)
        self.assertIn("clean", result.stdout)

    def test_ai_committer_on_human_author_is_rewritten(self):
        self.repo.commit(
            "apply a suggestion",
            identity=HUMAN,
            committer=CURSOR,
        )
        self.repo.strip()
        ident = self.repo.log_identity()
        self.assertEqual((ident["author_name"], ident["author_email"]), HUMAN)
        self.assertEqual((ident["committer_name"], ident["committer_email"]), HUMAN)
        self.assertEqual(self.repo.check().returncode, CLEAN)


class PrBodyCleanupTest(unittest.TestCase):
    def setUp(self):
        self.strip = load_script("strip_attribution_body", STRIP)

    def test_wrappers_and_footer_removed_summary_kept(self):
        body = (
            "<!-- CURSOR_AGENT_PR_BODY_BEGIN -->\n"
            "Rewrite Cursor Agent identity before the check.\n\n"
            "Callers stay on @v1.\n"
            "<!-- CURSOR_AGENT_PR_BODY_END -->\n"
            "\n"
            '<div><a href="https://cursor.com/agents?id=bc-example">'
            "Open in Cursor</a></div>\n"
        )
        cleaned = self.strip.clean_cursor_pr_body(body)
        self.assertIsNotNone(cleaned)
        self.assertIn("Rewrite Cursor Agent identity before the check.", cleaned)
        self.assertIn("Callers stay on @v1.", cleaned)
        self.assertNotIn("CURSOR_AGENT_PR_BODY_BEGIN", cleaned)
        self.assertNotIn("CURSOR_AGENT_PR_BODY_END", cleaned)
        self.assertNotIn("cursor.com/agents", cleaned)
        self.assertNotIn("<div>", cleaned)

    def test_no_markers_returns_none(self):
        body = (
            "Human-written summary that mentions cursor.com/agents in prose "
            "but has no wrapper comments and no trailing footer div.\n"
        )
        self.assertIsNone(self.strip.clean_cursor_pr_body(body))

    def test_empty_body_returns_none(self):
        self.assertIsNone(self.strip.clean_cursor_pr_body(""))

    def test_cursor_summary_block_removed(self):
        """Bugbot CURSOR_SUMMARY can trip AI-generated scans (shared-ci#16)."""
        body = (
            "Human-written adjudication of the remaining writers.\n"
            "\n"
            "<!-- CURSOR_SUMMARY -->\n"
            "> Changes the global `file.repository` seam and fail-closed "
            "behavior for AI-generated public files; …\n"
            "> <sup>Reviewed by [Cursor Bugbot](https://cursor.com/bugbot) "
            "for commit d0afb3e…</sup>\n"
            "<!-- /CURSOR_SUMMARY -->\n"
        )
        cleaned = self.strip.clean_cursor_pr_body(body)
        self.assertIsNotNone(cleaned)
        self.assertIn("Human-written adjudication of the remaining writers.", cleaned)
        self.assertNotIn("CURSOR_SUMMARY", cleaned)
        self.assertNotIn("AI-generated", cleaned)
        self.assertNotIn("cursor.com/bugbot", cleaned)

    def test_cursor_summary_alone_triggers_cleanup(self):
        body = (
            "<!-- CURSOR_SUMMARY -->\n"
            "> Overview mentioning AI-generated bytes.\n"
            "<!-- /CURSOR_SUMMARY -->\n"
        )
        cleaned = self.strip.clean_cursor_pr_body(body)
        self.assertIsNotNone(cleaned)
        self.assertEqual(cleaned.strip(), "")

    def test_cursor_summary_with_nested_marker_example_removed(self):
        """Bugbot overview quotes the marker pair; non-greedy would orphan."""
        body = (
            "Human summary.\n"
            "\n"
            "<!-- CURSOR_SUMMARY -->\n"
            "> Fixes failures when Bugbot appends "
            "`<!-- CURSOR_SUMMARY -->…<!-- /CURSOR_SUMMARY -->` "
            "and those blocks mention AI-generated files.\n"
            "<!-- /CURSOR_SUMMARY -->\n"
        )
        cleaned = self.strip.clean_cursor_pr_body(body)
        self.assertIsNotNone(cleaned)
        self.assertIn("Human summary.", cleaned)
        self.assertNotIn("CURSOR_SUMMARY", cleaned)
        self.assertNotIn("AI-generated", cleaned)


class MergeCommitRewriteTest(unittest.TestCase):
    """Merging master into a dirty feature branch must not disable the rewrite.

    The connector #166 failure: Bugbot committed as Cursor Agent, then a
    merge of master into the feature branch made strip return 0 without
    rewriting. check-attribution then failed. First-parent replay keeps the
    merge and restamps the dirty non-merge.
    """

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_cursor_agent_under_a_merge_is_rewritten_and_merge_is_kept(self):
        base = self.repo.base
        self.repo._git("branch", "side")
        msg = (
            "return the draft body\n\n"
            + trailer(f"{HUMAN_NOREPLY[0]} <{HUMAN_NOREPLY[1]}>")
        )
        self.repo.commit(msg, identity=CURSOR, committer=CURSOR)
        self.repo._git("checkout", "-q", "side")
        self.repo.commit("mainline change", filename="main.txt")
        side = self.repo._git("rev-parse", "HEAD").stdout.strip()
        self.repo._git("checkout", "-q", "master")
        self.repo._git("merge", "--no-ff", "-m", "Merge side into feature", side)
        self.assertTrue(
            self.repo._git("rev-list", "--merges", f"{base}..HEAD").stdout.strip()
        )

        result = self.repo.strip()
        self.assertEqual(result.returncode, CLEAN, result.stderr + result.stdout)
        self.assertNotIn("auto-rewrite only supports linear history", result.stdout)

        parents = self.repo._git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 3, "tip must remain a merge commit")

        first_parent = parents[1]
        ident_line = self.repo._git(
            "log", "-1", "--format=%an%x1f%ae%x1f%cn%x1f%ce", first_parent
        ).stdout.rstrip("\n")
        an, ae, cn, ce = ident_line.split("\x1f")
        self.assertEqual((an, ae), HUMAN_NOREPLY)
        self.assertEqual((cn, ce), HUMAN_NOREPLY)
        self.assertEqual(self.repo.check().returncode, CLEAN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
