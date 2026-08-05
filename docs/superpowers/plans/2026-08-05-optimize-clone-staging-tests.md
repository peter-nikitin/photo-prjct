# Optimize Clone Staging Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan. Do not delegate any work.

**Goal:** Keep clone-staging safety coverage while reducing its default-suite wall time below 15 seconds.

**Architecture:** Register a strict `clone_staging_slow` pytest marker, exclude it in the normal Make targets, and provide a dedicated full-file target that does not apply the exclusion. Apply the marker only to exhaustive variants; retain the accepted critical-path scenarios in the default selection.

**Tech Stack:** GNU Make, pytest 9, Python 3.12, POSIX shell tests.

## Global Constraints

- Do not modify `scripts/clone-staging-db.sh` behavior.
- Default selection must retain confirmation, local Docker boundary, dump validation, exact replacement, recovery, and Django validation coverage.
- The full clone module must remain runnable with one Make target.
- Implementer and reviewer subagents must not modify Git index, history, branches, tags, or remotes.
- The implementer must leave changes unstaged and perform its own work without spawning another agent.

---

### Task 1: Split default and exhaustive clone-staging tests

**Files:**
- Modify: `Makefile`
- Modify: `pyproject.toml`
- Modify: `tests/deployment/test_clone_staging_database.py`
- Modify: `tests/test_repository_foundation.py`
- Modify: `README.md`

**Interfaces:**
- `make test` and `make check` exclude `clone_staging_slow`.
- `make test-clone-staging` runs all tests in `tests/deployment/test_clone_staging_database.py` without the default marker exclusion.

- [ ] **Step 1: Add failing repository-contract assertions**

  Require the strict marker registration, default Make exclusions, the full clone target, and README documentation. Run the focused repository-foundation selectors and confirm they fail because these interfaces do not exist yet.

- [ ] **Step 2: Add the minimal selection configuration**

  Register `clone_staging_slow` in `pyproject.toml`; update `make test` and the pytest portion of `make check` to exclude it; add `test-clone-staging` that runs the complete file without exclusion.

- [ ] **Step 3: Classify the existing test functions**

  Mark exhaustive failure matrices, signal timing cases, publication-rename cases, hostile control-character matrices, and concurrency locking cases as `clone_staging_slow`. Leave a critical set unmarked that directly proves confirmation, local-only Docker targeting, validated dump publication, exact replacement, successful recovery, and read-only Django validation.

- [ ] **Step 4: Document the commands**

  Explain briefly in README that normal test commands run the critical clone contract and `make test-clone-staging` runs the exhaustive clone suite.

- [ ] **Step 5: Verify behavior and timing**

  Run the focused repository-contract tests, default-selected clone tests with durations, and the complete clone target. Confirm default-selected clone time is at most 15 seconds and the complete module passes.

- [ ] **Step 6: Self-review**

  Inspect the unstaged diff, run `git diff --check`, verify no production script changed, and write the required report.
