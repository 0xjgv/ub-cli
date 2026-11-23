"""Copy the upstream engines into src/ub_cli/vendor at pinned commits.

    python scripts/vendor.py NAME COMMIT   vendor one engine at COMMIT and record the pin
    python scripts/vendor.py               re-vendor every pin in VENDORED (reproduces the tree)

Absolute imports of the engines become ub_cli.vendor imports, so the wheel never
installs top-level packages that collide with unrelated PyPI projects of the same name.
The directory is named `vendor` because the engines skip directories of that name,
so `ub crap`, `ub dry`, `ub mutate`, and `ub check` never analyze the engines themselves.
After the rewrite, each scripts/vendor-patches/NAME-*.patch is applied with `git apply`,
for fixes not yet upstream; delete a patch once the pin includes its fix.
Never edit vendored code by hand; change the pin or add a patch and rerun this script.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "src" / "ub_cli" / "vendor"
PINS = VENDOR / "VENDORED"
PATCHES = ROOT / "scripts" / "vendor-patches"
ENGINES = ("crapper", "dryer", "mutator")
NAMES = "|".join(ENGINES)
FROM_IMPORT = re.compile(rf"^(\s*)from ({NAMES})(\.[\w.]+)? import ", re.M)
PLAIN_IMPORT = re.compile(rf"^(\s*)import ({NAMES})\s*$", re.M)
SUBMODULE_IMPORT = re.compile(rf"^(\s*)import ({NAMES})(\.[\w.]+)\s*$", re.M)
LEFTOVER = re.compile(rf"^\s*import (?!ub_cli\.)[^\n]*\b({NAMES})\b", re.M)


def read_pins() -> dict[str, tuple[str, str]]:
    pins = {}
    for line in PINS.read_text().splitlines():
        if line and not line.startswith("#"):
            name, url, commit = line.split()
            pins[name] = (url, commit)
    return pins


def write_pins(pins: dict[str, tuple[str, str]]) -> None:
    lines = ["# name url commit; written by scripts/vendor.py"]
    lines += [f"{name} {url} {commit}" for name, (url, commit) in sorted(pins.items())]
    PINS.write_text("\n".join(lines) + "\n")


def rewrite(source: str, path: Path) -> str:
    source = FROM_IMPORT.sub(lambda m: f"{m[1]}from ub_cli.vendor.{m[2]}{m[3] or ''} import ", source)
    source = PLAIN_IMPORT.sub(r"\1from ub_cli.vendor import \2", source)
    # `import crapper.coverage` loads the submodule and binds `crapper`; keep both effects.
    source = SUBMODULE_IMPORT.sub(r"\1import ub_cli.vendor.\2\3\n\1from ub_cli.vendor import \2", source)
    if LEFTOVER.search(source):
        raise SystemExit(f"{path}: unsupported engine import form; extend scripts/vendor.py")
    return source


def vendor(name: str, url: str, commit: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        checkout = Path(directory)
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "fetch", "-q", "--depth", "1", url, commit], check=True)
        subprocess.run(["git", "-C", str(checkout), "checkout", "-q", "FETCH_HEAD"], check=True)
        target = VENDOR / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(checkout / "src" / name, target, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(checkout / "LICENSE", target / "LICENSE")
    for path in sorted(target.rglob("*.py")):
        path.write_text(rewrite(path.read_text(), path))
    for patch in sorted(PATCHES.glob(f"{name}-*.patch")):
        subprocess.run(["git", "apply", str(patch)], cwd=ROOT, check=True)
        print(f"{name}: applied {patch.name}")
    print(f"{name}: vendored {commit[:12]}")


def main(argv: list[str]) -> int:
    pins = read_pins()
    if len(argv) == 2:
        name, commit = argv
        if name not in ENGINES or not re.fullmatch(r"[0-9a-f]{40}", commit):
            print(__doc__, file=sys.stderr)
            return 2
        pins[name] = (f"https://github.com/unclebob/{name}.git", commit)
        vendor(name, *pins[name])
        write_pins(pins)
        return 0
    if argv:
        print(__doc__, file=sys.stderr)
        return 2
    for name, (url, commit) in sorted(pins.items()):
        vendor(name, url, commit)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
