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

    def install(self, target, *extra):
        return run(["skill", "install", "ub-mode", "--target", target, *extra])

    def test_targets_install_independently_and_repeat_without_writing(self):
        for target, agent in (("codex", ".agents"), ("claude", ".claude")):
            with self.subTest(target=target):
                self.assertEqual(self.install(target), 0)
                skill = self.home / agent / "skills" / "ub-mode" / "SKILL.md"
                self.assertIn("name: ub-mode", skill.read_text())
                self.assertIn("ub dry --threshold 0.8", skill.read_text())
                self.assertIn("MIT License", skill.with_name("LICENSE").read_text())
                timestamp = skill.stat().st_mtime_ns
                self.assertEqual(self.install(target), 0)
                self.assertEqual(skill.stat().st_mtime_ns, timestamp)

    def test_edit_requires_force_and_unrelated_files_survive(self):
        folder = self.home / ".agents/skills/ub-mode"
        folder.mkdir(parents=True)
        skill = folder / "SKILL.md"
        skill.write_text("personal edits")
        (folder / "notes.md").write_text("keep")
        self.assertEqual(self.install("codex"), 1)
        self.assertEqual(skill.read_text(), "personal edits")
        self.assertFalse((folder / "LICENSE").exists())
        self.assertEqual(self.install("codex", "--force"), 0)
        self.assertIn("ub mutate", skill.read_text())
        self.assertEqual((folder / "notes.md").read_text(), "keep")

    def test_no_arguments_installs_every_target(self):
        self.assertEqual(run(["skill", "install"]), 0)
        for agent in (".agents", ".claude"):
            skill = self.home / agent / "skills" / "ub-mode" / "SKILL.md"
            self.assertIn("name: ub-mode", skill.read_text())
            self.assertIn("MIT License", skill.with_name("LICENSE").read_text())

    def test_edit_in_one_target_blocks_every_target(self):
        skill = self.home / ".claude/skills/ub-mode/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("personal edits")
        self.assertEqual(run(["skill", "install"]), 1)
        self.assertEqual(skill.read_text(), "personal edits")
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual(run(["skill", "install", "--force"]), 0)
        self.assertIn("ub mutate", skill.read_text())
        self.assertTrue((self.home / ".agents/skills/ub-mode/SKILL.md").is_file())
