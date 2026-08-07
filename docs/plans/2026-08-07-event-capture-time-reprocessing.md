# Event Capture-Time Reprocessing Implementation Plan

- Date: 2026-08-07
- Status: Implementation locally verified; restored-snapshot 17,043-photo acceptance remains
  pending.
- Owner: project maintainer
- Related specification:
  [`2026-08-07-event-capture-time-reprocessing-design.md`](../superpowers/specs/2026-08-07-event-capture-time-reprocessing-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), implemented photo-processing
  control plane and event-scoped search direction
- Related ADRs:
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0004](../adr/0004-repository-engineering-knowledge.md), and
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0002, ADR 0004, and ADR 0017. No new or superseding ADR is required.

## Goal

Implement the approved capture-time correction and prove that all 17,043 photos in event 9 have
one consistent, timezone-aware version-2 result before designing customer gallery filtering.

## Scope

Implement the approved specification without scope changes. Customer-facing filtering remains a
separate follow-up specification after the restored-snapshot acceptance report passes.

## Global constraints

- Work only in the isolated `codex/capture-time-filtering` worktree created from current `main`.
- Preserve old jobs, attempts, results, and reports as immutable evidence.
- Do not enqueue or process another event through the one-off operation.
- Do not infer timezone from `city`; event 9 is explicitly `Europe/Moscow`.
- Store accepted instants canonically in UTC while interpreting offset-less camera values in the
  configured event timezone.
- Keep the existing ADR 0017 worker boundary: no database or permanent Object Storage credentials
  in the worker.
- Use project commands through `make test` and `make check`; do not invoke global Python tooling.
- Keep implementation and review-fix changes unstaged until final approval. Create one final task
  commit only after review and final verification, as required by `AGENTS.md`.

## Acceptance criteria

All sixteen acceptance criteria in the approved specification must pass. In addition, the final
evidence must identify separately:

- focused local automated checks;
- restored-staging-snapshot results;
- CI status after publication, if publication is requested; and
- staging deployment/backfill status, if live activation is requested.

Local checks must not be presented as CI or deployed-staging evidence.

## Current implementation evidence

- 2026-08-07: final root verification passed the required combined feature suite: 214 tests.
- 2026-08-07: final root `make check` passed Ruff format/lint, MyPy, 1,368 tests with 3 skips,
  43 deselections, and 238 warnings, the 75% coverage gate at 82.90%, Django checks, and
  migration-drift validation.
- No CI run, staging deployment, restored-snapshot dry run, private-original worker run, backfill,
  or 17,043-photo terminal acceptance report is evidenced. Customer gallery time filtering remains
  out of scope and blocked until restored-snapshot acceptance criteria 11-15 are satisfied.

## File and interface map

- `src/backend/picflow/models.py`: owns the event timezone field and publication invariant.
- `src/backend/picflow/admin.py`: exposes timezone explicitly to operators.
- `src/backend/picflow/migrations/0007_event_timezone.py`: adds the nullable draft-time field and
  assigns `Europe/Moscow` only to event 9 when that row exists.
- `src/backend/picflow/tests/test_models.py` and `test_admin.py`: protect validation and admin
  behavior.
- `src/backend/processing/services/enrollment.py`: owns processor version 2 configuration and
  event-specific configuration construction.
- `src/backend/processing/views.py`: validates the version-2 typed result.
- `src/backend/processing/tests/test_enrollment.py` and `test_views.py`: protect Django/worker
  contract agreement.
- `src/worker/photo_worker/contracts.py`: validates the claimed version-2 configuration and result
  vocabulary on the worker side.
- `src/worker/photo_worker/metadata.py`: owns bounded JPEG/MPO EXIF traversal and timezone
  normalization.
- `src/worker/photo_worker/runner.py`: passes the configured event timezone to extraction without
  changing transport or lease behavior.
- `src/worker/tests/fixtures/capture-time-nested.mpo`: contains one small repository-owned
  representative MPO fixture with no customer image content or identifying metadata.
- `src/worker/tests/test_metadata.py`, `test_contracts.py`, and `test_runner.py`: protect parser,
  configuration, and end-to-end worker behavior.
- `src/backend/processing/management/commands/reprocess_event_capture_times.py`: owns strict event-9
  dry-run/apply enrollment and idempotency.
- `src/backend/processing/management/commands/report_event_capture_times.py`: owns the privacy-safe
  completion and distribution report.
