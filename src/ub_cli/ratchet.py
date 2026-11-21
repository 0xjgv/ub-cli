"""Count escape hatches and fail when any count grows past .ub/ratchet."""

import argparse
from fnmatch import fnmatchcase
import io
from itertools import accumulate
import os
from pathlib import Path
import re
import subprocess
import sys
import tokenize
from typing import NamedTuple

from ub_cli.project import git, local_file, write_file


RATCHET = ".ub/ratchet"
CHECK = ".ub/check"
OVERRIDE = "UB_ALLOW_UB_CHANGE"
HEADER = ("# Escape-hatch counts; ub check fails when one grows. `ub ratchet --lower` lowers them.\n"
          "# Only a human raises a count or adds an `exclude GLOB` line, with `ub ratchet --update`.\n")
ACCEPT = ("  Remove the new escape hatches. To accept them instead, a human runs ub ratchet --update,\n"
          f"  commits {RATCHET}, and approves the raise: {OVERRIDE}=1 to push, the approval label in CI.")
REGULAR = ("100644", "100755")

PYTHON = (".py", ".pyi")
SCRIPT = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte")
JS_COMMENT = r"(?://|/\*+)\s*"
JS_TEST = r"\b(?:describe|it|test|suite|context)(?:\s*\.\s*concurrent)?\s*\.\s*"

# One row per kind: its key, the file extensions it applies to, and a pattern anchored
# to comment, decorator, or call syntax. Each match counts once. In Python, `skip.`
# rules see code only and the others see code and comments (see `views`).
# Add a language by adding rows.
PATTERNS = [
    ("lint.noqa", PYTHON, r"(?i:#\s*(?:(?:ruff|flake8)\s*:\s*)?noqa)\b"),
    ("lint.type_ignore", PYTHON, r"#\s*(?:type:\s*ignore|mypy:\s*ignore-errors)\b"),
    ("lint.pyright_ignore", PYTHON, r"#\s*pyright:\s*(?:ignore|basic)\b"),
    ("coverage.pragma_no_cover", PYTHON, r"(?i:#\s*pragma[:\s]?\s*no\s*cover)"),
    ("skip.unittest", PYTHON,
     r"^\s*@\s*(?:unittest\s*\.\s*)?(?:skip(?:If|Unless)?|expectedFailure)\b|\.\s*skipTest\s*\(|\bSkipTest\s*\("),
    ("skip.pytest", PYTHON,
     r"\b(?:pytest\s*\.\s*)?mark\s*\.\s*(?:skip|skipif|xfail)\b|\bpytest\s*\.\s*(?:skip|xfail|importorskip)\s*\("),
    ("lint.eslint_disable", SCRIPT, JS_COMMENT + r"eslint-disable"),
    ("lint.ts_ignore", SCRIPT, JS_COMMENT + r"@ts-ignore\b"),
    ("lint.ts_expect_error", SCRIPT, JS_COMMENT + r"@ts-expect-error\b"),
    ("lint.ts_nocheck", SCRIPT, JS_COMMENT + r"@ts-nocheck\b"),
    ("lint.biome_ignore", SCRIPT, JS_COMMENT + r"biome-ignore\b"),
    ("coverage.istanbul_ignore", SCRIPT, JS_COMMENT + r"istanbul\s+ignore\b"),
    ("coverage.c8_ignore", SCRIPT, JS_COMMENT + r"c8\s+ignore\b"),
    ("coverage.v8_ignore", SCRIPT, JS_COMMENT + r"v8\s+ignore\b"),
    ("skip.js", SCRIPT,
     JS_TEST + r"(?:skip|only|todo|fixme|fails|skipIf)\b|(?<![.\w$])[xf](?:it|describe|test)\s*\(|\bthis\s*\.\s*skip\s*\("),
    ("skip.no_mutate_tag", (".java",), r'@Tag\s*\(\s*(?:value\s*=\s*)?"no-mutate"'),
]
RULES = [(key, suffixes, re.compile(pattern)) for key, suffixes, pattern in PATTERNS]
STRINGS = {tokenize.STRING} | {getattr(tokenize, name) for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE")
                               if hasattr(tokenize, name)}


