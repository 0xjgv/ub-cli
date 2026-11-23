import importlib.util
import sys
import unittest
from pathlib import Path

from ub_cli.vendor.crapper.discover import iter_source_files as crap_files
from ub_cli.vendor.dryer.discover import iter_source_files as dry_files
from ub_cli.vendor.mutator.runner import CommandRunner

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vendor.py"
spec = importlib.util.spec_from_file_location("vendor", SCRIPT)
vendor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vendor)


class RewriteTests(unittest.TestCase):
    def test_engine_imports_move_under_ub_cli_vendor(self):
        source = (
            "import os\n"
            "from crapper.model import Entry\n"
            "from mutator import edn\n"
            "def load():\n"
            "    import crapper\n"
            "    import crapper.coverage\n"
            "    return crapper\n"
            "from dryerless import thing\n"
        )
        self.assertEqual(vendor.rewrite(source, Path("x.py")), (
            "import os\n"
            "from ub_cli.vendor.crapper.model import Entry\n"
            "from ub_cli.vendor.mutator import edn\n"
            "def load():\n"
            "    from ub_cli.vendor import crapper\n"
            "    import ub_cli.vendor.crapper.coverage\n"
            "    from ub_cli.vendor import crapper\n"
            "    return crapper\n"
            "from dryerless import thing\n"
        ))

    def test_unsupported_import_forms_stop_the_script(self):
        for source in ("import crapper as c\n", "import os, dryer\n"):
            with self.subTest(source=source), self.assertRaises(SystemExit):
                vendor.rewrite(source, Path("x.py"))


class LocationTests(unittest.TestCase):
    def test_engines_skip_the_vendored_tree(self):
        # The engines skip directories named `vendor`; a repin that drops that rule
        # makes every ub command analyze the engines again.
        package = Path(vendor.VENDOR).parent
        for discover in (crap_files, dry_files):
            with self.subTest(discover=discover.__module__):
                files = discover([package])
                self.assertIn(package / "check.py", files)
                self.assertFalse([path for path in files if vendor.VENDOR in path.parents])


class PatchTests(unittest.TestCase):
    def test_test_output_that_is_not_utf8_does_not_crash_mutator(self):
        # scripts/vendor-patches/mutator-runner-undecodable-output.patch
        emit = "import sys; sys.stdout.buffer.write(bytes([111, 107, 255])); sys.exit(3)"
        result = CommandRunner().run([sys.executable, "-c", emit], Path.cwd(), 30)
        self.assertEqual((result.code, result.output), (3, "ok\ufffd"))


if __name__ == "__main__":
    unittest.main()
