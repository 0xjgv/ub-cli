"""Run the pinned engines against the ub-mode quality gates."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path

from crapper.coverage import _profile_segments, load_bundle
from crapper.crap import make_entry, sort_entries
from crapper.discover import iter_source_files, language_of
from crapper.languages import functions_in_file
from crapper.metrics import write_metrics as write_crap
from crapper.report import format_report
from crapper.runners import run_coverage
from dryer.discover import iter_source_files as dry_files
from dryer.report import format_text, write_metrics as write_dry
from dryer.scan import find_duplicates, scan_files
from mutator.engine import mutate_file, restore_backups
from mutator.coverage import covered_lines
from mutator.report import format_results, format_site_log
from mutator.runner import CommandRunner, test_plan
from tree_sitter_language_pack import Error as ParserError


def _crap_failures(entries) -> list[str]:
    return [
        f"CRAP {entry.crap:g} >= 10 in {entry.namespace}/{entry.name}"
        for entry in entries
        if entry.crap is not None and entry.crap >= 10
    ]


def _dry_failures(candidates) -> list[str]:
    return [
        f"Duplication {candidate.score:g} >= 0.8 between "
        f"{candidate.left.file}:{candidate.left.start_line} and "
        f"{candidate.right.file}:{candidate.right.start_line}"
        for candidate in candidates
        if candidate.score >= 0.8
    ]


def _mutation_failures(forms) -> list[str]:
    return [
        f"{form.file}:{form.line} has {form.survived} surviving "
        f"and {form.uncovered} uncovered mutants"
        for form in forms
        if form.survived or form.uncovered
    ]


def _analyze(root: Path, files: list[Path], bundle) -> tuple[list, list, list[str]]:
    """Score every function. Return all entries, the measured ones, and missing coverage."""

    bundle.bind_sources([path.relative_to(root).as_posix() for path in files])
    entries = []
    measured = []
    missing = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        functions = functions_in_file(language_of(path), source, relative, str(root))
        for function in functions:
            coverage = bundle.percent_for(function)
            if coverage is None:
                missing.append(f"Missing coverage for {relative}:{function.start_line}")
                entries.append(make_entry(function, 0.0))
            else:
                entry = make_entry(function, coverage)
                measured.append(entry)
                entries.append(entry)
    return sort_entries(entries), measured, missing


def _report_patterns(languages: set[str]) -> list[str]:
    patterns = []
    if languages.intersection({"clojure", "typescript", "rust", "python"}):
        patterns.extend(["target/coverage/**/lcov.info", "coverage/**/lcov.info"])
    if "clojure" in languages:
        patterns.append("target/coverage/**/*.html")
    if "go" in languages:
        patterns.extend(
            ["target/coverage/coverage.out", "coverage.out"]
            + ["*/" * depth + "target/coverage/go/coverage.out" for depth in range(3)]
        )
    if "java" in languages:
        patterns.extend("*/" * depth + "target/site/jacoco/jacoco.xml" for depth in range(3))
    return patterns


def _report_states(root: Path, languages: set[str]) -> dict[Path, tuple[int, int, int]]:
    states = {}
    for pattern in _report_patterns(languages):
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if path.suffix == ".html" and "forms covered" not in path.read_text(errors="replace"):
                continue
            stat = path.stat()
            states[path] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return states


def _mutation_coverage(root: Path, path: Path, bundle) -> set[int] | None:
    language = language_of(path)
    if language != "go":
        return covered_lines(root, path, language)
    segments = _profile_segments(
        bundle.go_profile, path.relative_to(root).as_posix(), bundle.source_parts
    )
    if segments is None:
        return None
    lines = set()
    for start, end, _statements, hits in segments:
        if hits > 0:
            lines.update(range(start, end + 1))
    return lines


def _baselines(root: Path, files: list[Path], runner, override: str | None) -> dict:
    baselines = {}
    for path in files:
        command, directory = test_plan(root, path, language_of(path), override)
        identity = ("sh", command) if isinstance(command, str) else ("argv", *command)
        key = (identity, str(directory))
        if key in baselines:
            continue
        result = runner.run(command, directory, None)
        if result.code != 0:
            raise ValueError(f"Baseline failed for {path.relative_to(root)}\n{result.output}")
        baselines[key] = (True, result.seconds, "")
    return baselines


def _quietly(call, *args):
    """Run `call` with this process's and its children's stdout and stderr captured."""

    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with tempfile.TemporaryFile() as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        failed = True
        try:
            result = call(*args)
            failed = False
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            for descriptor, copy in zip((1, 2), saved):
                os.dup2(copy, descriptor)
                os.close(copy)
            log.seek(0)
            output = log.read().decode(errors="replace")
            if failed:
                print(output, end="", file=sys.stderr)
        return result, output


def _generate_coverage(root: Path, files: list[Path], options) -> tuple[str, list[Path]]:
    """Run coverage. Return its captured output and the reports it left unchanged."""

    languages = {language_of(path) for path in files}
    previous = _report_states(root, languages)
    if options.verbose:
        code, log = run_coverage(root, files, options.coverage_command), ""
    else:
        code, log = _quietly(run_coverage, root, files, options.coverage_command)
    current = _report_states(root, languages)
    stale = [path for path, state in current.items() if previous.get(path) == state]
    if code != 0 or len(stale) == len(current):
        print(log, end="", file=sys.stderr)
    if code != 0:
        raise ValueError("Coverage command failed.")
    if len(stale) == len(current):
        raise ValueError(
            "Coverage command wrote no new report."
            + "".join(f"\nUnchanged: {path}" for path in stale)
        )
    return log, stale


@contextlib.contextmanager
def _set_aside(reports: list[Path]):
    """Hide unchanged reports from the engines, which read every report on disk."""

    # shortcut: a killed process leaves `<report>.ub-stale`; restore it at startup if that bites.
    moved = []
    try:
        for report in reports:
            aside = report.with_name(report.name + ".ub-stale")
            report.rename(aside)
            moved.append((aside, report))
        yield
    finally:
        for aside, report in moved:
            if report.exists():
                aside.unlink()
            else:
                aside.rename(report)


def _gate(summary: str, failures: list[str]) -> None:
    print(summary)
    for failure in failures:
        print(f"  {failure}")


def _verdict(failures: list[str], incomplete: list[str]) -> int:
    for message in incomplete:
        print(message, file=sys.stderr)
    if incomplete:
        print(f"Check incomplete after {len(failures)} failing items.")
        return 2
    if failures:
        print(f"Gates failed with {len(failures)} failing items.")
        return 1
    print("All ub-mode quality gates passed.")
    return 0


def _static_gates(root: Path, files, duplicates_files, bundle, options, coverage_log: str):
    """Report CRAP and duplication. Return their failures and incomplete reasons."""

    entries, measured, incomplete = _analyze(root, files, bundle)
    write_crap(entries, root)
    if measured and not any(entry.coverage for entry in measured):
        incomplete.append(
            "Coverage reports show no executed lines in any analyzed function. "
            "Did the coverage command run the tests?"
        )
        measured = []
    if incomplete:
        print(coverage_log, end="", file=sys.stderr)
    if options.verbose:
        print(format_report(entries), end="")
    crap = _crap_failures(measured)
    _gate(f"CRAP: {len(crap)} of {len(entries)} functions >= 10", crap)

    normalized, warnings = scan_files(duplicates_files, root, min_lines=4, min_nodes=20)
    candidates = find_duplicates(normalized, 0.8)
    write_dry(candidates, root)
    if options.verbose:
        print(format_text(candidates), end="")
    dry = _dry_failures(candidates)
    _gate(f"Duplication: {len(dry)} pairs >= 0.8", dry)
    return crap + dry, incomplete + warnings


def _mutation_gate(root: Path, files, bundle, runner, baselines, options):
    forms = []
    incomplete = []
    for path in files:
        result = mutate_file(
            path,
            root,
            runner=runner,
            covered_lines=_mutation_coverage(root, path, bundle),
            ignore_coverage=False,
            mutate_all=True,
            lines=None,
            test_command=options.test_command,
            timeout_factor=10.0,
            mutation_warning=50,
            baselines=baselines,
            max_workers=options.max_workers,
        )
        if result.skipped or result.baseline_failed:
            incomplete.append(result.skipped or result.baseline_message)
            continue
        if options.verbose:
            print(format_site_log(result.sites, result.statuses), end="")
        forms.extend(result.forms)
    if options.verbose:
        print(format_results(forms), end="")
    failures = _mutation_failures(forms)
    killed = sum(form.killed for form in forms)
    survived = sum(form.survived for form in forms)
    uncovered = sum(form.uncovered for form in forms)
    _gate(f"Mutation: {killed} killed, {survived} survived, {uncovered} uncovered", failures)
    return failures, incomplete


def _gates(root: Path, files, duplicates_files, options, coverage_log: str) -> int:
    runner = CommandRunner()
    baselines = _baselines(root, files, runner, options.test_command)
    bundle = load_bundle(root)
    failures, incomplete = _static_gates(
        root, files, duplicates_files, bundle, options, coverage_log
    )
    if incomplete:
        return _verdict(failures, incomplete)
    mutation_failures, incomplete = _mutation_gate(root, files, bundle, runner, baselines, options)
    return _verdict(failures + mutation_failures, incomplete)


def _check(options) -> int:
    root = options.root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    for path in restore_backups(root):
        print(f"Restored {path} from an interrupted mutation.", file=sys.stderr)
    roots = [(root / source).resolve() for source in options.source_root] or [root]
    files = iter_source_files(roots)
    duplicates_files = dry_files(roots)
    if not files and not duplicates_files:
        raise ValueError("No supported source files found.")
    if options.use_existing_coverage:
        return _gates(root, files, duplicates_files, options, "")
    coverage_log, stale = _generate_coverage(root, files, options)
    with _set_aside(stale):
        return _gates(root, files, duplicates_files, options, coverage_log)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ub check",
        description="Check a whole project for CRAP >= 10, duplication >= 0.8, "
        "surviving mutants, and uncovered mutation sites. Every mutant is rerun.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root. Defaults to the current directory.",
    )
    parser.add_argument(
        "-s",
        "--source-root",
        action="append",
        default=[],
        metavar="PATH",
        help="Walk this tree, relative to the root, instead of the whole root. May be repeated.",
    )
    coverage = parser.add_mutually_exclusive_group()
    coverage.add_argument(
        "--use-existing-coverage",
        action="store_true",
        help="Read coverage reports already on disk instead of generating them.",
    )
    coverage.add_argument(
        "--coverage-command",
        help="Shell command that writes coverage reports. Defaults to each language's runner.",
    )
    parser.add_argument(
        "--test-command",
        help="Shell command that runs the tests on the original code and each mutant. "
        "Defaults to each language's runner.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Run at most this many mutants of one file at once. Defaults to one per core.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full reports and every mutant, and stream the coverage command's output.",
    )
    try:
        options = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code
    if options.max_workers is not None and options.max_workers < 1:
        print("--max-workers requires a positive integer", file=sys.stderr)
        return 2
    try:
        return _check(options)
    except (OSError, ValueError, ParserError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