def toplevel(root: Path) -> Path | None:
    try:
        return Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, ValueError):
        return None


class Baseline(NamedTuple):
    counts: dict[str, int]
    excludes: list[str]


def scan(top: Path, excludes=()) -> dict[str, list[str]]:
    """Each kind's `file:line` matches in the files Git tracks or would track under `top`.

    Files matching an `excludes` glob are skipped; `*` crosses directories.
    """

    command = ["git", "-C", str(top), "ls-files", "-z", "-co", "--exclude-standard"]
    listing = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if listing.returncode:
        raise ValueError(f"git ls-files failed: {listing.stderr.decode(errors='replace').strip()}")
    found = {key: [] for key, _, _ in RULES}
    # A conflicted index lists a path once per stage.
    for name in dict.fromkeys(filter(None, listing.stdout.split(b"\0"))):
        shown = name.decode(errors="replace")
        rules = [(key, pattern) for key, suffixes, pattern in RULES if shown.endswith(suffixes)]
        if any(fnmatchcase(shown, glob) for glob in excludes):
            continue
        path = top / os.fsdecode(name)
        # A path that resolves elsewhere passes through a symlink, which is not followed.
        if rules and path.is_file() and path.resolve() == path:
            _scan_file(path, shown, rules, found)
    return found


def views(path: Path) -> tuple[list[str], list[str]]:
    """The file's lines for directive rules and for skip rules.

    In Python, string literals are blanked from both, so quoted directives do not count,
    and comments are blanked from the second, so a commented-out skip does not count.
    """

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = io.StringIO(text).readlines()
    if path.suffix not in PYTHON:
        return lines, lines
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return lines, lines
    return blank(text, lines, tokens, STRINGS), blank(text, lines, tokens, STRINGS | {tokenize.COMMENT})


def blank(text: str, lines: list[str], tokens, types: set[int]) -> list[str]:
    starts = list(accumulate(map(len, lines), initial=0))
    pieces, last = [], 0
    for token in tokens:
        if token.type in types:
            start = starts[token.start[0] - 1] + token.start[1]
            end = starts[token.end[0] - 1] + token.end[1]
            pieces += [text[last:start], re.sub(r"[^\n]", " ", text[start:end])]
            last = end
    return io.StringIO("".join(pieces) + text[last:]).readlines()


def _scan_file(path: Path, name: str, rules, found: dict[str, list[str]]) -> None:
    directives, code = views(path)
    for number, (directive_line, code_line) in enumerate(zip(directives, code), 1):
        for key, pattern in rules:
            line = code_line if key.startswith("skip.") else directive_line
            found[key] += [f"{name}:{number}"] * len(pattern.findall(line))


def tally(found: dict[str, list[str]]) -> dict[str, int]:
    return {key: len(places) for key, places in found.items()}


def parse(text: str) -> Baseline:
    baseline = Baseline({}, [])
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        if len(words) == 2 and words[0] == "exclude":
            baseline.excludes.append(words[1])
        elif len(words) == 2 and words[1].isdigit():
            baseline.counts[words[0]] = int(words[1])
        else:
            raise ValueError(f"{RATCHET} line {number} is not 'key count' or 'exclude GLOB': {line}")
    return baseline


def render(counts: dict[str, int], excludes: list[str]) -> str:
    return (HEADER + "".join(f"exclude {glob}\n" for glob in excludes)
            + "".join(f"{key} {counts[key]}\n" for key in sorted(counts)))


def grown(current: dict[str, int], baseline: dict[str, int]) -> list[tuple[str, int, int]]:
    return [(key, count, baseline.get(key, 0)) for key, count in sorted(current.items())
            if count > baseline.get(key, 0)]


def ratchet_file(top: Path) -> Path:
    """The worktree's .ub/ratchet, which must be a regular file inside the repository if present."""

    if (top / RATCHET).is_symlink():
        raise ValueError(f"{RATCHET} must be a regular file, not a symlink.")
    return local_file(top, RATCHET)


