"""Set up a repository's shared quality command and agent instructions."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
from tempfile import NamedTemporaryFile


INSTRUCTIONS = """## UB quality checks

This repository uses the `ub` CLI for complexity/coverage, duplication, and
mutation testing. After changing production code or tests, run `./.ub/check`
from the repository root before declaring the work complete. Fix failures
within the task's scope; report unresolved failures or checks you could not run.
The command contains this repository's source scope and coverage/test commands.
Use `ub COMMAND --help` for focused diagnostics. `--changed` selects working-tree
changes, not the committed changes in a pull request.
Do not weaken the check, skip hooks, or claim success without a passing run.
`.ub/ratchet` caps lint suppressions, coverage exclusions, and skipped tests;
remove the new escape hatch instead. Never run `ub ratchet --update` or edit
`.ub/` unless the user asks.
"""


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed.")
    return result.stdout.rstrip("\n")


def repository(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--show-toplevel")).resolve()


def local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{name} points outside the project; leaving it unchanged.")
    if path.exists() and not path.is_file():
        raise ValueError(f"Not a regular file: {path}")
    return path


def managed_block(text: str, body: str, *, markdown: bool) -> str:
    start, end = (("<!-- ub:begin -->", "<!-- ub:end -->") if markdown
                  else ("# ub:begin", "# ub:end"))
    block = f"{start}\n{body.rstrip()}\n{end}\n"
    if start not in text and end not in text:
        return text + ("\n\n" if text and not text.endswith("\n") else "\n" if text else "") + block
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError("Malformed UB markers; repair the ub:begin/ub:end block first.")
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    return before + block.rstrip("\n") + after


def write_file(path: Path, data: str, mode: int | None = None) -> None:
    if path.exists() and path.read_text() == data:
        if mode is not None and path.stat().st_mode & 0o777 != mode:
            path.chmod(mode)
        return
    permissions = mode if mode is not None else path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as staged:
        temporary = Path(staged.name)
        try:
            staged.write(data)
            staged.flush()
            temporary.chmod(permissions)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def initialize(options) -> None:
    from ub_cli.ratchet import ratchet_file, render, scan, tally, toplevel

    root = options.root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    command = ["ub", "check"]
    for source in options.source_root:
        path = (root / source).resolve()
        if not path.is_relative_to(root) or not path.is_dir():
            raise ValueError(f"Source root must be a directory inside the project: {source}")
        command.extend(["-s", "./" + path.relative_to(root).as_posix()])
    for flag in ("coverage_command", "test_command", "max_workers"):
        value = getattr(options, flag)
        if value is not None:
            command.extend(["--" + flag.replace("_", "-"), str(value)])
    check = local_file(root, ".ub/check")
    script = ('#!/bin/sh\nset -eu\n'
              'cd -- "$(CDPATH=\'\' cd -- "$(dirname -- "$0")/.." && pwd)"\n'
              f'exec {shlex.join(command)} "$@"\n')
    customized = options.source_root or any(getattr(options, flag) is not None for flag in
                                            ("coverage_command", "test_command", "max_workers"))
    if check.exists():
        if customized and check.read_text() != script:
            raise ValueError(".ub/check already differs; edit it directly to change the project commands.")
        script = check.read_text()
    pending = {check: (script, 0o755)}
    top = toplevel(root)
    ratchet = top and ratchet_file(top)
    if ratchet and not ratchet.exists():
        pending[ratchet] = (render(tally(scan(top))), None)
    generated = [".ub/check"] + ([os.path.relpath(ratchet, root)] if ratchet else [])
    for name in ("AGENTS.md", "CLAUDE.md", ".gitignore"):
        path = local_file(root, name)
        text = path.read_text() if path.exists() else ""
        body = INSTRUCTIONS if name != ".gitignore" else "/.metrics/\n/target/mutation-workers/\n/target/mutator-backup/"
        content = managed_block(text, body, markdown=name != ".gitignore")
        if path in pending and pending[path] != (content, None):
            raise ValueError(f"Setup files resolve to the same path: {path}")
        pending[path] = (content, None)
    for path, (data, mode) in pending.items():
        write_file(path, data, mode)
    print(f"Ready: {root / '.ub/check'}")
    print(f"Review and commit {', '.join(generated)}, AGENTS.md, CLAUDE.md, and .gitignore.")
    print("Run ./.ub/check locally and in CI after installing the project's test and coverage tools.")
    print("Optional: ub hooks install (Git pre-push), or --target codex / --target claude.")


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ub init", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("-s", "--source-root", action="append", default=[])
    parser.add_argument("--coverage-command")
    parser.add_argument("--test-command")
    parser.add_argument("--max-workers", type=int)
    try:
        options = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if options.max_workers is not None and options.max_workers < 1:
            raise ValueError("--max-workers requires a positive integer")
        initialize(options)
    except (OSError, ValueError) as exc:
        print(f"ub init: {exc}", file=sys.stderr)
        return 1
    return 0
