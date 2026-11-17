import importlib.util
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
