---
name: select-verification-suites
description: Select focused test suites and record reusable verification evidence during plan implementation, review, and handoff.
---

# Select Verification Suites

Use this skill for test selection, approved-plan implementation or review, and
handoff.  Start with a focused RED/GREEN check for the changed behaviour, then
select the package-level suites for the final working package.

## Select the required suites

1. Give the changed paths, or the relevant base and head, to
   [`scripts/select_test_suites.py`](../../../scripts/select_test_suites.py).
   Its output and [`tests/suite-selection.toml`](../../../tests/suite-selection.toml)
   are the path-classification authority; do not copy or infer path rules here.
2. Run the core target and every selected expensive suite using the executable
   targets in the [`Makefile`](../../../Makefile).  For visual selection, use
   the commands owned by the [CI visual job](../../../.github/workflows/ci.yml).
   Follow the CI migration job for its pull-request-only immutability check
   when actual pull-request base and head revisions are being checked.
3. Re-run the selector after every affected task-file change so the recorded
   selection and reasons describe the final package.

The pressure cases and evaluator criteria are in
[scenarios/pressure-tests.md](scenarios/pressure-tests.md).

## Record and reuse evidence

For each focused or selected GREEN result, the report records the exact
command, exit status, result summary, selector reasons, package fingerprint,
and confirmation that the final GREEN followed the last affected task-file
change.  Obtain the fingerprint through the selector's `fingerprint`
subcommand using the same base revision used for the package.

Evidence is reusable only when its fingerprint is equal to the final package's
fingerprint and it covers every suite the selector requires.  The current
fingerprint covers the entire package, so any package change invalidates all
earlier suite evidence.  Do not claim suite-scoped reuse until the selector
can prove it.

An implementer runs missing or invalidated selected suites before handoff.  A
reviewer normally inspects the report, selection, fingerprint, and package;
the reviewer reruns a suite only for a named coverage gap, reproduction
concern, or invalid package. After all task and review loops, the root
controller reruns the selector and fingerprint on the final branch, runs
`make check`, and ensures every selector-required expensive target has GREEN
evidence for that exact fingerprint. The root may reuse exact-fingerprint
evidence; otherwise it runs each missing layer once. CI is post-push repetition
only.
