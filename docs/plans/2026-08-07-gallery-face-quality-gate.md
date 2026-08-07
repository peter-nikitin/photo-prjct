# Gallery Face Quality Gate Implementation Plan

- Date: 2026-08-07
- Status: Proposed; execution approval pending
- Owner: project maintainer
- Related specification:
  [`2026-08-07-gallery-face-quality-gate-design.md`](../superpowers/specs/2026-08-07-gallery-face-quality-gate-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), photo-processing control plane,
  face-search cohort selection, derived recognition data, and face-cluster corpus lifecycle
- Related ADRs:
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0025](../adr/0025-expand-selfie-search-with-face-clusters.md)
- ADR impact: Conforms to ADR 0017, ADR 0019, and ADR 0025; no new or superseding ADR is required
- Execution: use `$execute-implementation-plan` for task-isolated implementation and review

## Goal

Implement the approved gallery-face quality gate, preserve baseline and candidate generations side
by side, and produce manually reviewable local old/new evidence on the exact photo corpus of the
latest published event before any search activation.

## Scope

Implement the approved specification without scope changes. This plan delivers the production code,
local benchmark tooling, and local comparison evidence. It does not deploy or activate the new
generation on staging or production.

## Global constraints

- Preserve every historical attempt, detection, embedding, cluster corpus, and bearer result.
- Keep YuNet, SFace, 128-dimensional normalized embeddings, direct threshold `0.363`, and existing
  cluster thresholds unchanged.
- Reject a face before embedding persistence; never implement the quality rule inside ranking.
- Treat ambiguous and singly borderline faces as accepted.
- Keep photos, crops, labels, query artifacts, vectors, bearer values, and absolute private paths
  outside Git and ordinary logs.
- Do not enable the candidate generation merely because processing or benchmark execution succeeds.
- Run Python through `make test`, `make check`, or an explicit `.venv/bin/...` executable.
- Do not run overlapping full Django, experiment, or visual suites.

## Acceptance criteria