def gate(root: Path) -> tuple[list[str], list[str]]:
    """Print the ratchet summary. Return one failure per kind that grew, and incomplete reasons."""

    top = toplevel(root)
    if top is None:
        print("Ratchet: skipped outside a Git repository.")
        return [], []
    present = os.path.lexists(top / RATCHET)
    try:
        baseline = parse(ratchet_file(top).read_text()) if present else None
        found = scan(top, baseline.excludes if baseline else ())
    except (OSError, ValueError) as exc:
        if present:
            print("Ratchet: incomplete.")
            return [], [f"Ratchet: {exc}"]
        print(f"Ratchet: report only without {RATCHET}; counting failed: {exc}")
        return [], []
    counts = tally(found)
    if baseline is None:
        print(f"Ratchet: {sum(counts.values())} escape hatches, report only without {RATCHET}.")
        return [], []
    rows = grown(counts, baseline.counts)
    if not rows:
        lower = any(counts.get(key, 0) < count for key, count in baseline.counts.items())
        hint = "; lower it with ub ratchet --lower" if lower else ""
        print(f"Ratchet: {sum(counts.values())} escape hatches, none above {RATCHET}{hint}.")
        return [], []
    return report_growth(top, found, rows), []


def report_growth(top: Path, found: dict[str, list[str]], rows) -> list[str]:
    """Print each grown kind with its likely new matches. Return one failure per kind."""

    print(f"Ratchet: {len(rows)} kinds above {RATCHET}")
    recent = changed(top)
    failures = []
    for key, now, then in rows:
        failures.append(f"{key}: {now} > {then}")
        print(f"  {failures[-1]}")
        shown = shown_places(found[key], recent)
        print("".join(f"    {place}\n" for place in shown), end="")
        if len(shown) < now:
            print(f"    ... and {now - len(shown)} in files without uncommitted changes")
    print(ACCEPT)
    return failures


def changed(top: Path) -> set[str] | None:
    """Files with uncommitted changes, untracked ones included, or None when that is unknown."""

    try:
        names = (git(top, "diff", "--name-only", "-z", "HEAD") + "\0"
                 + git(top, "ls-files", "-z", "-o", "--exclude-standard"))
    except ValueError:
        return None
    return set(filter(None, names.split("\0")))


def shown_places(places: list[str], recent: set[str] | None) -> list[str]:
    """The matches in changed files, the likely new ones; all of them when there are none.

    The ratchet stores counts, not locations, so it cannot say which match is new.
    """

    fresh = [place for place in places if place.rsplit(":", 1)[0] in (recent or ())]
    return fresh or places


def current(root: Path) -> tuple[Path, Baseline | None, dict[str, int]]:
    """The worktree root, its baseline if any, and the counts under the baseline's excludes."""

    top = toplevel(root)
    if top is None:
        raise ValueError("ub ratchet --update and --lower need a Git repository.")
    path = ratchet_file(top)
    baseline = parse(path.read_text()) if path.exists() else None
    return top, baseline, tally(scan(top, baseline.excludes if baseline else ()))


def update(root: Path) -> None:
    top, baseline, counts = current(root)
    write_file(ratchet_file(top), render(counts, baseline.excludes if baseline else []))
    print(f"Wrote {top / RATCHET}: {sum(counts.values())} escape hatches.")


def lower(root: Path) -> None:
    """Lower each count to the current one. Never raises a count or adds an exclude."""

    top, baseline, counts = current(root)
    if baseline is None:
        raise ValueError(f"No {RATCHET} to lower; a human creates it with ub ratchet --update.")
    lowered = {key: min(count, counts.get(key, 0)) for key, count in baseline.counts.items()}
    write_file(ratchet_file(top), render(lowered, baseline.excludes))
    print(f"Lowered {sum(baseline.counts.values()) - sum(lowered.values())} escape hatches in {top / RATCHET}.")


def entry(root: Path, revision: str, name: str) -> tuple[str, str] | None:
    """The Git mode and object id of `name` at `revision`, or None when absent."""

    listing = git(root, "ls-tree", "--full-tree", revision, "--", name)
    return tuple(listing.split("\t")[0].split()[::2]) if listing else None


