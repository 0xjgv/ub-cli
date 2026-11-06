# ub-cli

Run the three [ub-mode](https://github.com/unclebob/ub-mode) quality tools from one CLI.

## Install

From this directory, run `uv tool install . --python 3.11`, or run
`python -m pip install .` in a Python 3.11+ virtual environment. Installation
requires Git and downloads the upstream engines at the revisions in
`pyproject.toml`. Run the installed command in the project you want to analyze.

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

## Tests

After installing the project in a virtual environment, run
`python -m unittest discover -s tests -v` from this directory.