Acceptance is defined by the specification's
[Acceptance criteria](../superpowers/specs/2026-08-07-gallery-face-quality-gate-design.md#acceptance-criteria).
Execution may reach “code complete” before it reaches “benchmark approved”; record those states
separately. The local evidence gate requires zero manually labelled clear-face losses, zero manually
confirmed relevant-result losses, and zero unresolved corpus items.

## File and module structure

- `src/worker/photo_worker/face_quality.py` owns the production quality value types, normalized-crop
  measurement, validation, and recall-first decision table.
- `src/worker/photo_worker/face_embedding.py` owns detection, invokes the quality module, and invokes
  SFace only for accepted faces.
- `src/worker/photo_worker/contracts.py` owns the v3 worker configuration and typed terminal payload.
- `src/backend/processing/models.py` owns immutable rejected detections, generation-bound accepted
  projections, event activation history, and benchmark approval summaries.
- `src/backend/processing/services/face_quality.py` owns backend payload validation, generation
  projection publication, benchmark-approval validation, and activation rules.
- `src/backend/processing/services/event_original_cache.py` owns read-only inventory freezing,
  resumable exact-object streaming, local verification, and atomic cache publication.
- `src/backend/processing/services/face_cohort.py` remains the single cohort eligibility seam shared
  by direct search and cluster building.
- `experiments/face_recognition_spike/face_spike/quality_comparison.py` owns private old/new
  detection matching, aggregate comparison, and manual-label validation.
- `experiments/face_recognition_spike/face_spike/quality_comparison_artifacts.py` owns atomic immutable
  comparison publication and schema loading.
- `experiments/face_recognition_spike/face_spike/quality_comparison_report.py` owns the local visual
  review bundle; it has no Django or production-media authority.
- Existing experiment index and smoke-search modules remain the search-level evaluator instead of
  creating a second ranking implementation.

## Implementation

### Task 0: Cache the latest published event originals locally

**Deliverable:** A read-only resumable operator command creates one complete verified private local
corpus that every baseline and candidate worker run reuses without downloading intact originals
again.

**Files:**

- Create: `src/backend/processing/services/event_original_cache.py`
- Create: `src/backend/processing/management/commands/cache_event_originals.py`
- Create: `src/backend/processing/tests/test_event_original_cache.py`
- Modify: `docs/engineering-jobs.md`

- **Specification:** Private local event corpus; Frozen comparison cohort; Privacy and
  authorization.
- **Depends on:** An authorized local database containing staging event metadata and existing
  private-media S3 settings supplied outside the worktree.
- **Produces:**
  `~/Documents/Projects/photo-prjct-private/event-corpora/<event-slug>/manifest.json` and a complete
  `originals/` directory whose inventory hash, sizes, ETags, and local SHA-256 values are frozen.

- [ ] Add failing service tests for deterministic latest-published selection by descending
  `start_date`, descending `end_date`, then ascending `slug`; explicit-slug selection; empty or draft
  events; duplicate photo IDs; missing original metadata; JPEG/PNG extension mapping; and safe
  generated local names.
- [ ] Add failing storage tests for manifest-first publication, conditional `GetObject`, streamed
  size/SHA-256 validation, body closure, partial cleanup, atomic rename, object change, network
  interruption, and zero database/S3 writes.
- [ ] Add failing resume tests proving a verified complete file causes zero S3 calls, while only a
  missing/corrupt/partial file is fetched again; reject an event/inventory/hash mismatch, unexpected
  extra file, altered manifest, symlink, nested directory, or output path outside the selected event
  root.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_event_original_cache.py"` and confirm
  failures identify the missing service and command.
- [ ] Implement the focused cache service with an injectable read-only storage protocol. Reuse the
  existing boto3 configuration but expose only `HeadObject` and conditional streaming `GetObject`;
  do not reuse upload, copy, delete, or presigned-write methods.
- [ ] Implement `cache_event_originals` with mutually exclusive `--event` and
  `--latest-published`, default root computed from
  `Path.home() / "Documents/Projects/photo-prjct-private/event-corpora"`, and an optional explicit
  `--output-root` for tests and authorized alternate storage. Never print object keys, headers,
  credentials, URLs, or private absolute paths.
- [ ] Run `make test TESTS="src/backend/processing/tests/test_event_original_cache.py"`.
  Expected: the complete selected suite passes, a second identical invocation downloads zero bytes,
  and every failure leaves either the prior complete cache or resumable private partial state.
- [ ] Run the command against the authorized local staging clone and existing external media
  configuration. Expected: `manifest.json` reports one verified file per eligible original,
  `unresolved_count=0`, and the private output directory contains no symlinks, nested input paths, or
  unexpected files.

### Task 1: Production recall-first quality decision

**Deliverable:** A deterministic worker module calculates bounded quality evidence from a fixed
`112 x 112` grayscale bbox crop and skips SFace for rejected detections.

**Files:**

- Create: `src/worker/photo_worker/face_quality.py`
- Create: `src/worker/tests/test_face_quality.py`
- Modify: `src/worker/photo_worker/face_embedding.py`
- Modify: `src/worker/photo_worker/contracts.py`
- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_face_embedding.py`
- Modify: `src/worker/tests/test_contracts.py`
- Modify: `src/worker/tests/test_runner.py`

- **Specification:** Quality measurements; Recall-first decision rule; Failure and rollback
  semantics.
- **Depends on:** Task 0 provides the reusable real corpus; unit implementation itself has no runtime
  dependency on the cache command.
- **Produces:** `FaceQualityThresholds`, `FaceQualityEvidence`, and `evaluate_face_quality(...)` in
  `photo_worker.face_quality`; a terminal face record with `status`, `quality`, optional `embedding`,
  and optional technical `error_code`.

- [ ] Add failing decision-table tests for hard small-face rejection, hard severe blur, corroborated
  borderline blur, each single weak signal remaining accepted, inclusive boundaries, non-finite
  values, invalid geometry, and deterministic `112 x 112` crop handling.
- [ ] Run `make test TESTS="src/worker/tests/test_face_quality.py"` and confirm failures identify the
  missing production quality interface.
- [ ] Implement the frozen value types and quality calculation. Keep threshold values constructor
  inputs; do not select production thresholds in this task.
- [ ] Add failing extraction tests proving quality runs before SFace, rejected faces have no
  embedding, accepted faces retain the existing normalized vector, truncation remains a separate
  warning, and one failed face does not discard successful sibling faces.
- [ ] Extend the typed v3 terminal payload and strict configuration parser with algorithm version,
  crop size, minimum face size, severe/borderline blur thresholds, relative-area threshold, and
  confidence threshold. Remove obsolete parsing paths rather than accepting both payload shapes for
  the new identity.
- [ ] Run
  `make test TESTS="src/worker/tests/test_face_quality.py src/worker/tests/test_face_embedding.py src/worker/tests/test_contracts.py src/worker/tests/test_runner.py"`.
  Expected: all selected worker tests pass and no test expects SFace for a rejected detection.

### Task 2: Persist rejected faces without vectors

**Deliverable:** Django validates the v3 result and persists every selected face as kept,
quality-rejected, or technically failed, while creating `FaceEmbedding` only for kept faces with a
valid vector.

**Files:**

- Create: `src/backend/processing/migrations/0007_face_quality_generation.py`
- Modify: `src/backend/processing/models.py`
- Create: `src/backend/processing/services/face_quality.py`
- Modify: `src/backend/processing/services/jobs.py`
- Modify: `src/backend/processing/views.py`
- Modify: `src/backend/processing/services/reports.py`
- Modify: `src/backend/processing/tests/test_models.py`
- Modify: `src/backend/processing/tests/test_jobs.py`
- Modify: `src/backend/processing/tests/test_views.py`
- Modify: `src/backend/processing/tests/test_reports.py`
- Modify: `src/backend/processing/tests/test_view_edge_cases.py`

- **Specification:** Persistence contract; Privacy and authorization.
- **Depends on:** Task 1 terminal payload.
- **Produces:** `PhotoFaceDetection.Status.QUALITY_REJECTED`, strict v3 payload validation, and
  bounded artifact counts separated into detected, kept, quality-rejected, embedded, and technical
  failures.

- [ ] Add failing model and migration tests for the new status, terminal-attempt immutability, and
  the database invariant that a quality-rejected detection cannot own a `FaceEmbedding`.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_models.py src/backend/selfie_search/tests/test_migrations.py"`
  and confirm the missing status/constraint failures.
- [ ] Add the status and database constraint in migration `0007`; preserve existing rows exactly and
  do not rewrite historical kept detections.
- [ ] Add failing service/view tests for accepted, quality-rejected, mixed, truncated, invalid
  quality, missing quality, rejected-with-vector, accepted-without-vector, and technical-failure
  payloads.
- [ ] Implement one strict v3 validator in `processing.services.face_quality` and route terminal
  persistence through it. Do not reinterpret invalid quality evidence as a rejection.
- [ ] Extend attempt artifacts and event reports with bounded separated counts and reasons without
  logging face, photo, crop, or vector identity.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_views.py src/backend/processing/tests/test_reports.py src/backend/processing/tests/test_view_edge_cases.py"`.
  Expected: valid mixed results persist; every rejected detection has zero embeddings; malformed
  results fail atomically.

### Task 3: Make accepted face projections generation-bound

**Deliverable:** Baseline and candidate accepted attempts coexist for one photo without the current
single `PhotoProcessingState.accepted_attempt` pointer deciding face-search eligibility.

**Files:**

- Modify: `src/backend/processing/migrations/0007_face_quality_generation.py`
- Modify: `src/backend/processing/models.py`
- Modify: `src/backend/processing/services/face_quality.py`
- Modify: `src/backend/processing/services/jobs.py`
- Modify: `src/backend/processing/services/face_cohort.py`
- Modify: `src/backend/processing/tests/test_models.py`
- Modify: `src/backend/processing/tests/test_jobs.py`
- Modify: `src/backend/processing/tests/test_face_cohort.py`
- Modify: `src/backend/selfie_search/tests/test_ranking.py`

- **Specification:** New immutable processor generation; Search and corpus compatibility.
- **Depends on:** Task 2 persistence contract.
- **Produces:** `PhotoFaceEmbeddingProjection`, uniquely keyed by photo plus contract version,
  processor version, and configuration hash, pointing to one accepted immutable attempt for that
  exact generation.

- [ ] Add failing model tests for exact generation identity, same-photo baseline/candidate
  coexistence, accepted-attempt consistency, immutable terminal evidence, and prohibition of a
  projection pointing across photo, processor type, or generation.
- [ ] Extend migration `0007` to create the projection and backfill one projection for each currently
  accepted historical face attempt without changing attempts, detections, embeddings, or state
  pointers.
- [ ] Publish a projection atomically only after a valid complete face result is persisted. A newer
  attempt for one generation may replace that generation's projection but cannot touch another
  generation's projection.
- [ ] Add failing cohort tests that create baseline and candidate projections together and prove an
  explicitly selected generation returns only its own kept embeddings.
- [ ] Change `compatible_face_embedding_queryset(...)` to join the exact projection instead of the
  processor-wide `accepted_states` pointer. Retain event, photo-media, model, dimensions,
  configuration, and immutable-attempt checks.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_models.py src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_face_cohort.py src/backend/selfie_search/tests/test_ranking.py"`.
  Expected: both generations remain independently queryable and cannot be unioned accidentally.

### Task 4: Add candidate enrollment and explicit event activation

**Deliverable:** The quality-gated v3 generation can be processed without entering customer search,
and an append-only event activation selects one reviewed generation set for future searches.

**Files:**

- Modify: `src/backend/processing/migrations/0007_face_quality_generation.py`
- Modify: `src/backend/processing/models.py`
- Modify: `src/backend/processing/contracts.py`
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/services/face_quality.py`
- Create: `src/backend/processing/management/commands/activate_face_embedding_generation.py`
- Modify: `src/backend/processing/services/face_cluster_corpora.py`
- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/processing/tests/test_enrollment.py`
- Create: `src/backend/processing/tests/test_face_quality_activation.py`
- Modify: `src/backend/processing/tests/test_face_cluster_corpora.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`
- Modify: `tests/processing/test_selfie_search_e2e.py`

- **Specification:** New immutable processor generation; Search and corpus compatibility; Approval
  record; Data flow.
- **Depends on:** Task 3 projection interface.
- **Produces:** v3 original-backed and preview-backed identities, candidate-only enrollment, and
  append-only `EventFaceEmbeddingActivation` records consumed by search and corpus defaults.

- [ ] Add failing contract/enrollment tests for exact v3 configuration identity, candidate-only
  enqueue, identical original/preview input identity, and no change to the event's current search
  generations when candidate processing completes.
- [ ] Define the v3 configuration with provisional threshold candidates only for benchmark
  execution. Mark it non-activatable until Task 6 records the accepted configuration hash.
- [ ] Add failing activation tests for append-only baseline selection, candidate selection,
  rollback as a new activation row, cross-event rejection, incomplete/unapproved evidence,
  mismatched generation/configuration hash, mixed baseline/candidate sets, and idempotent exact
  replay.
- [ ] Implement event-scoped activation lookup and the guarded management command. Existing events
  without an activation row resolve to the frozen baseline generation set; this is the initial data
  state, not a runtime fallback after an explicit activation exists.
- [ ] Change new-search configuration and default cluster-corpus input to consume the event's exact
  active generation set. A candidate-only benchmark query must pass its generations explicitly and
  cannot mutate activation.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_activation.py src/backend/processing/tests/test_face_cluster_corpora.py src/backend/selfie_search/tests/test_submission.py tests/processing/test_selfie_search_e2e.py"`.
  Expected: baseline remains active through candidate processing; activation and rollback are
  explicit, event-scoped, and auditable.

### Task 5: Build immutable local detection and search comparison artifacts

**Deliverable:** The existing private experiment can compare baseline and candidate runs over the
same inventory, render every new rejection for review, validate manual labels, and compare the same
closed queries against both indexes.

**Files:**

- Modify: `experiments/face_recognition_spike/face_spike/quality.py`
- Modify: `experiments/face_recognition_spike/face_spike/analysis.py`
- Create: `experiments/face_recognition_spike/face_spike/quality_comparison.py`
- Create: `experiments/face_recognition_spike/face_spike/quality_comparison_artifacts.py`
- Create: `experiments/face_recognition_spike/face_spike/quality_comparison_report.py`
- Modify: `experiments/face_recognition_spike/face_spike/cli.py`
- Modify: `experiments/face_recognition_spike/face_spike/smoke_search.py`
- Modify: `experiments/face_recognition_spike/README.md`
- Create: `experiments/face_recognition_spike/tests/test_quality_comparison.py`
- Create: `experiments/face_recognition_spike/tests/test_quality_comparison_artifacts.py`
- Create: `experiments/face_recognition_spike/tests/test_quality_comparison_report.py`
- Modify: `experiments/face_recognition_spike/tests/test_analysis.py`
- Modify: `experiments/face_recognition_spike/tests/test_selfie_search_cli.py`
- Modify: `experiments/face_recognition_spike/tests/test_smoke_search.py`

- **Specification:** Local benchmark and calibration; Privacy and authorization.
- **Depends on:** Task 1 quality interface and Task 4 generation identities.
- **Produces:** `compare-quality`, `finalize-quality-review`, and `compare-search` CLI commands with
  atomic immutable outputs and no serialized raw embeddings.

- [ ] Add failing comparison tests for exact inventory/media hashes, deterministic bbox matching,
  old-only/new-only/matched/rejected counts, all-new-rejection review coverage, threshold-band
  sampling, unresolved photos, and separated technical failures.
- [ ] Replace the experiment's divergent OR-style quality decision with the production
  `photo_worker.face_quality` interface. Keep experiment artifacts compatible only with the new
  schema; remove obsolete quality schema handling.
- [ ] Implement atomic machine-readable comparison artifacts and an HTML review bundle whose export
  accepts only `clear`, `blurred`, `unusably_small`, or `uncertain` for every new rejection.
- [ ] Add failing finalization tests for missing/duplicate/unknown labels, any clear loss, any
  uncertain rejection, unresolved corpus items, source-hash mismatch, overwrite attempts, and
  bounded approved aggregate output.
- [ ] Add search-comparison tests that reuse the existing exact-cosine index and smoke-search path,
  apply full-photo holdout to gallery proxies, report top-1/top-5/top-10 and unique-photo deltas, and
  fail approval on a lost confirmed relevant photo.
- [ ] Run
  `.venv/bin/pytest -q experiments/face_recognition_spike/tests/test_quality_comparison.py experiments/face_recognition_spike/tests/test_quality_comparison_artifacts.py experiments/face_recognition_spike/tests/test_quality_comparison_report.py experiments/face_recognition_spike/tests/test_analysis.py experiments/face_recognition_spike/tests/test_selfie_search_cli.py experiments/face_recognition_spike/tests/test_smoke_search.py`.
  Expected: all selected experiment tests pass and immutable artifacts contain no raw embedding.

### Task 6: Calibrate and compare on the latest published event

**Deliverable:** One complete immutable local evidence package identifies an approved configuration
or records that no candidate is safe enough to activate.

**Files:**

- Private inputs/outputs outside Git: exact event photo corpus, baseline run, candidate runs, review
  crops, manual labels, query artifacts, and comparison reports
- Modify after approval only: `src/backend/processing/services/enrollment.py`
- Modify after approval only: `src/worker/photo_worker/contracts.py`
- Modify after approval only: `src/worker/tests/test_contracts.py`
- Modify after approval only: `src/backend/processing/tests/test_checks.py`

- **Specification:** Frozen comparison cohort; Detection-level comparison; Search-level comparison;
  Approval record.
- **Depends on:** Tasks 0–5 and the complete verified local corpus produced by Task 0.
- **Produces:** approved bounded summary plus exact configuration hash, or a rejected benchmark with
  no production configuration change.

- [ ] Load the complete Task 0 manifest and refuse any photo inventory, media identity, size, ETag,
  SHA-256, unexpected-file, or completion mismatch before analysis begins.
- [ ] Produce one baseline run and candidate runs beginning with the historical candidate points
  `confidence=0.82`, `minimum_face_px=32`, `relative_area=0.0009`, and `sharpness=50`, translated to
  the new severe/borderline decision without treating them as approved defaults.
- [ ] Review every newly rejected face and finalize the immutable labels. If any clear or uncertain
  face is rejected, weaken exactly one threshold and publish a new candidate run rather than
  overwriting evidence.
- [ ] Run the same closed query set against baseline and each surviving candidate with full-photo
  holdout for gallery proxies. Record every disappeared confirmed result.
- [ ] Stop and report “benchmark rejected” if no candidate reaches zero clear-face loss, zero
  confirmed relevant-result loss, and zero unresolved items. Do not tune the direct threshold.
- [ ] After human approval, replace the provisional v3 thresholds with the exact approved values,
  freeze the final configuration hash, and update the contract tests. This is the only task allowed
  to select production quality thresholds.
- [ ] Rerun Task 1, Task 4, and Task 5 targeted suites. Expected: the approved hash and thresholds are
  identical in worker, Django, and local evidence, while baseline activation remains unchanged.

### Task 7: End-to-end regression and documentation reconciliation

**Deliverable:** The approved code is CI-ready, documented as implemented locally, and still has no
staging/production activation claim.

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/superpowers/specs/2026-08-07-gallery-face-quality-gate-design.md` only if delivered
  behavior requires reconciliation without changing the approved outcome
- Modify: `tests/test_repository_foundation.py` only when a new repository-level contract needs an
  executable guard

- **Specification:** Entire approved specification.
- **Depends on:** Tasks 1–6 and an approved benchmark.
- **Produces:** reconciled implementation evidence and explicit separation of local code, local
  benchmark, activation, deployment, and live verification states.

- [ ] Run all focused worker, processing, selfie-search, experiment, migration, and E2E tests named
  by Tasks 1–6. Expected: all pass without relying on active staging configuration.
- [ ] Run `make check`. Expected: repository Python, Ruff, formatting, mypy, Django, migration drift,
  JavaScript, and configured quality gates pass with no failures.
- [ ] Run `git diff --check`. Expected: no whitespace errors.
- [ ] Update architecture and job evidence with exact implemented/local-benchmark facts. Do not mark
  the candidate deployed, activated, or customer-validated.
- [ ] Compare delivered behavior line by line with the specification and ADRs 0017, 0019, and 0025.
  Record `Conforms to ADR 0017, ADR 0019, and ADR 0025; no ADR change` in the pull request.
- [ ] Use `$execute-implementation-plan` final whole-branch review, then let the root controller run
  fresh final verification before staging the exact files and creating the task commit.

## Verification

Targeted verification is specified in each task. Final verification is:

```bash
make check
.venv/bin/pytest -q experiments/face_recognition_spike/tests
git diff --check
```

Expected outcomes: all repository checks and experiment tests pass; Django reports no migration
drift; the final diff contains no private media, crops, labels, embeddings, bearer values, secrets,
or absolute private paths.

The real local benchmark is evidence, not a unit test. Its required outcome is one immutable
complete approved run with `clear_loss_count=0`, `relevant_result_loss_count=0`, and
`unresolved_count=0`. If that outcome is not reached, the code may be complete but the generation
is not approved or activatable.

## Operational impact and rollout

1. Build and verify the private local event cache without changing remote media or database state.
2. Merge and deploy code with every event still resolving to its baseline activation.
3. Process the candidate generation for the selected event without changing active search.
4. Complete and approve the private local detection/search comparison.
5. Freeze the approved configuration in the processor identity and rebuild the candidate event
   cohort if the calibrated hash differs from a provisional run.
6. Build a new compatible face-cluster corpus only if cluster expansion will be used; never reuse or
   mutate the baseline corpus.
7. Activate the approved event generation through the guarded command in a separately authorized
   operational change.
8. Observe new-search result and quality aggregates before considering another event.

This implementation plan stops before steps 2–8 are performed against staging or production. Task
0 is an authorized read-only local operation. A
merge, deployment, activation, and live verification require separate explicit evidence.

## Rollback

Append a new event activation selecting the previous baseline generation set. New searches then use
the baseline projection again. Do not delete candidate attempts, projections, embeddings, benchmark
summaries, activation history, cluster corpora, or existing bearer results. Continue retry/cleanup
for any already queued work under the normal processing contract.

## Architecture and ADR reconciliation

The planned topology remains Django/PostgreSQL plus the existing private polled worker and local
private experiment. The generation projection and append-only event activation are PostgreSQL
control-plane records, not new services or identity systems. Final reconciliation must confirm
conformance to ADR 0017's worker authority, ADR 0019's event/query/result boundaries, and ADR 0025's
immutable generation-bound corpus rules.

## Open questions and execution blockers

- Design questions: None.
- Execution blocker for Task 0: the worktree intentionally has a test-safe `.env`. The real download
  requires an authorized local staging database clone and existing external read credentials; do
  not copy or link the main checkout's `.env` into this worktree.
