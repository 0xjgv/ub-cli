"""Run the pinned engines against the ub-mode quality gates."""

from __future__ import annotations

import argparse
import sys
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


def _analyze(root: Path, files: list[Path], bundle) -> tuple[list, list[str]]:
    bundle.bind_sources([path.relative_to(root).as_posix() for path in files])
    entries = []
    missing = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        functions = functions_in_file(language_of(path), source, relative, str(root))
        for function in functions:
            coverage = bundle.percent_for(function)
            if coverage is None:
                missing.append(f"Missing coverage for {relative}:{function.start_line}")
            entries.append(make_entry(function, coverage if coverage is not None else 0.0))
    return sort_entries(entries), missing


def _report_states(root: Path, languages: set[str]) -> dict[Path, tuple[int, int, int]]:
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
    states = {}
    for pattern in patterns:
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


def _check(options) -> int:
    root = options.root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project root is not a directory: {root}")
    for path in restore_backups(root):
        print(f"Restored {path} from an interrupted mutation.", file=sys.stderr)
    files = iter_source_files([root])
    duplicates_files = dry_files([root])
    if not files and not duplicates_files:
        raise ValueError("No supported source files found.")
    if not options.use_existing_coverage:
        languages = {language_of(path) for path in files}
        previous_reports = _report_states(root, languages)
        if run_coverage(root, files, options.coverage_command) != 0:
            raise ValueError("Coverage command failed.")
        current_reports = _report_states(root, languages)
        stale = [path for path, state in current_reports.items() if previous_reports.get(path) == state]
        if stale:
            raise ValueError("Coverage reports were not regenerated:\n" + "\n".join(map(str, stale)))
    runner = CommandRunner()
    baselines = _baselines(root, files, runner, options.test_command)
    bundle = load_bundle(root)
    entries, missing = _analyze(root, files, bundle)
    write_crap(entries, root)
    print(format_report(entries), end="")
    failures = _crap_failures(entries)

    normalized, warnings = scan_files(duplicates_files, root, min_lines=4, min_nodes=20)
    candidates = find_duplicates(normalized, 0.8)
    write_dry(candidates, root)
    print(format_text(candidates), end="")
    failures.extend(_dry_failures(candidates))
    for message in missing + warnings:
        print(message, file=sys.stderr)
    if missing or warnings:
        return 2

    forms = []
    incomplete = False
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
            print(result.skipped or result.baseline_message, file=sys.stderr)
            incomplete = True
            continue
        print(format_site_log(result.sites, result.statuses), end="")
        forms.extend(result.forms)
        for form in result.forms:
            if form.survived or form.uncovered:
                failures.append(
                    f"{form.file}:{form.line} has {form.survived} surviving "
                    f"and {form.uncovered} uncovered mutants"
                )
    if forms:
        print(format_results(forms), end="")
    for failure in failures:
        print(failure, file=sys.stderr)
    if incomplete:
        return 2
    if failures:
        return 1
    print("All ub-mode quality gates passed.")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ub check",
        description="Check a whole project for CRAP >= 10, duplication >= 0.8, "
        "surviving mutants, and uncovered mutation sites. Every mutant is rerun.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    coverage = parser.add_mutually_exclusive_group()
    coverage.add_argument("--use-existing-coverage", action="store_true")
    coverage.add_argument("--coverage-command")
    parser.add_argument("--test-command")
    parser.add_argument("--max-workers", type=int, default=None)
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
