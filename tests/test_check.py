import contextlib
import io
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dryer.model import Entry

from ub_cli import check


class CheckAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = self.root / "src" / "example.py"
        source.parent.mkdir()
        self.original = "def add(a, b):\n    return a + b\n"
        source.write_text(self.original, encoding="utf-8")

    def coverage(self, hits=1):
        report = self.root / "target" / "coverage" / "python" / "lcov.info"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "SF:src/example.py\nDA:1,1\n"
            + "".join(
                f"DA:{line},{hits}\n"
                for line in range(2, len(self.original.splitlines()) + 1)
            )
            + "end_of_record\n",
            encoding="utf-8",
        )

    def command(self, program):
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

    def run_check(self, test_program, coverage_arguments=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = check.run(
                [
                    "--root",
                    str(self.root),
                    *(coverage_arguments or ["--use-existing-coverage"]),
                    "--test-command",
                    self.command(test_program),
                    "--max-workers",
                    "1",
                ]
            )
        self.assertEqual(
            self.original,
            (self.root / "src" / "example.py").read_text(encoding="utf-8"),
        )
        return status, stdout.getvalue() + stderr.getvalue()

    def test_all_gates_pass_and_preserve_viewer_metrics(self):
        self.coverage()
        status, output = self.run_check(
            "import runpy; add = runpy.run_path('src/example.py')['add']; "
            "assert add(2, 3) == 5"
        )
        self.assertEqual(0, status, output)
        self.assertTrue((self.root / ".metrics" / "crap.edn").is_file())
        self.assertTrue((self.root / ".metrics" / "dry.edn").is_file())
        snapshots = list((self.root / ".metrics" / "mutate").rglob("*.edn"))
        self.assertTrue(snapshots, output)
        self.assertIn(":killed 1", snapshots[0].read_text(encoding="utf-8"))

    def test_surviving_mutant_fails_the_gate(self):
        self.coverage()
        status, output = self.run_check("pass")
        self.assertEqual(1, status, output)
        self.assertIn("surviv", output.lower())

    def test_uncovered_site_fails_even_without_survivors(self):
        self.coverage(hits=0)
        status, output = self.run_check("pass")
        self.assertEqual(1, status, output)
        self.assertIn("uncovered", output.lower())

    def test_failed_baseline_is_incomplete(self):
        self.coverage()
        prior = self.root / ".metrics" / "mutate" / "example.edn"
        prior.parent.mkdir(parents=True)
        snapshot = '{:namespace "example" :forms []}\n'
        prior.write_text(snapshot, encoding="utf-8")
        status, output = self.run_check("raise SystemExit(1)")
        self.assertEqual(2, status, output)
        self.assertIn("baseline", output.lower())
        self.assertEqual(snapshot, prior.read_text(encoding="utf-8"))

    def test_missing_coverage_is_incomplete(self):
        status, output = self.run_check("pass")
        self.assertEqual(2, status, output)
        self.assertIn("coverage", output.lower())

    def test_failed_coverage_command_cannot_pass_using_an_old_report(self):
        self.coverage()
        status, output = self.run_check(
            "pass",
            ["--coverage-command", self.command("raise SystemExit(1)")],
        )
        self.assertEqual(2, status, output)
        self.assertIn("coverage", output.lower())

    def test_successful_coverage_command_must_generate_a_fresh_report(self):
        self.coverage()
        report = self.root / "target" / "coverage" / "python" / "lcov.info"
        previous = report.read_bytes()
        status, output = self.run_check("pass", ["--coverage-command", "true"])
        self.assertEqual(2, status, output)
        self.assertIn("coverage", output.lower())
        self.assertEqual(previous, report.read_bytes())

    def test_fully_covered_crap_ten_fails_the_combined_command(self):
        self.original = "def choose(a):\n" + "".join(
            f"    if a == {value}:\n        return {value}\n"
            for value in range(9)
        ) + "    return 9\n"
        (self.root / "src" / "example.py").write_text(self.original, encoding="utf-8")
        self.coverage()
        status, output = self.run_check(
            "import runpy; choose = runpy.run_path('src/example.py')['choose']; "
            "assert [choose(value) for value in range(10)] == list(range(10))"
        )
        self.assertEqual(1, status, output)
        self.assertIn("CRAP 10 >= 10", output)
        metrics = (self.root / ".metrics" / "crap.edn").read_text(encoding="utf-8")
        self.assertIn(":complexity 10", metrics)
        self.assertIn(":coverage 100.0", metrics)
        self.assertIn(":crap 10.0", metrics)
        self.assertNotIn("surviving and", output)

    def test_combined_command_uses_point_eight_duplication_threshold(self):
        self.coverage()
        for shared, total, expected in ((3, 4, 0), (4, 5, 1), (5, 5, 1)):
            with self.subTest(similarity=shared / total):
                candidates = [
                    Entry("python", "left.py", 1, 5, 25, frozenset(map(str, range(shared)))),
                    Entry("python", "right.py", 1, 5, 25, frozenset(map(str, range(total)))),
                ]
                with patch.object(check, "scan_files", return_value=(candidates, [])):
                    status, output = self.run_check(
                        "import runpy; add = runpy.run_path('src/example.py')['add']; "
                        "assert add(2, 3) == 5"
                    )
                self.assertEqual(expected, status, output)
                if expected:
                    self.assertIn("Duplication", output)
                    self.assertIn("left.py:1", output)
                    self.assertIn("right.py:1", output)

    def test_test_only_changes_rerun_killed_mutants_and_validate_the_baseline(self):
        self.coverage()
        test = self.root / "tests" / "test_example.py"
        test.parent.mkdir()
        setup = "import runpy\nadd = runpy.run_path('src/example.py')['add']\n"
        program = "import runpy; runpy.run_path('tests/test_example.py')"

        test.write_text(setup + "assert add(2, 3) == 5\n", encoding="utf-8")
        status, output = self.run_check(program)
        self.assertEqual(0, status, output)

        test.write_text(setup + "assert add(2, 3) is not None\n", encoding="utf-8")
        status, output = self.run_check(program)
        self.assertEqual(1, status, output)
        self.assertIn("surviv", output.lower())

        test.write_text("raise SystemExit(1)\n", encoding="utf-8")
        status, output = self.run_check(program)
        self.assertEqual(2, status, output)
        self.assertIn("baseline", output.lower())

    def test_go_module_coverage_path_matches_the_project_source(self):
        (self.root / "src" / "example.py").unlink()
        source = "package calc\n\nfunc Add(a, b int) int {\n    return a + b\n}\n"
        (self.root / "add.go").write_text(source, encoding="utf-8")
        (self.root / "go.mod").write_text(
            "module example.org/ub-fixture\n\ngo 1.22\n", encoding="utf-8"
        )
        report = self.root / "target" / "coverage" / "go" / "coverage.out"
        report.parent.mkdir(parents=True)
        report.write_text(
            "mode: set\nexample.org/ub-fixture/add.go:4.5,5.1 1 1\n",
            encoding="utf-8",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = check.run(
                [
                    "--root",
                    str(self.root),
                    "--use-existing-coverage",
                    "--max-workers",
                    "1",
                    "--test-command",
                    self.command(
                        "from pathlib import Path; "
                        "assert 'return a + b' in Path('add.go').read_text()"
                    ),
                ]
            )
        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(0, status, output)
        self.assertIn("KILLED", output)
        self.assertEqual(source, (self.root / "add.go").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
