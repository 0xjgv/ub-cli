import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ub_cli import ratchet


PYTHON_SAMPLE = {
    "x = 1  # noqa: E501": "lint.noqa",
    "x = 1  # NoQA: F401": "lint.noqa",
    "# ruff: noqa": "lint.noqa",
    "y = f()  # type: ignore[attr-defined]": "lint.type_ignore",
    "# mypy: ignore-errors": "lint.type_ignore",
    "z = g()  # pyright: ignore[reportGeneralTypeIssues]": "lint.pyright_ignore",
    "# pyright: basic": "lint.pyright_ignore",
    "if debug:  # pragma: no cover": "coverage.pragma_no_cover",
    "else:  # PRAGMA NO COVER": "coverage.pragma_no_cover",
    '@unittest.skip("slow")': "skip.unittest",
    '@skipIf(True, "x")': "skip.unittest",
    '@skipUnless(False, "x")': "skip.unittest",
    "@unittest.expectedFailure": "skip.unittest",
    'self.skipTest("later")': "skip.unittest",
    'raise unittest.SkipTest("x")': "skip.unittest",
    '@pytest.mark.skip(reason="x")': "skip.pytest",
    '@pytest.mark.skipif(True, reason="x")': "skip.pytest",
    "@mark.xfail": "skip.pytest",
    'pytest.skip("x")': "skip.pytest",
    'pytest.importorskip("numpy")': "skip.pytest",
    'message = "x = 1  # noqa"': None,
    "pattern = f'# type: ignore {x}'": None,
    "@skipper": None,
    "def skip_this(): pass": None,
    "@pytest.mark.skipping": None,
    "stream.skip(2)": None,
    'self.skipTest ("later")': "skip.unittest",
    'pytest . skip ("later")': "skip.pytest",
    "# Document pytest.skip() here.": None,
    "# @unittest.skip('old')": None,
}
SCRIPT_SAMPLE = {
    "// eslint-disable-next-line no-console": "lint.eslint_disable",
    "/* eslint-disable */": "lint.eslint_disable",
    "// @ts-ignore": "lint.ts_ignore",
    "/** @ts-ignore */": "lint.ts_ignore",
    "// @ts-expect-error": "lint.ts_expect_error",
    "// @ts-nocheck": "lint.ts_nocheck",
    "// biome-ignore lint: reason": "lint.biome_ignore",
    "/* istanbul ignore next */": "coverage.istanbul_ignore",
    "/* c8 ignore next */": "coverage.c8_ignore",
    "/* v8 ignore next */": "coverage.v8_ignore",
    'it.skip("x", () => {})': "skip.js",
    'describe.only("x", () => {})': "skip.js",
    'it.concurrent.skip("x", () => {})': "skip.js",
    'test.todo("x")': "skip.js",
    'test.skipIf(ci)("x", () => {})': "skip.js",
    'xit("x", () => {})': "skip.js",
    'fdescribe("x", () => {})': "skip.js",
    "this.skip()": "skip.js",
    'const s = "eslint-disable @ts-ignore"': None,
    "stream.skip(2)": None,
    "profit(2)": None,
    "model.fit(xs, ys)": None,
    'test . skip("later", fn)': "skip.js",
    'fit("x", () => {})': "skip.js",
    "x = 1  # noqa": None,
}


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def call(function, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = function(*args)
    return result, out.getvalue(), err.getvalue()


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "--no-gpg-sign", "--allow-empty", "-qm", "c")


