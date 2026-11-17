import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_cli import invoke
from types import SimpleNamespace
from unittest.mock import patch

from ub_cli import check, engines
from ub_cli.cli import run
from ub_cli.vendor.crapper import runners
from ub_cli.vendor.mutator import engine as mutator

SOURCE = "def add(a, b):\n    return a + b\n"
KILLING_TEST = (
    "import runpy; add = runpy.run_path('example.py')['add']; assert add(2, 3) == 5"
)


def ub(*arguments):
    """Run ub in a child process so subprocess output reaches the real descriptors."""

    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from ub_cli.cli import run; sys.exit(run(sys.argv[1:]))",
            *arguments,
        ],
        capture_output=True,
        text=True,
    )


class StandaloneEngineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "example.py").write_text(SOURCE, encoding="utf-8")

    def coverage_command(self, hits):
        report = "coverage/lcov.info"
        program = (
            "import pathlib, sys; print('Collecting coverage'); "
            f"pathlib.Path('{report}').parent.mkdir(exist_ok=True); "
            f"pathlib.Path('{report}').write_text("
            f"'SF:example.py\\nDA:1,{hits}\\nDA:2,{hits}\\nend_of_record\\n')"
        )
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"

    def test_crap_without_executed_lines_stops_with_one_cause(self):
        completed = ub("crap", "--root", str(self.root), "--coverage-command", self.coverage_command(0))
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout)
        self.assertEqual(1, completed.stderr.count("\n"), completed.stderr)
        self.assertIn(f"Did `{self.coverage_command(0)}` run the tests?", completed.stderr)

    def test_crap_without_executed_lines_hints_at_verbose(self):
        command = self.coverage_command(0)
        status, output, error = invoke(run, ["crap", "--root", str(self.root), "--coverage-command", command])
        self.assertEqual((2, ""), (status, output))
        self.assertTrue(error.endswith(" run the tests? Rerun with --verbose to see its output.\n"), error)

    def test_crap_reports_real_zero_coverage_of_a_named_file(self):
        command = self.coverage_command(0)
        completed = ub("crap", "--root", str(self.root), "--coverage-command", command, "example.py")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("CRAP: 0 of 1 functions >= 10\n", completed.stdout)

        completed = ub(
            "crap", "--root", str(self.root), "--coverage-command", command, "--threshold", "1", "example.py"
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual(
            "CRAP: 1 of 1 functions > 1\n  2.0 CRAP, 1 CC, 0% covered: example/add\n", completed.stdout
        )

    def test_crap_shows_a_failed_coverage_command_on_stderr(self):
        failing = self.coverage_command(1) + " && exit 3"
        completed = ub("crap", "--root", str(self.root), "--coverage-command", failing)
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stdout)
        self.assertIn("Collecting coverage", completed.stderr)
        self.assertIn("Coverage command exited 3", completed.stderr)

    def test_crap_keeps_coverage_output_off_stdout(self):
        completed = ub("crap", "--root", str(self.root), "--coverage-command", self.coverage_command(1))
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("CRAP: 0 of 1 functions >= 10\n", completed.stdout)
        self.assertEqual("Wrote .metrics/crap.edn\n", completed.stderr)

        completed = ub(
            "crap", "--root", str(self.root), "--coverage-command", self.coverage_command(1), "--verbose"
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Collecting coverage", completed.stdout)
        self.assertIn("CRAP Report", completed.stdout)

    def test_crap_lists_only_failing_functions_up_to_a_limit(self):
        branches = "".join(f"    if a == {n}:\n        return {n}\n" for n in range(11))
        for index in range(12):
            (self.root / f"complex{index}.py").write_text(
                f"def tangled(a):\n{branches}    return a\n", encoding="utf-8"
            )
        status, output, _ = invoke(run, ["crap", "--root", str(self.root), "--no-coverage"])
        lines = output.splitlines()
        self.assertEqual(0, status)
        self.assertEqual("Complexity: 12 of 13 functions >= 10, coverage not measured", lines[0])
        self.assertEqual(12, len(lines))
        self.assertEqual("  ... and 2 more; --verbose for all", lines[-1])
        self.assertNotIn("add", output)

        status, output, _ = invoke(run, ["crap", "--root", str(self.root), "--no-coverage", "--verbose"])
        self.assertEqual(0, status)
        self.assertEqual(13, sum(line.startswith(("tangled", "add")) for line in output.splitlines()))

    def test_dry_prints_one_line_per_pair(self):
        body = "def {name}(items):\n" + "".join(
            f"    items.append(len(items) + {n})\n" for n in range(8)
        )
        for name in ("first", "second"):
            (self.root / f"{name}.py").write_text(body.format(name=name), encoding="utf-8")
        status, output, error = invoke(run, ["dry", "--root", str(self.root)])
        self.assertEqual(0, status, error)
        self.assertEqual("Duplication: 1 candidate pairs\n  1.00 first.py:1-9 second.py:1-9\n", output)
        self.assertEqual("Wrote .metrics/dry.edn\n", error)

    def test_mutate_hides_killed_mutants_unless_verbose(self):
        arguments = [
            "mutate",
            "--root",
            str(self.root),
            "--no-coverage",
            "--max-workers",
            "1",
            "--test-command",
            f"{shlex.quote(sys.executable)} -c {shlex.quote(KILLING_TEST)}",
        ]
        status, output, error = invoke(run, [*arguments, "--mutate-all"])
        self.assertEqual(0, status, output + error)
        self.assertNotIn("KILLED", output)
        self.assertNotIn("Wrote", error)
        self.assertRegex(output, r"^Mutation: [1-9]\d* killed, 0 survived, 0 uncovered in 1 functions, score 100.0%\.")
        self.assertEqual(1, output.count("\n"), output)

        status, output, error = invoke(run, [*arguments, "--mutate-all", "--verbose"])
        self.assertEqual(0, status, output + error)
        self.assertIn("KILLED", output)
        self.assertIn("Wrote", error)

    def test_mutate_lists_each_unkilled_mutant(self):
        status, output, _ = invoke(
            run,
            ["mutate", "--root", str(self.root), "--no-coverage", "--mutate-all", "--test-command", "true"],
        )
        self.assertEqual(3, status, output)
        self.assertIn("example.py:2 survived + -> -\n", output)
        self.assertNotIn("SURVIVED", output)

    def test_mutate_scan_prints_one_line_per_file(self):
        status, output, _ = invoke(run, ["mutate", "--root", str(self.root), "--scan", "--no-coverage"])
        self.assertEqual(0, status)
        self.assertRegex(output, r"^Scan: \d+ mutation sites in example.py\n$")

    def test_mutate_scan_without_a_report_says_coverage_is_unknown(self):
        status, output, _ = invoke(run, ["mutate", "--root", str(self.root), "--scan"])
        self.assertEqual(0, status)
        self.assertRegex(output, r"^Scan: \d+ mutation sites in example.py \(coverage unknown\)\n$")

        report = self.root / "coverage" / "lcov.info"
        report.parent.mkdir()
        report.write_text("SF:example.py\nDA:1,1\nDA:2,0\nend_of_record\n", encoding="utf-8")
        status, output, _ = invoke(run, ["mutate", "--root", str(self.root), "--scan"])
        self.assertEqual(0, status)
        self.assertRegex(output, r"^Scan: (\d+) mutation sites in example.py \(\1 uncovered\)\n$")

    def test_argument_matching_nothing_fails_and_names_it(self):
        for command in ("crap", "dry", "mutate"):
            with self.subTest(command=command):
                status, output, error = invoke(
                    run, [command, "--root", str(self.root), "--no-coverage", "example", "missing_xyz"]
                    if command != "dry"
                    else [command, "--root", str(self.root), "example", "missing_xyz"],
                )
                self.assertEqual((2, "", "No source files match missing_xyz\n"), (status, output, error))

    def test_bare_run_on_an_empty_project_is_unchanged(self):
        (self.root / "example.py").unlink()
        status, output, _ = invoke(run, ["crap", "--root", str(self.root), "--no-coverage"])
        self.assertEqual((0, "No source files to analyze.\n"), (status, output))


def entry(crap, complexity, coverage, name):
    return SimpleNamespace(crap=crap, complexity=complexity, coverage=coverage, namespace="pkg", name=name)


class CrapReportTests(unittest.TestCase):
    def test_lists_functions_at_ten_or_above_the_threshold(self):
        entries = [entry(10.0, 10, 100.0, "hot"), entry(9.9, 9, 100.0, "cool"), entry(None, 1, None, "gone")]
        self.assertEqual(
            "CRAP: 1 of 3 functions >= 10\n  10.0 CRAP, 10 CC, 100% covered: pkg/hot\n",
            engines.crap_report(entries),
        )
        self.assertEqual(
            "CRAP: 0 of 3 functions > 10\n", engines.crap_report(entries, threshold=10.0)
        )
        self.assertEqual(
            "CRAP: 2 of 3 functions > 9.5\n  10.0 CRAP, 10 CC, 100% covered: pkg/hot\n"
            "  9.9 CRAP, 9 CC, 100% covered: pkg/cool\n",
            engines.crap_report(entries, threshold=9.5),
        )

    def test_caps_the_list_after_ten(self):
        for count, tail in ((10, []), (11, ["  ... and 1 more; --verbose for all"])):
            lines = engines.crap_report([entry(10.0, 10, 100.0, "hot")] * count).splitlines()
            self.assertEqual(1 + 10 + len(tail), len(lines))
            self.assertEqual(tail, lines[11:])

    def test_mutation_summary_scores_killed_over_executed(self):
        forms = [SimpleNamespace(killed=3, survived=1, uncovered=2)]
        self.assertEqual(
            "Mutation: 3 killed, 1 survived, 2 uncovered in 1 functions, score 75.0%. "
            "--verbose lists every mutant and function.\n",
            engines.mutation_summary(forms),
        )

    def test_lists_complexity_when_coverage_is_not_measured(self):
        entries = [entry(None, 10, None, "tangled"), entry(None, 9, None, "plain")]
        self.assertEqual(
            "Complexity: 1 of 2 functions >= 10, coverage not measured\n  10 CC: pkg/tangled\n",
            engines.crap_report(entries),
        )


class PythonTestsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "example.py").write_text(SOURCE, encoding="utf-8")

    def rewrite(self, command):
        return engines._tests_dir(command, self.root)

    def test_default_discovery_moves_to_a_tests_dir_that_dot_cannot_find(self):
        coverage = ["py", "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "."]
        plain = ["py", "-m", "unittest", "discover"]
        self.assertEqual(coverage, self.rewrite(coverage))
        for name in ("test", "tests"):
            (self.root / name).mkdir()
            self.assertEqual(coverage[:-1] + [name], self.rewrite(coverage))
            self.assertEqual(plain + ["-s", name], self.rewrite(plain))

    def test_default_discovery_stays_when_dot_finds_tests(self):
        command = ["py", "-m", "unittest", "discover"]
        (self.root / "tests").mkdir()
        (self.root / "test").mkdir()
        (self.root / "tests" / "__init__.py").write_text("")
        self.assertEqual(command, self.rewrite(command))
        (self.root / "tests" / "__init__.py").unlink()
        (self.root / "test_top.py").write_text("")
        self.assertEqual(command, self.rewrite(command))

    def test_other_commands_stay(self):
        (self.root / "tests").mkdir()
        for command in ("python -m unittest discover", ["py", "-m", "pytest"], ["py", "-m", "unittest", "discover", "-s", "src"]):
            self.assertEqual(command, self.rewrite(command))

    def test_swaps_rewrite_runs_and_record_coverage_commands(self):
        (self.root / "tests").mkdir()
        calls = []
        with patch.object(runners, "run_shell", lambda command, cwd: calls.append(command) or 0):
            ran = []
            swaps = engines.python_tests(ran)
        shell, plan = swaps[0][2], swaps[1][2]
        self.assertEqual([runners, mutator, check], [module for module, _, _ in swaps])
        self.assertEqual(0, shell(["py", "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "."], self.root))
        shell(["py", "-c", "import coverage"], self.root)
        shell("make coverage", self.root)
        self.assertEqual(["py -m coverage run -m unittest discover -s tests", "make coverage"], ran)
        self.assertEqual(["py", "-m", "coverage", "run", "-m", "unittest", "discover", "-s", "tests"], calls[0])
        command, directory = plan(self.root, self.root / "example.py", "python", None)
        self.assertEqual(["-s", "tests"], command[-2:])
        self.assertEqual(self.root.resolve(), directory)


if __name__ == "__main__":
    unittest.main()
