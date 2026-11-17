"""Update a standalone installation through the bundled release installer."""

import os
from pathlib import Path
import subprocess
import sys

USAGE = "Usage: ub update [VERSION]"


def run(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(f"{USAGE}\nInstall the latest native release, or VERSION such as v1.2.3, "
              "unless it is already installed.")
        return 0
    if argv[:1] == ["--verify-signature"]:
        return verify_signature(argv[1:])
    if len(argv) > 1 or argv[:1] and argv[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2
    if not getattr(sys, "frozen", False):
        print("Upgrade ub-cli with the Python package manager you used to install it.", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env.setdefault("UB_INSTALL_DIR", str(Path(sys.executable).resolve().parent))
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    # The installer calls this executable back to verify the release signature.
    env["UB_SIGNATURE_VERIFIER"] = sys.executable
    if argv:
        env["UB_VERSION"] = argv[0]
    return subprocess.run(["sh", Path(__file__).with_name("install.sh")], env=env).returncode


def verify_signature(argv: list[str]) -> int:
    """Internal: ub update --verify-signature PUBLIC_KEY FILE SIGNATURE_FILE."""
    if len(argv) != 3:
        print("Usage: ub update --verify-signature PUBLIC_KEY FILE SIGNATURE_FILE", file=sys.stderr)
        return 2
    from ub_cli.signature import verify

    public_key, message, signature = argv
    try:
        verify(public_key, Path(message).read_bytes(), Path(signature).read_text())
    except (OSError, ValueError) as error:
        print(f"ub update: {signature}: {error}", file=sys.stderr)
        return 1
    return 0
