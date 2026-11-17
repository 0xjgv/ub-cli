import contextlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ub_cli.vendor.crapper.coverage import load_bundle
from ub_cli.vendor.dryer.model import Entry
from ub_cli.vendor.mutator.model import RunResult

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

    def arguments(self, test_program, coverage_arguments=None):
        return [
            "--root",
            str(self.root),
            *(coverage_arguments or ["--use-existing-coverage"]),
            "--test-command",
            self.command(test_program),
            "--max-workers",
            "1",
        ]

    def run_check(self, test_program, coverage_arguments=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = check.run(self.arguments(test_program, coverage_arguments))
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
        self.assertIn("\n  src/example.py:2 survived + -> -\n", output)

    def test_uncovered_site_fails_even_without_survivors(self):
        self.coverage(hits=0)
        status, output = self.run_check("pass")
        self.assertEqual(1, status, output)
        self.assertIn("\n  src/example.py:2 uncovered + -> -\n", output)

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
        self.assertIn("\n  10.0 CRAP, 10 CC, 100% covered: example/choose\n", output)
        metrics = (self.root / ".metrics" / "crap.edn").read_text(encoding="utf-8")
        self.assertIn(":complexity 10", metrics)
        self.assertIn(":coverage 100.0", metrics)
        self.assertIn(":crap 10.0", metrics)
        self.assertNotRegex(output, r"\.py:\d+ (survived|uncovered) ")

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

    def lcov(self, relative, hits, *, path="target/coverage/python/lcov.info"):
        report = self.root / path
        report.parent.mkdir(parents=True, exist_ok=True)
        lines = len((self.root / relative).read_text(encoding="utf-8").splitlines())
        report.write_text(
            f"SF:{relative}\n"
            + "".join(f"DA:{line},{hits}\n" for line in range(1, lines + 1))
            + "end_of_record\n",
            encoding="utf-8",
        )
        return report

    def copy_command(self, source, destination):
        return self.command(
            "import os, shutil, sys; print('runner', 'noise'); "
            "print('runner', 'warning', file=sys.stderr); "
            f"os.makedirs(os.path.dirname({destination!r}), exist_ok=True); "
            f"shutil.copyfile({str(source)!r}, {destination!r})"
        )

    def test_report_without_executed_lines_is_incomplete(self):
        self.lcov("src/example.py", 0)
        status, output = self.run_check("pass")
        self.assertEqual(2, status, output)
        self.assertIn("Did the coverage command run the tests?", output)
        self.assertNotIn("Mutation:", output)

    def test_report_without_executed_lines_names_the_coverage_command(self):
        fresh = self.lcov("src/example.py", 0, path="fresh.info")
        command = self.copy_command(fresh, "target/coverage/python/lcov.info")
        status, output = self.run_check("pass", ["--coverage-command", command])
        self.assertEqual(2, status, output)
        self.assertIn(f"Did `{command}` run the tests?", output)

    def test_known_gate_failures_print_before_incomplete_coverage(self):
        self.original = "def choose(a):\n" + "".join(
            f"    if a == {value}:\n        return {value}\n"
            for value in range(9)
        ) + "    return 9\n"
        (self.root / "src" / "example.py").write_text(self.original, encoding="utf-8")
        (self.root / "src" / "other.py").write_text(
            "def other(a):\n    if a:\n        return 1\n    if a is None:\n"
            "        return 2\n    return 3\n",
            encoding="utf-8",
        )
        self.coverage()
        status, output = self.run_check("pass")
        self.assertEqual(2, status, output)
        self.assertIn("10.0 CRAP, 10 CC, 100% covered: example/choose", output)
        self.assertIn("Missing coverage for src/other.py:1", output)
        self.assertNotIn("other/other", output)

    def test_unchanged_report_beside_a_fresh_one_is_ignored(self):
        stale = self.lcov("src/example.py", 0, path="coverage/lcov.info")
        before = stale.read_bytes()
        fresh = self.root.parent / f"{self.root.name}-fresh.info"
        self.addCleanup(fresh.unlink, missing_ok=True)
        fresh.write_text(
            "SF:src/example.py\nDA:1,1\nDA:2,1\nend_of_record\n", encoding="utf-8"
        )
        status, output = self.run_check(
            "import runpy; add = runpy.run_path('src/example.py')['add']; "
            "assert add(2, 3) == 5",
            [
                "--coverage-command",
                self.copy_command(fresh, "target/coverage/python/lcov.info"),
            ],
        )
        self.assertEqual(0, status, output)
        self.assertEqual(before, stale.read_bytes())

    def test_coverage_command_output_is_hidden_unless_it_fails(self):
        fresh = self.root.parent / f"{self.root.name}-fresh.info"
        self.addCleanup(fresh.unlink, missing_ok=True)
        fresh.write_text(
            "SF:src/example.py\nDA:1,1\nDA:2,1\nend_of_record\n", encoding="utf-8"
        )
        coverage = self.copy_command(fresh, "target/coverage/python/lcov.info")
        program = (
            "import runpy; add = runpy.run_path('src/example.py')['add']; "
            "assert add(2, 3) == 5"
        )
        for verbose in (False, True):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from ub_cli.check import run; sys.exit(run(sys.argv[1:]))",
                    *self.arguments(program, ["--coverage-command", coverage]),
                    *(["--verbose"] if verbose else []),
                ],
                capture_output=True,
                text=True,
            )
            output = completed.stdout + completed.stderr
            self.assertEqual(0, completed.returncode, output)
            self.assertEqual(verbose, "runner noise" in output, output)
            self.assertEqual(verbose, "runner warning" in output, output)
            self.assertIn("All ub-mode quality gates passed.", completed.stdout)

        status, output = self.run_check(
            program, ["--coverage-command", coverage + " && exit 3"]
        )
        self.assertEqual(2, status, output)
        self.assertIn("runner noise", output)
        self.assertIn("runner warning", output)

    def test_coverage_output_explains_missing_coverage(self):
        (self.root / "src" / "other.py").write_text("def other():\n    return 1\n")
        fresh = self.lcov("src/example.py", 1, path="fresh.info")
        status, output = self.run_check(
            "pass",
            ["--coverage-command", self.copy_command(fresh, "coverage/lcov.info")],
        )
        self.assertEqual(2, status, output)
        self.assertIn("Missing coverage for src/other.py:1", output)
        self.assertIn("runner noise", output)

    def test_gate_caps_failures_unless_verbose(self):
        failures = [f"failure {number}" for number in range(25)]
        for verbose, shown in ((False, 20), (True, 25)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                check._gate("Gate:", failures, verbose)
            lines = stdout.getvalue().splitlines()
            self.assertEqual(["Gate:"] + [f"  failure {n}" for n in range(shown)], lines[: shown + 1])
            self.assertEqual(
                [] if verbose else ["  ... and 5 more; --verbose for all"], lines[shown + 1 :]
            )

    def test_gate_prints_twenty_failures_without_a_tail(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            check._gate("Gate:", [f"failure {n}" for n in range(20)], False)
        self.assertEqual(21, len(stdout.getvalue().splitlines()))
        self.assertNotIn("more", stdout.getvalue())

    def test_crap_failures_print_one_decimal(self):
        entries = [
            SimpleNamespace(crap=35.2693, complexity=7, coverage=49.6, namespace="pkg", name="hot"),
            SimpleNamespace(crap=9.99, complexity=9, coverage=100.0, namespace="pkg", name="cool"),
            SimpleNamespace(crap=None, complexity=1, coverage=None, namespace="pkg", name="unknown"),
        ]
        self.assertEqual(["35.3 CRAP, 7 CC, 50% covered: pkg/hot"], check._crap_failures(entries))

    def test_mutation_failures_list_each_unkilled_mutant(self):
        sites = [
            SimpleNamespace(mutation_id=key, file="src/a.py", line=line, description="> -> >=")
            for key, line in (("k", 1), ("s", 2), ("u", 3), ("none", 4))
        ]
        result = SimpleNamespace(sites=sites, statuses={"k": "killed", "s": "survived", "u": "uncovered"})
        self.assertEqual(
            ["src/a.py:2 survived > -> >=", "src/a.py:3 uncovered > -> >=", "src/a.py:4 survived > -> >="],
            check._mutation_failures([result]),
        )

    def test_quietly_shows_output_when_the_call_raises(self):
        def fail():
            os.write(1, b"tool diagnostics\n")
            raise RuntimeError("tool crashed")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(RuntimeError):
            check._quietly(fail)
        self.assertEqual("tool diagnostics\n", stderr.getvalue())

    def test_set_aside_hides_reports_and_keeps_newer_ones(self):
        kept = self.lcov("src/example.py", 1, path="coverage/lcov.info")
        replaced = self.lcov("src/example.py", 1)
        before = kept.read_bytes()
        with check._set_aside([kept, replaced]):
            self.assertFalse(kept.exists() or replaced.exists())
            replaced.write_text("newer\n", encoding="utf-8")
        self.assertEqual(before, kept.read_bytes())
        self.assertEqual("newer\n", replaced.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.root.rglob("*.ub-stale")))

    def test_source_root_limits_the_checked_files(self):
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "tool.py").write_text(
            "def tool():\n    return 1\n", encoding="utf-8"
        )
        self.coverage()
        program = (
            "import runpy; add = runpy.run_path('src/example.py')['add']; "
            "assert add(2, 3) == 5"
        )
        status, output = self.run_check(program)
        self.assertEqual(2, status, output)
        status, output = self.run_check(program, ["--use-existing-coverage", "-s", "src"])
        self.assertEqual(0, status, output)
        for full_report in ("CRAP Report", "No duplicate candidates", "KILLED", "Total"):
            self.assertNotIn(full_report, output)

    def test_verbose_restores_the_full_reports(self):
        self.coverage()
        status, output = self.run_check(
            "import runpy; add = runpy.run_path('src/example.py')['add']; "
            "assert add(2, 3) == 5",
            ["--use-existing-coverage", "--verbose"],
        )
        self.assertEqual(0, status, output)
        self.assertIn("CRAP Report", output)
        self.assertIn("KILLED", output)
        self.assertIn("Function", output)

    def test_skipped_or_failed_mutation_run_is_incomplete(self):
        self.coverage()
        for result in (
            RunResult("src/example.py", [], [], skipped="file is not UTF-8"),
            RunResult(
                "src/example.py", [], [], baseline_failed=True, baseline_message="baseline broke"
            ),
        ):
            with self.subTest(result=result):
                with patch.object(check, "mutate_file", return_value=result):
                    status, output = self.run_check("pass")
                self.assertEqual(2, status, output)
                self.assertIn(result.skipped or result.baseline_message, output)

    def test_project_needs_a_supported_source_file(self):
        (self.root / "src" / "example.py").unlink()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            status = check.run(["--root", str(self.root), "--use-existing-coverage"])
        self.assertEqual(2, status)
        self.assertIn("No supported source files found.", stderr.getvalue())
        # crapper scores .js files that dryer skips, so either list is enough to continue.
        (self.root / "src" / "lib.js").write_text("function f() {\n  return 1;\n}\n")
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            status = check.run(self.arguments("pass"))
        self.assertEqual(2, status)
        self.assertIn("Missing coverage for src/lib.js:1", stderr.getvalue())

    def test_usage_errors_are_incomplete(self):
        for arguments, message in (
            (["--max-workers", "0"], "--max-workers requires a positive integer"),
            (["--root", str(self.root / "absent")], "Project root is not a directory"),
            (["--unknown"], "unrecognized arguments: --unknown"),
        ):
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    status = check.run(["--root", str(self.root), *arguments])
                self.assertEqual(2, status)
                self.assertIn(message, stderr.getvalue())

    def test_report_states_cover_nested_go_and_java_reports(self):
        expected = set()
        for prefix in ("", "a/", "a/b/"):
            for name in ("target/coverage/go/coverage.out", "target/site/jacoco/jacoco.xml"):
                expected.add(self.root / prefix / name)
        expected |= {self.root / "coverage.out", self.root / "target/coverage/coverage.out"}
        for report in expected:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("data\n", encoding="utf-8")
        self.assertEqual(expected, set(check._report_states(self.root, {"go", "java"})))

    def test_report_states_keep_only_cloverage_html_files(self):
        forms = self.root / "target" / "coverage" / "app" / "core.clj.html"
        forms.parent.mkdir(parents=True)
        forms.write_text("<p>3 forms covered</p>", encoding="utf-8")
        (self.root / "target" / "coverage" / "index.html").write_text("<p>index</p>")
        (self.root / "target" / "coverage" / "lib.html").mkdir()
        self.assertEqual({forms}, set(check._report_states(self.root, {"clojure"})))

    def test_go_mutation_coverage_keeps_only_hit_lines(self):
        (self.root / "go.mod").write_text("module example.org/m\n\ngo 1.22\n", encoding="utf-8")
        source = self.root / "add.go"
        source.write_text(
            "package calc\n\nfunc Add(a, b int) int {\n    return a + b\n}\n"
            "func Sub(a, b int) int {\n    return a - b\n}\n",
            encoding="utf-8",
        )
        report = self.root / "target" / "coverage" / "go" / "coverage.out"
        report.parent.mkdir(parents=True)
        report.write_text(
            "mode: set\nexample.org/m/add.go:3.24,5.2 1 1\nexample.org/m/add.go:6.24,8.2 1 0\n",
            encoding="utf-8",
        )
        bundle = load_bundle(self.root)
        bundle.bind_sources(["add.go"])
        self.assertEqual({3, 4, 5}, check._mutation_coverage(self.root, source, bundle))

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
        self.assertIn("1 killed", output)
        self.assertEqual(source, (self.root / "add.go").read_text(encoding="utf-8"))


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_module_names_follow_packages(self):
        self.write("src/pkg/__init__.py")
        self.write("src/pkg/sub/__init__.py")
        self.assertEqual("pkg.sub.m", check._module_name(self.write("src/pkg/sub/m.py")))
        self.assertEqual("pkg.sub", check._module_name(self.root / "src/pkg/sub/__init__.py"))
        self.assertEqual("test_x", check._module_name(self.write("tests/test_x.py")))

    def test_imports_resolve_relative_and_nested_imports(self):
        self.write("src/pkg/__init__.py", "from . import mod\n")
        self.write("src/pkg/sub/__init__.py", "from .. import other\n")
        self.write(
            "src/pkg/sub/m.py",
            "import json\nfrom . import y\nfrom .. import mod\n"
            "def f():\n    from pkg.lazy import thing\n",
        )
        self.write("src/pkg/user.py", "from .mod import add\n")
        self.write("target/mutation-workers/pkg/copy.py", "import pkg.mod\n")
        self.write(".worktrees/other/src/pkg/copy.py", "import pkg.mod\n")
        self.write("node_modules/pkg/copy.py", "import pkg.mod\n")
        graph = check.python_imports(self.root)
        imports = {name: imported for name, imported in graph.values()}
        self.assertEqual({"pkg", "pkg.mod"}, imports["pkg"])
        self.assertEqual({"pkg", "pkg.other"}, imports["pkg.sub"])
        self.assertEqual(
            {"json", "pkg.sub", "pkg.sub.y", "pkg", "pkg.mod", "pkg.lazy", "pkg.lazy.thing"},
            imports["pkg.sub.m"],
        )
        self.assertEqual({"pkg.mod", "pkg.mod.add"}, imports["pkg.user"])
        self.assertEqual({"pkg", "pkg.sub", "pkg.sub.m", "pkg.user"}, set(imports))

    def test_affected_tests_follow_imports_transitively(self):
        self.write("src/pkg/__init__.py")
        self.write("src/pkg/mod.py", "def add(a, b):\n    return a + b\n")
        self.write("src/pkg/user.py", "from . import mod\n")
        self.write("tests/test_direct.py", "import pkg.mod\n")
        self.write("tests/test_from.py", "from pkg.mod import add\n")
        self.write("tests/test_indirect.py", "import pkg.user\n")
        self.write("tests/test_lazy.py", "def f():\n    from pkg import mod\n")
        self.write("tests/test_package.py", "import pkg\n")
        self.write("tests/test_other.py", "import json\n")
        self.write("tests/helper.py", "import pkg.mod\n")
        graph = check.python_imports(self.root)
        self.assertEqual(
            [["test_direct", "test_from", "test_lazy"], ["test_indirect"]],
            check.affected_tests(graph, "pkg.mod"),
        )

    def test_affected_tests_drop_distances_without_tests(self):
        graph = {
            Path("tests/test_late.py"): ("test_late", {"pkg.user"}),
            Path("src/pkg/user.py"): ("pkg.user", {"pkg.mod"}),
        }
        self.assertEqual([["test_late"]], check.affected_tests(graph, "pkg.mod"))

    def test_focus_orders_modules_nearest_then_fastest_and_drops_failing_ones(self):
        self.write("src/pkg/__init__.py")
        self.write("src/pkg/mod.py", "def add(a, b):\n    return a + b\n")
        self.write("src/pkg/user.py", "from . import mod\n")
        for name in ("test_early", "test_late", "test_broken"):
            self.write(f"tests/{name}.py", "import pkg.mod\n")
        self.write("tests/test_far.py", "import pkg.user\n")
        seconds = {"test_early": 3.0, "test_late": 0.5, "test_broken": 0.1, "test_far": 0.1}
        ran = []

        def run(command, directory, timeout):
            test = command.split("*")[-2].rstrip(".")
            ran.append(test)
            return SimpleNamespace(code=int(test == "test_broken"), seconds=seconds[test])

        focus = check._focuser(self.root, SimpleNamespace(run=run), "python -m unittest discover")
        commands = focus(self.root / "src/pkg/mod.py")
        self.assertEqual(
            [
                f"python -m unittest discover -f -k '*{test}.*'"
                for test in ("test_late", "test_early", "test_far")
            ],
            commands,
        )
        self.assertEqual(commands, focus(self.root / "src/pkg/mod.py"))
        self.assertEqual(4, len(ran))
        self.assertEqual([], check._focuser(self.root, None, "make test")(self.root / "src/pkg/mod.py"))

    def test_focused_runner_stops_at_the_first_failing_focus(self):
        runner = check.FocusedRunner()
        runner.focus = ["exit 0", "exit 3", "exit 4"]
        self.assertEqual(3, runner.run("exit 0", self.root, 5).code)
        runner.focus = ["exit 0"]
        self.assertEqual(5, runner.run("exit 5", self.root, 5).code)
        runner.focus = ["exit 3"]
        self.assertEqual(0, runner.run("exit 0", self.root, None).code)

    def test_focused_command_narrows_only_a_lone_unittest_discover(self):
        command = "uv run python -m unittest discover -s tests -q"
        self.assertEqual(
            command + " -f -k '*test_a.*' -k '*test_b.*'",
            check.focused_command(command, ["test_a", "test_b"]),
        )
        self.assertEqual(
            ["py", "-m", "unittest", "discover", "-f", "-k", "*test_a.*"],
            check.focused_command(["py", "-m", "unittest", "discover"], ["test_a"]),
        )
        for unfocused, tests in (
            (command, []),
            (command + " -k slow", ["test_a"]),
            (command + " '-k' slow", ["test_a"]),
            (command + " -kslow", ["test_a"]),
            (command + " -qk slow", ["test_a"]),
            (command + " # quiet", ["test_a"]),
            (command + " 2>&1", ["test_a"]),
            ("cd tests && python -m unittest discover", ["test_a"]),
            ("python -m pytest", ["test_a"]),
            ("python -m unittest test_a", ["test_a"]),
            ("python -m unittest discover -p 'test*.py", ["test_a"]),
            (["py", "-m", "pytest"], ["test_a"]),
        ):
            with self.subTest(command=unfocused, tests=tests):
                self.assertIsNone(check.focused_command(unfocused, tests))

    def run_gate(self, direct_test):
        self.write("src/pkg/__init__.py")
        self.write("src/pkg/mod.py", "def add(a, b):\n    return a + b\n")
        self.write(
            "tests/test_direct.py",
            "import unittest\nimport pkg.mod\n\n"
            f"class Direct(unittest.TestCase):\n    def test_add(self):\n        {direct_test}\n",
        )
        self.write(
            "tests/test_hidden.py",
            "import importlib\nimport unittest\n\n"
            "class Hidden(unittest.TestCase):\n    def test_add(self):\n"
            "        add = importlib.import_module('pkg.mod').add\n"
            "        self.assertEqual(5, add(2, 3))\n",
        )
        self.write(
            "target/coverage/python/lcov.info",
            "SF:src/pkg/mod.py\nDA:1,1\nDA:2,1\nend_of_record\n",
        )
        test_command = f"PYTHONPATH=src {shlex.quote(sys.executable)} -m unittest discover -s tests"
        calls = []
        run = check.CommandRunner.run

        def record(runner, command, cwd, timeout):
            calls.append((command, timeout))
            return run(runner, command, cwd, timeout)

        output = io.StringIO()
        with patch.object(check.CommandRunner, "run", autospec=True, side_effect=record):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                status = check.run(
                    [
                        "--root", str(self.root), "-s", "src", "--use-existing-coverage",
                        "--test-command", test_command, "--max-workers", "1",
                    ]
                )
        focused = test_command + " -f -k '*test_direct.*'"
        self.assertEqual(0, status, output.getvalue())
        self.assertIn("Mutation: 1 killed, 0 survived", output.getvalue())
        self.assertIn((focused, None), calls)
        mutant_runs = [command for command, timeout in calls if timeout is not None]
        return mutant_runs, focused, test_command

    def test_focused_kill_skips_the_full_command(self):
        mutant_runs, focused, _full = self.run_gate("self.assertEqual(5, pkg.mod.add(2, 3))")
        self.assertEqual([focused], mutant_runs)

    def test_mutant_only_an_unselected_test_kills_is_still_killed(self):
        mutant_runs, focused, full = self.run_gate("self.assertIsNotNone(pkg.mod.add(2, 3))")
        self.assertEqual([focused, full], mutant_runs)

    def test_mutation_count_warning_needs_verbose(self):
        self.write("src/example.py", "def add(a, b):\n    return a" + " + b" * 51 + "\n")
        self.write(
            "target/coverage/python/lcov.info", "SF:src/example.py\nDA:1,1\nDA:2,1\nend_of_record\n"
        )
        for verbose in ([], ["--verbose"]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                check.run(
                    ["--root", str(self.root), "--use-existing-coverage",
                     "--test-command", "exit 0", *verbose]
                )
            with self.subTest(verbose=verbose):
                self.assertEqual(bool(verbose), "WARNING: 51 covered mutations" in output.getvalue())


if __name__ == "__main__":
    unittest.main()
