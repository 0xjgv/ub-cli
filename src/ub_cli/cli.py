"""Dispatch ub commands to the engines and the ub-only tools."""

from importlib import import_module
from importlib.metadata import version
import sys

HELP = """Usage: ub COMMAND [options]

  crap     Score function complexity and test coverage
  dry      Find structurally similar code
  mutate   Test whether mutations are caught
  check    Enforce all three ub-mode gates and the escape-hatch ratchet
  ratchet  Count escape hatches against .ub/ratchet, or guard .ub/ changes
  init     Set up repository quality checks and agent instructions
  hooks    Install Git and agent hooks for the repository
  skill    Install a bundled agent skill
  update   Update the standalone executable

Run ub --version to print the installed version.
Run ub COMMAND --help for command-specific options.
"""

ENGINES = {"crap": "crapper", "dry": "dryer", "mutate": "mutator"}
# Each command's module, imported on use.
COMMANDS = {"update": "update", "skill": "skill", "check": "check", "ratchet": "ratchet",
            "init": "project", "hooks": "hooks"}


def run(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP, end="")
        return 0

    if argv == ["--version"]:
        print(f"ub {version('ub-cli')}")
        return 0

    if argv == ["--licenses"]:
        # The native entry point answers --licenses before reaching this module.
        print(
            "ub --licenses needs the native build; "
            "pip and uv installs keep each license in the package metadata.",
            file=sys.stderr,
        )
        return 1

    command, *args = argv
    if command in ENGINES:
        from ub_cli.engines import run as run_engine

        return run_engine(command, ENGINES[command], args)
    if command not in COMMANDS:
        print(f"Unknown command: {command}\nRun ub --help for usage.", file=sys.stderr)
        return 2
    return import_module(f"ub_cli.{COMMANDS[command]}").run(args)


def main() -> None:
    sys.exit(run(sys.argv[1:]))