- `src/backend/processing/tests/test_capture_time_commands.py`: protects both command contracts.
- `docs/architecture.md`: records the delivered timezone-aware capture-metadata behavior only after
  implementation verification.

## Implementation

### Task 1: Add explicit event timezone and publication validation

**Files:** modify `src/backend/picflow/models.py`, `src/backend/picflow/admin.py`,
`src/backend/picflow/tests/test_models.py`, and `src/backend/picflow/tests/test_admin.py`; create
`src/backend/picflow/migrations/0007_event_timezone.py`.

- **Specification:** Event-Timezone Contract; acceptance criteria 7.
- **Depends on:** None.
- **Produces:** `Event.timezone_name: str | None`, validated as an IANA identifier; published events
  require a value; event 9 receives `Europe/Moscow` through a bounded data migration.

- [ ] Add model tests proving a draft accepts no timezone, a published event rejects no timezone or
  an invalid identifier, and `Europe/Moscow` is accepted without using Django/server timezone.
- [ ] Add admin tests proving the timezone field is visible/editable and publication validation is
  enforced through the admin form.
- [ ] Add a migration test proving only row ID 9 receives `Europe/Moscow`; other existing rows remain
  null and no timezone is inferred from city text.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/picflow/tests/test_photo_migrations.py"`
  and confirm the new assertions fail before implementation.
- [ ] Implement the nullable bounded field, `ZoneInfo` validation, published-event invariant, admin
  field exposure, schema migration, and event-9-only data operation.
- [ ] Rerun the same command and expect all selected tests to pass with no pending migration from
  `make test TESTS="src/backend/picflow/tests/test_photo_migrations.py"`.

### Task 2: Define one event-specific capture-metadata version-2 contract

**Files:** modify `src/backend/processing/services/enrollment.py`,
`src/backend/processing/views.py`, `src/backend/processing/tests/test_enrollment.py`,
`src/backend/processing/tests/test_views.py`, `src/worker/photo_worker/contracts.py`, and
`src/worker/tests/test_contracts.py`.

- **Specification:** Event-Timezone Contract; Capture-Metadata Processor Version 2; Typed Result;
  acceptance criteria 6 and 9.
- **Depends on:** Task 1's `Event.timezone_name`.
- **Produces:** `CAPTURE_METADATA_PROCESSOR_VERSION = 2`, a configuration builder that includes the
  exact event timezone, and matching Django/worker validation of `source_offset`, `event_timezone`,
  warnings, and timezone states.

- [ ] Add failing enrollment tests proving two events with different timezones receive different
  immutable normalized configurations and enrollment refuses a missing/invalid timezone.
- [ ] Add failing Django result-validation tests for valid explicit/event-timezone/missing results,
  forbidden `inferred_none`, mismatched event timezone, malformed offsets, and the two new warning
  codes.
- [ ] Add equivalent failing worker contract tests using the exact literal version-2 configuration
  and prove processor version 1 is no longer claimable by the new worker build.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_views.py src/worker/tests/test_contracts.py"`
  and record failures at the missing version-2 interfaces.
- [ ] Implement the smallest shared vocabulary and configuration builders needed for Django and the
  worker to agree without introducing a new dependency or compatibility fallback.
- [ ] Rerun the same command and expect all selected contract tests to pass.

### Task 3: Implement bounded JPEG/MPO EXIF and timezone parsing

**Files:** modify `src/worker/photo_worker/metadata.py`,
`src/worker/photo_worker/runner.py`, `src/worker/tests/test_metadata.py`, and
`src/worker/tests/test_runner.py`; create
`src/worker/tests/fixtures/capture-time-nested.mpo` only after verifying it contains no customer
pixels, filename, serial number, GPS, author, or other identifying metadata.

- **Specification:** Supported Container Formats; EXIF Traversal and Precedence; Timezone
  Resolution; Failure Semantics; acceptance criteria 1-5.
- **Depends on:** Task 2's version-2 configuration and typed result.
- **Produces:**
  `extract_capture_metadata(path, *, max_bytes, max_pixels, date_field_precedence, event_timezone)`
  returning the approved version-2 result for JPEG and MPO.

- [ ] Add a fixture inspection test that rejects identifying metadata and confirms Pillow detects
  the repository fixture as MPO with nested EXIF date/offset tags.
