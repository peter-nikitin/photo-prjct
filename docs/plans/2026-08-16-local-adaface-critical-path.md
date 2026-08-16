# Local AdaFace Critical-Path Implementation Plan

- Date: 2026-08-16
- Status: Approved in conversation on 2026-08-16
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-16-local-adaface-critical-path-design.md`](../superpowers/specs/2026-08-16-local-adaface-critical-path-design.md)
- Prerequisite specification:
  [`docs/superpowers/specs/2026-08-16-scrfd-production-detector-design.md`](../superpowers/specs/2026-08-16-scrfd-production-detector-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), Search and derived recognition
  data
- Related ADRs: [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md)
- ADR impact: None — reversible local experiment

## Goal

Deliver the approved local AdaFace critical path: run the ordinary event selfie-search UI against a
complete SCRFD-10G_KPS plus AdaFace backfill of the saved local
`cyclingrace-vechernee-sadovoe` preview corpus, while the production site remains the independent
SCRFD-10G_KPS plus SFace control.

## Scope

Implement the approved specification without scope changes. In particular, add no comparison UI,
dual-model production path, migration, remote event-media access, or staging/production mutation.
PR135's approved SCRFD production-detector contract is a prerequisite: `640×640`, confidence `0.5`,
NMS `0.4`, five landmarks, and no YuNet fallback. Execution must use
`$execute-implementation-plan`.

The combined experimental identity pins SCRFD SHA-256
`5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91`, AdaFace artifact SHA-256
`3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b`, AdaFace revision
`0dd53f188fa27968b0a1326970ebf4aeb37ce2ca`, and `scrfd-five-landmark-112x112` alignment for both
gallery and selfie processing.

## Acceptance criteria

The specification's success criteria apply. The delivery milestone additionally requires one local
URL, the frozen provisional AdaFace threshold, complete event reconciliation totals, and before/after
evidence that the already-running neighboring container is unchanged. Any earlier YuNet canary,
jobs, projections, or reconciliation evidence is invalid; the isolated database must be freshly
recloned after the SCRFD prerequisite before the new canary or full backfill.

## Implementation

### Task 1: Isolated local media and Compose runtime

**Files:** modify `docker-compose.yml`; create `docker-compose.adaface-local.yml`; create
`src/backend/processing/management/commands/seed_local_event_preview_corpus.py`; create
`src/backend/processing/tests/test_seed_local_event_preview_corpus.py`; modify `.env.example`; modify
`tests/deployment/test_deployment_scripts.py` or create a focused Compose-contract test when that
keeps responsibilities clearer.

- **Specification:** Isolated local runtime; local corpus paragraphs; failure and rollback semantics.
- **Depends on:** approved PR135 SCRFD prerequisite present in the implementation branch.
- **Produces:** a uniquely named local stack with PostgreSQL on `127.0.0.1:15433`, Django on
  `127.0.0.1:18080`, an internal MinIO endpoint, isolated volumes, and an idempotent manifest-backed
  seeding command.

- [ ] Add failing tests proving host-port parameterization, loopback binding, separate volumes,
  MinIO health dependency, read-only corpus mount, and refusal to seed without the exact event,
  complete manifest, zero unresolved rows, 17,043-photo database join, file size, and SHA-256. The
  resolved local Compose contract must also require an explicit distance threshold, set the web
  local-experiment gate, route the worker only through
  `3/face_embedding/5,1/selfie_query/2`, remove legacy type selectors, and preserve the MinIO HTTP
  exception only in the local override.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_seed_local_event_preview_corpus.py tests/deployment/test_deployment_scripts.py"`
  and confirm the new assertions fail for missing runtime and command behavior.
- [ ] Implement the local override and command. The command must dry-run by default, require
  `--event-slug cyclingrace-vechernee-sadovoe --manifest <absolute-manifest> --files-root <absolute-directory> --apply`,
  derive each exact accepted preview object key from the cloned projection, upload only to the
  configured local S3 endpoint, and publish aggregate counts/hashes without filenames or keys.
- [ ] Treat an earlier isolated AdaFace database as disposable experiment state. Before the canary,
  require the guarded staging-clone workflow to freshly replace it after the SCRFD prerequisite is
  present; do not reuse YuNet jobs, attempts, results, projections, or activation rows.
