# Selfie Detector Feedback Benchmark Implementation Plan

- Date: 2026-08-16
- Status: Approved for local execution
- Owner: project maintainer
- Related specification:
  [`2026-08-16-selfie-detector-feedback-benchmark-design.md`](../superpowers/specs/2026-08-16-selfie-detector-feedback-benchmark-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md)
- Related ADRs: [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md)
- ADR impact: Conforms to ADR 0023; no production architecture change

## Goal

Execute the approved private offline comparison of current YuNet selfie detection on original,
normalized, and normalized-plus-quality-gated inputs.

## Scope

Implement only an isolated experiment harness and sanitized report. Do not change product code,
runtime dependencies, staging state, deployment configuration, or model behavior.

## Acceptance criteria

The specification's acceptance criteria are authoritative. Execution must additionally leave an
immutable verified private snapshot and run, and a sanitized repository report with no personal
data or absolute private paths.

## Implementation

Execute this plan with `$execute-implementation-plan`.

### Task 1: Build and verify the isolated experiment harness

**Files:** `experiments/selfie_detector_benchmark/`, focused experiment tests.

- **Specification:** Frozen inputs, Detector variants, Artifacts and review, Failure handling.
- **Depends on:** None.
- **Produces:** a tested snapshot exporter, offline detector runner, review bundle, finalizer, and
  documented Docker-based invocation against the pinned deployed worker image.

- [ ] Write focused failing tests for manifest redaction and completeness, normalization geometry,
  post-quality cardinality, immutable publication, and complete review finalization.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the smallest isolated harness without importing it into production packages.
- [ ] Run focused model-independent tests and confirm success.

### Task 2: Capture the private snapshot and execute the real benchmark

**Files:** private external snapshot/run directories; no tracked media.

- **Specification:** Privacy and isolation, Frozen inputs, Detector variants, Acceptance criteria.
- **Depends on:** Task 1 harness.
- **Produces:** verified 40-record snapshot, exact 36-case cohort, three completed detector variants,
  private review bundle, completed review labels, and finalized aggregate analysis.

- [ ] Export the exact sanitized metadata and 40 objects read-only from staging, transfer once, and
  verify count, byte size, content type, and SHA-256 locally.
- [ ] Pin or copy the deployed worker image/model once and record their identities.
- [ ] Run all three variants locally with networking disabled.
- [ ] Inspect the complete private review bundle and record `correct`, `incorrect`, or `uncertain`
  for all 108 case/variant rows.
- [ ] Finalize analysis and verify acceptance/guardrail calculations from the immutable evidence.

### Task 3: Publish a sanitized evidence report

**Files:** `docs/research/2026-08-16-selfie-detector-feedback-benchmark.md`.

- **Specification:** Artifacts and review, Acceptance criteria, Excluded.
- **Depends on:** finalized Task 2 analysis.
- **Produces:** sanitized decision report with commands, hashes, aggregate results, limitations, and
  recommendation.

- [ ] Write the report from finalized aggregate evidence without media, record IDs, contacts,
  object keys, or absolute private paths.
- [ ] Reconcile every number against the final JSON analysis.
- [ ] Run Markdown and secret/absolute-path checks and confirm success.

### Final task: Architecture and ADR reconciliation

- [ ] Confirm no production code, dependency, schema, configuration, or deployed state changed.
- [ ] Confirm the workflow remained within ADR 0023 and the approved specification.
- [ ] Record `no architecture update required` in the report.

## Verification

- `make test TESTS="experiments/selfie_detector_benchmark/tests"` passes all model-independent
  experiment tests.
- Snapshot verifier reports exactly 40 verified records and no forbidden fields.
- The offline run reports 36 cases and 108 variant results with networking disabled.
- Finalizer accepts exactly one complete review and emits the frozen acceptance/guardrail metrics.
- `git diff --check` succeeds and `git status --short` contains no private media or generated review
  artifact.

## Operational impact and rollout

None. One read-only staging export is transferred to a local private directory. No deployment,
activation, bucket change, database write, or worker configuration change is allowed.

## Rollback

Delete the local private snapshot and generated runs after the approved evaluation lifecycle. The
staging bucket lifecycle and database remain unchanged.

## Open questions

None.
