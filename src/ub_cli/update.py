"""Update a standalone installation through the bundled release installer."""

import os
from pathlib import Path
import subprocess
import sys


def run(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print("Usage: ub update\nInstall the latest native release using the bundled installer.")
        return 0
    if argv:
        print("Usage: ub update", file=sys.stderr)
        return 2
    if not getattr(sys, "frozen", False):
        print("Upgrade ub-cli with the Python package manager you used to install it.", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env.setdefault("UB_INSTALL_DIR", str(Path(sys.executable).resolve().parent))
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return subprocess.run(["sh", Path(__file__).with_name("install.sh")], env=env).returncode
