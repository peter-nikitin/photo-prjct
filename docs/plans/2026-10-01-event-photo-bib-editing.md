# Event Photo Bib Editing Implementation Plan

- Date: 2026-10-01
- Status: Approved
- Owner: project maintainer
- Related specification: [Event photo bib editing and filtering](../superpowers/specs/2026-10-01-event-photo-bib-editing-design.md)
- Related architecture: [Current architecture](../architecture.md)
- Related ADRs: [ADR 0001](../adr/0001-django-modular-monolith.md), [ADR 0002](../adr/0002-postgresql-system-of-record.md), [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md), and [ADR 0033](../adr/0033-keep-durable-knowledge-test-executable-contracts.md)
- ADR impact: Conforms to ADRs 0001, 0002, 0017, and 0033; no ADR or topology change is required.

## Goal

Deliver the approved [operator editing, audit, and filter behavior](../superpowers/specs/2026-10-01-event-photo-bib-editing-design.md#1-outcome) in the existing private event photo workspace.

## Scope

Implement the specification without scope changes. Execute this plan with
`$execute-implementation-plan`.

## Acceptance criteria

The final package must satisfy all [specification acceptance criteria](../superpowers/specs/2026-10-01-event-photo-bib-editing-design.md#10-acceptance-criteria), with focused tests proving transactional mutations, authorization, shared search semantics, filter persistence, and the overlay interaction.

## Worker/state/artifact release safeguards

This plan changes the durable current bib projection through an operator path but does not change a
worker contract or generated binary artifact.

- [x] **Live-state inventory.** No live mutation is part of implementation. Existing `BibReading`
  and accepted-attempt counts can remain in place; the schema migration only adds an empty audit
  table. Deployment verification records migration state and public health.
- [x] **Compatibility matrix.** Old and new workers submit the unchanged bib contract. Old rows are
  readable and editable after the Django migration. New Django remains compatible with existing
  attempts/readings. Old Django can ignore the new audit table during rollback, while manually
  changed `BibReading` values remain current data.
- [x] **Reviewed data-state migration or reset semantics.** Additive schema migration only; no
  backfill, reset, purge, requeue, or projection rewrite.
- [x] **End-to-end contract sizing.** Worker result size and HTTP callback remain unchanged. The edit
  endpoint is bounded by the existing finite readings for one photo plus form limits and 1-16 digit
  values; focused tests cover a multi-number save.
- [x] **Previous-snapshot upgrade rehearsal.** Migration tests and the ordinary suite cover a
  pre-migration database with existing attempts/readings. Successful, failed, retryable, stale,
  active, terminal, and never-enrolled processing rows are untouched by the additive migration.
- [x] **Staged activation and rollback order.** Deploy compatible Django migration and code
  together, verify private edit permissions and public health, then exercise one bounded test photo.
  Stop on migration, authorization, or public-search regression. Roll back application code first;
  retain the additive table and current readings.
- [x] **Supported bounded operational commands.** No operational requeue, backfill, or purge command
  is added. Use existing migration inspection and bib event reporting only for read-only checks.

Rationale: [2026-07-31 staging processing-state reset postmortem](../postmortems/2026-07-31-staging-processing-state-reset.md).

## Implementation

### Task 1: Persist and apply audited manual bib changes

**Files:** `src/backend/processing/models.py`, a new `src/backend/processing/migrations/` migration,
`src/backend/picflow/event_management_bibs.py`, and focused processing/picflow tests.

- **Specification:** Sections 3-5 and 8-9.
- **Depends on:** None.
- **Produces:** One transaction-safe event/photo edit interface plus append-only change evidence.

- [ ] Add failing model and service tests for constraints, add, replace, duplicate merge, delete-to-zero, rollback, stale/cross-event IDs, policy, accepted-attempt, and authorization-independent domain behavior.
- [ ] Run the focused tests and confirm the expected missing-model/module failures.
- [ ] Add the audit model/migration and minimal transaction module that mutates current readings and records changes.
- [ ] Run the exact focused tests and confirm all scenarios pass.

### Task 2: Reuse exact bib search and add the no-number filter

**Files:** `src/backend/picflow/forms.py`, `src/backend/picflow/event_management_forms.py`,
`src/backend/picflow/event_management.py`, and their focused tests.

- **Specification:** Section 7.
- **Depends on:** Task 1 current-reading behavior.
- **Produces:** Validated `bib` and `without_bib` values in the reusable event-photo filter object.

- [ ] Add failing tests proving public-form validation parity, exact leading-zero matching,
  mutual exclusion, applicable zero-reading selection, canonical query persistence, and filtered
  bulk-selection parity.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Reuse `BibSearchForm` inside the administrative filter and apply exact/absence predicates to
  the event-scoped queryset.
- [ ] Run the exact focused tests and confirm all scenarios pass.

### Task 3: Deliver the card editor endpoint and fragment state

**Files:** `src/backend/picflow/event_management_urls.py`,
`src/backend/picflow/event_management_views.py`,
`src/backend/templates/picflow/_event_photo_results.html`, and focused view/template tests.

- **Specification:** Sections 5-6 and 8-9.
- **Depends on:** Tasks 1-2 interfaces.
- **Produces:** Authorized POST endpoint and complete per-card read/edit markup.

- [ ] Add failing tests for zero/one/many read state, editor eligibility, CSRF/method/permission and
  event isolation, successful JSON, form-error JSON, and rerendered current numbers.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Materialize bounded reading state for page cards, add the POST adapter, and render accessible
  read and editor markup without changing ordinary card height.
- [ ] Run the exact focused tests and confirm all scenarios pass.

### Task 4: Implement overlay interaction and presentation

**Files:** `src/backend/static/ui/event-photo-management.js`,
`src/backend/static/ui/event-photo-management.css`, JavaScript tests, and selected visual fixtures or
snapshots when required by the suite selector.

- **Specification:** Section 6.
- **Depends on:** Task 3 markup and endpoint response.
- **Produces:** Downward overlay, vertical dynamic inputs, explicit successful-save exit, and inline
  errors without grid movement.

- [ ] Add failing JavaScript interaction tests for opening, adding an input, blank deletion payload,
  retained open state on validation/network error, success rerender/close, and independent cards.
- [ ] Run the focused JavaScript tests and confirm the expected failures.
- [ ] Implement the smallest delegated interaction and CSS anchored overlay.
- [ ] Run focused JavaScript tests and any selector-required visual check to GREEN.

### Task 5: Documentation and complete regression verification

**Files:** `docs/architecture.md`, `docs/product-jobs.md`, and the final verification evidence under
`.superpowers/sdd/2026-10-01-event-photo-bib-editing/`.

- **Specification:** Sections 1-11.
- **Depends on:** Tasks 1-4.
- **Produces:** Reconciled implemented facts and final-package verification evidence.

- [ ] Update only current implemented architecture and PJ-007 evidence affected by delivered manual
  editing; retain historical status entries.
- [ ] Run the changed-path selector and final fingerprint.
- [ ] Run `make check` once on the final package and every selector-required expensive target once
  for the same fingerprint.
- [ ] Confirm migration drift is absent and all final evidence follows the last affected-file change.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, applicable ADRs, and
  `docs/architecture.md`.
- [ ] Confirm conformance to ADRs 0001, 0002, 0017, and 0033 with no new or superseding ADR.
- [ ] Record the reconciliation outcome in the pull request.

## Verification

- `make test TESTS="processing.tests.test_models picflow.tests.test_event_management picflow.tests.test_event_management_views"` — model, mutation, filtering, view, permission, and rendering coverage passes.
- Run the repository's focused JavaScript test command for `event-photo-management.js` — overlay and save-state interactions pass.
- `.venv/bin/pre-commit run --files <all changed Python files>` — Ruff formatting/lint and full-project mypy pass after the last Python change.
- `scripts/select_test_suites.py select --base origin/main --head HEAD` (or changed-path form while unstaged) and `scripts/select_test_suites.py fingerprint --base origin/main` — final suite selection and package fingerprint are recorded.
- `make check` — the complete Python quality suite passes once on the final package.
- Every expensive suite selected by the executable selector, including visual checks when selected,
  passes for that exact final fingerprint.

## Operational impact and rollout

The release adds one PostgreSQL audit table and private Django endpoint/UI. There is no environment,
secret, worker image, processor identity, queue, storage, or feature-flag change. Apply the ordinary
deployment migration before serving the new UI. Verify one bounded photo add/edit/delete cycle and
the corresponding public exact search after deployment; do not run a backfill.

## Rollback

Roll back the application image while retaining the additive audit table. Existing manually changed
`BibReading` values remain valid current search data. Do not reverse operator corrections or delete
audit evidence during code rollback. The migration may be removed only after a separate verified
data-retention decision.

## Open questions

None.