- [ ] Run the same focused tests. Confirm Compose interpolation fails when
  `ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD` is absent. For config-only inspection, supply the
  explicitly labelled, uncalibrated backfill placeholder required by Django settings:

  ```bash
  ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD=0.42 \
    docker compose -f docker-compose.yml -f docker-compose.adaface-local.yml config
  ```

  Expect resolved loopback ports, internal-only MinIO, read-only host corpus, project-scoped
  volumes, exact experimental identities, and no legacy worker type selector. `0.42` here is only a
  runnable placeholder for config/backfill; it is not a calibrated or accepted UI threshold.

### Task 2: Pinned SCRFD plus AdaFace worker adapter

**Files:** create `src/worker/photo_worker/adaface.py`; modify
`src/worker/photo_worker/face_embedding.py`; modify `src/worker/photo_worker/contracts.py`; modify
`src/worker/photo_worker/model_smoke.py`; modify `src/worker/requirements.txt`; modify
`Dockerfile.worker`; modify `src/worker/tests/test_face_embedding.py`; modify
`src/worker/tests/test_contracts.py`; modify `src/worker/tests/test_runner.py`; modify
`tests/processing/test_worker_container_contract.py`.

- **Specification:** SCRFD prerequisite and branch-local recognizer replacement; incompatible
  experimental generation.
- **Depends on:** Task 1 local worker runtime and the approved PR135 SCRFD detector implementation.
- **Produces:** one shared `adaface-ir18-webface4m` adapter returning finite normalized
  512-dimensional vectors for gallery and selfie processing from the same pinned SCRFD-10G_KPS
  detections and five landmarks.

- [ ] Add failing tests for SCRFD five-landmark
  `scrfd-five-landmark-112x112` alignment, BGR-to-RGB conversion, per-channel
  `(value / 255 - 0.5) / 0.5` normalization, shared gallery/query inference, 512-value validation,
  L2 normalization, runtime reuse, and model/dimension/payload rejection. Pin detector input
  `640×640`, confidence `0.5`, NMS `0.4`, and the exact SCRFD artifact identity in the gallery and
  selfie claim contracts.
- [ ] Run
  `make test TESTS="src/worker/tests/test_face_embedding.py src/worker/tests/test_contracts.py src/worker/tests/test_runner.py tests/processing/test_worker_container_contract.py"`
  and confirm failures identify the absent AdaFace adapter and old 128-dimensional contract.
- [ ] Implement the smallest branch-local replacement using the official Hugging Face repository
  `minchul/cvlface_adaface_ir18_webface4m` pinned to revision
  `0dd53f188fa27968b0a1326970ebf4aeb37ce2ca`. Fetch its safetensors and custom model code only at
  image build, require expected model digest
  `3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b`, and load the pinned local
  snapshot without runtime network access or an unpinned `trust_remote_code` lookup.
- [ ] Keep PR135's SCRFD detector as the sole detector for both gallery and selfie. Feed its five
  landmarks directly into AdaFace alignment; do not package, call, or fall back to YuNet or SFace in
  the experimental recognizer branch. Keep SCRFD plus SFace unchanged when the experiment gate is
  absent.
- [ ] Update terminal payload bounds from their measured worst-case 32-face 512-vector JSON size,
  retaining concurrency one and existing image/resource limits.
- [ ] Run the focused tests and build the worker image; expect the build-time smoke to load the
  pinned artifact and return one normalized 512-dimensional synthetic-face feature without network
  access after the model layer is built.

### Task 3: Experimental generation, exact ranking, and bounded event backfill

**Files:** modify `src/backend/config/settings.py`; modify `src/backend/selfie_search/apps.py`;
modify `src/backend/selfie_search/services/ranking.py`; modify
`src/backend/processing/services/enrollment.py`; modify
`src/backend/processing/services/face_quality.py`; modify
`src/backend/processing/management/commands/reprocess_event_face_embeddings.py`; modify
`src/backend/processing/management/commands/activate_face_embedding_generation.py`; modify focused
tests under `src/backend/selfie_search/tests/` and `src/backend/processing/tests/`.

- **Specification:** Incompatible experimental generation; existing search and privacy path.
- **Depends on:** Task 2 model identity, dimensions, and worker contract.
- **Produces:** a fail-closed local experiment configuration, processor generation `5`, and guarded
  event-scoped enrollment/reconciliation/activation for the exact SCRFD plus AdaFace generation.

- [ ] Add failing tests proving `MONITORING_ENVIRONMENT=local` plus an explicit local-experiment
  flag is required for AdaFace; model `adaface-ir18-webface4m`, 512 dimensions, processor version 5,
  SCRFD model/artifact, `640×640`, confidence `0.5`, NMS `0.4`,
  `scrfd-five-landmark-112x112`, and the complete configuration hash are exact; SFace generations
  and old YuNet-derived v5 rows cannot enter the cohort; ranking remains event-scoped,
  deterministic, one-best-face-per-photo, transient-query-only, and threshold-driven.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_activation.py src/backend/processing/tests/test_face_quality_reprocessing_command.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_jobs.py"`
  and confirm the new AdaFace generation tests fail until the production SCRFD plus SFace contract
  is replaced by the exact locally gated SCRFD plus AdaFace generation.
