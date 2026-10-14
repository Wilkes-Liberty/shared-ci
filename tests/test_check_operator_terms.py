import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".github/scripts/check-operator-terms.py"
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
            "Apply the `needs-human` label.",
            "Use human_coauthors() here.",
            "See HUMAN-CHECKPOINTS.md.",
            'Templates say "must be written by a human".',
            "The Human Action Model.",
            "a human merges <!-- operator-terminology: allow -->",
            "Use `a human` in code spans.",
            "{{ $value | humanize }}",
        ])
        self.assertEqual(mod.hits(text), [])

    def test_scope(self):
        self.assertTrue(mod.in_scope("docs/README.md"))
        self.assertTrue(mod.in_scope(".github/workflows/ci.yml"))
        self.assertTrue(mod.in_scope("skills/x/run.sh"))
        self.assertFalse(mod.in_scope("src/app.py"))


class CliTest(unittest.TestCase):
    def run_on(self, body, *flags):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "doc.md"
            p.write_text(body, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), *flags, str(p)],
                capture_output=True, text=True,
            )

    def test_warn_only_exits_zero(self):
        r = self.run_on("a human merges\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("::warning", r.stdout)

    def test_strict_fails(self):
        r = self.run_on("a human merges\n", "--strict")
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error", r.stdout)

    def test_clean_passes_strict(self):
        self.assertEqual(self.run_on("an operator merges\n", "--strict").returncode, 0)


if __name__ == "__main__":
    unittest.main()
