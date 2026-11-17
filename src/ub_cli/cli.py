"""Dispatch upstream commands without interpreting their flags or exit codes."""

from importlib import import_module
from importlib.metadata import version
import sys

HELP = """Usage: ub COMMAND [options]

  crap     Score function complexity and test coverage
  dry      Find structurally similar code
  mutate   Test whether mutations are caught
  check    Enforce all three ub-mode gates
  skill    Install a bundled agent skill
  update   Update the standalone executable

Run ub --version to print the installed version.
Run ub COMMAND --help for command-specific options.
"""

ENGINES = {"crap": "crapper", "dry": "dryer", "mutate": "mutator"}


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
        engine = import_module(f"{ENGINES[command]}.cli")
        # shortcut: rewrites upstream's module HELP, which hardcodes the engine name
        # and ignores argv[0]; tests catch a renamed or reformatted HELP. Drop this
        # once the engines accept a program name.
        engine.HELP = engine.HELP.replace(f"Usage: {ENGINES[command]} ", f"Usage: ub {command} ", 1)
        execute = engine.run
    elif command == "update":
        from ub_cli.update import run as execute
    elif command == "skill":
        from ub_cli.skill import run as execute
    elif command == "check":
        from ub_cli.check import run as execute
    else:
        print(f"Unknown command: {command}\nRun ub --help for usage.", file=sys.stderr)
        return 2
    return execute(args)


def main() -> None:
    sys.exit(run(sys.argv[1:]))
