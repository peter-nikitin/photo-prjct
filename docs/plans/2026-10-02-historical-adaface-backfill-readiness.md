# Historical AdaFace Backfill Readiness Implementation Plan

- Date: 2026-10-02
- Status: Approved for implementation on 2026-10-02; no production enrollment authorized
- Owner: project maintainer
- Related specification: [approved design](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md)
- Related architecture: [worker placement and photo ingestion](../architecture.md#current-architecture--implemented), [search](../architecture.md#search)
- Related ADRs: [0040](../adr/0040-use-pgvector-for-exact-face-search.md), [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
- ADR impact: ADR 0049 accepted on 2026-10-02 and revised before this release merged; it supersedes ADRs 0042/0046 only for post-acceptance local photo-worker recovery. The remote-only transition is a separate pre-backfill dependency.

The previously planned later local-worker retirement order is replaced by the
[remote-only transition design](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md).
This plan's vector-only AdaFace implementation remains applicable; do not execute its
older retirement task or rollout wording as production instructions.

## Goal

Implement and verify the [approved first-phase design](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#outcome-and-boundary) up to, but not including, the first production historical enrollment or event activation.

## Scope

The deliverable is a reviewable, deployable code package and operator runbook for vector-only AdaFace candidate enrollment, reconciliation and event activation. The remote-only worker transition is now a separate pre-backfill dependency. This plan does not run a production backfill, switch an event, commit the pending fleet activation, remove production containers, or delete legacy SFace/JSON data.

## Acceptance criteria

The [specification's acceptance criteria](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#acceptance-criteria) govern. Before handoff, tests must prove bounded dry-run/enrollment, idempotent retry, candidate vector-only persistence, independent native eligibility and both query sources, fail-closed activation, unchanged old result links, foreground progress and a future Deploy that cannot silently revive retired local photo workers. No live acceptance claim is made from local tests.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Read-only canonical Django/Compose snapshot at 2026-10-02 09:05 UTC: 9 SFace events (1 published, 8 unavailable), 17,310 photos; 17,297 satisfy the current private-original predicate and 17,194 have accepted `preview-small-v1` derivatives. The difference, including 13 without complete original metadata, is an explicit pre-enrollment blocker to classify, not a success count. For these events, current face jobs are 17,043 succeeded `3/face_embedding/4`, 17,194 succeeded `2/face_embedding/2`, and 6 succeeded `1/face_embedding/1`; 17,200 face states succeeded. Old attempts include 1 failed and 11 expired, with no in-progress or active/expired lease. Accepted projections match those succeeded job counts; 94,039 SFace vector rows exist for these events. Globally JSON and pgvector embedding rows each number 202,916. Existing media references use private `originals/` and `derivatives/previews/.../preview-small-v1/` prefixes; this change creates no Object Storage artifact. The canonical Compose has web/db/nginx/import/commerce/certbot running and no local photo/selfie containers; the remote fleet receipt is `verified`, not `committed`. Refresh every count, source reference, lease, image and fleet state before any live enrollment.
- [x] **Compatibility matrix.** Old SFace jobs/results remain readable and active for SFace events under the current compatible release; new `3/face_embedding/5` candidate attempts are additional evidence and do not change an event's active generation. Candidate completion writes AdaFace `FaceEmbeddingVector` only. Current AdaFace events with parallel JSON evidence continue to work, while a newly activated vector-only AdaFace event requires the native reader and a compatible web/worker image; an old legacy-reader or image rollback must be rejected. Existing ready result snapshots remain immutable and render with current presentation authorization. If a candidate callback reaches old code or a new code path sees an unsupported row/processor identity, fail closed rather than reinterpret it.
- [x] **Reviewed data-state migration or reset semantics.** No schema/data reset and no old-row purge. Bounded explicit enrollment creates/reuses immutable candidate jobs; terminal failures retain their attempt evidence and require an explicit reviewed retry or disposition. Accepted candidate projection/vector evidence is reconciled per photo before event activation. Old SFace state remains the serving generation until switch; after switch, recovery uses compatible AdaFace evidence, not SFace or incomplete JSON. No original, preview, watermark, bib or saved result is rewritten.
- [x] **End-to-end contract sizing.** The representative maximum is one `3/face_embedding/5` terminal response with 32 faces and 512 components per embedding. Keep the existing AdaFace transport budget of 384 KiB from worker serialization through the private HTTP client, Django's `PHOTO_PROCESSING_MAX_REQUEST_BYTES`, callback validation and PostgreSQL attempt/vector persistence; do not reuse the 128 KiB SFace budget. Add a test exercising this maximum and rejecting an oversized result, including the actual JSON encoding. The private worker route does not pass through the public Nginx body limit; verify its actual edge limit in the integration test.
- [x] **Previous-snapshot upgrade rehearsal.** Build a database fixture from the prior release shapes with accepted SFace projections, a failed attempt, a retryable/expired lease, stale state, a published preview, a saved result link and a never-enrolled valid photo. The planned outcome is: all old rows and links remain intact; active/expired claims use existing lease recovery; only the explicit bounded candidate cohort is enrolled; repeated enrollment creates no duplicate exact job or current vector; a missing source and unexplained old searchable face block activation. Run this fixture against the new package before deployment; its GREEN evidence is a release gate.
- [x] **Staged activation and rollback order.** Deliver code behind an operator-only dry-run/apply boundary, deploy a compatible web/worker SHA, read back release and fleet state, and require the existing `complete` action before first new-only candidate publication. Freeze fresh event/source inventory, enroll a small bounded batch, reconcile durable outcomes, then proceed event by event only after separate live approval. Stop on source gaps, technical failures, queue pressure, stale telemetry or incompatible reader setting. Before an event switch, keep SFace active; after a switch, recover forward or to a fully compatible pinned image. Local photo-worker removal is later than complete backfill and remote acceptance, with a separate reviewed deployment.
- [x] **Supported bounded operational commands.** Add one management command for privacy-safe cohort/status dry-run and explicit `--apply --event-id --limit` enrollment (positive bounded limit, no implicit all-events apply); add separate read-only reconciliation and guarded per-event activation with exact confirmation. Use existing `report_worker_pool_state`, `observe_worker_pool_cloud`, remote fleet `status`/`verify`, and canonical Deploy only for release operations. Document exact syntax and expected scalar receipts in the runbook. Do not use `backfill_pgvector_face_embeddings` or `--local-adaface` for production enrollment; do not run broad Docker prune or direct SQL updates.

## Implementation

Execute approved tasks with `$execute-implementation-plan`; use its implementer/reviewer loop, and `$select-verification-suites` for every final package.

### Task 1: Vector-only candidate publication and native eligibility

**Files:** `src/backend/processing/services/vector_embeddings.py`, `src/backend/processing/services/jobs.py`, `src/backend/processing/services/face_cohort.py`, `src/backend/selfie_search/services/vector_ranking.py`, `src/backend/selfie_search/services/submission.py`, `src/backend/selfie_search/services/read_selection.py`; focused tests in `src/backend/processing/tests/test_vector_embeddings.py`, `test_face_cohort.py`, `test_pgvector_callbacks.py`, `src/backend/selfie_search/tests/test_submission.py`, `test_read_selection.py`, `test_vector_ranking.py`.

- **Specification:** [Candidate publication](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#event-scoped-bounded-generation-replacement), [reconciliation](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#reconciliation-and-activation).
- **Depends on:** None.
- **Produces:** accepted AdaFace candidate vectors and exact native cohort/gallery-source eligibility without `FaceEmbedding`; new-only events cannot select an incomplete legacy reader.

- [ ] Add failing tests for vector-only accepted completion, rejected/failed faces, independent native identity and gallery-source lookup, legacy-reader denial for new-only events, and no change to pre-existing JSON-backed events.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_vector_embeddings.py src/backend/processing/tests/test_face_cohort.py src/backend/selfie_search/tests/test_submission.py"`; observe intended RED assertions.
- [ ] Implement the smallest accepted-candidate path while retaining parallel writes for ordinary current work. Keep immutable attempt/detection/projection relationships and exact event/model scoping.
- [ ] Rerun the focused suites and the selected reader/ranking regressions; expect GREEN, including the 32-face response size and fail-closed missing-vector test.

### Task 2: Bounded production enrollment and per-event reconciliation

**Files:** `src/backend/processing/services/enrollment.py`, `src/backend/processing/services/face_quality.py`, new focused service/management-command modules under `src/backend/processing/management/commands/`, `src/backend/processing/tests/test_enrollment.py`, `test_face_quality_reprocessing_command.py`, `test_face_quality_activation.py`; optionally a new focused command test file. Do not repurpose `reprocess_event_face_embeddings --local-adaface` or the pgvector copy command.

- **Specification:** [Event-scoped, bounded generation replacement](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#event-scoped-bounded-generation-replacement), [reconciliation and activation](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#reconciliation-and-activation).
- **Depends on:** Task 1's vector-only candidate and cohort identity.
- **Produces:** explicit dry-run/apply bounded event enrollment; privacy-safe backlog/status report; exact terminal disposition and per-event activation guard.

- [ ] Add failing tests for published/unavailable/hidden photos, missing source or preview, bounded positive limits, repeat/resume, stable configuration identity, failure/quality outcomes, active leases, divergent projection/vector, and unchanged event generation on every blocker.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_reprocessing_command.py src/backend/processing/tests/test_face_quality_activation.py"`; observe intended RED assertions.
- [ ] Implement explicit operator controls using existing job/lease/attempt protocol; never enqueue the whole historical corpus by default. Activation freezes/drains old queued searches and rejects incomplete native evidence and unsafe reader state.
- [ ] Rerun the focused suites; expect GREEN and deterministic scalar reports without photo IDs, object keys, embeddings or bearer URLs.

### Task 3: Foreground progress and offline dependent readers

**Files:** `src/backend/processing/services/jobs.py`, `src/backend/processing/services/worker_pool_state.py`, `src/backend/processing/services/face_cluster_corpora.py`, `src/backend/processing/services/vector_reconciliation.py`, `src/backend/selfie_search/services/submission.py`, `src/backend/processing/tests/test_jobs.py`, `test_worker_pool_state_command.py`, `test_face_cluster_corpora.py`, `test_vector_reconciliation.py`, `src/backend/selfie_search/tests/test_jobs.py`.

- **Specification:** [Event-scoped, bounded generation replacement](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#event-scoped-bounded-generation-replacement), [reconciliation and activation](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#reconciliation-and-activation).
- **Depends on:** Tasks 1–2.
- **Produces:** foreground jobs progress while paused/bounded historical work remains separately visible; no offline corpus or reconciliation path mistakes absent JSON for absent vectors.

- [ ] Add failing mixed-queue and corpus/reconciliation tests, including a live foreground photo arriving during historical backlog, old-generation cluster isolation and an empty claimable queue with not-yet-enrolled work.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_face_cluster_corpora.py src/backend/processing/tests/test_vector_reconciliation.py"`; observe intended RED assertions.
- [ ] Implement only the scheduling and dependent-reader changes needed by those tests; preserve autoscaler input as claimable jobs plus active attempts, without treating unenrolled backlog as cloud demand.
- [ ] Rerun focused suites; expect GREEN and no change to current-event processing priority or saved result behavior.

### Task 4: Release and local-worker-retirement preparation

**Files:** `deploy/apply-deployment.sh`, `deploy/run-remote.sh`, `docker-compose.yml`, `deploy/worker-pools/release.py`, `docs/runbooks/worker-pools.md`, new `docs/runbooks/historical-adaface-backfill.md`; `tests/deployment/test_worker_pool_release.py`, `tests/deployment/test_adaface_local_compose.py`.

- **Specification:** [Remote-worker proof and on-host retirement](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md#remote-worker-proof-and-on-host-retirement); [ADR 0049](../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md).
- **Depends on:** Tasks 1–3 and accepted ADR 0049.
- **Produces:** documented, guarded future retirement path that cannot activate until all cohort and remote proof conditions hold; compatible remote-only release recovery instructions. This task does not execute `complete`, remove a live container or change an Instance Group.

- [ ] Add failing deployment tests that reject premature removal, incompatible rollback and any ordinary Deploy attempt to revive local photo claims after retirement; preserve web/db/import/commerce/private API/metrics services.
- [ ] Run `make test TESTS="tests/deployment/test_worker_pool_release.py tests/deployment/test_adaface_local_compose.py"`; observe intended RED assertions.
- [ ] Implement minimal guarded deployment/Compose changes and the operator runbook. Require separate live approval for `complete`, backfill enrollment, event switches and local cleanup; report exact current/desired state, evidence and rollback at each gate.
- [ ] Rerun focused deployment and operational tests; expect GREEN, including initial-stage `abort` unchanged and established remote `rollout`/`rollback` within cap one.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the delivered code with the approved specification and ADRs 0040/0042/0046/0049; update `docs/architecture.md` only for implemented facts, not projected live success.
- [ ] Confirm old SFace/JSON/Python paths remain for current evidence and ready result links, while the follow-on retirement remains explicit.
- [ ] Record the exact code-ready, deployed, live-verified and backfill-not-started states separately in the PR.

## Verification

For each task, preserve RED/GREEN evidence and use `make test TESTS="<exact changed test paths>"`. After the final code change run `.venv/bin/pre-commit run --files <exact changed Python paths>`, `make static`, `.venv/bin/python scripts/select_test_suites.py select --base origin/main`, `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main`, `make check` and each selector-required expensive target with matching final-package fingerprint. Run the exact previous-snapshot upgrade fixture, 32-face transport test and deployment rollback test before the PR. After push, wait for every required CI job to finish GREEN; no pending job is a pass.

## Operational impact and rollout

Code and operator controls may be merged/deployed under canonical Deploy after review, but the default runtime must continue serving current generations without any historical enrollment. The currently `verified` fleet must first be committed and the remote-only package deployed under the separately reviewed [transition design](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md). Refresh event/media/jobs/lease/fleet/metric inventories and resolve all source gaps before the first bounded candidate batch. Later operations reconcile/activate one event at a time and prove sustained remote bulk wake/idle/disk deletion and warm selfie results. The current `0..1` bulk and `1..1` selfie ceilings and cloud IAM/network scope remain unchanged.

## Rollback

Before candidate event activation, stop enrollment and keep old SFace generation serving; a compatible code rollback may retain unused candidate rows. After an event activates vector-only AdaFace, do not roll back to a JSON-dependent reader, SFace or an image missing the new contract; pause affected work and recover with a pinned compatible release. Worker recovery follows ADR 0049 remote-only placement once the pre-backfill transition commits. No database restore, old-vector deletion or broad image prune is part of this plan.

## Open questions

None for the pre-backfill code package. The 13 missing-original-metadata photos, 116 photos without accepted previews, current `verified` fleet state, fresh cloud read-back and live search/worker proof are **operational stop conditions**, not assumptions of success. They must be resolved or explicitly dispositioned under the specification before production enrollment or activation; this plan authorizes neither.
