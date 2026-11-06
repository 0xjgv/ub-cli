#!/bin/sh
# Exercise the packaged executable outside the source checkout.
set -eu
ub=${1:?usage: sh scripts/smoke-release.sh /absolute/path/to/ub}
case "$ub" in /*) ;; *) echo "Expected an absolute executable path" >&2; exit 2 ;; esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"
unset PYTHONPATH
cat > arithmetic.py <<'PY'
def add(a, b):
    return a + b
PY
cat > test_arithmetic.py <<'PY'
import unittest
from arithmetic import add

class ArithmeticTests(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
PY
mkdir -p target/coverage/python
printf '%s\n' SF:arithmetic.py DA:1,1 DA:2,1 end_of_record > target/coverage/python/lcov.info
"$ub" --version
"$ub" --licenses > notices.txt
grep -F 'BSD Zero Clause License' notices.txt >/dev/null
grep -F 'Robert C. Martin' notices.txt >/dev/null
"$ub" crap --use-existing-coverage
"$ub" dry
"$ub" mutate --scan --no-coverage
"$ub" check --use-existing-coverage --max-workers 1
grep -q ':killed 1' .metrics/mutate/arithmetic.edn
# A test-only change must turn the previously killed mutant into a survivor.
printf '%s\n' 'import unittest' 'class Tests(unittest.TestCase):' '    def test_weak(self): pass' > test_arithmetic.py
status=0
"$ub" check --use-existing-coverage --max-workers 1 || status=$?
[ "$status" -eq 1 ]
grep -q ':survived 1' .metrics/mutate/arithmetic.edn

# Exercise the packaged parsers without needing the projects' build toolchains.
mkdir languages
cd languages
printf '%s\n' 'package calc' 'func SumGo(a, b int) int { return a + b }' > calc.go
printf '%s\n' 'class Calc { int sumJava(int a, int b) { return a+b; } }' > Calc.java
printf '%s\n' 'pub fn sum_rust(a: i32, b: i32) -> i32 { a+b }' > calc.rs
printf '%s\n' 'export function sumJs(a, b) { return a+b; }' > calc.js
printf '%s\n' 'export function sumTs(a: number, b: number) { return a+b; }' > calc.ts
printf '%s\n' 'export function viewTsx() { return <div/>; }' > view.tsx
printf '%s\n' '(ns calc)' '(defn sum-clj [a b] (+ a b))' > calc.clj
"$ub" crap --no-coverage > report.txt
for name in SumGo sumJava sum_rust sumJs sumTs viewTsx sum-clj; do
    grep -F "$name" report.txt >/dev/null
done
"$ub" dry
"$ub" mutate --scan --no-coverage > sites.txt
grep -F 'calc.clj' sites.txt >/dev/null