- [ ] Implement processor version 5 by reusing immutable attempts, detections, embeddings,
  projections, and event activation records. Do not add a migration or compatibility fallback. Add
  the approved local event/manifest identity as explicit command input rather than weakening the
  existing production candidate approval. Reuse applies only to rows created by the exact SCRFD plus
  AdaFace configuration; old YuNet canary/jobs are invalid and must not be reconciled or resumed.
- [ ] Make the AdaFace cosine-distance threshold an explicit finite local setting; refuse `0.363`
  and refuse startup unless the local experiment gate is active. Keep all production defaults and
  SFace behavior unchanged when the gate is absent.
- [ ] Run the focused tests plus
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run`;
  expect all tests to pass and no migration.

### Task 4: Fresh clone, seed, backfill, provisional calibration, and local HTTP smoke

**Files:** create an ignored run artifact under `var/adaface-critical-path/`; modify no tracked
production data. Update the specification only if execution reveals a contradicting implemented
boundary.

- **Specification:** Data flow; acceptance evidence; failure and rollback semantics.
- **Depends on:** Tasks 1–3 complete and reviewed.
- **Produces:** a running local URL and complete evidence for visual comparison.

- [ ] Capture `docker ps` names/ports for the neighboring project and verify resolved host ports
  `15433` and `18080` are free. Compose requires a numeric threshold even before web starts, so set
  an explicitly uncalibrated backfill-only placeholder and start only isolated PostgreSQL and MinIO:

  ```bash
  export COMPOSE_PROJECT_NAME=adaface-critical-path
  export ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD=0.42
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml up -d --wait db minio
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    run --rm --no-deps minio-init
  ```

  Do not start `web`, `worker`, or use `--profile worker up` at this stage. The placeholder satisfies
  settings/interpolation only and is not calibration evidence.
- [ ] Regardless of any prior local canary, freshly replace the isolated database after PR135 is
  present:

  ```bash
  STAGING_SSH_TARGET="petrnikitin@111.88.151.64" \
    CONFIRM_REPLACE_LOCAL_DB=yes \
    COMPOSE_FILE="docker-compose.yml:docker-compose.adaface-local.yml" \
    COMPOSE_PROJECT_NAME=adaface-critical-path \
    ADAFACE_DB_HOST_PORT=15433 \
    make db-clone-staging
  ```

  Verify the target database container belongs to this Compose project before replacement. While
  web and worker remain stopped, query the fresh clone and require `0|0|0|0` for all v5 jobs,
  attempts, projections, and activations; this stronger all-v5 check includes the invalid old YuNet
  configuration hash:

  ```bash
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    exec -T db psql --username="${DB_USER:-app}" --dbname="${DB_NAME:-app}" \
    --tuples-only --no-align --set=ON_ERROR_STOP=1 --command="
      SELECT
        (SELECT count(*) FROM processing_processingjob
          WHERE processor_type = 'face_embedding' AND processor_version = 5),
        (SELECT count(*) FROM processing_processingattempt
          WHERE processor_type = 'face_embedding' AND processor_version = 5),
        (SELECT count(*) FROM processing_photofaceembeddingprojection
          WHERE contract_version = 3 AND processor_version = 5),
        (SELECT count(*) FROM processing_eventfaceembeddingactivation
          WHERE generations @> '[{\"contract_version\":3,\"processor_type\":\"face_embedding\",\"processor_version\":5}]'::jsonb);"
  ```
- [ ] Seed while web and worker remain stopped. Run the corpus command dry-run, require event ID `9`,
  17,043 joined files, zero unresolved, and manifest SHA-256
  `62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879`; then apply and verify
  aggregate uploaded/existing counts without remote requests. Only after successful seeding start
  the long-running application services:

  ```bash
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    --profile manual-seed run --rm --no-deps seed-local-preview-corpus
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    --profile manual-seed run --rm --no-deps seed-local-preview-corpus \
    seed_local_event_preview_corpus \
    --event-slug cyclingrace-vechernee-sadovoe \
    --manifest /corpus/manifest.json --files-root /corpus --apply
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    --profile worker up -d --build web worker
  ```

  Inspect the resolved worker environment before start and require only
  `3/face_embedding/5,1/selfie_query/2`, with neither legacy processor-type selector present.
- [ ] Enroll a canonical 100-photo canary only for `cyclingrace-vechernee-sadovoe` using the exact
  SCRFD plus AdaFace configuration. Record accepted, quality-rejected, failed, unresolved,
  projection, and unexpected-attempt counts. Continue with the remaining 16,943 photos only after
  the canary is compatible and bounded; do not activate unless full-cohort failures, unresolved
  jobs, incompatible projections, and unexpected attempts are all zero.
- [ ] Select a provisional threshold at fixed low false-accept cost using the existing local
  Peakshot silver labels only as calibration evidence, freeze the threshold before opening the UI,
  and label it `local-provisional`; do not alter detector or quality thresholds. Calibration has not
  happened merely because this plan supplies `0.42` for startup/backfill. Before any UI request,
  replace the placeholder and recreate web with the separately recorded frozen value:

  ```bash
  test -n "$FROZEN_LOCAL_PROVISIONAL_THRESHOLD"
  test "$FROZEN_LOCAL_PROVISIONAL_THRESHOLD" != "0.363"
  export ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD="$FROZEN_LOCAL_PROVISIONAL_THRESHOLD"
  docker compose -p adaface-critical-path \
    -f docker-compose.yml -f docker-compose.adaface-local.yml \
    --profile worker up -d --force-recreate web worker
  ```
- [ ] Activate the complete local SCRFD plus AdaFace generation, open
  `http://127.0.0.1:18080/events/cyclingrace-vechernee-sadovoe/`, submit one bounded JPEG/PNG, and
  verify HTTP event → submission → worker → cleanup → ready result with no persisted query vector.
