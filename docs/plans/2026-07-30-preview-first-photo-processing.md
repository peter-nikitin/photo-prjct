# Preview-First Photo Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task-by-task. The root controller
> owns review dispatch, final verification, staging, and the single implementation commit.

**Goal:** Implement the approved preview-first pipeline so newly confirmed photos publish a
normalized `preview-small-v1` before becoming gallery-visible or entering face processing.

**Architecture:** Extend the existing Django/PostgreSQL-polled worker with a versioned binary
processor. The worker uploads only to an attempt-scoped staging key; Django verifies and publishes
the derivative, then atomically enrolls preview-backed face processing.

**Tech stack:** Django 6, PostgreSQL 16, Pillow, boto3/S3-compatible Yandex Object Storage, the
standalone Python worker, Docker Compose, pytest, Ruff, mypy, and Playwright.

- Date: 2026-07-30
- Status: Approved for implementation
- Owner: project maintainer
- Related specification:
  [Preview-first photo processing](../superpowers/specs/2026-07-30-preview-first-photo-processing-design.md)
- Related architecture:
  [Current architecture](../architecture.md#current-architecture--implemented),
  [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing), and
  [Evolution stages](../architecture.md#evolution-stages)
- Related ADRs:
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0015](../adr/0015-allow-anonymous-free-event-original-delivery.md), and
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017 and ADR 0015; no new ADR is required.

## Scope

Implement the specification without scope changes. In particular, do not backfill existing photos,
add watermarks, add a second derivative, change face models or thresholds, or activate the feature
in a live environment as part of implementation.

## Global constraints

- Output is JPEG, sRGB, quality 85, with a maximum 1600-pixel long edge and no upscaling.
- Apply EXIF orientation before resize and copy no EXIF, GPS, comment, or embedded thumbnail.
- New photos use `preview_required`; existing rows remain explicitly
  `legacy_original_allowed`.
- `face_embedding` for a preview-first photo remains `not_requested` until Django accepts and
  publishes its preview.
- A preview-required gallery tile has no original fallback; `preview-large` continues to use the
  original under ADR 0015.
- The worker has concurrency one, no Django/database access, and no permanent Object Storage
  credentials.
- The worker never selects storage keys or publishes a derivative.
- Preserve immutable attempts, explicit state, bounded retries, stale-result rejection, and
  event-scoped reports.
- Implementation subagents do not create nested agents or modify Git state. The root controller
  creates one final implementation commit only after independent review and final verification.

## Cross-task interfaces

The implementation uses these stable names so adjacent tasks do not invent incompatible contracts:

- `Photo.processing_generation`:
  `legacy_original_v1 | preview_first_v1`.
- `Photo.gallery_media_policy`:
  `legacy_original_allowed | preview_required`.
- Valid pairs are
  `(legacy_original_v1, legacy_original_allowed)` and
  `(preview_first_v1, preview_required)`.
- Processor type: `generate_preview`.
- Derivative variant: `preview-small-v1`.
- `PhotoDerivative`: immutable published derivative metadata linked to `Photo` and the accepted
  `ProcessingAttempt`, unique by `(photo, variant)`.
- Preview attempt staging key:
  `processing-staging/previews/<attempt-id>/preview-small-v1.jpg`.
- Preview final key:
  `derivatives/previews/<photo-id>/preview-small-v1/<attempt-id>.jpg`.
- Preview output checksum: lowercase SHA-256 hex.
- Generic versioned input fingerprint fields:
  `object_key`, `object_size`, `object_content_type`, `object_etag`,
  `media_kind`, `pixel_width`, and `pixel_height`.
- Published preview geometry fields:
  `pixel_width`, `pixel_height`, `oriented_source_width`, and
  `oriented_source_height`.
- Preview-backed face geometry declares `coordinate_space=preview-small-v1`,
  the four published preview/source dimensions, and derives scale factors from those integers;
  no date, filename, or image probe participates in coordinate mapping.
- `media_kind` is `original` for `generate_preview` and
  `preview-small-v1` for preview-first `face_embedding`.
- Worker processor identities are exact triples of
  `(contract_version, processor_type, processor_version)` and are polled round-robin by one worker
  process.

Legacy `capture_metadata` and already-enrolled original-based face jobs retain their existing
version-1 wire contract. Preview generation uses processor contract/version `2/1`.
Preview-backed face processing uses `2/2`. The worker must temporarily support face identities
`1/1` and `2/2` so deploys do not strand a previously queued job.

## Acceptance criteria

The authoritative criteria are in the
[specification](../superpowers/specs/2026-07-30-preview-first-photo-processing-design.md#acceptance-criteria).
Delivery additionally requires:

- a forward migration that explicitly classifies every existing photo as legacy without creating
  preview or face jobs;
- a clean reverse migration path for schema rollback while no preview-first photo has been
  activated;
- one real-JPEG end-to-end test spanning confirmation, preview claim/download/upload,
  Django verification/publication, gallery selection, and preview-backed face enrollment;
- one worker-container contract proving it receives only the API token and processor identity
  list; and
- the repository-wide CI-equivalent checks listed in [Verification](#verification).

## Implementation

### Task 1: Persist explicit photo policy and immutable derivative identity

**Files:**

- Modify: `src/backend/picflow/models.py`
- Create: `src/backend/picflow/migrations/0006_photo_processing_policy.py`
- Modify: `src/backend/picflow/tests/test_models.py`
- Modify: `src/backend/picflow/tests/test_photo_migrations.py`
- Modify: `src/backend/processing/models.py`
- Create: `src/backend/processing/migrations/0003_add_preview_derivative_schema.py`
- Modify: `src/backend/processing/signals.py`
- Modify: `src/backend/processing/tests/test_models.py`

- **Specification:** Explicit Applicability and State; Binary Publication Contract.
- **Depends on:** None.
- **Produces:** explicit photo generation/policy fields, `GENERATE_PREVIEW_PROCESSOR`, an initial
  `not_requested` preview state for newly created preview-first photos, and immutable
  `PhotoDerivative` persistence for later tasks.

- [ ] Add migration and model tests that fail because policy fields, valid-pair constraints,
  preview state creation, and derivative uniqueness/immutability do not exist.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 ../../.venv/bin/pytest -q src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_photo_migrations.py src/backend/processing/tests/test_models.py`
  and confirm failures name the missing fields/models.
- [ ] Add `processing_generation` and `gallery_media_policy` with database defaults set to the
  legacy pair. The migration must populate all existing rows with that pair and must not enqueue
  work.
- [ ] Add the valid-pair database constraint and model validation. Do not use `uploaded_at` or
  derivative presence to derive either value.
- [ ] Add `PhotoDerivative` in `processing`, depending on both `picflow.0006` and
  `processing.0002`. Store variant, final key, byte size, JPEG content type, width, height,
  oriented-source width/height, SHA-256, accepted attempt, and publication timestamp. Protect
  photo/attempt deletion, reject mutation after insert, and enforce one published row per
  `(photo, variant)`.
- [ ] Extend state initialization so a preview-first photo has explicit `generate_preview` and
  `face_embedding` states at `not_requested`; legacy row creation must not request preview work.
- [ ] Run the targeted test command again and expect all selected tests to pass.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 ../../.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`
  and expect `No changes detected`.

### Task 2: Define versioned preview and generic-media worker contracts

**Files:**

- Modify: `src/backend/processing/contracts.py`
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/views.py`
- Modify: `src/backend/processing/tests/test_enrollment.py`
- Modify: `src/backend/processing/tests/test_views.py`
- Modify: `src/backend/processing/tests/test_view_edge_cases.py`
- Modify: `src/worker/photo_worker/contracts.py`
- Modify: `src/worker/photo_worker/client.py`
- Modify: `src/worker/tests/test_contracts.py`

- **Specification:** Preview Media Contract; Preview-First Enrollment; ML Input Contract.
- **Depends on:** Task 1 policy and derivative types.
- **Produces:** exact `2/1 generate_preview` and `2/2 face_embedding` claim/result schemas,
  output-slot metadata, and generic-media fingerprints consumed by Tasks 3-5.

- [ ] Add failing Django and worker contract tests for the processor identities, normalized preview
  configuration, generic input fingerprint, and one exact preview upload slot.
- [ ] Prove legacy `1/1 capture_metadata` and `1/1 face_embedding` claims still parse and validate.
- [ ] Run
  `../../.venv/bin/pytest -q src/worker/tests/test_contracts.py`
  plus the Django processing tests named above with CI-like `DB_*` variables; expect failures only
  for the absent version-2 contracts.
- [ ] Define `GENERATE_PREVIEW_CONFIGURATION` with the approved 1600/JPEG/85/sRGB/no-upscale/
  orientation/metadata rules, retry limits, output byte/dimension ceilings, SHA-256, and bounded
  report fields.
- [ ] Generalize input validation by contract version. Version 1 keeps the existing
  `original_*` fields; version 2 accepts only the generic fields listed in Cross-task interfaces.
- [ ] Extend claim payloads for preview jobs with exactly one output slot: variant, PUT URL,
  expiry, required `Content-Type: image/jpeg`, staging identity, maximum bytes/dimensions, and
  checksum algorithm. Do not persist the signed URL or fields.
- [ ] Define the preview success result as variant, content type, byte size, width, height,
  oriented-source width/height, SHA-256, upload duration, and bounded warning codes. Add stable
  retryable/permanent failure codes from the specification.
- [ ] Make preview-backed face enrollment build its fingerprint only from a published
  `PhotoDerivative`; make original-based version-1 face enrollment remain compatible.
- [ ] Re-run the targeted suites and expect them to pass.

### Task 3: Add exact staging upload and verified non-overwriting publication

**Files:**

- Modify: `src/backend/processing/storage.py`
- Modify: `src/backend/processing/tests/test_storage.py`
- Create: `src/backend/processing/services/previews.py`
- Create: `src/backend/processing/tests/test_previews.py`
- Modify: `src/backend/processing/services/jobs.py`
- Modify: `src/backend/processing/tests/test_jobs.py`
- Modify: `src/backend/processing/views.py`
- Modify: `src/backend/processing/tests/test_views.py`

- **Specification:** Binary Publication Contract; Failure and Retry Semantics; Privacy and
  Security.
- **Depends on:** Task 2 schemas.
- **Produces:** `ExactPreviewStorage` and a Django-owned preview completion service that Task 5 can
  connect to downstream enrollment.

- [ ] Add failing storage tests for exact-key presigned PUT, lease-bounded expiry, strict key
  validation, HEAD verification, checksum streaming, source-conditional copy, final-key
  non-overwrite behavior, and sanitized storage errors.
- [ ] Add failing service tests for current-attempt preflight, object verification, recovery after
  copy-before-transaction interruption, duplicate completion, conflict, stale completion, and
  immutable publication.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 ../../.venv/bin/pytest -q src/backend/processing/tests/test_storage.py src/backend/processing/tests/test_previews.py src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_views.py`
  and confirm the new cases fail before implementation.
- [ ] Implement `ExactPreviewStorage` using the existing private-media credentials only in Django.
  It may sign one staging PUT, inspect/stream-hash that key, inspect a final key, and promote with a
  source precondition; it must reject every key outside the two declared preview prefixes.
- [ ] Implement a two-phase completion service: lock and validate current lease; release the
  transaction for storage verification/promotion; then reacquire run/job/state/attempt locks and
  revalidate current ownership before atomically accepting the attempt and creating
  `PhotoDerivative`.
- [ ] When a matching final object already exists after an interrupted copy, verify every declared
  property and converge; reject a mismatching object without overwrite.
- [ ] Ensure stale/expired attempts are retained through existing late-receipt semantics but cannot
  publish a derivative or change preview state.
- [ ] Keep temporary grants and keys out of attempts, reports, public errors, and logs.
- [ ] Re-run the targeted command and expect all selected tests to pass.

### Task 4: Generate and upload normalized previews in the standalone worker

**Files:**

- Create: `src/worker/photo_worker/preview.py`
- Create: `src/worker/tests/test_preview.py`
- Modify: `src/worker/photo_worker/client.py`
- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_runner.py`

- **Specification:** Preview Media Contract; Failure and Retry Semantics; Privacy and Security.
- **Depends on:** Task 2 worker contracts.
- **Produces:** bounded preview generation and exact-slot upload used by the end-to-end pipeline.

- [ ] Add failing image tests for landscape, portrait, EXIF rotations 1-8, 1600-pixel long edge,
  no upscale, aspect-ratio preservation, ICC-to-sRGB conversion, absent-profile default, JPEG
  quality configuration, and complete metadata stripping.
- [ ] Add failing error tests for excessive input pixels/bytes, corrupt JPEG, invalid dimensions,
  unsupported color conversion, output limit violation, upload interruption, grant expiry, and
  fingerprint mismatch.
- [ ] Add runner tests proving the preview is uploaded before completion and that lease loss stops
  publication without logging URLs, tokens, keys, EXIF, or hostile response details.
- [ ] Run
  `../../.venv/bin/pytest -q src/worker/tests/test_preview.py src/worker/tests/test_runner.py`
  and confirm failures identify the missing processor/upload path.
- [ ] Implement `generate_preview` with Pillow's EXIF transpose, bounded decode, sRGB conversion,
  long-edge resize without enlargement, JPEG quality 85, and an output save that passes no source
  metadata.
- [ ] Stream or write only inside the worker temporary directory, calculate SHA-256 from the final
  bytes, enforce output bounds before upload, and delete the local file in `finally`.
- [ ] Add exact PUT upload with declared content type and bounded response handling. Upload and
  signed-URL values must pass through existing redaction.
- [ ] Dispatch `generate_preview` from the runner and submit the typed result only after successful
  upload.
- [ ] Re-run the targeted worker tests and expect them to pass.

### Task 5: Enforce preview-first enrollment and single-process processor scheduling

**Files:**

- Modify: `src/backend/config/settings.py`
- Modify: `.env.example`
- Modify: `src/backend/ingestion/services/confirmation.py`
- Modify: `src/backend/ingestion/tests/test_confirmation.py`
- Modify: `src/backend/processing/services/enrollment.py`
- Modify: `src/backend/processing/services/previews.py`
- Modify: `src/backend/processing/tests/test_enrollment.py`
- Modify: `src/backend/processing/tests/test_previews.py`
- Modify: `src/worker/photo_worker/runner.py`
- Modify: `src/worker/tests/test_runner.py`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.prod.yml`
- Modify: `tests/processing/test_worker_container_contract.py`

- **Specification:** Explicit Applicability and State; Preview-First Enrollment; ML Input Contract.
- **Depends on:** Tasks 1-4.
- **Produces:** activation-gated new-photo classification, strict preview-to-face transition, safe
  reconciliation, and one-concurrency round-robin polling across exact processor identities.

- [ ] Add failing confirmation tests proving the disabled flag creates the explicit legacy pair,
  the enabled flag creates the preview-first pair, only preview is queued at confirmation, and
  face remains `not_requested`.
- [ ] Add failing completion/reconciliation tests proving accepted preview publication queues
  version-2 face processing exactly once and that dates, S3 objects, embeddings, and face counts
  never trigger enrollment.
- [ ] Add failing face-result persistence tests proving every version-2 detection records
  `coordinate_space=preview-small-v1`, preview/source dimensions, and unambiguous scale factors,
  while legacy version-1 detection evidence remains readable.
- [ ] Add worker tests for fair round-robin polling of configured exact identities, skipping an
  empty identity, preserving global concurrency one, and continuing to support legacy face `1/1`.
- [ ] Run the targeted confirmation, enrollment, preview, runner, and container-contract tests and
  confirm the new cases fail.
- [ ] Add `PHOTO_PROCESSING_PREVIEW_ENABLED`, defaulting false, and
  `PHOTO_WORKER_PROCESSOR_IDENTITIES`, defaulting to the currently deployed identity. Validate
  configuration strictly and never infer activation from the worker being present.
- [ ] At upload confirmation, persist one of the two valid explicit photo pairs and request only
  `generate_preview` when enabled. Preserve capture-metadata behavior independently.
- [ ] In the accepted preview transaction, request preview-backed face `2/2` idempotently. Keep
  face state `not_requested` on preview retry, failure, cancellation, expiry, or stale completion.
- [ ] Extend version-2 face result validation/persistence with the declared coordinate-space and
  dimension metadata. Reject dimensions that disagree with the accepted derivative; do not inspect
  image storage to repair them.
- [ ] Restrict preview reconciliation to explicit `preview_first_v1 + preview_required`; restrict
  face `2/2` reconciliation to accepted `preview-small-v1` plus explicit face
  `not_requested`.
- [ ] Change the standalone worker to parse an ordered list of exact identities and poll them
  round-robin while processing only one job at a time. Keep the singular legacy environment value
  accepted during one release for deployment compatibility.
- [ ] Pass only API URL, token, build, lease, and processor identities through Compose; prove no
  database/Django/permanent-storage settings reach the worker.
- [ ] Re-run the targeted suites and expect them to pass.

### Task 6: Select preview media in the gallery without changing presentation URLs

**Files:**

- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/config/views.py`

- **Specification:** Gallery Behavior; Privacy and Security.
- **Depends on:** Tasks 1 and 3.
- **Produces:** policy-aware gallery eligibility and resolver behavior behind the existing
  `preview-small`/`preview-large` application routes.

- [ ] Add failing factory/query tests for legacy inclusion, preview-required exclusion in every
  non-success preview state, inclusion after accepted publication, and stable event ordering.
- [ ] Add failing resolver/endpoint tests proving legacy small/large use the original, new small
  uses the derivative, new large uses the original, and missing preview never falls back.
- [ ] Add tests that HTML and responses expose no original/derivative permanent key, signed URL,
  checksum, or internal storage detail.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 ../../.venv/bin/pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py`
  and confirm the new behavior fails.
- [ ] Filter preview-required photos by explicit succeeded state plus published derivative. Keep
  legacy eligibility unchanged and perform no S3 probe while building the page.
- [ ] Resolve `preview-small` from `PhotoDerivative` only for preview-required photos; continue
  resolving `preview-large` from the original under the existing event/photo authorization checks.
- [ ] Preserve the current `GalleryPhoto`, template, GLightbox, accessibility, cache, and sanitized
  404/503 contracts.
- [ ] Re-run the targeted gallery tests and expect them to pass.

### Task 7: Prove the complete pipeline, reporting, resource use, and operational contract

**Files:**

- Modify: `tests/processing/test_pipeline_e2e.py`
- Modify: `src/backend/processing/services/reports.py`
- Modify: `src/backend/processing/tests/test_reports.py`
- Modify: `tests/processing/test_worker_container_contract.py`
- Modify: `docs/local-photo-processing-check.md`
- Modify: `docs/photo-processing-vm-sizing.md`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`

- **Specification:** Reporting; Acceptance Criteria; Verification Boundaries.
- **Depends on:** Tasks 1-6.
- **Produces:** end-to-end evidence, preview-specific immutable reports, deployment-disabled
  defaults, and a decision-complete staging activation/runbook boundary.

- [ ] Extend the real-JPEG e2e test so confirmation creates preview work, one worker iteration
  downloads and uploads the preview, Django verifies/publishes it, gallery selection changes, and
  face `2/2` becomes queued with the preview fingerprint.
- [ ] Add e2e failures for stale completion and rejected staging metadata, proving neither gallery
  visibility nor face enrollment changes.
- [ ] Add preview report tests for cohort, terminal counts, retries/stale attempts, durations,
  dimensions, bytes, warnings, stable failures, and absence of keys/grants/image bytes/EXIF.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 ../../.venv/bin/pytest -q tests/processing/test_pipeline_e2e.py src/backend/processing/tests/test_reports.py tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py`
  and confirm the new cases fail.
- [ ] Extend reports with preview-only bounded fields while keeping capture and face reports
  compatible.
- [ ] Keep preview activation false in tracked defaults and deployment unless an operator
  explicitly supplies the activation settings. Deployment must not enable preview processing by
  image release alone.
- [ ] Update the local runbook with exact state/report inspection for `generate_preview`, exact
  proof that face is queued afterward, safe worker stop, and no-volume-deletion guidance.
- [ ] Extend the sizing procedure to measure preview decode/encode/upload and preview-backed face
  processing at total concurrency one. Record CPU, peak RSS, temporary disk, bytes, and latency;
  do not claim the current VM is adequate without measurements.
- [ ] Re-run the targeted command and expect all selected tests to pass.
- [ ] Run a representative original-versus-preview ML comparison using the repository's existing
  face fixtures/models. Record detection coverage and embedding/search deltas as immutable test or
  PR evidence; any material regression blocks activation, not code completion.

### Task 8: Configure staging-object expiry with a separate live approval gate

**Files:**

- Modify: `.agents/skills/manage-yandex-cloud/references/inventory.md` only if read-only discovery
  identifies a stable non-secret bucket mapping missing from the inventory.
- Modify: `docs/local-photo-processing-check.md`

- **Specification:** Binary Publication Contract; Operational impact.
- **Depends on:** Task 3 exact staging prefix.
- **Produces:** a documented seven-day lifecycle rule for
  `processing-staging/previews/`, or a recorded activation blocker if the existing bucket tooling
  cannot scope that rule safely.

- [ ] Use `manage-yandex-cloud` before live inspection. Show the active profile name, cloud ID, and
  folder ID with non-secret commands; resolve the private-media bucket to a stable identifier and
  inspect its current lifecycle configuration read-only.
- [ ] Confirm from installed `yc ... --help` and official Yandex Object Storage documentation the
  exact command/API shape for adding a prefix-scoped expiry rule without replacing unrelated
  lifecycle rules.
- [ ] Prepare the exact current-state, intended-state, validation, rollback, availability, data,
  and price-impact statement. Seven-day expiry applies only to
  `processing-staging/previews/`; published derivatives and originals are excluded.
- [ ] Stop for explicit manual approval immediately before any lifecycle mutation. Plan approval
  is not mutation approval, and an unknown price delta must be stated as unknown.
- [ ] After approval, apply the smallest scoped change and verify it read-only. If a safe additive
  operation is unavailable, do not replace the bucket lifecycle wholesale; record activation as
  blocked until a reviewed exact configuration is available.
- [ ] Add the verified rule and rollback command to the local processing runbook without recording
  credentials or ephemeral tokens.

### Task 9: Reconcile architecture, jobs, and final task evidence

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/product-jobs.md` only if gallery evidence changes the status of PJ-005
- Modify: `docs/superpowers/specs/2026-07-30-preview-first-photo-processing-design.md`
- Modify: `docs/plans/2026-07-30-preview-first-photo-processing.md`

- **Specification:** Architecture and ADR Reconciliation.
- **Depends on:** Tasks 1-8 code and proportional verification.
- **Produces:** repository truth aligned with delivered behavior and one reviewable complete-task
  diff.

- [ ] Compare delivered behavior line-by-line with the approved specification and acceptance
  criteria; record any unmet item as blocking rather than weakening the document.
- [ ] Update `docs/architecture.md` from proposed to implemented only for behavior present and
  executable in the repository. State separately whether preview generation is merely shipped,
  locally verified, staging-configured, or live-activated.
- [ ] Update engineering/product job evidence only with commands or browser/runtime evidence
  actually obtained.
- [ ] Confirm the result still conforms to ADR 0017 and ADR 0015. Stop instead of silently changing
  worker credentials, publication ownership, polling, or original-delivery policy.
- [ ] Mark the plan completed only after review and final verification; do not change the approved
  specification's decisions to match implementation shortcuts.
- [ ] Have the root controller prepare the complete unstaged review package, including untracked
  files. Dispatch one independent reviewer for the whole task. Return fixes to the same implementer
  and re-review to the same reviewer.
- [ ] After approval, have the root controller run [Verification](#verification), stage only task
  files, and create one implementation commit.

## Verification

Run targeted commands at the end of each task as listed above. Before final approval, run the
repository's CI-equivalent checks from the worktree with the project virtual environment:

```bash
../../.venv/bin/ruff format --check .
../../.venv/bin/ruff check .
../../.venv/bin/mypy
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  ../../.venv/bin/pytest --cov --cov-report=term-missing
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  ../../.venv/bin/python src/backend/manage.py check
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  ../../.venv/bin/python src/backend/manage.py makemigrations --check --dry-run
npm ci
npm run test:js
sh tests/visual/run-in-container.sh test
git diff --check
```

Expected outcomes:

- Ruff format and lint exit 0.
- mypy exits 0.
- the full Python suite passes with branch coverage at or above the repository's 75% guard.
- Django system checks report no issues.
- migration drift reports `No changes detected`.
- JavaScript tests pass.
- the containerized visual suite passes with no unreviewed snapshot change.
- `git diff --check` reports no whitespace errors.

Also build and exercise the worker image after unit tests:

```bash
docker build -f Dockerfile.worker -t photo-prjct-worker:preview-first-local .
docker run --rm photo-prjct-worker:preview-first-local python -m photo_worker --help
```

Expected: the image builds from the standalone worker package, starts without Django code, and
does not require database or permanent Object Storage settings. If `--help` is not an existing
supported entrypoint at implementation time, use the container-contract startup command recorded
by `tests/processing/test_worker_container_contract.py`; do not add a CLI solely for this check.

## Operational impact and rollout

1. Merge and deploy schema/code with `PHOTO_PROCESSING_PREVIEW_ENABLED=False`. Migrations classify
   existing photos as legacy and create no preview jobs.
2. Keep the worker on its currently deployed processor identities. Verify migration counts, legacy
   gallery behavior, existing capture/face processing, and zero preview-first rows.
3. Complete Task 8's read-only lifecycle inspection and obtain separate manual approval before
   changing the bucket. Apply and verify the seven-day staging-prefix expiry rule.
4. Run the representative ML comparison and local/staging capacity measurement at total worker
   concurrency one. Treat material quality regression or inadequate VM headroom as activation
   blockers.
5. In a separately authorized staging change, set the worker identity list to include
   `2/generate_preview/1` and `2/face_embedding/2`, then enable
   `PHOTO_PROCESSING_PREVIEW_ENABLED=True`.
6. Upload one new staging JPEG. Verify explicit policy, preview attempt, derivative metadata,
   gallery small/large sources, downstream face fingerprint, immutable reports, redacted logs, and
   resource headroom.
7. Do not activate production until a production environment exists and the same migration,
   lifecycle, quality, capacity, and smoke gates pass through the normal promotion workflow.

No backfill job is run at any point.

## Rollback

- Before activation, revert the application image/migration only if no preview-first row or
  derivative exists. Otherwise retain the additive schema and disable behavior rather than
  destructively reversing data.
- After activation, set `PHOTO_PROCESSING_PREVIEW_ENABLED=False` to classify subsequent confirmed
  photos under the explicit legacy policy, and remove version-2 identities from the worker only
  after current attempts finish or are recovered. Existing preview-first photos retain their
  explicit state and published derivative.
- Reverting gallery selection for already preview-first photos requires an explicit compatibility
  patch; never rewrite their policy from absence or date and never silently expose the original as
  a tile fallback.
- Removing the staging lifecycle rule requires the exact rollback operation approved in Task 8.
  It does not recover objects already expired by the rule.
- Never use `docker compose down --volumes`, database reset, object deletion, or immutable-attempt
  mutation as rollback.

## Open questions

None. Live Yandex Cloud lifecycle mutation and staging activation require fresh operational
approval during execution, but their intended states and gates are decision-complete.