def worktree_entry(root: Path, name: str) -> tuple[str, str] | None:
    path = root / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return ("not a regular file", name)
    if not path.exists():
        return None
    return ("100755" if os.access(path, os.X_OK) else "100644", git(root, "hash-object", "--", name))


def uncommitted(root: Path) -> list[str]:
    """The .ub/ files whose worktree copy, file type, or mode differs from HEAD."""

    try:
        git(root, "rev-parse", "--verify", "-q", "HEAD")
    except ValueError:
        head = {}
    else:
        head = {name: entry(root, "HEAD", name) for name in (CHECK, RATCHET)}
    return [name for name in (CHECK, RATCHET) if worktree_entry(root, name) != head.get(name)]


def committed_baseline(root: Path, item: tuple[str, str] | None) -> Baseline | None:
    if item is None:
        return None
    if item[0] not in REGULAR:
        raise ValueError(f"Committed {RATCHET} is not a regular file.")
    return parse(git(root, "cat-file", "blob", item[1]))


def tampering(root: Path, start: str, head: str) -> list[str]:
    """How `head`'s .ub/ loosens the gate relative to commit `start`."""

    problems = []
    check = entry(root, start, CHECK)
    if check is not None and check != entry(root, head, CHECK):
        problems.append(f"{CHECK} changed")
    before = committed_baseline(root, entry(root, start, RATCHET))
    after = committed_baseline(root, entry(root, head, RATCHET))
    if before is not None and after is None:
        problems.append(f"{RATCHET} was deleted")
    elif before is not None:
        problems += [f"{RATCHET} raised {key}: {then} -> {now}"
                     for key, now, then in grown(after.counts, before.counts)]
        problems += [f"{RATCHET} added exclude {glob}" for glob in after.excludes if glob not in before.excludes]
    return problems


def guard(root: Path, base: str, head: str = "HEAD") -> int:
    try:
        start = git(root, "merge-base", head, base)
    except ValueError as exc:
        print(f"ub ratchet: no merge base of {head} and {base}: {exc}\n"
              "Fetch the base branch with full history (actions/checkout fetch-depth: 0).",
              file=sys.stderr)
        return 2
    problems = tampering(root, start, head) + [f"{name} has uncommitted changes" for name in uncommitted(root)]
    if not problems:
        print(f"Guard: {head} does not loosen .ub/ since {base}.")
        return 0
    print(f"Guard: {head} against {base}:")
    for problem in problems:
        print(f"  {problem}")
    if os.environ.get(OVERRIDE) == "1":
        print(f"Guard: allowed by {OVERRIDE}=1.")
        return 0
    print(f"Guard failed: .ub/ changes need human review; a human may override with {OVERRIDE}=1.")
    return 1


def check(root: Path) -> int:
    failures, incomplete = gate(root)
    for reason in incomplete:
        print(reason, file=sys.stderr)
    return 2 if incomplete else 1 if failures else 0


def arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ub ratchet", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true",
                      help=f"Write the current counts to {RATCHET}, raising any that grew (a human's call)")
    mode.add_argument("--lower", action="store_true", help=f"Lower the counts in {RATCHET}; never raises one")
    mode.add_argument("--guard", action="store_true",
                      help="Fail when commits change .ub/check, delete .ub/ratchet, or raise a count")
    parser.add_argument("--base", metavar="REF", help="Compare with the merge base of the head and REF (--guard)")
    parser.add_argument("--head", metavar="REF", help="Commit to guard (default: HEAD)")
    options = parser.parse_args(argv)
    if options.guard != bool(options.base) or (options.head and not options.guard):
        parser.error("--guard needs --base REF; --base and --head need --guard.")
    return options


def run(argv: list[str]) -> int:
    try:
        options = arguments(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        root = toplevel(options.root) or options.root.resolve()
        if options.guard:
            return guard(root, options.base, options.head or "HEAD")
        if options.update or options.lower:
            (update if options.update else lower)(root)
            return 0
        return check(root)
    except (OSError, ValueError) as exc:
        print(f"ub ratchet: {exc}", file=sys.stderr)
        return 2
