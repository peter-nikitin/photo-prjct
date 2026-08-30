# Selfie Foreground Selection Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `$execute-implementation-plan` to implement
> this plan task by task with the repository's implementer and independent-review gates.

- Date: 2026-08-16
- Status: Proposed for execution
- Owner: project maintainer
- Related specification:
  [`2026-08-16-selfie-foreground-selection-benchmark-design.md`](../superpowers/specs/2026-08-16-selfie-foreground-selection-benchmark-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md)
- Related ADRs: [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md)
- ADR impact: Conforms to ADR 0023; no architecture update or new ADR required

## Goal

Implement and evaluate the approved conservative foreground-selection rule against the existing
immutable 36-case local detector run, then publish a sanitized decision report and the complete
benchmark work as one pull request without product activation.

## Scope

The approved specification is authoritative. There are no scope changes.

The implementation stays under `experiments/selfie_detector_benchmark/`. It consumes the verified
source run and writes new derived artifacts only to the existing external private benchmark root.
It does not rerun YuNet, contact staging, add dependencies, or change production code.

## Acceptance criteria

The specification's frozen gates are authoritative. Execution must additionally demonstrate that:

- an altered or incomplete source run, rule definition, review visual, or completed-label file
  cannot retain a valid derived-run identity;
- exactly 36 foreground rows are produced and reviewed;
- all derived outcomes and comparison counts in the tracked report reconcile with the immutable
  final analysis; and
- the branch contains no private media, labels, mappings, generated HTML, customer identifiers, or
  absolute private paths.

## Implementation

### Task 1: Build the immutable foreground derivation and review path

**Files:**

- Create `experiments/selfie_detector_benchmark/detector_benchmark/foreground.py`.
- Create `experiments/selfie_detector_benchmark/tests/test_foreground.py`.
- Modify `experiments/selfie_detector_benchmark/detector_benchmark/review.py` to add shared
  `build_identity_bound_review(...)` and `finalize_identity_bound_review(...)` helpers used by both
  verified run types while preserving the existing public behavior.
- Modify `experiments/selfie_detector_benchmark/detector_benchmark/cli.py`.
- Modify `experiments/selfie_detector_benchmark/README.md`.

**Specification:** Selected design; Evidence and review; Privacy, isolation, and failure semantics.

**Depends on:** The source detector run produced by commit `1cc32ad` and verified by the existing
`verify_run(Path) -> str` interface.

**Produces:**

- `classify_foreground(detections, quality) -> ForegroundOutcome`, a pure implementation of the
  frozen rule whose result includes outcome, selected source index or `None`, raw count, and
  per-secondary disposition evidence;
- `derive_foreground_run(source_run: Path, output: Path, *, experiment_revision: str) -> tuple[ReviewRow, ...]`,
  which verifies the source, publishes one immutable 36-row variant with bounded review visuals,
  and never overwrites an existing output;
- `verify_foreground_run(run: Path) -> str` and a corresponding row loader that bind source-run
  identity, rule constants, evidence, review rows, report, and every displayed visual;
- `build_identity_bound_review(rows, run_identity, output)` and
  `finalize_identity_bound_review(rows, run_identity, labels_csv, output)` in `review.py`; existing
  detector-run and new foreground-run wrappers call these only after their own complete verifier;
- CLI commands to derive, verify, build an identity-bound review bundle, and finalize exactly one
  complete foreground review without network access.

- [ ] Add focused failing tests for zero/one-face behavior, strict largest-area selection, the
  frozen 4:1 boundary, accepted-primary requirement, allowed `severe_blur`/`too_small` secondary
  reasons, genuine multiple-face preservation, and malformed detection/quality pairing.
- [ ] Add failing artifact tests proving exact 36-row/source-identity validation, atomic
  non-overwrite publication, and identity failure after mutations to the rule manifest, evidence,
  review rows, report, or a displayed visual.
- [ ] Add failing review tests proving labels are bound to the complete derived-run identity and
  that missing, duplicate, uncertain, or foreign labels cannot produce an authoritative result.
- [ ] Run
  `make test TESTS="experiments/selfie_detector_benchmark/tests/test_foreground.py"` and confirm the
  new tests fail because the foreground interfaces do not yet exist.
- [ ] Implement the smallest foreground module and narrow shared review helper required by those
  tests. Use only dependencies already present in the pinned worker and project environments.
- [ ] Add the parameterized offline command trail to the experiment README. Require a clean exact
  harness revision, a verified source-run identity, read-only source input, a private output root,
  and no network access.
- [ ] Run
  `make test TESTS="experiments/selfie_detector_benchmark/tests/test_foreground.py experiments/selfie_detector_benchmark/tests/test_review.py experiments/selfie_detector_benchmark/tests/test_run_identity.py"`
  and expect all selected tests to pass.
- [ ] Run `make test TESTS="experiments/selfie_detector_benchmark/tests"` and expect the complete
  model-independent harness suite to pass with no regression.

### Task 2: Execute and independently review the private derived benchmark

**Files:** The external private foreground run, review bundle, completed labels, contact sheets, and
final analysis only; no tracked media or mappings.

**Specification:** Evidence and review; Acceptance criteria; Privacy, isolation, and failure
semantics.

**Depends on:** Task 1's reviewed harness revision and the existing verified source-run identity
`19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa`.

