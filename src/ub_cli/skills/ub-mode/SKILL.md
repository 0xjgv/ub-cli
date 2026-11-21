---
name: ub-mode
description: >
  Use the ub CLI for code complexity and coverage (ub crap), duplication
  (ub dry), and mutation testing (ub mutate). Use when working in a repository
  initialized with ub init, running UB quality checks, or when the user invokes
  ub-mode or asks for the reduced principles.
---

# ub-mode

Every time you are asked to make a change follow:
* First Make it Work.
* Then Make it Right.
* Follow Practice

## Repository workflow

* Read the repository's AGENTS.md or CLAUDE.md and use its configured checks.
* When `.ub/check` exists, run `./.ub/check` from the repository root after
  changing production code or tests, before declaring completion. It supplies
  the repository's scope, coverage command, and test command to `ub check`.
* Use focused `ub crap`, `ub dry`, and `ub mutate` commands while iterating.
  Prefer the repository's configured scope over a whole-project scan. Pass
  `--use-existing-coverage` only when coverage is current for the code under test.
* `--changed` selects working-tree changes from git status, not a PR's commits.
* For requested setup, `ub init` creates the shared check and persistent agent
  instructions. `ub hooks install` adds a Git pre-push hook; `--target codex`
  or `--target claude` adds a completion hook. Use `--help` for project options.
* A skill describes the workflow; installed hooks and required CI checks enforce
  it. Do not weaken checks or bypass hooks. Report remaining failures, missing
  tools, and checks that could not finish without claiming success.
* `.ub/ratchet` caps lint suppressions, coverage exclusions, and skipped tests.
  When `ub check` reports a count above it, remove the new escape hatch. After
  removing escape hatches, run `ub ratchet --lower` and commit `.ub/ratchet`.
  Never run `ub ratchet --update` or otherwise edit `.ub/` unless the user asks.
* Keep fixes within the requested task. This skill does not authorize unrelated
  cleanup, commits, or publication.

# First Make it Work

## Laziness Protocol

* One decision lives in one place.
* Ship the smallest edit that solves the problem.

## Foundational Thinking

* Get the data shape right before you write the logic.
* Do first whatever helps every later phase.
* If a concurrent writer would matter, do not share state.

## TDD

* Write the failing test before you change the code.
* See it fail for the reason you expect.
* Make the smallest change that makes it pass.
* If an honest test is impractical, do not write a bad one.

## Redesign From First Principles

* Do not bolt a new requirement onto the old design.
* Ask what you would have built if it had been there from the start, think that through, and deliver it in pieces.

## Attack the Premise

* When two fixes that share one premise fail the same gate, write that premise in one sentence and count which actors hold the imbalance.
* Remove the assignment.
* Do not start another fix until you have done that.

## Subtract Before You Add

* Take complexity out before you add anything.

## Outcome-Oriented Execution

* Aim at the end state you can check.
* A break in the middle is allowed when you planned it and can undo it.

## Build the Lever

* If the work is not obvious at a glance, build the tool that does it.
* Do one unit by hand, then make the tool match that unit.

## Architecture

### Model the Domain

* Put the domain in a structure that makes the illegal states impossible.
* Maintain a glossary of domain terms.
* If the code is already clear and local, leave it.

### Boundary Discipline

* Check the data where it enters.
* Inside, trust it.
* Keep the business logic in a pure function.

### Type System Discipline

* Make the illegal value impossible to write.
* Parse outside data once, where it comes in.
* Do not silence the compiler.

### Make Operations Idempotent

* Running it twice, or running it after a crash halfway through, ends in the same state.

### Separate Before Serializing Shared State

* If they do not need the same mutable thing, give each its own.
* If they do, allow one writer, and enforce that in the structure.

# Then Make it Right

## Fix Root Causes

* Reproduce it.
* Fix the cause.
* A guard that only hides the crash is not a fix.

## Sequence work into verifiable units

* Finish one small unit and check it.
* Then start the next.

## Test Behavior, Not Implementation

* The test calls the code the way its user does and checks the result that user would see.
* If the test would still pass when every function returned nothing, throw it out.

## Crapper

* Run `ub crap` and inspect every function scoring 10 or higher.
* Reduce each function under  CRAP 10 by splitting it, by testing it, or both.
* Consider whether case statements are worth reducing.

## Dryer

* Run `ub dry --threshold 0.8` and eliminate duplication scoring 0.8 or above.

## Mutator

* Run `ub mutate` for mutation testing.
* Cover the uncovered.
* Kill the survivors.
* Delete dead code.

## Refactoring

* Pin what the code does.
* Change the structure.
* Prove the behavior did not change.

# Practice

## Keep a Log

* Keep a log of every action you take and the reason you took it.
* Report that log upon completion.

## Guard the Context Window

* Keep the main thread small.
* Send the bulk elsewhere, and keep the summary.

## Worktree Cleanup

* Find what is safe to remove, confirm nothing still in use needs it, then reclaim the disk.
* Uncommitted work waits for a decision.

## Commit and Push

* Commit or push only when authorized by the user and repository policy.
* When committing, use small ordered commits that say why, what changed, and how you proved it.
* Follow the repository's branch and review policy.
