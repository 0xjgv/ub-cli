"""Compare the vendored engines' pinned commits with their upstream HEADs."""

import subprocess
import sys
from pathlib import Path

pins = Path(__file__).resolve().parents[1] / "src" / "ub_cli" / "_vendor" / "VENDORED"
dependencies = [line.split() for line in pins.read_text().splitlines() if line and not line.startswith("#")]

failed = False
for name, url, pin in dependencies:
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", url, "HEAD"],
        text=True, stdout=subprocess.PIPE,
    )
    if result.returncode:
        print(f"{name}: could not read upstream HEAD", file=sys.stderr)
        failed = True
        continue
    head = result.stdout.split()[0]
    status = "current" if head == pin else "differs"
    print(f"{name}: {status}  pinned={pin[:12]}  latest={head[:12]}")
    if head != pin:
        print(f"  {url.removesuffix('.git')}/compare/{pin}...{head}")
        print(f"  update: python scripts/vendor.py {name} {head}")

sys.exit(int(failed))
