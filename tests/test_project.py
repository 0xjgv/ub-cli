import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest

import ub_cli


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ub setup ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "repo with spaces"
        self.root.mkdir()
        self.bin = Path(temporary.name) / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        PYTHONPATH=str(Path(ub_cli.__file__).resolve().parent.parent))
        self.executable(self.bin / "ub", f"exec {shlex.quote(sys.executable)} -c 'from ub_cli.cli import main; main()' \"$@\"\n")
        self.git("init", "-q")

    def executable(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def command(self, args, *, cwd=None, input="", env=None):
        return subprocess.run(args, cwd=cwd or self.root, env=env or self.env, input=input,
                              capture_output=True, text=True, timeout=30)

    def ub(self, *args, **kwargs):
        return self.command([str(self.bin / "ub"), *args], **kwargs)

    def git(self, *args):
        result = self.command(["git", *args])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def initialize(self, *args):
        result = self.ub("init", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.root / ".ub/check"


class ProjectInitTests(RepositoryTests):
    def test_preserves_instructions_and_local_commands_on_repeat(self):
        (self.root / "AGENTS.md").write_text("# Existing rules\nKeep these.\n")
        (self.root / "CLAUDE.md").symlink_to("AGENTS.md")
        (self.root / ".gitignore").write_text("secrets/\n")
        check = self.initialize()
        self.assertTrue(os.access(check, os.X_OK))
        self.assertTrue((self.root / "CLAUDE.md").is_symlink())
        self.assertTrue((self.root / "AGENTS.md").read_text().startswith("# Existing rules\nKeep these.\n"))
        self.assertIn("./.ub/check", (self.root / "CLAUDE.md").read_text())
        self.assertTrue((self.root / ".gitignore").read_text().startswith("secrets/\n"))
        self.executable(check, "exit 0\n")
        files = [check, self.root / "AGENTS.md", self.root / ".gitignore"]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
        self.initialize()
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in files])

    def test_writes_the_ratchet_once_and_never_overwrites_it(self):
        (self.root / "t.py").write_text("x = 1  # noqa\n")
        self.initialize()
        ratchet = self.root / ".ub/ratchet"
        self.assertIn("\nlint.noqa 1\n", ratchet.read_text())
        ratchet.write_text("lint.noqa 5\n")
        self.initialize()
        self.assertEqual(ratchet.read_text(), "lint.noqa 5\n")
        self.assertIn("ub ratchet --update", (self.root / "AGENTS.md").read_text())

    def test_script_forwards_literal_commands_scope_and_runtime_arguments(self):
        source = "src ' $cash"
        (self.root / source).mkdir()
        coverage = "printf '%s' '$HOME'; echo `literal`"
        check = self.initialize("-s", source, "--coverage-command", coverage,
                                "--test-command", "python -m unittest", "--max-workers", "2")
        self.executable(self.bin / "ub", f"exec {shlex.quote(sys.executable)} -c 'import json,os,sys; print(json.dumps([os.getcwd(),sys.argv[1:]]))' \"$@\"\n")
        result = self.command([str(check), "--verbose"], cwd=self.root / source)
        self.assertEqual(result.returncode, 0, result.stderr)
        cwd, args = json.loads(result.stdout)
        self.assertEqual(Path(cwd), self.root)
        self.assertEqual(args, ["check", "-s", "./" + source, "--coverage-command", coverage,
                                "--test-command", "python -m unittest", "--max-workers", "2", "--verbose"])

    def test_validation_failure_does_not_write_other_files(self):
        for name, contents in (("AGENTS.md", "<!-- ub:begin --> broken"), ("CLAUDE.md", "<!-- ub:end -->")):
            with self.subTest(name=name):
                file = self.root / name
                file.write_text(contents)
                result = self.ub("init")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / ".ub").exists())
                self.assertEqual(file.read_text(), contents)
                file.unlink()

    def test_refuses_external_symlinks_and_invalid_source_roots(self):
        external = self.root.parent / "external.md"
        external.write_text("keep")
        (self.root / "CLAUDE.md").symlink_to(external)
        self.assertNotEqual(self.ub("init").returncode, 0)
        self.assertEqual(external.read_text(), "keep")
        self.assertFalse((self.root / "AGENTS.md").exists())
        (self.root / "CLAUDE.md").unlink()
        for source in ("missing", ".."):
            self.assertNotEqual(self.ub("init", "-s", source).returncode, 0)
        self.assertFalse((self.root / ".ub").exists())

    def test_explicit_reconfiguration_does_not_overwrite_existing_command(self):
        check = self.initialize("--test-command", "test-one")
        before = check.read_bytes()
        self.assertNotEqual(self.ub("init", "--test-command", "test-two").returncode, 0)
        self.assertEqual(check.read_bytes(), before)
        self.initialize()

    def test_instruction_alias_cannot_replace_the_check_script(self):
        check = self.root / ".ub/check"
        self.executable(check, "exit 0\n")
        (self.root / "AGENTS.md").symlink_to(".ub/check")
        before = check.read_bytes()
        result = self.ub("init")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(check.read_bytes(), before)
        self.assertFalse((self.root / "CLAUDE.md").exists())


