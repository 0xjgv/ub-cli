"""Install the bundled ub-mode skill into the selected agent's user directory."""

import argparse
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


def install(target: str, force: bool) -> int:
    agent_dir = {"codex": ".agents", "claude": ".claude"}[target]
    directory = Path.home() / agent_dir / "skills" / "ub-mode"
    bundled = Path(__file__).parent / "skills" / "ub-mode"
    pending = []
    # Check both files before changing either. Publish the skill after its license.
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

    directory.mkdir(parents=True, exist_ok=True)
    for path, data in pending:
        staged = NamedTemporaryFile(dir=directory, delete=False)
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

    status = "Installed" if pending else "Already installed"
    print(f"{status}: {directory / 'SKILL.md'}")
    invocation = "$ub-mode" if target == "codex" else "/ub-mode"
    print(f"Invoke {invocation} in {target}.")
    return 0


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ub skill")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("install", help="Install a bundled agent skill")
    command.add_argument("name", choices=["ub-mode"])
    command.add_argument("--target", required=True, choices=["codex", "claude"])
    command.add_argument("--force", action="store_true", help="Replace different installed files")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        return install(args.target, args.force)
    except OSError as exc:
        print(f"ub skill: {exc}", file=sys.stderr)
        return 1
