"""The DeepSeek port must stay structurally complete.

The port is a separate runtime tree (`.deepcode/`) rather than a rename of the
legacy Claude tree, so a missing skill or an accidental `.claude/` path is
silent at runtime until the user invokes the command. This test runs the same
validator the CI lint job runs.
"""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VALIDATOR = REPO / "tools" / "verify_deepseek_port.py"
SKILLS = REPO / ".deepcode" / "skills"

REQUIRED = {
    "setup",
    "scrape",
    "rank",
    "apply",
    "interview",
    "outcome",
    "gmail-sync",
    "notion-sync",
    "html-report",
    "dashboard",
    "ui",
    "expand",
    "upskill",
    "add-template",
    "add-portal",
    "reset",
}


class DeepSeekPortTests(unittest.TestCase):
    def test_validator_passes(self):
        result = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("verify_deepseek_port: OK", result.stdout)

    def test_every_workflow_is_an_invokable_skill(self):
        for name in REQUIRED:
            with self.subTest(name=name):
                path = SKILLS / name / "SKILL.md"
                self.assertTrue(path.is_file(), f"missing /{name} skill at {path}")
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"name: {name}", text.split("---", 2)[1])

    def test_settings_has_no_secret(self):
        text = (REPO / ".deepcode" / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("API_KEY", text)
        self.assertNotIn("sk-", text)

    def test_profile_is_canonical(self):
        profile = (REPO / "DEEPSEEK.md").read_text(encoding="utf-8")
        self.assertIn("[YOUR_NAME]", profile)
        self.assertIn("[YOUR_CITY]", profile)
        self.assertNotIn("CLAUDE.md", profile)


if __name__ == "__main__":
    unittest.main()