class HookTests(RepositoryTests):
    def install(self, *args):
        result = self.ub("hooks", "install", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def stop(self, payload=None, *args):
        result = self.ub("hooks", "run", "stop", *args, input=json.dumps(payload or {"cwd": str(self.root)}))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_requires_initialization(self):
        result = self.ub("hooks", "install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ub init", result.stderr)
        self.assertFalse((self.root / ".git/hooks/pre-push").exists())

    def test_git_preserves_hook_arguments_stdin_exit_and_runs_check(self):
        check = self.initialize()
        self.executable(check, 'echo quality >> order\nexit "${CHECK_STATUS:-0}"\n')
        hook = self.root / ".git/hooks/pre-push"
        self.executable(hook, 'echo original >> order\nprintf "%s\\n" "$@" > arguments\ncat > refs\nexit "${OLD_STATUS:-0}"\n')
        original = hook.read_bytes()
        self.install()
        self.install()
        self.assertEqual(hook.with_name("pre-push.ub-original").read_bytes(), original)
        result = self.command([str(hook), "origin", "url with spaces"], input="ref1\nref2\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "order").read_text(), "quality\noriginal\n")
        self.assertEqual((self.root / "arguments").read_text(), "origin\nurl with spaces\n")
        self.assertEqual((self.root / "refs").read_text(), "ref1\nref2\n")
        (self.root / "order").unlink()
        result = self.command([str(hook)], env=dict(self.env, OLD_STATUS="9"))
        self.assertEqual(result.returncode, 9)
        self.assertEqual((self.root / "order").read_text(), "quality\noriginal\n")
        (self.root / "order").unlink()
        result = self.command([str(hook)], env=dict(self.env, CHECK_STATUS="7"))
        self.assertEqual(result.returncode, 7)
        self.assertEqual((self.root / "order").read_text(), "quality\n")

    def test_existing_shell_dispatcher_keeps_its_original_name(self):
        check = self.initialize()
        self.executable(check, "exit 0\n")
        hook = self.root / ".git/hooks/pre-push"
        self.executable(hook, 'test "$(basename "$0")" = pre-push || exit 17\necho dispatched\n')
        self.install()
        result = self.command([str(hook)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dispatched", result.stdout)

    def test_non_shell_hook_is_preserved_for_manual_integration(self):
        self.initialize()
        hook = self.root / ".git/hooks/pre-push"
        hook.write_text("#!/usr/bin/python3\nprint('original')\n")
        hook.chmod(0o755)
        before = hook.read_bytes()
        result = self.ub("hooks", "install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manually", result.stderr)
        self.assertEqual(hook.read_bytes(), before)
        self.assertFalse(hook.with_name("pre-push.ub-original").exists())

    def test_git_respects_local_hooks_path_and_does_not_activate_inert_hook(self):
        check = self.initialize()
        self.executable(check, "exit 0\n")
        self.git("config", "core.hooksPath", ".custom hooks")
        hook = self.root / ".custom hooks/pre-push"
        hook.parent.mkdir()
        hook.write_text("not an executable")
        self.install()
        self.assertEqual(self.command([str(hook)]).returncode, 0)
        hook.chmod(0o644)
        self.install()
        self.assertTrue(os.access(hook, os.X_OK))

    def test_git_refuses_shared_external_path_and_backup_collision(self):
        self.initialize()
        self.git("config", "core.hooksPath", str(self.root.parent / "shared"))
        self.assertNotEqual(self.ub("hooks", "install").returncode, 0)
        self.assertFalse((self.root.parent / "shared").exists())
        self.git("config", "--unset", "core.hooksPath")
        backup = self.root / ".git/hooks/pre-push.ub-original"
        backup.write_text("keep")
        self.assertNotEqual(self.ub("hooks", "install").returncode, 0)
        self.assertEqual(backup.read_text(), "keep")

    def test_agent_install_preserves_settings_and_is_idempotent(self):
        self.initialize()
        for target, filename in (("codex", ".codex/hooks.json"), ("claude", ".claude/settings.json")):
            file = self.root / filename
            file.parent.mkdir()
            existing = {"permissions": {"allow": ["Bash(make test)"]}, "hooks": {"Stop": [
                {"hooks": [{"type": "command", "command": "existing-check"}]}]}}
            file.write_text(json.dumps(existing))
            self.install("--target", target)
            data = json.loads(file.read_text())
            self.assertEqual(data["permissions"], existing["permissions"])
            self.assertEqual(data["hooks"]["Stop"][0], existing["hooks"]["Stop"][0])
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            before = file.stat().st_mtime_ns
            self.install("--target", target)
            self.assertEqual(file.stat().st_mtime_ns, before)

    def test_invalid_agent_settings_remain_unchanged(self):
        self.initialize()
        file = self.root / ".codex/hooks.json"
        file.parent.mkdir()
        for contents in ("not json", "[]", '{"hooks": []}', '{"hooks":{"Stop":{}}}'):
            file.write_text(contents)
            result = self.ub("hooks", "install", "--target", "codex")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(file.read_text(), contents)

    def test_stop_pass_fail_loop_guard_and_subdirectory(self):
        check = self.initialize()
        self.executable(check, "echo 'diagnostic output'\nexit 1\n")
        subdir = self.root / "subdir"
        subdir.mkdir()
        failure = self.stop({"cwd": str(subdir), "stop_hook_active": False})
        self.assertEqual(failure["decision"], "block")
        self.assertIn("diagnostic output", failure["reason"])
        self.executable(check, "touch ran\nexit 0\n")
        self.assertNotIn("decision", self.stop({"cwd": str(self.root), "stop_hook_active": True}))
        self.assertFalse((self.root / "ran").exists())
        self.assertNotIn("decision", self.stop())
        self.assertTrue((self.root / "ran").exists())

    def test_stop_warns_the_human_about_uncommitted_ub_changes(self):
        check = self.initialize()
        self.executable(check, "exit 0\n")
        notice = "ub config has uncommitted changes: review .ub/ before committing."
        guard = "UB continuation guard: report any unresolved check failures."
        active = {"cwd": str(self.root), "stop_hook_active": True}
        self.assertEqual(self.stop(), {"systemMessage": notice})
        self.assertEqual(self.stop(active), {"systemMessage": f"{guard} {notice}"})
        self.git("add", ".ub")
        self.git("-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                 "commit", "--no-gpg-sign", "-qm", "ub")
        self.assertEqual(self.stop(), {})
        self.assertEqual(self.stop(active), {"systemMessage": guard})
        self.executable(check, "exit 1\n")
        failure = self.stop()
        self.assertEqual((failure["decision"], failure["systemMessage"]), ("block", notice))

    def test_stop_missing_check_bad_event_and_timeout_block(self):
        self.assertEqual(self.stop()["decision"], "block")
        self.assertEqual(self.stop({"cwd": 1})["decision"], "block")
        check = self.initialize()
        self.executable(check, "sleep 20\n")
        failure = self.stop(None, "--timeout", "1")
        self.assertEqual(failure["decision"], "block")
        self.assertIn("exceeded 1s", failure["reason"])

    def test_agent_command_blocks_when_ub_is_missing(self):
        self.initialize()
        self.install("--target", "codex")
        data = json.loads((self.root / ".codex/hooks.json").read_text())
        command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        result = self.command(["/bin/sh", "-c", command], env=dict(self.env, PATH="/usr/bin:/bin"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Ensure ub is installed", result.stderr)

    def test_real_push_is_blocked_then_succeeds_after_check_passes(self):
        check = self.initialize()
        self.executable(check, "exit 1\n")
        self.install()
        remote = self.root.parent / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("add", ".")
        self.git("-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                 "commit", "--no-gpg-sign", "-qm", "fixture")
        failed = self.command(["git", "push", str(remote), "HEAD:refs/heads/main"])
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.git("ls-remote", str(remote)), "")
        self.executable(check, "exit 0\n")
        self.git("push", str(remote), "HEAD:refs/heads/main")
        self.assertIn("refs/heads/main", self.git("ls-remote", str(remote)))

    def test_push_that_raises_the_ratchet_is_refused_unless_a_human_allows_it(self):
        check = self.initialize()
        self.executable(check, "exit 0\n")
        remote = self.root.parent / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("remote", "add", "origin", str(remote))
        commit = ["-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                  "commit", "--no-gpg-sign", "-qam", "change"]
        self.git("add", ".")
        self.git(*commit)
        self.git("push", "-qu", "origin", "HEAD:main")
        self.install()
        ratchet = self.root / ".ub/ratchet"
        ratchet.write_text(ratchet.read_text().replace("lint.noqa 0", "lint.noqa 1"))
        self.git(*commit)
        refused = self.command(["git", "push", "-q", "origin", "HEAD:main"])
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("\n  .ub/ratchet raised lint.noqa: 0 -> 1\n", refused.stdout + refused.stderr)
        allowed = self.command(["git", "push", "-q", "origin", "HEAD:main"],
                               env=dict(self.env, UB_ALLOW_UB_CHANGE="1"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_push_guards_each_pushed_branch_not_the_checked_out_one(self):
        check = self.initialize()
        self.executable(check, "exit 0\n")
        remote = self.root.parent / "remote.git"
        self.git("init", "--bare", "-q", str(remote))
        self.git("remote", "add", "origin", str(remote))
        commit = ["-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                  "commit", "--no-gpg-sign", "-qam", "change"]
        self.git("add", ".")
        self.git(*commit)
        self.git("push", "-q", "origin", "HEAD:main")
        self.install()
        self.git("checkout", "-qb", "tamper")
        self.executable(check, "exit 0 # loosened\n")
        self.git(*commit)
        self.git("checkout", "-q", "-")
        unguarded = self.command(["git", "push", "-q", "origin", "tamper"])
        self.assertNotEqual(unguarded.returncode, 0)
        self.assertIn("run: git remote set-head origin --auto", unguarded.stderr)
        self.git("remote", "set-head", "origin", "main")
        refused = self.command(["git", "push", "-q", "origin", "tamper"])
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("\n  .ub/check changed\n", refused.stdout)

    def test_install_upgrades_the_previous_hook_block(self):
        from ub_cli.hooks import GIT_CHECK, LEGACY_GIT_CHECK

        self.initialize()
        hook = self.root / ".git/hooks/pre-push"
        self.executable(hook, LEGACY_GIT_CHECK + "echo original\n")
        self.install()
        self.assertEqual(hook.read_text(), "#!/bin/sh\n" + GIT_CHECK + "echo original\n")

    def test_hook_install_works_from_linked_worktree_subdirectory(self):
        self.git("-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "--no-gpg-sign", "-qm", "fixture")
        linked = self.root.parent / "linked"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        initialized = self.ub("init", cwd=linked)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.executable(linked / ".ub/check", "touch checked\n")
        (linked / "nested").mkdir()
        installed = self.ub("hooks", "install", cwd=linked / "nested")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        result = self.command([str(self.root / ".git/hooks/pre-push")], cwd=linked)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((linked / "checked").exists())

    def test_stop_keeps_only_the_tail_of_large_diagnostics(self):
        check = self.initialize()
        self.executable(check, f"{shlex.quote(sys.executable)} -c 'print(\"x\" * 20000); print(\"last diagnostic\")'\nexit 1\n")
        failure = self.stop()
        self.assertLess(len(failure["reason"]), 8500)
        self.assertIn("last diagnostic", failure["reason"])

    def test_timeout_terminates_tests_started_in_a_new_session(self):
        check = self.initialize()
        code = ("from pathlib import Path; from ub_cli.vendor.mutator.runner import CommandRunner; "
                "CommandRunner().run('echo $$ > child.pid; sleep 30', Path.cwd(), None)")
        self.executable(check, f"exec {shlex.quote(sys.executable)} -c {shlex.quote(code)}\n")
        failure = self.stop(None, "--timeout", "1")
        self.assertIn("exceeded 1s", failure["reason"])
        pid = int((self.root / "child.pid").read_text())
        status = self.command(["ps", "-p", str(pid), "-o", "stat="])
        if status.returncode == 0 and not status.stdout.strip().startswith("Z"):
            os.killpg(pid, signal.SIGKILL)
            self.fail(f"Detached test process {pid} survived: {status.stdout}")


class StopDecisionTests(RepositoryTests):
    def test_decision_covers_continuation_pass_fail_and_bad_events(self):
        from ub_cli.hooks import stop_decision

        check = self.initialize()
        self.assertEqual(stop_decision({"stop_hook_active": True}, 5),
                         {"systemMessage": "UB continuation guard: report any unresolved check failures."})
        self.executable(check, "exit 0\n")
        self.assertEqual(stop_decision({"cwd": str(self.root)}, 5), {})
        self.executable(check, "echo broken\nexit 3\n")
        failure = stop_decision({"cwd": str(self.root)}, 5)
        self.assertEqual(failure["decision"], "block")
        self.assertTrue(failure["reason"].startswith("UB checks failed (exit 3)."))
        self.assertIn("broken", failure["reason"])
        for payload in ([], {"cwd": ""}, {"cwd": 1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                stop_decision(payload, 5)
        self.executable(check, "sleep 20\n")
        self.assertIn("exceeded 1s", stop_decision({"cwd": str(self.root)}, 1)["reason"])

    def test_changed_sees_staged_untracked_and_hidden_edits(self):
        from ub_cli.hooks import ub_changed

        commit = ["-c", "user.name=UB Test", "-c", "user.email=test@example.invalid",
                  "commit", "--no-gpg-sign", "-qm", "ub"]
        payload = {"cwd": str(self.root)}
        self.assertFalse(ub_changed(payload))
        check = self.initialize()
        self.assertTrue(ub_changed(payload))
        self.git("add", ".ub")
        self.git(*commit)
        self.assertFalse(ub_changed(payload))
        (self.root / ".ub/notes").write_text("x")
        self.assertTrue(ub_changed(payload))
        (self.root / ".ub/notes").unlink()
        original = check.read_text()
        check.write_text(original + "# staged\n")
        self.git("add", ".ub/check")
        check.write_text(original)
        self.assertTrue(ub_changed(payload))
        self.git("reset", "-q", ".ub/check")
        self.git("update-index", "--assume-unchanged", ".ub/check")
        check.write_text(original + "# hidden\n")
        self.assertTrue(ub_changed(payload))
        for bad in (None, {}, {"cwd": 1}, {"cwd": str(self.root.parent)}):
            self.assertFalse(ub_changed(bad))
