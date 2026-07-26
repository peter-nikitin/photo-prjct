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