**Produces:** One immutable 36-row foreground run, one complete identity-bound manual review, one
immutable final analysis, and an independently verified acceptance decision.

- [ ] Preflight the exact Task 1 revision, focused tests, source-run identity, 36-case cohort, and
  absence of an existing destination with the selected run name.
- [ ] Run the derivation locally with networking disabled or unavailable and the source run mounted
  read-only. Do not invoke the detector or access the staging host, database, or buckets.
- [ ] Verify the new run identity and confirm exactly 36 evidence rows, 36 review rows, 36 bounded
  visuals, one frozen variant, and no private artifact under the tracked worktree.
- [ ] Build the private review bundle and complete all 36 manual labels using the approved
  foreground-person rule. Record `uncertain` instead of inferring when a visual is ambiguous.
- [ ] Finalize only one complete review. Independently inspect every row and recompute the 17-case
  recovery, 16-case successful-control, 3-case multi-face guardrail, changed-case, helped-case, and
  harmed-case counts from immutable evidence.
- [ ] Stop without tuning if any frozen gate fails. Do not change the 4:1 ratio, quality constants,
  or labels and rerun the same cohort as a replacement experiment.

### Task 3: Publish the sanitized result and prepare the pull request

**Files:**

- Create `docs/research/2026-08-16-selfie-foreground-selection-benchmark.md`.
- Modify `docs/research/2026-08-16-selfie-detector-feedback-benchmark.md` only to link the completed
  follow-up report; do not rewrite the frozen first-experiment result.

**Specification:** Acceptance criteria; Architecture reconciliation; Next hypothesis; Excluded.

**Depends on:** Task 2's independently verified final analysis.

**Produces:** A sanitized repository decision record and a reviewed branch ready for GitHub
publication.

- [ ] Write the report from aggregate final-analysis evidence. Include the exact source and derived
  identities, frozen rule, cohort totals, acceptance table, changes versus both prior normalized
  variants, limitations, privacy handling, and the decision without record IDs or private paths.
- [ ] State the separately scoped alternative-detector hypothesis exactly as approved; do not select
  a model, dependency, license, or threshold in this report.
- [ ] Reconcile every number against the immutable analysis and independently reviewed labels.
- [ ] Run `git diff --check` and a targeted tracked-file scan for media extensions, generated review
  artifacts, customer identifiers, secrets, and absolute private paths; expect no task artifact or
  sensitive match.
- [ ] Run `make test TESTS="experiments/selfie_detector_benchmark/tests"`; expect the complete
  focused suite to pass after the documentation change.

### Final task: Architecture, ADR, and delivery reconciliation

- [ ] Compare the complete branch with the approved specification, ADR 0023, and
  `docs/architecture.md`; confirm no production component, dependency, schema, configuration,
  deployment, or runtime path changed.
- [ ] Record `Conforms to ADR 0023; no architecture update required` in the report and pull request.
- [ ] Obtain independent final review of the complete `origin/main..HEAD` diff and resolve all
  blocking findings through the same implementer/reviewer loop.
- [ ] Rerun the focused suite, diff check, clean-status check, report/evidence reconciliation, and
  changed-file private-artifact scan after final review.
- [ ] Push `codex/selfie-detector-benchmark` and open a draft pull request containing the complete
  detector benchmark, foreground follow-up, sanitized results, test evidence, privacy statement,
  and explicit `no deployment or activation` boundary.
- [ ] Do not merge, deploy, tune thresholds, or start the alternative-detector execution from this
  plan. Begin that hypothesis only through its own approved specification and plan.

## Verification

- `make test TESTS="experiments/selfie_detector_benchmark/tests/test_foreground.py"` passes the
  frozen rule and artifact-contract tests.
- `make test TESTS="experiments/selfie_detector_benchmark/tests/test_foreground.py experiments/selfie_detector_benchmark/tests/test_review.py experiments/selfie_detector_benchmark/tests/test_run_identity.py"`
  passes the foreground and shared identity/review regression paths.
- `make test TESTS="experiments/selfie_detector_benchmark/tests"` passes the complete isolated
  harness suite.
- The foreground verifier returns one immutable run identity bound to the exact source identity and
  reports exactly 36 rows and visuals.
- The finalizer accepts exactly 36 complete identity-matched labels with zero uncertainty and emits
  the frozen 17/16/3 gates plus helped/harmed comparisons.
- `git diff --check` succeeds.
- `git status --short --branch` is clean apart from the branch-ahead state before push.
- The changed-file scan finds no customer media, mappings, private labels, generated HTML, secrets,
  or private absolute paths.
- GitHub reports a draft pull request for the pushed branch; merge and deployment remain absent.

## Operational impact and rollout

None. The run is local and derived from existing immutable private evidence. There is no staging or
production call, data write, model execution, dependency change, configuration change, deployment,
activation, or customer-visible behavior.

The pull request publishes only experiment code, tests, specifications/plans, and sanitized reports.

## Rollback

Before merge, close the draft pull request and delete the remote experiment branch if delivery is
abandoned; the local commits and external private evidence remain available for explicit cleanup.

Delete the derived private run and review artifacts if the experiment must be rerun because of an
implementation defect. Never overwrite or relabel an immutable completed run. Delete the existing
local customer-data snapshot and all derived artifacts after this evaluation and any explicitly
approved immediate follow-up are complete.

No staging or production rollback is required because this plan changes neither environment.

## Open questions

None.