- [ ] Re-run `docker ps`; require the neighboring container name, image, start time, state, and port
  mapping to match the before snapshot. Record the local URL, generation, threshold, model digest,
  corpus manifest hash, and reconciliation summary in the ignored run artifact.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADRs 0017/0019/0024, and
  `docs/architecture.md`.
- [ ] Confirm the result remains a reversible local experiment with no architecture status update,
  migration, staging/production activation, or ADR impact.
- [ ] Stop for a decision instead of broadening the experiment if any production boundary would
  need to change.
- [ ] Record `ADR impact: none — reversible local experiment` in the final handoff.

## Verification

- `make test TESTS="src/worker/tests/test_face_embedding.py src/worker/tests/test_contracts.py src/worker/tests/test_runner.py src/backend/processing/tests/test_seed_local_event_preview_corpus.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_face_quality_activation.py src/backend/processing/tests/test_face_quality_reprocessing_command.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_jobs.py tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py"`
  — all selected critical-path and regression tests pass.
- `make check` — formatting, lint, type checking, full non-slow Python suite, system checks, and
  migration drift checks pass.
- `ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD=0.42 docker compose -p adaface-critical-path -f
  docker-compose.yml -f docker-compose.adaface-local.yml config` — config-only/backfill placeholder;
  only loopback host ports and project-scoped writable volumes; host corpus mount is read-only;
  web gate and exact experimental worker identities are present. The same command without the
  threshold fails interpolation.
- Worker image build-time model smoke — pinned AdaFace digest and normalized 512-dimensional output.
- Combined contract checks — exact pinned SCRFD artifact, `640×640`, confidence `0.5`, NMS `0.4`,
  `scrfd-five-landmark-112x112`, AdaFace digest/revision, and no YuNet or recognizer fallback.
- Event reconciliation — 17,043 eligible local preview inputs accounted for, no unresolved jobs,
  incompatible projections, unexpected attempts, activation mismatch, or reused YuNet state.
- Local HTTP smoke — ordinary event page and ready result return HTTP 200 after successful cleanup.

## Operational impact and rollout

Local only. The run creates an isolated Compose project, local PostgreSQL/MinIO volumes, derived
AdaFace rows in the cloned database, and ignored evidence files. It does not push an image, deploy,
change cloud IAM, or mutate staging/production. Backfill ETA and resumability are reported after a
100-photo measured SCRFD plus AdaFace canary on the mandatory fresh clone and before the remaining
16,943 photos run. Old YuNet canary timing, quality, or reconciliation results are not reused.

## Rollback

Stop only the explicitly resolved `adaface-critical-path` Compose project. Retain its volumes by
default so the backfill is resumable. Deleting those named local volumes or the saved host corpus is
not part of rollback and requires separate explicit approval. The neighboring Compose project,
staging, production, and the saved local corpus remain untouched.

## Open questions

None.
