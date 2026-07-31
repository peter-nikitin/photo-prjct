# Public Selfie Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-07-30
- Status: Approved
- Owner: project maintainer
- Related specification:
  [`Public Selfie Search Design`](../superpowers/specs/2026-07-30-public-selfie-search-design.md)
- Related architecture:
  [`Current architecture`](../architecture.md#current-architecture--implemented),
  [`Search`](../architecture.md#search), and
  [`Security, privacy, and legal boundaries`](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md)
- ADR impact: Resolved — conforms to accepted ADR 0019; ADR 0019 supersedes ADR 0015.

## Goal

Implement the approved path from one selfie on any published event page to an immutable,
shareable, event-scoped probable-match result, while deleting the selfie before terminal
publication and never persisting its query embedding.

## Architecture

Add a focused `selfie_search` Django app for product state, temporary-object ownership, exact
ranking, public result presentation, and search-specific lease state. Keep existing photo
processing tables unchanged: they require a `Photo` and immutable `EventProcessingRun`, while a
selfie is neither. Extend the existing private worker API and worker runtime with a typed
`selfie_query` work variant; the worker returns one transient query vector and Django performs the
bounded exact comparison over the frozen event cohort.

## Tech Stack

Django 6, PostgreSQL 16, Yandex Object Storage through `boto3`, Pillow 12, the existing
YuNet/SFace OpenCV worker, server-rendered templates, vanilla JavaScript polling, GLightbox, pytest,
Node test runner, and Playwright visual regression.

## Global Constraints

- Implement the complete approved specification without widening its exclusions.
- Keep normal photo-processing models and accepted photo embeddings immutable and compatible.
- Keep the worker free of Django/PostgreSQL configuration and permanent Object Storage
  credentials.
- Use direct cosine distance only; no cluster expansion, ANN, `pgvector`, or new service.
- Use the existing SFace model identity and normalized 128-dimensional embeddings.
- Start the versioned MVP cosine-distance threshold at `0.363`; changing it affects new searches
  only.
- Accept JPEG and PNG selfies up to `20 MiB` and `25,000,000` decoded pixels. These values fit
  below the current Nginx `25m` request boundary including multipart overhead.
- Require exactly one detected face with minimum side `32 px`; multiple detected faces are never
  truncated into an accepted query.
- Poll public status every `2 seconds` while nonterminal; the stable page has no client-side expiry.
- Use a `24-hour` lifecycle bound for the private `selfie-search/` temporary prefix as a backstop;
  normal application cleanup still deletes the object before terminal publication.
- Store only a SHA-256 digest of a 32-byte URL-safe random bearer token.
- Keep public result and media responses `private, no-store` and prevent token-bearing referrers
  from being sent.
- `SELFIE_SEARCH_ENABLED` defaults to `False`; rollback disables new submissions without deleting
  accepted result evidence.
- Implementer and reviewer subagents must not modify Git state. The root controller creates one
  final task commit only after independent review and final verification, following `AGENTS.md`.

---

## Scope

Implements the approved specification without scope changes. The operational lifecycle rule and
feature-flag activation are included because selfie deletion and rollback are accepted critical
path requirements.

## Acceptance criteria

The implementation must satisfy all
[17 specification acceptance criteria](../superpowers/specs/2026-07-30-public-selfie-search-design.md#acceptance-criteria).
Delivery additionally requires:

- a forward and backward PostgreSQL migration test for the new app;
- one real JPEG end-to-end contract test from search creation through worker result, exact ranking,
  selfie deletion, and ready-page rendering;
- unchanged normal paid-gallery denial alongside paid-result media access; and
- activation remaining off until the temporary-prefix lifecycle rule and current worker resource
  headroom are verified in staging.

## File structure

### New Django app

- `src/backend/selfie_search/apps.py`: Django app registration and settings checks.
- `src/backend/selfie_search/models.py`: search, frozen candidate, job, attempt, and result rows.
- `src/backend/selfie_search/forms.py`: bounded JPEG/PNG upload validation.
- `src/backend/selfie_search/storage.py`: strict temporary-prefix put, delete, inspect, and
  short-lived exact-object grants.
- `src/backend/selfie_search/services/submission.py`: token creation, upload, cohort freeze, and
  job enrollment.
- `src/backend/selfie_search/services/jobs.py`: claim, lease, retry, callback, cleanup, and
  idempotency transitions.
- `src/backend/selfie_search/services/ranking.py`: finite normalized-vector validation and exact
  unique-photo ranking.
- `src/backend/selfie_search/services/results.py`: token lookup and ordered eligible presentation.
- `src/backend/selfie_search/views.py` and `urls.py`: submission, result, polling, and
  result-authorized media endpoints.
- `src/backend/selfie_search/templates/selfie_search/result.html`: all public search states.
- `src/backend/static/ui/selfie-search.css` and `selfie-search.js`: event form, progress polling,
  result refresh, and accessible state behavior.
- `src/backend/selfie_search/migrations/0001_initial.py`: durable schema and constraints.
- `src/backend/selfie_search/tests/`: model, migration, form, storage, job, ranking, view, and
  end-to-end tests organized by the owning module.

### Existing integration points

- `src/backend/config/settings.py` and `urls.py`: app settings and public routes.
- `src/backend/templates/catalog/event_detail.html`: approved upload block.
- `src/backend/config/views.py` and `picflow/gallery.py`: retain normal gallery behavior; share only
  bounded presentation construction where useful.
- `src/backend/processing/views.py`, `contracts.py`, and tests: dispatch typed selfie work through
  the existing protected API without changing photo-job semantics.
- `src/worker/photo_worker/contracts.py`, `face_embedding.py`, `runner.py`, `client.py`, and tests:
  accept and execute the selfie-query variant.
- `.env.example`, `docker-compose.yml`, `docker-compose.prod.yml`,
  `.github/workflows/deploy.yml`, `deploy/apply-deployment.sh`, and deployment contract tests:
  propagate the feature and multi-processor worker configuration.
- `tests/js/selfie-search.test.js`: deterministic polling behavior.
- `tests/visual/views.py`, `urls.py`, `visual.spec.js`, templates, and snapshots: event block,
  progress, empty, failure, and ready layouts at desktop and mobile widths.
- `pyproject.toml`: include `src/backend/selfie_search` in coverage measurement.
- `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` only after verified
  delivery evidence exists.

## Implementation

### Task 1: Add constrained search state and migration

**Files:**

- Create `src/backend/selfie_search/__init__.py`
- Create `src/backend/selfie_search/apps.py`
- Create `src/backend/selfie_search/models.py`
- Create `src/backend/selfie_search/migrations/__init__.py`
- Create `src/backend/selfie_search/migrations/0001_initial.py`
- Create `src/backend/selfie_search/tests/test_models.py`
- Create `src/backend/selfie_search/tests/test_migrations.py`
- Modify `src/backend/config/settings.py`
- Modify `pyproject.toml`

- **Specification:** Product State, Candidate Cohort, Result Rows, Stable URL, and Privacy.
- **Depends on:** Accepted ADR 0019.
- **Produces:**
  - `SelfieSearch` with explicit public/internal state, token digest, temporary-object metadata,
    frozen configuration/counts, terminal evidence, and cleanup timestamp.
  - `SelfieSearchCandidate(search, embedding, photo)` as the immutable cohort identity.
  - `SelfieSearchJob(search, status, configuration, available_at, claimed_at, completed_at)`.
  - `SelfieSearchAttempt(job, status, lease timestamps, result_hash, bounded timings/error
    metadata)` with no result-vector field.
  - `SelfieSearchResult(search, photo, detection, rank, cosine_distance)`.

- [ ] Write migration and model tests that prove state/check constraints, one job per search,
  unique candidate embedding, unique result photo and rank, cross-event rejection, terminal result
  immutability, and the absence of a query-vector field.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_models.py src/backend/selfie_search/tests/test_migrations.py`
  and confirm failure because the app and tables do not exist.
- [ ] Implement the app, models, database constraints, settings checks, migration, and coverage
  source entry. Use `PROTECT` for event/photo/detection evidence and generated UUID primary keys.
- [ ] Run the same targeted command and confirm all new model and migration tests pass, including
  forward/backward migration.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` with the same
  database environment and confirm “No changes detected”.

### Task 2: Accept one selfie, store it privately, and freeze the cohort

**Files:**

- Create `src/backend/selfie_search/forms.py`
- Create `src/backend/selfie_search/storage.py`
- Create `src/backend/selfie_search/services/__init__.py`
- Create `src/backend/selfie_search/services/submission.py`
- Create `src/backend/selfie_search/tests/test_forms.py`
- Create `src/backend/selfie_search/tests/test_storage.py`
- Create `src/backend/selfie_search/tests/test_submission.py`
- Modify `src/backend/config/settings.py`
- Modify `src/backend/config/urls.py`
- Create `src/backend/selfie_search/urls.py`
- Create `src/backend/selfie_search/views.py`

- **Specification:** Event Page, Candidate Cohort, Object Storage, and data-flow steps 1–2.
- **Depends on:** Task 1 models.
- **Produces:**
  - `submit_selfie_search(*, event, upload, storage) -> CreatedSearch(search, public_token)`.
  - `TemporarySelfieStorage.put/delete/create_download_grant` restricted to
    `selfie-search/<uuid-hex>`.
  - `resolve_public_search(event_slug, public_token) -> SelfieSearch` using the token digest.

- [ ] Write failing form tests for empty, spoofed, truncated, unsupported, over-20-MiB, and
  over-25-million-pixel inputs, plus valid JPEG and PNG. Verify with Pillow without executing face
  inference in Django.
- [ ] Write failing storage tests proving strict prefix validation, exact content type and size,
  private upload, idempotent delete, bounded grant lifetime, and sanitized storage failures.
- [ ] Write failing submission tests proving published free/paid eligibility, draft rejection,
  32-byte random token hashing, no plaintext token persistence/logging, exactly one job, and a
  frozen candidate set containing only compatible accepted embeddings from the selected event.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_forms.py src/backend/selfie_search/tests/test_storage.py src/backend/selfie_search/tests/test_submission.py`
  with the CI database environment and confirm the expected missing-interface failures.
- [ ] Implement the minimal form, storage adapter, and transaction boundary. If object upload
  succeeds but database creation fails, immediately attempt exact-object deletion; the lifecycle
  rule remains the crash backstop.
- [ ] Implement cohort freezing with bulk candidate rows from the current accepted
  `face_embedding` attempt, matching SFace model/version/dimension and eligible event photos.
- [ ] Add the CSRF-protected multipart POST route. Initial validation/storage failure remains on
  the event page; success returns an HTTP redirect containing the plaintext token only in the
  destination URL.
- [ ] Rerun the targeted tests and confirm all pass.

### Task 3: Add a typed selfie-query worker contract

**Files:**

- Modify `src/worker/photo_worker/contracts.py`
- Modify `src/worker/photo_worker/face_embedding.py`
- Modify `src/worker/photo_worker/runner.py`
- Modify `src/worker/photo_worker/client.py`
- Modify `src/worker/tests/test_contracts.py`
- Modify `src/worker/tests/test_face_embedding.py`
- Modify `src/worker/tests/test_runner.py`

- **Specification:** ML Worker, Query Validation, and exactly-one-face failure semantics.
- **Depends on:** Task 2 input fingerprint and limits.
- **Produces:**
  - `PROCESSOR_TYPE_SELFIE_QUERY = "selfie_query"` at contract/processor version `1`.
  - A strict claimed-work union for existing photo work and selfie work.
  - `extract_selfie_embedding(...) -> SelfieEmbeddingResult` returning exactly one normalized
    vector or raising a stable `no_face_detected`, `multiple_faces_detected`,
    `quality_rejected`, or inference failure.
  - `WorkerConfig.processor_types` with backward-compatible parsing of the old singular variable
    and ordered `PHOTO_WORKER_PROCESSOR_TYPES`.

- [ ] Write failing contract tests for the new exact field set, JPEG/PNG temporary fingerprint,
  strict processor configuration, bounded dimensions, URLs, IDs, and rejection of mixed
  photo/selfie payloads.
- [ ] Write failing real/fake-model unit tests proving zero faces, two faces, too-small face,
  invalid/non-finite embedding, exactly one normalized vector, and release of decoded/query arrays.
- [ ] Write failing runner tests proving ordered polling gives interactive `selfie_query` work
  priority without losing existing `face_embedding` and `capture_metadata` support; verify typed
  success/failure callbacks and no token/vector logging.
- [ ] Run
  `.venv/bin/pytest -q src/worker/tests/test_contracts.py src/worker/tests/test_face_embedding.py src/worker/tests/test_runner.py`
  and confirm the new cases fail against the current two-processor worker.
- [ ] Implement the strict union, selfie wrapper, ordered processor polling, and backward-compatible
  environment parsing. Do not retain a model or image between jobs and keep concurrency exactly
  one.
- [ ] Rerun the targeted worker tests and confirm all pass.

### Task 4: Lease selfie work, rank in Django, and delete before terminal state

**Files:**

- Create `src/backend/selfie_search/services/jobs.py`
- Create `src/backend/selfie_search/services/ranking.py`
- Create `src/backend/selfie_search/tests/test_jobs.py`
- Create `src/backend/selfie_search/tests/test_ranking.py`
- Modify `src/backend/processing/contracts.py`
- Modify `src/backend/processing/views.py`
- Modify `src/backend/processing/tests/test_views.py`
- Modify `src/backend/processing/tests/test_view_edge_cases.py`

- **Specification:** Django responsibilities, Exact Ranking, Data Flow, and Failure Semantics.
- **Depends on:** Tasks 1–3.
- **Produces:**
  - Search-specific claim/heartbeat/refresh/complete/fail/recover services with transport attempt
    references `selfie_<uuid>`.
  - `rank_search(search, query_vector) -> tuple[RankedPhoto, ...]`.
  - Existing worker endpoints dispatching by processor/attempt kind while retaining exact
    photo-job behavior.

- [ ] Write failing ranking tests for finite normalized 128-dimensional vectors, threshold boundary
  `0.363`, incompatible model/dimension rejection, event isolation, best-face photo
  deduplication, distance ordering, and photo-ID tie breaking.
- [ ] Write failing transition tests for atomic claim, lease refresh, retry/backoff, expiry,
  stale/conflicting callback, hash-only idempotency, search-first claim priority, and the absence
  of persisted query-vector bytes.
- [ ] Write failing cleanup-gate tests: set `cleanup_pending`, delete the exact object, publish the
  intended terminal state only after deletion, return retryable `503` when deletion fails, and
  accept an identical retried callback without duplicate results.
- [ ] Write API regression tests proving existing photo claim/heartbeat/download/complete/fail
  payloads remain unchanged and unsupported or mixed attempt references fail closed.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_jobs.py src/backend/processing/tests/test_views.py src/backend/processing/tests/test_view_edge_cases.py`
  with the CI database environment and confirm the expected failures.
- [ ] Implement search-specific lease transitions and API dispatch. Do not make
  `ProcessingJob.photo`, `ProcessingJob.run`, or existing attempt foreign keys nullable.
- [ ] Validate the query callback, rank only frozen candidate rows, prepare result rows in memory,
  delete the selfie, clear its temporary key, then atomically publish hash-only attempt evidence,
  results, counts, and the terminal state.
- [ ] Map permanent worker outcomes to `no_face`, `multiple_faces`, `quality_rejected`,
  `search_unavailable`, or sanitized `failed`; keep retryable infrastructure failures within the
  existing bounded retry policy.
- [ ] Rerun the targeted tests and confirm all pass.

### Task 5: Serve stable results and narrowly authorize paid originals

**Files:**

- Create `src/backend/selfie_search/services/results.py`
- Create `src/backend/selfie_search/templates/selfie_search/result.html`
- Create `src/backend/selfie_search/tests/test_views.py`
- Modify `src/backend/selfie_search/views.py`
- Modify `src/backend/selfie_search/urls.py`
- Modify `src/backend/config/urls.py`
- Modify `src/backend/picflow/gallery.py`
- Modify `src/backend/picflow/tests/test_gallery.py`
- Modify `src/backend/picflow/tests/test_views.py`

- **Specification:** Stable Result Page, Ready Result, Stable URL and Visibility, and acceptance
  criteria 4, 12–16.
- **Depends on:** Task 4 terminal state and result rows.
- **Produces:**
  - Stable page and bounded JSON status endpoint.
  - Ordered eligible result presentation without recomputation.
  - Token-scoped result media URLs that authorize only saved members.

- [ ] Write failing view tests for every public state, stable reopening/sharing, token/event
  mismatch, draft/unpublished event 404, removed-photo omission with relative order preserved,
  bounded polling JSON, `private, no-store`, and no sensitive fields.
- [ ] Write failing media tests proving free and paid result members stream inline through a valid
  ready bearer link while normal paid gallery media, unrelated paid photos, wrong-event tokens,
  non-ready searches, and invalid tokens remain unavailable.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_views.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py`
  with the CI database environment and confirm the new routes/authorization fail.
- [ ] Implement digest-based lookup, current publication checks, saved-order result resolution,
  bounded status serialization, result-scoped presentation URLs, and streaming through the
  existing close-safe media resolver.
- [ ] Add `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, and
  `X-Content-Type-Options: nosniff` to token-bearing pages/status/media without exposing the token
  to structured logs.
- [ ] Rerun the targeted tests and confirm all pass.

### Task 6: Deliver the event-page and result-page interaction

**Files:**

- Modify `src/backend/templates/catalog/event_detail.html`
- Create `src/backend/static/ui/selfie-search.css`
- Create `src/backend/static/ui/selfie-search.js`
- Create `tests/js/selfie-search.test.js`
- Modify `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify `src/backend/selfie_search/tests/test_views.py`
- Modify `tests/visual/views.py`
- Modify `tests/visual/urls.py`
- Modify or create focused templates under `tests/visual/templates/`
- Modify `tests/visual/visual.spec.js`
- Create/update the corresponding desktop and mobile snapshots under
  `tests/visual/visual.spec.js-snapshots/`

- **Specification:** Event Page, Stable Result Page, Ready Result, user-facing disclosures, and
  probabilistic language.
- **Depends on:** Task 5 routes and serialized states.
- **Produces:** Accessible form, progress polling, terminal states, ready gallery, and visual
  evidence.

- [ ] Add failing Django template assertions for all published free/paid events, absence on drafts,
  JPEG/PNG accept metadata, CSRF form, probabilistic copy, deletion disclosure, public-link
  disclosure, and new-search action.
- [ ] Add failing Node tests for immediate disabled submission, two-second polling, continued
  polling for `queued`/`processing`/`cleanup_pending`, terminal refresh, network backoff without
  duplicate timers, and safe no-JavaScript fallback.
- [ ] Add deterministic visual fixture routes for event form plus processing, empty, error, and
  ready result states. Add desktop/mobile screenshot cases and keyboard/focus assertions.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_views.py src/backend/picflow/tests/test_views.py && npm run test:js`
  and confirm the new markup/interaction tests fail.
- [ ] Implement the minimal server-rendered markup, dedicated CSS, and polling script. Reuse current
  gallery/lightbox classes rather than introducing a second card system.
- [ ] Rerun the Django and Node targets and confirm they pass.
- [ ] Run `sh tests/visual/run-in-container.sh update`, inspect every changed desktop/mobile
  snapshot, then run `sh tests/visual/run-in-container.sh test` and confirm zero visual failures.

### Task 7: Wire safe configuration, deployment, and lifecycle activation

**Files:**

- Modify `.env.example`
- Modify `docker-compose.yml`
- Modify `docker-compose.prod.yml`
- Modify `.github/workflows/deploy.yml`
- Modify `deploy/apply-deployment.sh`
- Modify `tests/processing/test_worker_container_contract.py`
- Modify `tests/deployment/test_deployment_scripts.py`
- Modify `tests/test_repository_foundation.py`
- Create `src/backend/selfie_search/management/commands/verify_selfie_search_storage.py`
- Create `src/backend/selfie_search/tests/test_storage_contract_command.py`

- **Specification:** Object Storage, Compatibility and Evolution, deletion/lifecycle backstop, and
  rollback.
- **Depends on:** Tasks 2–6.
- **Produces:** Disabled-by-default deployment contract, a multi-processor worker configuration,
  and read-only storage/lifecycle preflight evidence.

- [ ] Write failing settings/deployment tests for all global constants, boolean validation,
  `PHOTO_PROCESSING_FACE_ENABLED`, `SELFIE_SEARCH_ENABLED`, and ordered
  `PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,generate_preview`
  propagation without credentials entering the worker environment.
- [ ] Write a failing contract-command test proving the preflight uses a generated exact temporary
  key, verifies private put/head/grant/delete, reports only sanitized markers, and fails if the
  configured `selfie-search/` lifecycle does not bound retention to 24 hours.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_storage_contract_command.py tests/processing/test_worker_container_contract.py tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py`
  and confirm the missing configuration/preflight failures.
- [ ] Implement settings and deployment propagation. Preserve the existing singular worker
  variable as a local compatibility fallback, but deployed configuration uses the ordered plural
  contract.
- [ ] Implement a read-only/scratch-object preflight that cleans its own object in `finally` and
  never changes bucket lifecycle configuration.
- [ ] Rerun the targeted tests and confirm all pass.
- [ ] Before activation, use the project `manage-yandex-cloud` and
  `deliver-operational-change` workflows to inspect and then, with explicit operational authority,
  configure the private bucket lifecycle for prefix `selfie-search/` at 24 hours. Record sanitized
  evidence; do not broaden existing original/incoming rules.

### Task 8: Prove the end-to-end critical path and reconcile documentation

**Files:**

- Create `tests/processing/test_selfie_search_e2e.py`
- Modify `docs/architecture.md`
- Modify `docs/product-jobs.md`
- Modify `docs/engineering-jobs.md` only if a distinct delivered operational capability needs
  evidence
- Modify `README.md` with local feature-flag and smoke instructions

- **Specification:** complete Data Flow and all acceptance criteria.
- **Depends on:** Tasks 1–7.
- **Produces:** End-to-end evidence and implemented architecture status.

- [ ] Write an end-to-end test with a real JPEG and supplied YuNet/SFace fixtures that creates a
  published event with accepted gallery embeddings, submits one selfie, claims it through the
  private API, runs the real worker processor, returns the transient vector, ranks unique photos,
  deletes the selfie, and renders the stable ready result.
- [ ] Extend the test with one paid matched photo and prove result-token media succeeds while the
  normal paid gallery/media path remains denied.
- [ ] Run
  `.venv/bin/pytest -q tests/processing/test_selfie_search_e2e.py -m face_models`
  with the documented local model paths and confirm the real path passes.
- [ ] Run the complete verification matrix below and record counts and any environment-specific
  skips.
- [ ] Update `docs/architecture.md` from proposed/accepted to implemented only for behavior proven
  by the final checks. Update PJ-008 evidence/status and README operation; do not claim production
  activation from local tests.
- [ ] Compare delivered behavior line by line with the specification and ADR 0019. Record the
  reconciliation outcome in the pull request and stop rather than silently diverging.

### Final task: Architecture and ADR reconciliation

- [ ] Confirm ADR 0019 remains `Accepted`, ADR 0015 remains `Superseded`, and both records are
  cross-linked in the index.
- [ ] Confirm no delivered behavior widens bearer access beyond saved event result members.
- [ ] Confirm `docs/architecture.md` distinguishes implemented local behavior from staging
  activation evidence.
- [ ] Confirm all deferred consent, revocation, moderation, watermark, ANN, and abuse work remains
  outside this delivery unless a current critical-path failure brought it back into scope.
- [ ] Record the explicit outcome: implementation conforms to ADR 0019 and supersedes no further
  ADR.

## Verification

Use the project virtual environment explicitly. Start or reuse a disposable local PostgreSQL 16
instance; never point these commands at staging.

```bash
export DB_NAME=app
export DB_USER=app
export DB_PASSWORD=app
export DB_HOST=localhost
export DB_PORT=5432
export SECRET_KEY=ci-not-a-secret
export DEBUG=False
export ALLOWED_HOSTS=localhost,127.0.0.1

.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest --cov --cov-report=term-missing
.venv/bin/python src/backend/manage.py check
.venv/bin/python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
sh tests/visual/run-in-container.sh test
```

Expected successful outcome:

- Ruff formatting and lint: zero findings.
- mypy: zero errors.
- Python: all tests pass, documented model-dependent skips only, branch coverage at or above the
  repository `75%` regression guard.
- Django checks: no issues.
- Migration drift: “No changes detected”.
- JavaScript: all tests pass.
- Playwright: all desktop/mobile visual and interaction tests pass with no unexpected snapshot
  changes.

Run the host-process real-model test separately when local public model files are available. This
proves the Django/worker boundary but does not replace smoke evidence from the rollout image:

```bash
PHOTO_WORKER_YUNET_MODEL_PATH=/absolute/path/to/yunet.onnx \
PHOTO_WORKER_SFACE_MODEL_PATH=/absolute/path/to/sface.onnx \
.venv/bin/pytest -q tests/processing/test_selfie_search_e2e.py -m face_models
```

Expected: the real JPEG search reaches `ready`, returns the expected event-only order, and the
temporary selfie fake/storage object is absent.

The existing worker image (not a new worker service) packages the pinned public OpenCV Zoo YuNet and
SFace files and runs `photo_worker.model_smoke` while building. Before activation, run the smoke in
the exact immutable worker image selected for rollout:

```bash
docker run --rm --entrypoint python "$WORKER_IMAGE" -m photo_worker.model_smoke
```

Expected: `face-model-smoke-ok`; both the photo-embedding and selfie-query consumers load the same
YuNet/SFace files, the synthetic JPEG produces the expected no-face outcome, and SFace returns a
128-dimensional feature.

## Operational impact and rollout

1. Build one immutable application image containing the migration and one immutable existing worker
   image containing pinned public OpenCV Zoo YuNet/SFace files and all four processor types:
   `selfie_query`, `face_embedding`, `capture_metadata`, and `generate_preview`. The worker polls
   five exact identities: `1/selfie_query/1`,
   `1/capture_metadata/1`, `1/face_embedding/1`, `2/generate_preview/1`, and
   `2/face_embedding/2`. Run `photo_worker.model_smoke` in that exact worker image before proceeding.
2. Keep `SELFIE_SEARCH_ENABLED=False`. Apply the database migration; it is additive and does not
   rewrite existing photo or processing rows.
3. Verify current accepted photo embeddings exist for representative published free and paid
   events. Keep `PHOTO_PROCESSING_FACE_ENABLED=True` and the worker processor list configured.
4. Inspect the private bucket and obtain explicit authority for the scoped `selfie-search/`
   24-hour lifecycle rule. Apply and verify that rule without changing original/incoming prefixes.
5. Run `verify_selfie_search_storage` and record sanitized put/grant/delete/lifecycle evidence.
6. With concurrency still one, run one staging selfie through zero-face, multiple-face, no-match,
   free-match, and paid-match cases. Measure worker peak RSS, whole-VM headroom, callback duration,
   exact-search cohort size, and cleanup duration.
7. Enable `SELFIE_SEARCH_ENABLED=True` only if deletion, event isolation, paid-result-only media,
   and current VM headroom pass. Verify the public form, stable URL, reload/share behavior, and
   normal paid-gallery denial.
8. Production activation, when a separate production environment exists, promotes the same
   staging-verified images and repeats lifecycle/preflight checks before enabling the flag.

Minimum structured observations are search state transitions, processor/model version, cohort
counts, match count, bounded durations, stable error code, and cleanup outcome. Logs exclude bearer
tokens, storage keys, signed URLs, vectors, image bytes, and raw callback bodies.

## Rollback

Set `SELFIE_SEARCH_ENABLED=False` to stop new uploads immediately while leaving result GETs and
cleanup callbacks available. Keep the worker able to finish or retry already queued searches until
their temporary objects are deleted. If the worker contract itself is unhealthy, stop new
submissions first, drain or mark searches failed only after exact-object cleanup, then deploy the
previous worker/web images.

Do not reverse the additive migration while any selfie-search rows exist. A later code rollback may
leave the new tables unused. Existing non-expiring bearer results remain readable under ADR 0019
unless a separately approved retention/access migration changes that contract. Keep the
temporary-prefix lifecycle rule during and after rollback so abandoned selfies remain bounded.

## Open questions

None. The implementation values in Global Constraints are reversible, versioned MVP settings. A
material change to public bearer access, paid-original scope, query-vector retention, or event
isolation must return to the specification and ADR 0019 rather than being decided during
implementation.