class ScanTests(RepositoryTest):
    def test_each_kind_matches_only_its_syntax_and_language(self):
        expected = {key: [] for key, _, _ in ratchet.PATTERNS}
        for name, sample in (("pkg/a.py", PYTHON_SAMPLE), ("web/b.ts", SCRIPT_SAMPLE)):
            self.write(name, "\n".join(sample) + "\n")
            for number, key in enumerate(sample.values(), 1):
                if key:
                    expected[key].append(f"{name}:{number}")
        self.write("C.java", '@Tag("slow")\n@Tag ( value = "no-mutate" )\n')
        expected["skip.no_mutate_tag"].append("C.java:2")
        self.write("notes.md", "x  # noqa\n")
        self.write("ignored.py", "x  # noqa\n")
        self.write(".gitignore", "ignored.py\n")
        self.commit()
        (self.root / "web/b.ts").rename(self.root / "web/c.ts")
        expected = {key: [place.replace("web/b.ts", "web/c.ts") for place in places]
                    for key, places in expected.items()}
        self.assertEqual(ratchet.scan(self.root), expected)

    def test_python_strings_are_blanked_but_their_lines_still_count(self):
        self.write("a.py", 'doc = """\n# noqa\n@unittest.skip\n"""; x = 1  # noqa\ny = 2  # type: ignore\n')
        found = ratchet.scan(self.root)
        self.assertEqual((found["lint.noqa"], found["skip.unittest"], found["lint.type_ignore"]),
                         (["a.py:4"], [], ["a.py:5"]))
        self.assertEqual(ratchet.views(self.root / "a.py"),
                         (["doc =    \n", "      \n", "              \n",
                           "   ; x = 1  # noqa\n", "y = 2  # type: ignore\n"],
                          ["doc =    \n", "      \n", "              \n",
                           "   ; x = 1" + " " * 8 + "\n", "y = 2" + " " * 16 + "\n"]))

    def test_a_conflicted_path_is_scanned_once(self):
        self.write("a.py", "x = 1\n")
        self.commit()
        git(self.root, "checkout", "-qb", "other")
        self.write("a.py", "x = 2  # noqa\n")
        self.commit()
        git(self.root, "checkout", "-q", "main")
        self.write("a.py", "x = 3  # noqa\n")
        self.commit()
        merge = subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                "merge", "-q", "other"], capture_output=True, text=True)
        self.assertIn("CONFLICT", merge.stdout + merge.stderr)
        self.assertIn("a.py\0a.py", subprocess.run(["git", "-C", str(self.root), "ls-files", "-z", "-co"],
                                                    capture_output=True, text=True).stdout)
        self.assertEqual(len(ratchet.scan(self.root)["lint.noqa"]), 2)

    def test_each_occurrence_counts_and_bad_files_do_not_hide_matches(self):
        self.write("a.test.ts", 'it.skip("a"); it.skip("b")\n')
        (self.root / "latin.py").write_bytes(b"# caf\xe9\nx = 1  # noqa\n")
        self.write("broken.py", 'x = (\n"# noqa"  # noqa\n')
        found = ratchet.scan(self.root)
        self.assertEqual(found["skip.js"], ["a.test.ts:1", "a.test.ts:1"])
        self.assertEqual(found["lint.noqa"], ["broken.py:2", "broken.py:2", "latin.py:2"])

    def test_symlinks_and_unreadable_names_are_skipped(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("x = 1  # noqa\n")
        (self.root / "link.py").symlink_to(outside)
        self.write("pkg/a.py", "")
        self.commit()
        (self.root / "pkg/a.py").unlink()
        (self.root / "pkg").rmdir()
        (self.root.parent / "elsewhere").mkdir()
        (self.root.parent / "elsewhere/a.py").write_text("x = 1  # noqa\n")
        (self.root / "pkg").symlink_to(self.root.parent / "elsewhere")
        blob = git(self.root, "hash-object", "-w", str(outside))
        subprocess.run([b"git", b"-C", bytes(self.root), b"update-index", b"--add", b"--cacheinfo",
                        b"100644," + blob.encode() + b",caf\xe9.py"], check=True)
        self.assertEqual(ratchet.scan(self.root)["lint.noqa"], [])

    def test_outside_git_skips_and_listing_errors_are_incomplete(self):
        with tempfile.TemporaryDirectory() as outside:
            self.assertEqual(call(ratchet.gate, Path(outside))[:2],
                             (([], []), "Ratchet: skipped outside a Git repository.\n"))
        (self.root / ".git/index").write_bytes(b"corrupt")
        (failures, incomplete), output, _ = call(ratchet.gate, self.root)
        self.assertEqual((failures, incomplete), ([], []))
        self.assertRegex(output, "^Ratchet: report only without .ub/ratchet; counting failed: git ls-files failed: ")
        self.write(".ub/ratchet", "lint.noqa 0\n")
        (failures, incomplete), output, _ = call(ratchet.gate, self.root)
        self.assertEqual((failures, output), ([], "Ratchet: incomplete.\n"))
        self.assertRegex(incomplete[0], "^Ratchet: git ls-files failed: .*index")


class GateTests(RepositoryTest):
    def test_without_a_ratchet_file_reports_only(self):
        self.write("t.py", "x  # noqa\ny  # noqa\n")
        self.assertEqual(call(ratchet.gate, self.root)[:2],
                         (([], []), "Ratchet: 2 escape hatches, report only without .ub/ratchet.\n"))

    def test_growth_fails_with_every_current_location_from_any_directory(self):
        self.write("t.py", "x  # noqa\n")
        call(ratchet.update, self.root)
        self.write("t.py", "x  # noqa\n@unittest.skip('x')\n" + "z  # noqa\n" * 20)
        self.write("sub/u.py", "y  # noqa\n")
        (failures, incomplete), output, _ = call(ratchet.gate, self.root / "sub")
        self.assertEqual((failures, incomplete), (["lint.noqa: 22 > 1", "skip.unittest: 1 > 0"], []))
        noqa = "".join(f"    {place}\n" for place in ["sub/u.py:1", "t.py:1"]
                       + [f"t.py:{line}" for line in range(3, 23)])
        self.assertEqual(output, "Ratchet: 2 kinds above .ub/ratchet, with every current match\n"
                         f"  lint.noqa: 22 > 1\n{noqa}"
                         "  skip.unittest: 1 > 0\n    t.py:2\n")

    def test_equal_and_lower_counts_pass(self):
        self.write("t.py", "x  # noqa\n")
        self.write(".ub/ratchet", "lint.noqa 1\n")
        self.assertEqual(call(ratchet.gate, self.root)[:2],
                         (([], []), "Ratchet: 1 escape hatches, none above .ub/ratchet.\n"))
        self.write(".ub/ratchet", "lint.noqa 1\nretired.kind 1\n")
        self.assertEqual(call(ratchet.gate, self.root)[:2],
                         (([], []), "Ratchet: 1 escape hatches, none above .ub/ratchet; "
                                    "a human can lower it with ub ratchet --update.\n"))

    def test_malformed_or_symlinked_ratchet_is_incomplete(self):
        self.write(".ub/ratchet", "lint.noqa many\n")
        (_, incomplete), _, _ = call(ratchet.gate, self.root)
        self.assertEqual(incomplete, ["Ratchet: .ub/ratchet line 1 is not 'key count': lint.noqa many"])
        (self.root / ".ub/ratchet").unlink()
        (self.root / ".ub/ratchet").symlink_to("missing")
        (_, incomplete), _, _ = call(ratchet.gate, self.root)
        self.assertEqual(incomplete, ["Ratchet: .ub/ratchet must be a regular file, not a symlink."])
        with self.assertRaisesRegex(ValueError, "not a symlink"):
            ratchet.update(self.root)

    def test_update_refuses_a_ub_directory_outside_the_repository(self):
        outside = self.root.parent / "elsewhere"
        outside.mkdir()
        (self.root / ".ub").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside the project"):
            ratchet.update(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_update_writes_every_kind_and_round_trips(self):
        self.write("t.py", "x  # noqa\n")
        result, output, _ = call(ratchet.update, self.root)
        text = (self.root / ".ub/ratchet").read_text()
        self.assertTrue(text.startswith("# "))
        counts = ratchet.parse(text)
        self.assertEqual(counts, {key: int(key == "lint.noqa") for key, _, _ in ratchet.PATTERNS})
        self.assertEqual(list(counts), sorted(counts))
        self.assertEqual(output, f"Wrote {self.root / '.ub/ratchet'}: 1 escape hatches.\n")

    def test_parse_skips_comments_and_rejects_malformed_lines(self):
        self.assertEqual(ratchet.parse("# note\n\n  lint.noqa  2\n"), {"lint.noqa": 2})
        for line in ("lint.noqa", "lint.noqa -1", "lint.noqa 1 2", "lint.noqa x"):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "line 2 is not 'key count'"):
                ratchet.parse("# note\n" + line + "\n")


class GuardTests(RepositoryTest):
    def setUp(self):
        super().setUp()
        self.write(".ub/check", "#!/bin/sh\nexec ub check\n")
        (self.root / ".ub/check").chmod(0o755)
        self.write(".ub/ratchet", "lint.noqa 1\nskip.js 2\n")
        self.commit()
        git(self.root, "checkout", "-qb", "work")

    def guard(self, override=None, head="HEAD"):
        environ = {key: value for key, value in os.environ.items() if key != ratchet.OVERRIDE}
        if override:
            environ[ratchet.OVERRIDE] = override
        with patch.dict(os.environ, environ, clear=True):
            return call(ratchet.guard, self.root, "main", head)

    def symlink_same_ratchet(self):
        copy = self.root.parent / "ratchet-copy"
        copy.write_bytes((self.root / ".ub/ratchet").read_bytes())
        (self.root / ".ub/ratchet").unlink()
        (self.root / ".ub/ratchet").symlink_to(copy)

    def test_unchanged_and_lowered_config_passes(self):
        self.commit()
        self.assertEqual(self.guard()[:2], (0, "Guard: HEAD does not loosen .ub/ since main.\n"))
        self.write(".ub/ratchet", "lint.noqa 0\n")
        self.commit()
        self.assertEqual(self.guard()[0], 0)

    def test_loosening_commits_and_worktree_edits_fail(self):
        cases = {
            "raise": (lambda: self.write(".ub/ratchet", "lint.noqa 2\nskip.js 2\nskip.pytest 1\n"), True,
                      [".ub/ratchet raised lint.noqa: 1 -> 2", ".ub/ratchet raised skip.pytest: 0 -> 1"]),
            "delete": (lambda: (self.root / ".ub/ratchet").unlink(), True, [".ub/ratchet was deleted"]),
            "edit": (lambda: self.write(".ub/check", "#!/bin/sh\nexit 0\n"), True, [".ub/check changed"]),
            "mode": (lambda: (self.root / ".ub/check").chmod(0o644), True, [".ub/check changed"]),
            "dirty check": (lambda: self.write(".ub/check", "#!/bin/sh\nexit 0\n"), False,
                            [".ub/check has uncommitted changes"]),
            "dirty ratchet": (lambda: (self.root / ".ub/ratchet").unlink(), False,
                              [".ub/ratchet has uncommitted changes"]),
            "symlinked ratchet": (self.symlink_same_ratchet, False, [".ub/ratchet has uncommitted changes"]),
        }
        for name, (change, commit, problems) in cases.items():
            with self.subTest(name):
                git(self.root, "reset", "-q", "--hard", "main")
                change()
                if commit:
                    self.commit()
                code, output, _ = self.guard()
                self.assertEqual(code, 1)
                lines = output.splitlines()
                self.assertEqual(lines[0], "Guard: HEAD against main:")
                self.assertEqual(lines[1:-1], [f"  {problem}" for problem in problems])
                self.assertIn(ratchet.OVERRIDE, lines[-1])
                code, output, _ = self.guard("1")
                self.assertEqual(code, 0)
                self.assertTrue(output.endswith(f"Guard: allowed by {ratchet.OVERRIDE}=1.\n"))
                self.assertEqual(self.guard("yes")[0], 1)

    def test_head_selects_the_pushed_commit(self):
        self.write(".ub/check", "#!/bin/sh\nexit 0\n")
        self.commit()
        tampered = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "main")
        self.assertEqual(self.guard()[0], 0)
        self.assertEqual(self.guard(head=tampered)[0], 1)

    def test_adopting_ub_passes_unless_the_ratchet_is_malformed(self):
        git(self.root, "rm", "-rq", ".ub")
        self.commit()
        git(self.root, "branch", "-qf", "main")
        self.write(".ub/check", "#!/bin/sh\n")
        self.write(".ub/ratchet", "lint.noqa 9\n")
        self.commit()
        self.assertEqual(self.guard()[0], 0)
        self.write(".ub/ratchet", "lint.noqa many\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "not 'key count'"):
            self.guard()

    def test_missing_base_is_incomplete(self):
        code, output, error = call(ratchet.guard, self.root, "origin/missing")
        self.assertEqual((code, output), (2, ""))
        self.assertIn("no merge base of HEAD and origin/missing", error)
        self.assertIn("fetch-depth: 0", error)

    def test_symlinked_or_malformed_committed_ratchet_is_incomplete(self):
        def symlink():
            (self.root / ".ub/ratchet").unlink()
            (self.root / ".ub/ratchet").symlink_to("../t.py")

        for change in (lambda: self.write(".ub/ratchet", "lint.noqa many\n"), symlink):
            with self.subTest(change=change):
                git(self.root, "reset", "-q", "--hard", "main")
                change()
                self.commit()
                code, _, error = call(ratchet.run, ["--root", str(self.root), "--guard", "--base", "main"])
                self.assertEqual(code, 2, error)


class CommandTests(RepositoryTest):
    def ub(self, *args, cwd=None):
        return call(ratchet.run, ["--root", str(cwd or self.root), *args])

    def test_check_update_and_usage(self):
        (self.root / "sub").mkdir()
        self.assertEqual(self.ub("--update", cwd=self.root / "sub")[0], 0)
        self.assertTrue((self.root / ".ub/ratchet").is_file())
        self.assertEqual(self.ub()[0], 0)
        for args in (["--guard"], ["--base", "main"], ["--head", "HEAD"],
                     ["--update", "--guard", "--base", "main"]):
            with self.subTest(args=args):
                self.assertEqual(self.ub(*args)[0], 2)
        self.write("t.py", "x  # noqa\n")
        self.assertEqual(self.ub(cwd=self.root / "sub")[0], 1)
        self.write(".ub/ratchet", "bad\n")
        code, _, error = self.ub()
        self.assertEqual(code, 2)
        self.assertIn("line 1 is not 'key count'", error)

    def test_guard_from_a_subdirectory_reads_the_worktree_root(self):
        self.write(".ub/check", "#!/bin/sh\n")
        self.write("sub/x.txt", "")
        call(ratchet.update, self.root)
        GuardTests.commit(self)
        self.assertEqual(self.ub("--guard", "--base", "HEAD", cwd=self.root / "sub")[:2],
                         (0, "Guard: HEAD does not loosen .ub/ since HEAD.\n"))

    def test_outside_git_checks_nothing_and_cannot_update(self):
        with tempfile.TemporaryDirectory() as outside:
            self.assertEqual(self.ub(cwd=Path(outside))[0], 0)
            code, _, error = self.ub("--update", cwd=Path(outside))
        self.assertEqual(code, 2)
        self.assertIn("needs a Git repository", error)
