"""Run a vendored engine with ub's terse output and stricter exits.

The engines stay untouched on disk. While one runs, ub swaps a few of its
module-level names: the report formatters, the coverage runner, and the
file selection. The engine still prints each report when it is ready, so a
long mutation run streams per file. A renamed name fails loudly in the tests.
"""

from __future__ import annotations

import contextlib
import dataclasses
import sys
from importlib import import_module
from pathlib import Path

from ub_cli.check import NO_EXECUTED_LINES, _quietly, unexecuted

LIMIT = 10
VERBOSE_HELP = {
    "crap": "Print every function and the coverage output.",
    "dry": "Print every candidate pair.",
}
HELP_LINE = "  -h, --help                    Print this help and exit.\n"


class Stop(Exception):
    """End the run with status 2 and this one-line cause."""


@contextlib.contextmanager
def _swapped(replacements):
    """Set each (module, name, value) for the duration of the block."""

    saved = [(module, name, getattr(module, name)) for module, name, _ in replacements]
    try:
        for module, name, value in replacements:
            setattr(module, name, value)
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def _unmatched(options, files: list[Path]) -> str | None:
    """The first positional argument that selected no file, like the engines resolve it."""

    if options.changed:
        return None
    root = options.project_root.resolve()
    for argument in options.positionals:
        candidate = Path(argument) if Path(argument).is_absolute() else root / argument
        if candidate.exists():
            target = candidate.resolve()
            hit = any(path == target or path.is_relative_to(target) for path in files)
        else:
            hit = any(argument in path.as_posix() for path in files)
        if not hit:
            return argument
    return None


def _strict_selection(select, seen: dict):
    def selected(options):
        seen["options"] = options
        files = select(options)
        argument = _unmatched(options, files)
        if argument is not None:
            raise Stop(f"No source files match {argument}")
        return files

    return selected


def _strict_analysis(analyze, verbose: bool, seen: dict):
    def analyzed(files, root, bundle):
        entries = analyze(files, root, bundle)
        options = seen["options"]
        # A narrowed selection can be honestly untested; only a whole run proves coverage broke.
        narrowed = options.positionals or options.changed
        if bundle is not None and not narrowed and unexecuted(entries):
            hint = "" if verbose else " Rerun with --verbose to see its output."
            raise Stop(NO_EXECUTED_LINES + hint)
        return entries

    return analyzed


def _quiet(call):
    """Hide the coverage run's output unless the run reports a failure."""

    def quiet(*args):
        code, log = _quietly(call, *args)
        if code:
            print(log, end="", file=sys.stderr)
        return code

    return quiet


def _shown(lines: list[str]) -> list[str]:
    """Cap the list the way `ub check` caps its failures."""

    if len(lines) <= LIMIT:
        return lines
    return lines[:LIMIT] + [f"  ... and {len(lines) - LIMIT} more; --verbose for all"]


def crap_report(entries, threshold: float | None = None) -> str:
    """List CRAP >= 10, or CRAP above --threshold when given, matching its exit status."""

    if any(entry.crap is not None for entry in entries):
        if threshold is None:
            notable = [entry for entry in entries if entry.crap is not None and entry.crap >= 10]
            cutoff = ">= 10"
        else:
            notable = [entry for entry in entries if entry.crap is not None and entry.crap > threshold]
            cutoff = f"> {threshold:g}"
        summary = f"CRAP: {len(notable)} of {len(entries)} functions {cutoff}"
        lines = [
            f"  {entry.crap:.1f} CRAP, {entry.complexity} CC, {entry.coverage:.0f}% covered: "
            f"{entry.namespace}/{entry.name}"
            for entry in notable
        ]
    else:
        notable = [entry for entry in entries if entry.complexity >= 10]
        summary = (
            f"Complexity: {len(notable)} of {len(entries)} functions >= 10, "
            "coverage not measured"
        )
        lines = [f"  {entry.complexity} CC: {entry.namespace}/{entry.name}" for entry in notable]
    return "\n".join([summary, *_shown(lines)]) + "\n"