- [ ] Add failing parser tests for nested MPO `DateTimeOriginal +03:00`, offset-less Moscow JPEG,
  nested-versus-root conflict, malformed offset fallback, equal normalized instants, missing time,
  unsupported decoded format, corrupt input, byte/pixel limits, and ambiguous/nonexistent DST wall
  times using an appropriate DST-observing IANA zone.
- [ ] Add a failing runner test proving the claimed event timezone is passed unchanged to the parser
  and returned provenance survives terminal serialization.
- [ ] Run
  `make test TESTS="src/worker/tests/test_metadata.py src/worker/tests/test_runner.py -k 'metadata or capture_time'"`
  and confirm failures identify the old JPEG-only/assume-UTC behavior.
- [ ] Implement standards-defined root/nested IFD lookup, decoded `JPEG`/`MPO` acceptance, paired
  offset resolution, `ZoneInfo` wall-time validation, UTC normalization, and bounded provenance.
  Do not decode pixels or enumerate MPO frames.
- [ ] Rerun the same focused command and expect all selected tests to pass, then run
  `make test TESTS="src/worker/tests"` and expect the complete worker suite to pass.

### Task 4: Add the strict event-9 reprocessing command

**Files:** create
`src/backend/processing/management/commands/reprocess_event_capture_times.py` and
`src/backend/processing/tests/test_capture_time_commands.py`; modify
`src/backend/processing/services/enrollment.py` only if a narrow public batch-enrollment service is
required.

- **Specification:** Reprocessing Operation; Failure Semantics; acceptance criteria 8-10.
- **Depends on:** Tasks 1-3.
- **Produces:** `manage.py reprocess_event_capture_times --event-id 9 [--apply]`, dry-run by default,
  and an idempotent service that enrolls exactly the fixed event-9 cohort under processor version 2.

- [ ] Add failing command tests proving default dry-run writes nothing; missing or non-9 IDs fail;
  wrong event name/status/timezone/photo count/configuration fail before writes; output contains only
  bounded counts/IDs; and no photo from another event is touched.
- [ ] Add failing apply/idempotency tests with a reduced test cohort that prove one version-2 job per
  event photo, the union of immutable run cohorts equals the exact event cohort, a second invocation
  creates no duplicates, and version-1 attempts/results remain byte-for-byte unchanged.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_capture_time_commands.py"`
  and confirm the command is missing.
- [ ] Implement the command and narrow transactional enrollment service using existing ADR 0017
  models and state transitions. Under row lock, rotate each event-9 capture state from its old
  terminal job to the new queued version-2 job, clear current/accepted attempt pointers from the
  mutable state row, and leave every old terminal attempt row unchanged. Do not bypass normal
  worker completion.
- [ ] Rerun the command tests and expect all cases to pass, then rerun
  `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_jobs.py"`
  to protect existing enrollment and lease semantics.

### Task 5: Add the read-only acceptance report and prove the restored snapshot

**Files:** create
`src/backend/processing/management/commands/report_event_capture_times.py`; extend
`src/backend/processing/tests/test_capture_time_commands.py`.

- **Specification:** Reprocessing Operation; Restored Staging Snapshot acceptance criteria 11-15.
- **Depends on:** Task 4 and a local clone of the current staging database. Completion-data checks
  additionally require access to the private originals through the normal Django/worker boundary.
- **Produces:** `manage.py report_event_capture_times --event-id 9 --processor-version 2`, a
  read-only, privacy-safe JSON report with cohort/terminal/accepted/missing/failure counts, timezone
  states, warning counts, UTC min/max, event-local hourly distribution, and source-mode comparison.

- [ ] Add failing report tests proving strict event/version scope, deterministic JSON ordering,
  bounded aggregate-only output, zero object keys/filenames/raw EXIF, and explicit incomplete versus
  accepted status.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_capture_time_commands.py -k report"`
  and confirm the report command is missing.
- [ ] Implement database-side bounded aggregation plus only the small bounded conversions needed to
  compare normalized UTC instants with event-local wall hours.
- [ ] Rerun the focused report tests and expect all cases to pass.
- [ ] Start only the isolated worktree database, restore/reuse the verified current staging clone,
  and run the command in dry-run mode. Expect event ID 9, `Europe/Moscow`, 17,043 photos, no writes,
  and no data from other events.
