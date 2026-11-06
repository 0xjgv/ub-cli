"""Compare the project's pinned Git dependencies with their upstream HEADs."""

import subprocess
import sys
import tomllib
from pathlib import Path

project = Path(__file__).resolve().parents[1] / "pyproject.toml"
with project.open("rb") as config:
    dependencies = tomllib.load(config)["project"]["dependencies"]

failed = False
for dependency in dependencies:
    name, separator, reference = dependency.partition(" @ git+")
    if not separator:
        continue
    url, pin = reference.rsplit("@", 1)
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

sys.exit(int(failed))
