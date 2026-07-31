# Admin Processing Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking. Project `AGENTS.md` overrides generic per-task commit guidance:
> implementers leave changes unstaged, and the root controller makes one final commit only after
> independent review and final verification.

- Date: 2026-07-31
- Status: Approved
- Owner: project maintainer
- Related specification:
  [Admin processing progress design](../superpowers/specs/2026-07-31-admin-processing-progress-design.md)
- Related architecture:
  [current background-processing flow](../architecture.md#core-data-flows--proposed) and
  [Operations module](../architecture.md#target-mvp-architecture--proposed)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md), and
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to accepted ADR 0017; no new ADR is needed.

## Goal

Implement the approved specification's [goal](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#goal): a staff-only, read-only page showing event processing progress and the deliberately simple current-job ETA.

## Scope

Implement the specification without scope changes.

- No mutation controls, retry operation, worker/API change, model/migration, cache, JavaScript
  polling, chart, filter, or visual test.
- Keep Django/PostgreSQL job and run records as the sole source of truth.
- The page is an administrative read model; it must not expose attempts, results, errors, object
  keys, grants, worker credentials, or face data.

## Acceptance criteria

Implement all [specification acceptance criteria](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#acceptance-criteria).

The delivery check additionally confirms that the custom route precedes the catch-all `admin/`
route, so `/admin/processing/` cannot be consumed by Django Admin's own URL resolver.

## Implementation

### Task 1: Staff processing-progress read page

**Files:**

- Create `src/backend/processing/admin_progress.py`.
- Create `src/backend/processing/tests/test_admin_progress.py`.
- Create `src/backend/templates/processing/admin_progress.html`.
- Modify `src/backend/config/urls.py`.

- **Specification:** [Access and route](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#access-and-route), [Read model](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#read-model), [ETA](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#eta), and [Failure and empty states](../superpowers/specs/2026-07-31-admin-processing-progress-design.md#failure-and-empty-states).
- **Depends on:** Existing `EventProcessingRun` and `ProcessingJob` persistence contract from ADR 0017.
- **Produces:** Named URL `admin_processing_progress`; staff-only server-rendered response with one presentation row per `EventProcessingRun`.

- [ ] Add focused failing tests in `test_admin_progress.py` using existing `Event`, `Photo`,
  `EventProcessingRun`, and `ProcessingJob` factories or local helpers. Create one staff user and
  one ordinary authenticated user.
- [ ] Assert that an ordinary authenticated user is redirected to Django Admin login from
  `reverse("admin_processing_progress")`, while a staff user receives HTTP 200.
- [ ] With fixed `timezone.now()`, construct an active run containing `queued`, `processing`,
  `retry_wait`, `succeeded`, `failed`, and `cancelled` jobs. Assert the response identifies the
  event, processor, run status, cohort total, all six explicit counts, processed total
  (`succeeded + failed + cancelled`), remaining total (`queued + processing + retry_wait`), and
  finish estimate calculated from the sole processing job's `claimed_at` and remaining count.
- [ ] Create a second run with no uniquely eligible current processing job (for example, only
  queued jobs) and assert the response displays `—`. Create a closed run and assert it displays
  `Completed` rather than an ETA.
- [ ] Run the focused test file with CI-equivalent Django settings and confirm it fails because
  the route and page do not exist:
  `SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 .venv/bin/pytest -q src/backend/processing/tests/test_admin_progress.py`.
- [ ] Implement `processing.admin_progress.admin_processing_progress` as a GET-only,
  `staff_member_required` view. Query runs with their events and aggregate only the six approved
  `ProcessingJob` statuses. Build plain presentation values for the template; calculate ETA only
  for exactly one `processing` job with `claimed_at`, and use the specification's terminal,
  remaining, unknown, and closed-run rules exactly.
- [ ] Add the named `/admin/processing/` route before `path("admin/", admin.site.urls)` in
  `config/urls.py`.
- [ ] Render a minimal `admin/base_site.html`-based table. Include an explicit zero for every
  status, a no-runs message, and an event link to `admin:picflow_event_change`; do not render any
  sensitive job/attempt fields or action controls.
- [ ] Re-run the focused test command. Expected: all authorization, aggregation, calculable-ETA,
  unavailable-ETA, and closed-run tests pass.
- [ ] Run formatting, linting, typing, migration-drift, and Django checks with the same
  CI-equivalent settings:
  ` .venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, `.venv/bin/mypy`,
  `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`, and
  `.venv/bin/python src/backend/manage.py check`.
  Expected: each command exits zero. Do not run or add visual tests for this change.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the delivered route, access control, query, template fields, and ETA behavior with
  the approved specification and ADR 0017.
- [ ] Confirm no worker protocol, job/attempt/run state transition, persistent schema, deployment
  configuration, or observability topology changed.
- [ ] Record the outcome as: conforms to ADR 0017; no architecture-document update and no new ADR
  required, because the page is a reversible staff-only read model over existing authoritative
  records.
- [ ] Prepare the complete unstaged diff, obtain independent review, rerun the focused verification
  after approval, and only then create one task commit as required by `AGENTS.md`.

## Verification

Use the Task 1 focused Django test command first. It must prove the minimal changed contract:
staff authorization, current status aggregation, one calculable ETA, and non-calculable ETA
outcomes. Then run the five non-visual checks listed in Task 1. No visual-test command is part of
this plan.

## Operational impact and rollout

No configuration, migration, worker, deployment, monitoring, or compatibility change is required.
Deploy through the existing normal Django image path. After deployment, a staff operator opens
`/admin/processing/` and confirms that the page renders without mutating any processing records.

## Rollback

Revert the application change. It creates no data and changes no processing state, so no database
or worker recovery is needed.

## Open questions

None.