- [ ] Run the version-2 cohort through the normal worker boundary against the restored snapshot and
  exact private originals. Do not claim this step if object access is unavailable; record the
  operational blocker distinctly from automated test results.
- [ ] Run the acceptance report and require: 17,043 terminal jobs, 17,043 accepted non-null capture
  times, zero terminal failures, zero missing outcomes, zero `inferred_none`, and a reviewed bounded
  distribution with no JPEG/MPO three-hour split. Any mismatch returns to parser diagnosis before
  the filtering specification begins.

### Task 6: Regression verification and architecture reconciliation

**Files:** modify `docs/architecture.md`; update the approved specification status and this plan's
status/evidence only with results actually observed.

- **Specification:** entire approved specification, especially Rollout and Rollback.
- **Depends on:** Tasks 1-5.
- **Produces:** verified implementation, accurate architecture text, and one reviewable unstaged
  working-tree diff ready for independent review.

- [ ] Run the focused combined suite:
  `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/picflow/tests/test_photo_migrations.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_views.py src/backend/processing/tests/test_capture_time_commands.py src/worker/tests/test_contracts.py src/worker/tests/test_metadata.py src/worker/tests/test_runner.py"`.
  Expect all selected tests to pass.
- [ ] Run `make check` once, without overlapping Django or visual suites, and require a zero exit
  status for formatting, lint, types, Django checks, migration drift, tests, and coverage gates.
- [ ] Compare the delivered behavior line by line with the specification and ADR 0017. Confirm the
  worker still has no database/permanent-storage credential and old attempts remain immutable.
- [ ] Update `docs/architecture.md` to describe the implemented event timezone and capture-metadata
  version-2 facts. Do not describe gallery filtering as implemented.
- [ ] Run `git diff --check`, inspect every changed task file, and prepare the complete unstaged diff
  including new files for one independent review gate.
- [ ] Classify review findings as blocking or future under `AGENTS.md`; return blocking fixes to the
  implementation, rerun affected focused checks, and use the same reviewer for re-review.
- [ ] After approval, rerun the focused combined suite and `make check`, stage exactly the reviewed
  task files, and create one final task commit. No intermediate implementation/review-fix commits.
- [ ] Record architecture/ADR reconciliation as: `Conforms to ADR 0002, ADR 0004, and ADR 0017; no
  new or superseding ADR required.`

## Verification

Run in this order, never concurrently:

1. Task-focused RED/GREEN commands listed above.
2. Complete worker regression: `make test TESTS="src/worker/tests"`.
3. Combined feature suite from Task 6.
4. `make check` with expected exit status 0.
5. `git diff --check` with no output.
6. Restored-snapshot dry-run with exactly event 9 / 17,043 photos / `Europe/Moscow` and no writes.
7. Restored-snapshot terminal acceptance report with 17,043 accepted non-null version-2 results,
   zero failures/missing/`inferred_none`, and reviewed privacy-safe distributions.

The plan is not complete if only fixture tests pass. The customer-filter specification remains
blocked until item 7 is evidenced.

## Operational impact and rollout

The schema migration adds a nullable event timezone and assigns `Europe/Moscow` only to event 9.
The worker image and Django image must be deployed together because both validate the same version-2
contract. Normal deployment runs the schema migration before workers claim version-2 work.

After candidate health and focused staging smoke pass:

1. verify event 9's name, published status, timezone, and 17,043-photo count read-only;
2. run the backfill command without `--apply` and review its bounded output;
3. run once with `--apply`;
4. let the normal worker process the immutable cohort and monitor existing run/job counts;
5. run the acceptance report; and
6. write the gallery-filter specification only when every event-9 gate passes.

No Yandex Cloud topology, IAM, pricing, or production activation changes are part of this plan.
Live deployment and backfill, if requested, use the normal merge/CI/staging workflow and must be
reported separately from local restored-snapshot evidence.

## Rollback

Before backfill enrollment, deploy the preceding immutable image if application validation fails.
After enrollment, stop new version-2 claiming by deploying the preceding image; retain all jobs and
attempts. Already accepted version-2 attempts are evidence and are never rewritten or deleted.

Do not restore version-1 results as semantically correct local event times. A parser defect requires
processor version 3 and a new event-scoped run. Reverting the nullable timezone schema is optional
only while no later feature depends on it; data rollback must not erase version-2 evidence.

## Open questions

None.
