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

## Combined gate

`ub check` checks the whole project, or the trees named with `-s PATH`. It
generates coverage once, verifies the unmutated tests pass, and reruns every
covered mutant so a previous kill cannot hide a weakened test. For faster
differential mutation runs, use `ub mutate`.

By default it prints one summary line per gate, the items that failed (one
line per CRAP function, duplicate pair, and surviving or uncovered mutant, the
first 20 of each), and a verdict. The coverage command's output appears only when coverage fails or is
incomplete. Pass `--verbose` for the full CRAP table, duplicate list, and
mutant log, and to stream the coverage command's output.

| Status | Meaning |
| --- | --- |
| 0 | All gates passed. |
| 1 | A function scored CRAP ≥ 10, a pair scored similarity ≥ 0.8, or a mutation site was uncovered or survived. |
| 2 | The check was incomplete, including missing coverage, coverage with no executed lines, a failing baseline, or a usage error. |

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
wheel. The CI workflow runs `make test` and the vendored-tree check below on
every pull request and every push to `main`.

[Crapper](https://github.com/unclebob/crapper),
[Dryer](https://github.com/unclebob/dryer), and
[Mutator](https://github.com/unclebob/mutator) are vendored under
`src/ub_cli/vendor` at the commits in `src/ub_cli/vendor/VENDORED`, with their
imports rewritten to `ub_cli.vendor`. Never edit that directory by hand.
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
