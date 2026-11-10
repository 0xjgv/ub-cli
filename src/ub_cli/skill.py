"""Install the bundled ub-mode skill into each selected agent's user directory."""

import argparse
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

AGENT_DIRS = {"codex": ".agents", "claude": ".claude"}


def install(targets: list[str], force: bool) -> int:
    directories = {target: Path.home() / AGENT_DIRS[target] / "skills" / "ub-mode" for target in targets}
    bundled = Path(__file__).parent / "skills" / "ub-mode"
    pending = []
    # Check every file before changing any. Publish each skill after its license.
    for directory in directories.values():
        for name in ("LICENSE", "SKILL.md"):
            path = directory / name
            data = (bundled / name).read_bytes()
            if path.is_file() and path.read_bytes() == data:
                continue
            if (path.exists() or path.is_symlink()) and not force:
                print(f"{path} already exists and differs. Use --force to replace it.", file=sys.stderr)
                return 1
            if path.is_dir():
                raise IsADirectoryError(path)
            pending.append((path, data))

    for path, data in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = NamedTemporaryFile(dir=path.parent, delete=False)
        temporary = Path(staged.name)
        try:
            with staged:
                staged.write(data)
            if force:
                temporary.replace(path)
            else:
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    changed = {path.parent for path, _ in pending}
    for target, directory in directories.items():
        status = "Installed" if directory in changed else "Already installed"
        print(f"{status}: {directory / 'SKILL.md'}")
        invocation = "$ub-mode" if target == "codex" else "/ub-mode"
        print(f"Invoke {invocation} in {target}.")
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ub skill")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("install", help="Install a bundled agent skill")
    command.add_argument("name", nargs="?", choices=["ub-mode"], default="ub-mode")
    command.add_argument("--target", choices=list(AGENT_DIRS), help="Install for one agent (default: all)")
    command.add_argument("--force", action="store_true", help="Replace different installed files")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return install([args.target] if args.target else list(AGENT_DIRS), args.force)
    except OSError as exc:
        print(f"ub skill: {exc}", file=sys.stderr)
        return 1
