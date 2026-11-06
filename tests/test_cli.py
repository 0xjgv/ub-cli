import contextlib
import importlib
import io
import tempfile
import unittest
from pathlib import Path

from ub_cli.cli import run


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
                ("crap", "crapper.cli", ["--no-coverage"]),
                ("dry", "dryer.cli", []),
                ("mutate", "mutator.cli", ["--scan", "--no-coverage"]),
            )
            for command, module, options in cases:
                with self.subTest(command=command):
                    arguments = ["--root", str(root), *options]
                    upstream = invoke(importlib.import_module(module).run, arguments)
                    unified = invoke(run, [command, *arguments])
                    self.assertEqual(upstream, unified)

    def test_subcommands_preserve_upstream_help(self):
        for command, module in (
            ("crap", "crapper.cli"),
            ("dry", "dryer.cli"),
            ("mutate", "mutator.cli"),
        ):
            with self.subTest(command=command):
                upstream = invoke(importlib.import_module(module).run, ["--help"])
                self.assertEqual(upstream, invoke(run, [command, "--help"]))

    def test_top_level_help_names_all_commands(self):
        status, output, _ = invoke(run, ["--help"])
        self.assertEqual(0, status)
        for command in ("crap", "dry", "mutate", "check"):
            self.assertIn(command, output)

    def test_unknown_command_fails(self):
        status, _, error = invoke(run, ["unknown"])
        self.assertNotEqual(0, status)
        self.assertIn("unknown", error)


if __name__ == "__main__":
    unittest.main()
