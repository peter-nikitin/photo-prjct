# Photo Capture-Time Projection Implementation Plan

- Date: 2026-08-08
- Status: Release A operational gate accepted; Release B candidate locally verified and awaiting
  review, pull request, CI, and normal staging deployment
- Current evidence: Release A was accepted on staging at `41e3068`; Release B has separate local
  reconciliation, benchmark, integrated-suite, visual-suite, and quality-gate evidence. Release B
  review, PR, CI, deployment, live candidate checks, and customer acceptance remain separate
  pending states.
- Owner: project maintainer
- Related specification:
  [Photo Capture-Time Projection Design](../superpowers/specs/2026-08-08-photo-capture-time-projection-design.md)
- Related architecture:
  [Current architecture — implemented](../architecture.md#current-architecture--implemented),
  [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing), and
  [Search](../architecture.md#search)
- Related ADRs: [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0022](../adr/0022-use-numbered-gallery-pages.md), and
  [ADR 0027](../adr/0027-project-capture-time-onto-photo.md)
- ADR impact: ADR 0027 is accepted. The implementation must conform to ADRs 0002, 0017, 0022,
  and 0027 and supersedes none of them.

## Goal

Deliver the approved synchronous `Photo` capture-time projection in Release A, prove its accepted
global backfill and live write consistency, then deliver the projection-only gallery reader in a
separate Release B after the required operational gate.

## Scope

Implements the approved specification without scope changes. Release A and Release B are separate
pull requests/deployments. Execution uses `$execute-implementation-plan` and must stop at the
Release A operational gate until Release A is merged, deployed, globally backfilled, reconciled,
and explicitly accepted. Release B must not be committed to the Release A branch or deployed in the
same service switch.

## Acceptance criteria

The specification's acceptance criteria are authoritative. Sequence-dependent delivery checks are:

- Release A deploys nullable projection schema and atomic projection writers while the customer
  gallery continues using the direct current-v2 evidence query;
- the global Release A report has zero missing, mismatching, stale, extra, partial-pair, or
  unsupported-version projections, and event 9 reports exactly 17,043 exact source/value pairs;
- a live transition smoke test proves post-deploy enrollment clears and accepted completion
  publishes the projection without changing immutable evidence;
- Release B begins only from the accepted deployed Release A revision and passes a final global
  report immediately before candidate switch;
- Release B's event-9 first/midpoint/last benchmark passes every database and rendered ratio at no
  more than 2x its matching unfiltered baseline; and
- the implementation, PR, CI, merge, deployment, backfill, live reconciliation, and customer
  acceptance states are recorded independently.

## Global constraints

- Immutable current accepted `capture_metadata` version-2 evidence is the sole source of truth.
- The projection pair is both null or both non-null and never accepts version 1, a future version,
  stale/late/failed/unaccepted evidence, or manual input.
- Every projection-aware path follows the complete applicable lock order
  `Event -> Run -> Job -> Photo -> State -> Attempt`, never acquiring leftward.
- Backfill discovers identifiers, locks in that order, re-reads under lock, and retries on identity
  change before publishing; no earlier discovered value may be written.
- Release A is writer/direct-reader; Release B is writer/projection-reader and removes the direct
  JSON join/cast path.
- Commands emit aggregate bounded JSON only: no filenames, photo IDs, storage keys, EXIF values,
  individual timestamps, customer identifiers, or biometric data.
- There is no schema/data rollback that rewrites or deletes immutable attempts.

## Implementation

### Task 1: Add the projection schema and atomic lifecycle writer

**Files:** modify `src/backend/picflow/models.py`, create
`src/backend/picflow/migrations/0008_photo_capture_time_projection.py`, modify
`src/backend/picflow/gallery.py`, `src/backend/picflow/tests/test_gallery.py`,
`src/backend/processing/services/jobs.py`, `src/backend/processing/services/enrollment.py`, and
focused tests under `src/backend/picflow/tests/`, `src/backend/processing/tests/test_jobs.py`,
`src/backend/processing/tests/test_enrollment.py`, and `src/backend/processing/tests/test_views.py`.

- **Specification:** Data model; Source of truth and projection state; Synchronous freshness
  algorithm; Failure and correction semantics; acceptance criteria 1-6.
- **Depends on:** Accepted ADR 0027 and the existing version-2 result validator.
- **Produces:** Nullable `Photo.capture_time`, nullable `Photo.capture_time_source_attempt`, their
  both-null/both-non-null constraint, `(event, capture_time)` index, and one internal projection
  transition interface used by enrollment and terminal completion.

- [ ] Add failing migration/model tests for field nullability, `PROTECT` source behavior, pair-shape
  constraint, index definition, and no data population during schema migration.
- [ ] Add failing lifecycle tests proving version-2 current non-null success atomically publishes
  the exact aware UTC value and exact accepted attempt; valid null success publishes a null pair.
- [ ] Add a failing Release A gallery regression under the migrated model. Rename only the direct
  JSON query's annotation/filter alias from `capture_time` to a non-field name such as
  `direct_capture_time`, because Django rejects an annotation that collides with the new model
  field. Preserve the direct evidence joins/cast and prove Release A still reads them rather than
  the projection.
- [ ] Add failing transition tests proving enrollment/reprocessing clears the pair, and retry,
  terminal failure, cancellation, expired/late/stale/duplicate, version-1, wrong-processor, and
  non-current attempts cannot publish it.
- [ ] Add transaction-rollback tests proving accepted state cannot commit without its projection
  transition and projection cannot commit without the accepted state.
- [ ] Add concurrency/lock-order tests around the real enrollment and completion services. Record
  lock acquisition through a narrow tested helper and prove every applicable path follows
  `Event -> Run -> Job -> Photo -> State -> Attempt`; reproduce and eliminate the former
  `Photo -> Job` versus `Job -> Photo` inversion.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_views.py"`
  and record the expected RED failures before production changes.
- [ ] Implement the smallest projection transition and lock-order refactor. Parse only the already
  validated canonical result; do not create a second result validator, signal, async job, generic
  projection framework, or manual correction API.
- [ ] Generate/check migration state, then rerun the focused command and require all selected tests
  to pass. Run MyPy on changed production modules and `git diff --check`.

### Task 2: Add privacy-safe global backfill and reconciliation

**Files:** create
`src/backend/picflow/management/commands/rebuild_photo_capture_time_projection.py`,
`src/backend/picflow/management/commands/report_photo_capture_time_projection.py`, and
`src/backend/picflow/tests/test_capture_time_projection_commands.py`; reuse a focused service module
only if needed to keep command orchestration out of command classes.

- **Specification:** Backfill and reconciliation contract; Privacy and authorization; acceptance
  criteria 7-8 and 13.
- **Depends on:** Task 1's schema, transition interface, and complete lock order.
- **Produces:** Two aggregate JSON interfaces:
  `rebuild_photo_capture_time_projection (--event-id N | --all-events) [--apply]` and
  `report_photo_capture_time_projection (--event-id N | --all-events) [--require-clean]`.

- [ ] Add failing command tests for mutually exclusive exact event/all-event scopes, dry-run
  default, explicit `--apply`, deterministic aggregate JSON, idempotency, bounded batches, no
  authoritative-evidence writes, and no row-level/private values.
- [ ] Add failing cohort tests covering exact current accepted v2 source/value projection; null,
  partial-pair, missing, mismatching, stale, extra, other-event, version-1/future-version, failed,
  cancelled, and unaccepted states; event 9 exact-count enforcement; and zero projections for
  events without qualifying evidence.
- [ ] Add a concurrency regression that changes current identity between discovery and lock
  acquisition, proves the earlier value is never written, and verifies retry under the Task 1 lock
  order without deadlock.
- [ ] Add SQL mutation guards proving rebuild changes only the two `Photo` projection columns and
  report changes nothing. `--require-clean` must emit the aggregate failed report before exiting
  nonzero on any mismatch.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_capture_time_projection_commands.py"`
  and record the expected RED failures before implementing the commands.
- [ ] Implement event-scoped batches and all-event enumeration without loading the corpus into
  memory or holding one transaction across events. Re-read authoritative identity/result only after
  ordered locks are held; retry boundedly on identity change and fail safely after exhaustion.
- [ ] Rerun the focused command and require all tests to pass. Run Ruff/MyPy on new modules and
  `git diff --check`.

### Task 3: Complete and verify Release A without switching gallery reads

**Files:** modify `docs/architecture.md`, `docs/engineering-jobs.md`, `docs/product-jobs.md`, the
approved specification and this plan only for observed evidence/status; modify deployment tests or
runbooks only when needed for the Release A operator commands. Do not modify the projection reader
in `src/backend/picflow/gallery.py` during this task beyond Task 1's completed direct-reader alias
rename.

- **Specification:** Release A portion of Rollout and cutover contract; Privacy and authorization;
  acceptance criteria 10, 12, and 14.
- **Depends on:** Tasks 1-2.
- **Produces:** One reviewable Release A diff/PR whose deployed application writes the projection
  but still reads direct current-v2 evidence.

- [x] Run the integrated Release A suite:
  `make test TESTS="src/backend/picflow/tests src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_views.py tests/deployment tests/test_repository_foundation.py"`.
- [x] Run the existing visual suite once and require all no-JavaScript and screenshot cases to pass;
  then run `make check` separately and require zero exit status.
- [x] On the accepted local clone, apply migrations, confirm the gallery remains direct-read, run
  global rebuild dry-run, explicit apply, and `report_photo_capture_time_projection --all-events
  --require-clean`. Require event 9 to report 17,043 exact pairs and every mismatch category zero.
- [x] Verify idempotency by rerunning apply and requiring zero changed rows; rerun the capture-time
  source report and prove authoritative attempt/state counts and accepted values are unchanged.
- [x] Update current documentation only for locally implemented/verified Release A facts. Record
  local clone, CI, deployed Release A, live backfill, and live transition evidence separately; do
  not claim Release B or customer cutover.
- [x] Obtain final whole-Release-A review, rerun changed focused checks, `make check`, visual suite,
  migration drift, and `git diff --check`, then open the Release A PR. Merge/deployment remain
  separate states and require their normal authorization/check gates.

## Release A operational evidence status — 2026-08-08

Release A was accepted on staging at `41e3068` after deployment run `31248157078`, final global
reconciliation at 17,043/17,043 event-9 source/value pairs, and a transaction-rollback lifecycle
smoke that cleared then republished the projection. It remains the deployed synchronous writer and
direct current-v2 JSON/cast reader.

The integrated Release A suite passed 539 tests with 2 skipped and 43 deselected. The visual suite
passed 92 tests in 1.2 minutes after the `<=30` index fix. `make check` passed with Ruff/format/MyPy clean,
1,591 tests passed, 3 skipped, 43 deselected, 83.53% coverage, a clean Django check, and no
migration drift.

The accepted local staging clone contains 9 events and 17,310 photos; event 9 contains 17,043.
Before backfill, event 9 had 17,043 accepted results, 17,043 non-null results, 17,043 terminal jobs,
and 17,043 version-2 jobs, with zero missing or terminal failures, status `accepted`, and timezone
`Europe/Moscow`. The global dry
run reported `would_change=17043`, `unchanged=267`, `events=9`, `photos=17310`, and zero
exhausted/retries/skipped. Apply changed 17,043 and left 267 unchanged. The `--require-clean` report
was clean with exact/projection/qualifying non-null counts of 17,043 and zero missing, mismatching,
stale, extra, partial, or unsupported rows; event 9 was accepted at exactly 17,043/17,043. The
idempotent apply changed 0 and left 17,310 unchanged, and the authoritative after-report was
identical and accepted.

This historical local-clone evidence supported the accepted Release A operation. It is distinct
from the later Release B candidate and does not itself evidence Release B review, CI, deployment,
or customer acceptance.

### Operational gate: Accept deployed Release A before Release B

- [x] Merge only after GitHub checks are green and deploy the exact merged Release A revision through
  the normal workflow.
- [x] Verify staging health and exact deployed image, then run global rebuild dry-run/apply and final
  `report_photo_capture_time_projection --all-events --require-clean` on the real database.
- [x] Require event 9: 17,043 current accepted v2 evidence rows, 17,043 exact projection pairs, zero
  missing/mismatch/stale/extra/partial/unsupported rows, and unchanged immutable evidence counts.
- [x] Exercise one authorized capture-metadata lifecycle smoke or equivalent controlled fixture on
  the deployed Release A: enrollment clears projection and accepted completion republishes it in the
  same transaction. Do not reprocess customer event 9 merely for the smoke.
- [x] Record terminal aggregate evidence and explicitly accept Release A. If any check fails, keep
  the direct reader active, repair/rebuild Release A, and do not create or implement Release B.

### Task 4: Implement Release B projection-only gallery reads and candidate gates

**Files:** on a fresh Release B worktree based on accepted Release A, modify
`src/backend/picflow/gallery.py`, `src/backend/picflow/tests/test_gallery.py`,
`src/backend/picflow/tests/test_views.py`,
`src/backend/picflow/management/commands/benchmark_event_gallery_time_filter.py`,
`src/backend/picflow/tests/test_gallery_time_filter_benchmark.py`, `deploy/apply-deployment.sh`,
and focused deployment tests/workflow wiring required for candidate pre-switch checks.

- **Specification:** Gallery query behavior; Release B portion of Rollout and cutover contract;
  acceptance criteria 9-12.
- **Depends on:** Explicitly accepted deployed Release A operational gate. This task is blocked until
  that evidence exists.
- **Produces:** Projection-only filtered gallery queryset and candidate pre-switch reconciliation/
  benchmark gates; no direct processing JSON join/cast fallback remains.

- [x] Add failing gallery tests proving filtered reads include/exclude solely by `Photo.capture_time`
  after existing media eligibility, preserve inclusive bounds/order/page size, exclude nulls, and
  issue no capture-metadata processing joins or JSON casts. Preserve all approved form/UI behavior.
- [x] Add failing regression tests proving unfiltered galleries remain independent of projection and
  no media/selfie/privacy/authorization boundary changes.
- [x] Update benchmark tests so the command requires a clean global projection report, measures the
  projection query, retains aggregate-only output, and fails before emitting success on drift,
  request errors, timeouts, or any unrounded ratio greater than 2.
- [x] Add failing deployment tests proving candidate pre-switch runs final global `--require-clean`
  and event-9 first/midpoint/last benchmark before service switch; any nonzero exit leaves Release A
  active. No backfill or evidence mutation occurs in Release B deployment.
- [x] Run the affected focused gallery, benchmark, and deployment suites and record RED before
  implementation.
- [x] Replace the direct join/cast branch with the projection range and remove obsolete imports,
  annotations, tests, and fallback paths. Add the smallest candidate pre-switch invocation through
  the established deployment entrypoint.
- [x] Rerun all focused suites, MyPy/Ruff, and `git diff --check` and require success.

### Task 5: Verify, document, and deliver Release B

**Files:** modify `docs/architecture.md`, `docs/product-jobs.md`, `docs/engineering-jobs.md`, the
approved specification/plan status, and the sanitized Release B local-clone benchmark report; no new
ADR is expected.

- **Specification:** all sections and acceptance criteria.
- **Depends on:** Task 4 and accepted Release A evidence.
- **Produces:** One reviewable Release B PR and evidence package ready for normal CI/deployment.

- [x] On an immutable accepted local-clone snapshot, run final global projection reconciliation,
  then event-9 benchmark pages `1,mid,last`. Retain sanitized JSON only when every database and
  rendered ratio is at most 2x and no private value appears.
- [x] Run integrated gallery/processing/projection/deployment tests, the visual suite once, and
  `make check` separately. Require all commands to exit zero; run `git diff --check` and migration
  drift checks.
- [x] Update implemented architecture facts to distinguish Release A writer deployment, Release B
  projection reader, source-of-truth/rebuild boundary, global reconciliation, and local versus live
  evidence. Record conformance to ADRs 0002, 0017, 0022, and 0027.
- [ ] Obtain final whole-branch review, fix blocking findings through one review loop, rerun final
  verification, open the Release B PR, and wait for green CI before merge.
- [ ] After normal staging deployment, verify exact image/health, final global reconciliation,
  event-9 17,043 source/value pairs, projection benchmark ≤2x on every page, gallery filter/reset/
  pagination, selfie/gallery-origin paths, media authorization, and no direct evidence query path.
  Only this live evidence authorizes customer acceptance.

## Verification

Run task RED/GREEN commands in order. For each release, run focused suites before broad suites and
never overlap full Django and visual runs. Final Release A and Release B checks each require:

1. affected focused Python suites with zero failures;
2. `npm run test:visual` with all visual/no-JavaScript tests passing;
3. `make check` separately with zero exit status;
4. `git diff --check` with no output and no migration drift;
5. aggregate projection report with all mismatch counts zero;
6. unchanged immutable source report; and
7. independent task reviews plus a whole-release review.

Release B additionally requires the sanitized accepted-clone and live event-9 benchmark with every
filtered/unfiltered database and rendered ratio at most 2x.

## Operational impact and rollout

This is a two-release schema/data/application rollout on the existing Django/PostgreSQL/Docker
Compose deployment. It adds no service, broker, storage object, secret, worker protocol, or pricing
change.

Release A adds nullable schema and synchronous writes, then performs a retryable global operational
backfill while direct reads remain active. Release B is a later revision based on accepted Release A;
its candidate gates are read-only and switch the service only after final reconciliation and
performance acceptance. Never combine the two service switches or bypass the normal GitHub deploy
workflow.

## Rollback

Before Release B, rollback keeps or redeploys Release A's direct reader; projection columns may
remain populated because they are rebuildable and do not change authority. Do not remove Release A
writers while a later projection reader is live.

A failed Release B candidate never switches service. After a successful Release B switch, application
rollback must return to Release A, which continues maintaining projection while reading direct
evidence. Schema removal is a separately reviewed later cleanup and is never part of incident
rollback. Immutable attempts, states, runs, jobs, and result JSON are never deleted or rewritten.

## Open questions

None.