def dry_report(candidates) -> str:
    lines = [
        f"  {candidate.score:.2f} {candidate.left.file}:{candidate.left.start_line}-"
        f"{candidate.left.end_line} {candidate.right.file}:{candidate.right.start_line}-"
        f"{candidate.right.end_line}"
        for candidate in candidates
    ]
    summary = f"Duplication: {len(candidates)} candidate pairs"
    return "\n".join([summary, *_shown(lines)]) + "\n"


def _site_log(format_site_log):
    def unkilled(sites, statuses):
        kept = {key: status for key, status in statuses.items() if status != "killed"}
        return format_site_log(sites, kept)

    return unkilled


def mutation_summary(forms) -> str:
    if not forms:
        return "No functions to mutate.\n"
    killed = sum(form.killed for form in forms)
    survived = sum(form.survived for form in forms)
    uncovered = sum(form.uncovered for form in forms)
    executed = killed + survived
    score = f", score {killed / executed:.1%}" if executed else ""
    return (
        f"Mutation: {killed} killed, {survived} survived, {uncovered} uncovered "
        f"in {len(forms)} functions{score}. --verbose lists every mutant and function.\n"
    )


def scan_summary(path, sites, changed, covered) -> str:
    if not sites:
        return ""
    notes = []
    if covered is not None:
        missed = sum(not covered.get(site.mutation_id, True) for site in sites)
        notes.append(f"{missed} uncovered")
    if changed:
        notes.append(f"{len(changed)} changed functions")
    detail = f" ({', '.join(notes)})" if notes else ""
    return f"Scan: {len(sites)} mutation sites in {path}{detail}\n"


def _unwritten(mutate_file):
    def mutated(*args, **kwargs):
        return dataclasses.replace(mutate_file(*args, **kwargs), written=[])

    return mutated


def _replacements(command: str, engine, verbose: bool) -> list:
    seen = {}
    replacements = [(engine, "select_files", _strict_selection(engine.select_files, seen))]
    if command == "crap":
        replacements.append(
            (engine, "analyze_files", _strict_analysis(engine.analyze_files, verbose, seen))
        )
    if verbose:
        return replacements
    if command == "crap":
        return replacements + [
            (engine, "run_coverage", _quiet(engine.run_coverage)),
            (engine, "format_report", lambda entries: crap_report(entries, seen["options"].threshold)),
        ]
    if command == "dry":
        return replacements + [(engine, "format_text", dry_report)]
    # mutator reaches the coverage runner through crapper's module at call time.
    runners = import_module("ub_cli.vendor.crapper.runners")
    mutator = import_module("ub_cli.vendor.mutator.engine")
    return replacements + [
        (runners, "run_coverage", _quiet(runners.run_coverage)),
        (engine, "format_site_log", _site_log(engine.format_site_log)),
        (engine, "format_results", mutation_summary),
        (engine, "mutate_file", _unwritten(engine.mutate_file)),
        (mutator, "format_scan", scan_summary),
    ]


def _help(command: str, name: str, engine) -> str:
    # shortcut: rewrites upstream's module HELP, which hardcodes the engine name
    # and ignores argv[0]; tests catch a renamed or reformatted HELP. Drop this
    # once the engines accept a program name.
    text = engine.HELP.replace(f"Usage: {name} ", f"Usage: ub {command} ", 1)
    if command not in VERBOSE_HELP:
        return text
    line = f"  --verbose                     {VERBOSE_HELP[command]}\n"
    return text.replace(HELP_LINE, HELP_LINE + line, 1)


def run(command: str, name: str, args: list[str]) -> int:
    engine = import_module(f"ub_cli.vendor.{name}.cli")
    verbose = "--verbose" in args
    if command in VERBOSE_HELP:
        # The engine has no --verbose of its own; ub consumes it.
        args = [arg for arg in args if arg != "--verbose"]
    replacements = [(engine, "HELP", _help(command, name, engine))]
    try:
        with _swapped(replacements + _replacements(command, engine, verbose)):
            return engine.run(args)
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 2
