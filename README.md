# ub-cli

Run the three [ub-mode](https://github.com/unclebob/ub-mode) quality tools from one CLI.

## Install

The native release targets macOS and Linux on arm64 and amd64. It packages the
`ub` runtime. Your project still needs its own test runner and coverage tools.

After a public release is available, install or update with:

```sh
curl -fsSL https://raw.githubusercontent.com/0xjgv/ub-cli/main/install.sh | sh
```

The installer selects the archive for your machine, verifies its SHA-256 against
the release manifest, and installs `ub` in `~/.local/bin`. Put that directory
on your `PATH`. Set `UB_VERSION` to pin a release or `UB_INSTALL_DIR` to
choose the destination:

```sh
curl -fsSL https://raw.githubusercontent.com/0xjgv/ub-cli/main/install.sh | UB_VERSION=v0.1.0 UB_INSTALL_DIR="$HOME/bin" sh
ub --version
```

After a native install, run `ub update` to replace the running executable
in its install directory with the latest release. It uses the same checksum
verification as the initial install. For Python installations, upgrade with
the package manager you used to install `ub-cli`.

For a manual install, download your platform archive and `checksums.txt` from
[GitHub Releases](https://github.com/0xjgv/ub-cli/releases), compare the archive's
`shasum -a 256` (or `sha256sum`) with the entry for its filename, and extract
`ub` into a directory on your `PATH`. To uninstall, remove that executable.

To install from a source checkout, run `make install` with Git and uv available,
or use `python -m pip install .` in a Python 3.11+ virtual environment.

## Install the agent skill

```sh
ub skill install ub-mode --target codex
ub skill install ub-mode --target claude
```

Codex installs to `~/.agents/skills/ub-mode/` and Claude to
`~/.claude/skills/ub-mode/`. Run the command for each agent you use.
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

The first three commands forward arguments, output, and exit status to the
corresponding engine. Use `ub COMMAND --help` for its options. Coverage and
mutation runs use the target project's toolchain. Upstream coverage runners can
install missing coverage tools. To read coverage already collected by your CI,
use `ub check --use-existing-coverage`.

## Combined gate

`ub check` checks the whole project. It generates coverage once, verifies the
unmutated tests pass, and reruns every covered mutant so a previous kill cannot
hide a weakened test. For faster differential mutation runs, use `ub mutate`.

| Status | Meaning |
| --- | --- |
| 0 | All gates passed. |
| 1 | A function scored CRAP ≥ 10, a pair scored similarity ≥ 0.8, or a mutation site was uncovered or survived. |
| 2 | The check was incomplete, including missing coverage, a failing baseline, or a usage error. |

The tools write their native reports to `.metrics/crap.edn`, `.metrics/dry.edn`,
and `.metrics/mutate/`. Standalone commands retain their upstream exit codes.

To use project-specific commands, pass `--coverage-command '...'` and
`--test-command '...'`. The coverage command must produce a report in an
upstream-supported location. A generation run reports unchanged coverage inputs
as incomplete and names the paths. Use `--use-existing-coverage` when reuse is
intentional. The test command runs first on the original code
and then against mutations. Reference the source relative to the working
directory so the tests exercise the mutated copy.

## Development and releases

Run `make test` for the test suite, `make check-upstreams` to compare engine
pins, and `make build-native` to produce the archive for the current machine
under `dist/`. `make build` builds the Python wheel.

Before tagging, run the Release workflow manually and inspect its build
artifacts. Each platform must pass the packaged CLI smoke test. To prepare a
GitHub release, set the version in `pyproject.toml`, merge the tested revision
to `main`, and push its matching tag (for example, `v0.1.0`). Inspect the
four platform archives, installer, and SHA-256 manifest in the draft before
publishing it. Public installation requires a public repository and a published
release.

## License

The ub-cli code uses the [BSD Zero Clause License](LICENSE), matching Crapper,
Dryer, and Mutator. Bundled third-party components retain their own licenses.
Run `ub --licenses` on a native installation to print the bundled notices.
