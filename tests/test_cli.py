import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ub_cli.cli import main, run


def invoke(entrypoint, arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = entrypoint(arguments)
    return status, stdout.getvalue(), stderr.getvalue()


class UpstreamParityTests(unittest.TestCase):
    def test_subcommands_preserve_upstream_reports_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
            cases = (
                ("crap", "ub_cli._vendor.crapper.cli", ["--no-coverage"]),
                ("dry", "ub_cli._vendor.dryer.cli", []),
                ("mutate", "ub_cli._vendor.mutator.cli", ["--scan", "--no-coverage"]),
            )
            for command, module, options in cases:
                with self.subTest(command=command):
                    arguments = ["--root", str(root), *options]
                    upstream = invoke(importlib.import_module(module).run, arguments)
                    unified = invoke(run, [command, *arguments])
                    self.assertEqual(upstream, unified)

    def test_subcommand_help_names_the_ub_command(self):
        for command, engine in (("crap", "crapper"), ("dry", "dryer"), ("mutate", "mutator")):
            with self.subTest(command=command):
                upstream = invoke(importlib.import_module(f"ub_cli._vendor.{engine}.cli").run, ["--help"])[1]
                status, output, error = invoke(run, [command, "--help"])
                self.assertEqual((0, ""), (status, error))
                self.assertTrue(upstream.startswith(f"Usage: {engine} [options]"), upstream)
                self.assertEqual(f"Usage: ub {command}" + upstream.removeprefix(f"Usage: {engine}"), output)

    def test_top_level_help_names_all_commands(self):
        status, output, _ = invoke(run, ["--help"])
        self.assertEqual(0, status)
        for command in ("crap", "dry", "mutate", "check"):
            self.assertIn(command, output)

    def test_version_prints_package_version(self):
        status, output, _ = invoke(run, ["--version"])
        self.assertEqual(0, status)
        self.assertRegex(output, r"^ub \d")

    def test_unknown_command_fails_with_one_hint(self):
        self.assertEqual(
            (2, "", "Unknown command: unknown\nRun ub --help for usage.\n"),
            invoke(run, ["unknown"]),
        )

    def test_licenses_outside_native_build_explains_itself(self):
        status, output, error = invoke(run, ["--licenses"])
        self.assertEqual((1, ""), (status, output))
        self.assertEqual(1, error.count("\n"))
        self.assertIn("native build", error)

    def test_main_passes_arguments_after_program_name(self):
        output = io.StringIO()
        with (
            patch("sys.argv", ["ub", "--version"]),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as raised,
        ):
            main()
        self.assertEqual(0, raised.exception.code)
        self.assertTrue(output.getvalue().startswith("ub "))


if __name__ == "__main__":
    unittest.main()
