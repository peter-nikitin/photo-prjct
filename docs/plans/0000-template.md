# Topic Implementation Plan

- Date: YYYY-MM-DD
- Status: Draft
- Owner: project maintainer
- Related specification: link or `none`
- Related architecture: link
- Related ADRs: links or `none`
- ADR impact: resolved classification and links

## Goal

Link the approved specification's goal. Add one sentence only when needed to identify the delivery
milestone covered by this plan.

## Scope

Reference the approved specification's scope. List only task-specific scope deltas; write `None`
when the plan implements the specification without changing its scope.

## Acceptance criteria

Reference the approved specification's acceptance criteria. Add only observable delivery checks
that depend on the implementation sequence.

## Worker/state/artifact release safeguards

Complete this section when the plan changes a worker contract, durable processing state, or
generated/derived artifact. Replace every checkbox with the named decision, evidence, or command
for this release. An unknown outcome makes the plan blocked until its owner resolves it.

- [ ] **Live-state inventory.** Record the current processor/contract identities, state and
  job/attempt counts, retry eligibility, stale state, active or expired leases, accepted results,
  published artifacts, and related Object Storage prefixes.
- [ ] **Compatibility matrix.** State the supported outcome for each applicable old/new
  Django/worker/row-version combination, including whether existing work is readable, drained,
  retried, superseded, backfilled, or purged.
- [ ] **Reviewed data-state migration or reset semantics.** Select the reviewed treatment for every
  existing durable row and derived artifact, separate from schema migration: compatible drain,
  version-aware reconciliation/requeue, bounded backfill, explicit derived-state purge, or a
  documented intentional reset.
- [ ] **End-to-end contract sizing.** Name one representative maximum result and verify its single
  contract through worker serialization, HTTP client, proxy/Django request handling, callback and
  model validation, and database persistence.
- [ ] **Previous-snapshot upgrade rehearsal.** Rehearse a previous-version snapshot containing old
  successful, failed and retryable attempts, stale state, active or expired leases, terminal state,
  published derived artifacts, and never-enrolled photos; record the outcome for each case.
- [ ] **Staged activation and rollback order.** Name the compatible deployment, state inspection,
  bounded cohort, validation, public activation, stop conditions, and the rollback sequence that
  preserves the declared rows and artifact identities/versions.
- [ ] **Supported bounded operational commands.** Name reviewed commands and bounds for compatibility
  inspection, requeue, backfill, or derived-state purge, including required confirmation and
  preservation checks.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

### Task 1: Focused deliverable

**Files:** exact paths to create or modify.

- **Specification:** exact sections implemented by this task.
- **Depends on:** earlier task outputs or `None`.
- **Produces:** cross-task interface or independently verifiable result.

- [ ] Add or update the failing test when behavior changes.
- [ ] Run the targeted test and confirm the expected failure.
- [ ] Implement the smallest complete change.
- [ ] Run the exact targeted check and record its expected successful outcome.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, applicable ADRs, and
  `docs/architecture.md`.
- [ ] Update implemented architecture facts when boundaries, topology, or status changed.
- [ ] Stop for a decision instead of contradicting an accepted ADR; supersede rather than edit it.
- [ ] Record the reconciliation outcome in the pull request.

## Verification

List exact commands and expected successful outcomes.

## Operational impact and rollout

Describe configuration, migration, deployment order, monitoring, and compatibility. Write `None` if
there is no runtime effect.

## Rollback

Describe safe reversal and any irreversible data effects. Write `Revert the change` only when that is
actually sufficient.

## Open questions

- Questions that must be resolved before implementation; use `None` for a decision-complete plan.
