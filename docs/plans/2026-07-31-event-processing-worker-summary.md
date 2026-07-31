# Event Processing Worker Summary Implementation Plan

- Date: 2026-07-31
- Status: Approved
- Related specification: [event processing worker summary design](../superpowers/specs/2026-07-31-event-processing-worker-summary-design.md)
- ADR impact: Conforms to ADR 0017; no ADR, schema, or worker-protocol change.

## Implementation

### Task 1: Replace run rows with event worker summaries

**Files:** modify `src/backend/processing/admin_progress.py`,
`src/backend/templates/processing/admin_progress.html`, and
`src/backend/processing/tests/test_admin_progress.py`.

- Add failing focused view tests for one common distinct-photo total, three fixed worker columns,
  event completion, per-worker ETA, and Embedding `Waiting for preview`.
- Implement the smallest read-only event aggregate over existing jobs; preserve staff-only GET
  access and no sensitive output.
- Run only the focused test file and changed-file format/lint/type checks. No visual tests.

### Final task: reconciliation

- Confirm the page does not change job creation, dependency, state-transition, deployment, or
  schema behavior and conforms to ADR 0017.
