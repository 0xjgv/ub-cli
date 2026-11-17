import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ub_cli.cli import run


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.enterContext(patch.dict(os.environ, HOME=tmp.name))

    def skill(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = run(["skill", *arguments])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return status

    def install(self, target, *extra):
        return self.skill("install", "ub-mode", "--target", target, *extra)

    def test_targets_install_independently_and_repeat_without_writing(self):
        for target, agent, invocation in (("codex", ".agents", "$ub-mode"), ("claude", ".claude", "/ub-mode")):
            with self.subTest(target=target):
                self.assertEqual(self.install(target), 0)
                skill = self.home / agent / "skills" / "ub-mode" / "SKILL.md"
                self.assertEqual(f"Installed: {skill}\nInvoke {invocation} in {target}.\n", self.stdout)
                self.assertIn("name: ub-mode", skill.read_text())
                self.assertIn("ub dry --threshold 0.8", skill.read_text())
                self.assertIn("MIT License", skill.with_name("LICENSE").read_text())
                timestamp = skill.stat().st_mtime_ns
                self.assertEqual(self.install(target), 0)
                self.assertEqual(skill.stat().st_mtime_ns, timestamp)
                self.assertTrue(self.stdout.startswith(f"Already installed: {skill}\n"))

    def test_edit_requires_force_and_unrelated_files_survive(self):
        folder = self.home / ".agents/skills/ub-mode"
        folder.mkdir(parents=True)
        skill = folder / "SKILL.md"
        skill.write_text("personal edits")
        (folder / "notes.md").write_text("keep")
        self.assertEqual(self.install("codex"), 1)
        self.assertIn(f"{skill} already exists and differs. Use --force", self.stderr)
        self.assertEqual(skill.read_text(), "personal edits")
        self.assertFalse((folder / "LICENSE").exists())
        self.assertEqual(self.install("codex", "--force"), 0)
        self.assertIn("ub mutate", skill.read_text())
        self.assertEqual((folder / "notes.md").read_text(), "keep")

    def test_no_arguments_installs_every_target(self):
        self.assertEqual(self.skill("install"), 0)
        self.assertEqual(2, self.stdout.count("Installed: "))
        for agent in (".agents", ".claude"):
            skill = self.home / agent / "skills" / "ub-mode" / "SKILL.md"
            self.assertIn("name: ub-mode", skill.read_text())
            self.assertIn("MIT License", skill.with_name("LICENSE").read_text())

    def test_edit_in_one_target_blocks_every_target(self):
        skill = self.home / ".claude/skills/ub-mode/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("personal edits")
        self.assertEqual(self.skill("install"), 1)
        self.assertIn("already exists and differs", self.stderr)
        self.assertEqual(skill.read_text(), "personal edits")
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual(self.skill("install", "--force"), 0)
        self.assertIn("ub mutate", skill.read_text())
        self.assertTrue((self.home / ".agents/skills/ub-mode/SKILL.md").is_file())

    def test_missing_subcommand_is_a_usage_error(self):
        self.assertEqual(self.skill(), 2)
        self.assertIn("usage: ub skill", self.stderr)
        self.assertFalse((self.home / ".claude").exists())

    def test_filesystem_error_reports_and_fails(self):
        (self.home / ".claude/skills/ub-mode/SKILL.md").mkdir(parents=True)
        self.assertEqual(self.install("claude", "--force"), 1)
        self.assertTrue(self.stderr.startswith("ub skill: "), self.stderr)
