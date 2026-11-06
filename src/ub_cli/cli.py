"""Dispatch upstream commands without interpreting their flags or exit codes."""

import sys
from importlib.metadata import version

HELP = """Usage: ub COMMAND [options]

  crap     Score function complexity and test coverage
  dry      Find structurally similar code
  mutate   Test whether mutations are caught
  check    Enforce all three ub-mode gates

Run ub --version to print the installed version.
Run ub COMMAND --help for command-specific options.
"""


def run(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(HELP, end="")
        return 0

    if argv == ["--version"]:
        print(f"ub {version('ub-cli')}")
        return 0

    command, *args = argv
    if command == "crap":
        from crapper.cli import run as execute
    elif command == "dry":
        from dryer.cli import run as execute
    elif command == "mutate":
        from mutator.cli import run as execute
    elif command == "check":
        from ub_cli.check import run as execute
    else:
        print(f"Unknown command: {command}\n{HELP}", file=sys.stderr, end="")
        return 2
    return execute(args)


def main() -> None:
    sys.exit(run(sys.argv[1:]))
