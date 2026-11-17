import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_cli import invoke
from ub_cli.cli import run

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
        self.assertIn("no executed lines", completed.stderr)

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
        self.assertNotIn("Collecting", completed.stderr)

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

    def test_mutate_scan_prints_one_line_per_file(self):
        status, output, _ = invoke(run, ["mutate", "--root", str(self.root), "--scan", "--no-coverage"])
        self.assertEqual(0, status)
        self.assertRegex(output, r"^Scan: \d+ mutation sites in example.py\n$")

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


if __name__ == "__main__":
    unittest.main()
