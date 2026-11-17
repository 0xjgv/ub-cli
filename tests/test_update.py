import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ub_cli import update


def invoke(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = update.run(arguments)
    return status, stdout.getvalue(), stderr.getvalue()


class UpdateTests(unittest.TestCase):
    def test_help_prints_usage(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                status, output, error = invoke([flag])
                self.assertEqual((0, ""), (status, error))
                self.assertTrue(output.startswith("Usage: ub update\n"))

    def test_extra_arguments_fail_with_usage(self):
        self.assertEqual((2, "", "Usage: ub update\n"), invoke(["--now"]))

    def test_source_install_points_to_package_manager(self):
        # A source install has no sys.frozen at all; leave it unset.
        self.assertFalse(hasattr(sys, "frozen"))
        with patch("ub_cli.update.subprocess.run") as installer:
            status, output, error = invoke([])
        installer.assert_not_called()
        self.assertEqual((1, ""), (status, output))
        self.assertIn("Python package manager", error)

    def run_frozen(self, environment):
        executable = Path("/opt/ub/bin/ub")
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
            patch.dict(os.environ, environment, clear=True),
            patch("ub_cli.update.subprocess.run") as installer,
        ):
            installer.return_value.returncode = 7
            status = update.run([])
        self.assertEqual(7, status)
        command = installer.call_args.args[0]
        self.assertEqual(["sh", Path(update.__file__).with_name("install.sh")], command)
        env = installer.call_args.kwargs["env"]
        self.assertEqual("1", env["PYINSTALLER_RESET_ENVIRONMENT"])
        return env

    def test_frozen_install_defaults_to_executable_directory(self):
        env = self.run_frozen({"PATH": "/bin"})
        self.assertEqual(str(Path("/opt/ub/bin/ub").resolve().parent), env["UB_INSTALL_DIR"])
        self.assertEqual("/bin", env["PATH"])

    def test_frozen_install_keeps_chosen_directory(self):
        env = self.run_frozen({"UB_INSTALL_DIR": "/custom"})
        self.assertEqual("/custom", env["UB_INSTALL_DIR"])


if __name__ == "__main__":
    unittest.main()
