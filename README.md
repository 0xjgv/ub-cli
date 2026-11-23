# ub-cli

Run the three [ub-mode](https://github.com/unclebob/ub-mode) quality tools from one CLI.

## Install

The native release targets macOS and Linux on arm64 and amd64. It packages the
`ub` runtime. Your project still needs its own test runner and coverage tools.

Install or update with the latest release's installer:

```sh
curl -fsSL https://github.com/0xjgv/ub-cli/releases/latest/download/install.sh | sh
```

The installer selects the archive for your machine, verifies its SHA-256 against
the release manifest, and installs `ub` in `~/.local/bin`. When
[minisign](https://jedisct1.github.io/minisign/) is installed, it first verifies
the manifest's signature (`checksums.txt.minisig`) against this public key:

```text
RWTLb536jI8+3VG6zclr5kbCi69IAiL7HkhEefxbOJSlJRl2v9muK6ow
```

Put `~/.local/bin` on your `PATH`. Set `UB_VERSION` to pin a release or
`UB_INSTALL_DIR` to choose the destination:

```sh
curl -fsSL https://github.com/0xjgv/ub-cli/releases/latest/download/install.sh | UB_VERSION=vX.Y.Z UB_INSTALL_DIR="$HOME/bin" sh
ub --version
```

After a native install, run `ub update` to replace the running executable
in its install directory with the latest release, or `ub update vX.Y.Z` for a
specific one. It runs the installer bundled in the executable and always
verifies the manifest's signature with the key embedded there, so v0.2.0 and
earlier releases, which are unsigned, cannot be installed this way. It exits
without downloading when that release is already installed. For Python
installations, upgrade with the package manager you used to install `ub-cli`.

For a manual install, download your platform archive, `checksums.txt`, and
`checksums.txt.minisig` from [GitHub Releases](https://github.com/0xjgv/ub-cli/releases).
Run `minisign -V -P <key above> -m checksums.txt`, compare the archive's
`shasum -a 256` (or `sha256sum`) with the entry for its filename, and extract
`ub` into a directory on your `PATH`. To uninstall, remove that executable.

To install from a source checkout instead, run `make install` with uv
available, or `python -m pip install .` in a Python 3.11+ virtual environment.

## Install the agent skill

```sh
ub skill install
```

Codex installs to `~/.agents/skills/ub-mode/` and Claude to
`~/.claude/skills/ub-mode/`. Add `--target codex` or `--target claude` to
install for one agent.
An identical install succeeds without rewriting files. Add `--force` to
replace a locally edited skill or license. Invoke `$ub-mode` in Codex or
`/ub-mode` in Claude.

The installer works offline. It includes the upstream MIT license and a copy of
the skill adapted to call the three `ub` subcommands.
[Upstream revision](https://github.com/unclebob/ub-mode/tree/3da62bf3fcfd2034de41f30d920d2739e566171f)

## Set up a repository

Run from the repository root:

```sh
ub init -s src
./.ub/check
```

Use the source directory for your project, repeat `-s` for multiple trees, or
omit it to check the whole project. `ub init` creates an executable `.ub/check`
that runs `ub check`, writes `.ub/ratchet` with the current escape-hatch counts
(see [Escape-hatch ratchet](#escape-hatch-ratchet)) unless one exists, adds a managed instruction block to `AGENTS.md` and
`CLAUDE.md`, and ignores generated metrics and mutation-worker directories.
It does not run tests or install hooks. Review and commit these files so new
agent sessions and teammates use the same workflow. Other instructions are
preserved, including in-repository symlinks between instruction files.

For custom runners, pass `--coverage-command`, `--test-command`, and optionally
`--max-workers` to `ub init`, as you would to `ub check`. The commands are saved
as literal arguments in `.ub/check`; coverage must use a supported report
location. The script locates its project root even when invoked from elsewhere.
Edit `.ub/check` to change the commands later. Repeating `ub init` preserves
that script; conflicting explicit command options fail instead of replacing it.
`--root PATH` initializes a different project directory.

Installing the personal skill makes the workflow discoverable. The repository's
instruction files remind agents to run `./.ub/check` after production-code or
test changes and report any unresolved failures. CI should run the same command
after installing `ub` and the project's test and coverage tools. Start with a
reporting job to understand findings and runtime, then make the job required in
your branch rules. `ub init` does not change CI configuration or branch rules.

## Install hooks

After initializing and validating the check:

```sh
ub hooks install                        # Git pre-push
ub hooks install --target codex          # .codex/hooks.json, Stop event
ub hooks install --target claude         # .claude/settings.json, Stop event
```

Repeat `--target` to select several hooks. Installation is explicit and repeatable;
`ub skill install` does not install hooks. Git must be available, and `ub` must
be on the PATH used by Git and your agent. Installation from a subdirectory
uses the Git worktree root. `--root PATH` selects another repository.

The Git hook first runs `ub ratchet --guard` on each pushed branch (see
[Escape-hatch ratchet](#escape-hatch-ratchet)). The guard runs from the
installed `ub`, not from `.ub/check`, which the pushed commits could edit. Hooks
installed by earlier releases lack the guard; rerun `ub hooks install` to
upgrade the block. The hook then runs `.ub/check`, then the existing shell hook, preserving its
path, arguments, stdin, and failing exit status. A copy of the original is kept
beside it as `pre-push.ub-original`. Git's configured `core.hooksPath`
is respected when it is inside the repository; external shared directories and
symlinked or non-shell hooks require manual integration. Hooks in Git's common directory
also apply to linked worktrees: each must contain `.ub/check`. Git hooks are
local to the clone and need installing in each clone.

Agent installation preserves other settings and hook handlers. Restart the agent
and inspect `/hooks`; Codex requires reviewing and trusting the new hooks.
The hooks use the [Codex](https://learn.chatgpt.com/docs/hooks) and
[Claude Code](https://code.claude.com/docs/en/hooks) Stop-event contracts. Each
completion runs the configured check, even on turns without edits, so select a
scope and test command whose runtime fits interactive use. The default check
timeout is 120 seconds; set `ub hooks install --target codex --timeout 300` (or
`--target claude`) for a longer budget. Edit an existing handler to change it.
On timeout, the check and its descendants (including detached test sessions)
are terminated and the agent receives a failure. A missing `ub` also blocks
completion. Cancellation uses the system's `ps` command on macOS and Linux.

Failed checks return a blocking decision with the diagnostic tail. A repeated
Stop continuation (`stop_hook_active`) skips the check to avoid an endless loop
and asks the agent to report unresolved failures. This is feedback, not proof
that the check passed: required CI remains the final gate. Host-level hook
disablement or timeout may prevent enforcement; review the agent's hook status.

To remove a Git hook, delete its `# ub:begin` / `# ub:end` block. Alternatively,
if the hook has no subsequent edits, restore `pre-push.ub-original` (or remove
the generated hook if there was no original). To remove
an agent hook, delete only the `ub hooks run stop ...` handler from its JSON file.
Keep other hooks and settings.

## Usage

Run these commands in the project you want to analyze.

```sh
ub crap --changed
ub dry --threshold 0.8
ub mutate --scan --no-coverage
ub check --root .
```

The first three commands forward arguments and exit status to the
corresponding engine. Use `ub COMMAND --help` for its options; its usage line
names `ub COMMAND` instead of the engine. Coverage and
mutation runs use the target project's toolchain. Upstream coverage runners can
install missing coverage tools. To read coverage already collected by your CI,
use `ub check --use-existing-coverage` or `ub crap --use-existing-coverage`.
When the default Python run uses `unittest discover` and discovery from the
project root would find no `test*.py`, ub points it at a `tests/` or `test/`
directory, which plain discovery skips when it has no `__init__.py`.

By default each command prints a summary line and the items that need work:
`ub crap` lists functions with CRAP ≥ 10, or above `--threshold` (complexity
≥ 10 with `--no-coverage`), `ub dry` one line per duplicate pair, and
`ub mutate` the surviving and uncovered mutants. `ub crap` and `ub dry` show
the first 10 items; `ub mutate --scan` prints one line per file with sites,
saying `coverage unknown` when no report covers the file. A CRAP line reads
`18.0 CRAP, 8 CC, 46% covered: pkg.module/function` and a mutant line
`src/pkg/module.py:109 survived > -> >=`; `ub check` prints the same lines.
Coverage output stays hidden unless a coverage command fails. Pass `--verbose`
for the engine's full report, every mutant, and the coverage output.

ub exits 2 with one cause line where the engine would report nothing useful:
when a whole-project `ub crap` reads coverage in which no analyzed function
ran a line (the message names the custom or Python coverage command that ran),
and when a path or filter argument matches no source file (except
with `--changed`). A bare run on a project without source files still exits 0.

## Escape-hatch ratchet

Agents can pass a gate by switching a check off instead of fixing the code.
`ub ratchet` counts those escape hatches in every file Git tracks or would
track (`git ls-files -co --exclude-standard`), tests included, whatever `-s`
selects. Each kind has its own count:

| Language | Keys |
| --- | --- |
| Python | `lint.noqa` (also `# ruff: noqa`, `# flake8: noqa`, any case), `lint.type_ignore` (also `# mypy: ignore-errors`), `lint.pyright_ignore` (also `# pyright: basic`), `coverage.pragma_no_cover`, `skip.unittest` (`@unittest.skip`, `@skipIf`, `@skipUnless`, `@expectedFailure`, `self.skipTest(`, `SkipTest(`), `skip.pytest` (`mark.skip`, `mark.skipif`, `mark.xfail`, `pytest.skip(`, `pytest.xfail(`, `pytest.importorskip(`) |
| JS/TS, Vue, Svelte | `lint.eslint_disable`, `lint.ts_ignore`, `lint.ts_expect_error`, `lint.ts_nocheck`, `lint.biome_ignore`, `coverage.istanbul_ignore`, `coverage.c8_ignore`, `coverage.v8_ignore`, `skip.js` (`.skip`, `.only`, `.todo`, `.fixme`, `.fails`, `.skipIf` on `describe`/`it`/`test`/`suite`/`context`, `xit(`, `fit(`, `xdescribe(`, `fdescribe(`, `this.skip(`) |
| Java | `skip.no_mutate_tag` (`@Tag("no-mutate")`, which the mutation tests exclude) |

Lint and coverage patterns match only in comments; skips match decorator and
call syntax. Python string literals are blanked before matching, so a quoted
directive does not count. Each occurrence counts, including two on one line.
Symlinks are not followed.

`.ub/ratchet` sits at the Git worktree root and holds one `key count` line per
kind, plus optional `exclude GLOB` lines for vendored or generated files that
should not be counted (`fnmatch` rules on the repository-relative path; `*`
crosses directories, so `exclude vendor/*` skips the whole tree). `ub check`
runs the ratchet before coverage and fails when any count exceeds its line; the
other gates still run and report. The file stores counts, not locations, so a
failure lists the kind's matches in files with uncommitted changes, the likely
new ones, and the number elsewhere; with none in changed files, as in CI, it
lists every match. It ends with how to remove or accept them. Equal or lower
counts pass. Without `.ub/ratchet` the counts are report-only, and outside
a Git repository the ratchet is skipped. A malformed or symlinked `.ub/ratchet`,
or a failed file listing, makes the check incomplete. `ub ratchet` runs the
same comparison alone.

Raising a count or adding an exclude belongs to a human: run
`ub ratchet --update`, which writes the current counts and keeps the excludes,
and commit the file. `ub ratchet --lower` only lowers counts to the current
ones and never raises one or adds an exclude, so agents may run it after
removing escape hatches; otherwise each removal leaves room for a free new one.
The managed agent instructions tell agents to lower the counts and never to run
`--update` or edit `.ub/` unless asked.

`ub ratchet --guard --base REF [--head REF]` fails when the commits since the
merge base of the head (default `HEAD`) and `REF` change `.ub/check` (content
or mode), delete `.ub/ratchet`, raise any of its counts, or add an exclude, and when the
worktree's `.ub/check` or `.ub/ratchet` differs from `HEAD`. Adding them to a
repository that had none, and lowering counts, pass. Set `UB_ALLOW_UB_CHANGE=1`
to accept a reviewed change. The guard exits 2 when it cannot find the merge
base, as in a shallow clone, or when a committed `.ub/ratchet` is malformed or
not a regular file. Run it in CI as a separate step, not through `.ub/check`,
with full history:

```yaml
on:
  pull_request:
    # labeled and unlabeled rerun the guard when a human adds the approval label.
    types: [opened, synchronize, reopened, labeled, unlabeled]
# ...
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- run: ub ratchet --guard --base origin/main
  env:
    # A human approves a .ub/ change by labelling the pull request.
    UB_ALLOW_UB_CHANGE: ${{ contains(github.event.pull_request.labels.*.name, 'ub-change') && '1' || '' }}
- run: ./.ub/check
```

The pre-push hook guards each pushed branch against the remote's default
branch (`refs/remotes/<remote>/HEAD`), as CI does, or, without one, against the
remote's copy of the branch or the branch's upstream. With none of these it
exits 2 and asks for `git remote set-head <remote> --auto`, unless the remote
has no known branches yet, when it skips the guard with a note. The local hook
is advisory: an agent can skip it with `--no-verify`, set the override, or
change Git config. An agent that edits the CI workflow can remove the CI steps.
Put `/.ub/` and `/.github/workflows/` under
[CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
with required review; that review is the human gate. The Stop hook also warns
the human, without blocking, when `.ub/` has staged, unstaged, or untracked
changes, or files that differ from `HEAD`.

Known limits:

- Moving an escape hatch, by removing one and adding another of the same kind,
  keeps the count. So does widening one, such as `# noqa: E501` to `# noqa`.
- Configuration-file hatches are not counted: per-file lint ignores, coverage
  `omit`, pytest `addopts`, `collect_ignore`, `tsconfig` strictness.
- A directive inside a multi-line block comment, after its first line, is missed.
- Outside Python, strings and comments are not parsed: a quoted directive or a
  commented-out skip in JS/TS or Java counts.
- The Stop hook keeps the last 8,000 bytes of output. When no match of a grown
  kind is in a changed file, the full list is printed and can push the kind and
  counts out of the agent's view.
- Deleting a failing test and passing extra arguments to `./.ub/check` are not
  detected.
- Go, Rust, and Clojure patterns are not yet counted.

| `ub ratchet` status | Meaning |
| --- | --- |
| 0 | No count above `.ub/ratchet`, or the guard found no loosening (or `UB_ALLOW_UB_CHANGE=1`). |
| 1 | A count grew, or the guard found `.ub/check` changed, `.ub/ratchet` deleted, a count raised, an exclude added, or uncommitted `.ub/` changes. |
| 2 | Usage error, a malformed or symlinked `.ub/ratchet`, a failed file listing, `--update` or `--lower` outside Git, `--lower` without `.ub/ratchet`, or no merge base for the guard. |

## Combined gate

`ub check` checks the whole project, or the trees named with `-s PATH`. It
generates coverage once, verifies the unmutated tests pass, and reruns every
covered mutant so a previous kill cannot hide a weakened test. For faster
differential mutation runs, use `ub mutate`.

When the test command is a Python `python -m unittest discover` with no shell
operators, comment, or `-k`, each Python mutant first runs the test modules that
import its module, directly or through other project modules, one at a time:
nearest import first, then fastest on the original code. The first failure
kills the mutant. When all pass, the full test command reruns, so the verdict
matches a full-suite run. A test module that fails on the original code is
skipped.

By default it prints one summary line per gate, the items that failed (one
line per CRAP function, duplicate pair, and surviving or uncovered mutant, the
first 20 of each), and a verdict. The coverage command's output appears only when coverage fails or is
incomplete. Pass `--verbose` for the full CRAP table, duplicate list, and
mutant log, and to stream the coverage command's output.

| Status | Meaning |
| --- | --- |
| 0 | All gates passed. |
| 1 | A function scored CRAP ≥ 10, a pair scored similarity ≥ 0.8, a mutation site was uncovered or survived, or an escape-hatch count exceeded `.ub/ratchet`. |
| 2 | The check was incomplete, including missing coverage, coverage with no executed lines, a failing baseline, a malformed `.ub/ratchet`, or a usage error. |

The tools write their native reports to `.metrics/crap.edn`, `.metrics/dry.edn`,
and `.metrics/mutate/`. Standalone commands retain their upstream exit codes,
plus the status 2 cases above.

To use project-specific commands, pass `--coverage-command '...'` and
`--test-command '...'`. The coverage command must produce a report in an
upstream-supported location. A generation run that leaves every report
unchanged is incomplete and names the paths. Reports it left unchanged beside a
fresh one are set aside as `<report>.ub-stale` during the check, so only fresh
coverage is read. Use `--use-existing-coverage` when reuse is intentional. The
test command runs first on the original code and then against mutations.
Reference the source relative to the working directory so the tests exercise
the mutated copy.

## Development and releases

Run `make test` for the test suite and `make build-native` to produce the
archive for the current machine under `dist/`. `make build` builds the Python
wheel. `make gate` runs this checkout's `ub check` on `src/`. Its coverage run
also measures the child processes the hook and init tests start
(`scripts/gate-coverage.toml`), so that code counts as covered. The CI workflow
runs `make test` and the vendored-tree check below as the `test` job, and
`make gate` as the `gate` job, on every pull request and every push to `main`.
The gate leaves out `scripts/`, the release tooling. Its one script with real
logic, `scripts/vendor.py`, has its import rewriting unit-tested and its full
re-vendor run by the vendored-tree check on every pull request.

[Crapper](https://github.com/unclebob/crapper),
[Dryer](https://github.com/unclebob/dryer), and
[Mutator](https://github.com/unclebob/mutator) are vendored under
`src/ub_cli/vendor` at the commits in `src/ub_cli/vendor/VENDORED`, with their
imports rewritten to `ub_cli.vendor`. Never edit that directory by hand.
A fix not yet upstream goes in `scripts/vendor-patches/NAME-*.patch`, which
`scripts/vendor.py` applies after vendoring; delete the patch once the pin
includes the fix. Current patch: `mutator` decodes test output with
`errors="replace"`, so output that is not valid UTF-8 no longer crashes a run.
`make check-upstreams` compares the pins with upstream and prints the update
command, `python scripts/vendor.py NAME COMMIT`. The CI and Release workflows
fail when the directory differs from what `scripts/vendor.py` produces.

`make release [BUMP=patch|minor|major]` bumps the version in `pyproject.toml`
on a clean, up-to-date `main`, tags it, and pushes both. Rerunning it after an
interruption re-pushes the same tag. The Release workflow runs only when the
repository owner pushed or re-ran it:

1. Test and build each platform archive, and smoke-test the packaged CLI.
2. Assemble the archives, `install.sh`, and `checksums.txt`.
3. In the `release` environment, sign `checksums.txt` with the
   `MINISIGN_SECRET_KEY` secret, check the signature against the key in
   `install.sh`, and upload everything to a draft release.
4. On every platform, install from the draft with its `install.sh`, update
   with the new `ub`, and update from the previous published release.
5. Publish the draft.

A published release is never modified. Running the workflow manually builds
and tests the archives without creating a release.

## License

The ub-cli code uses the [BSD Zero Clause License](LICENSE), matching Crapper,
Dryer, and Mutator. Bundled third-party components retain their own licenses.
Run `ub --licenses` on a native installation to print the bundled notices.
