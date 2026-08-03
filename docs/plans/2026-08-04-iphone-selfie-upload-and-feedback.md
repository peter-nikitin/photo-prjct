# iPhone Selfie Upload and Rejection Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan
> task by task. Follow the repository TDD and single-final-commit boundaries in `AGENTS.md`.

- Date: 2026-08-04
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`iPhone Selfie Upload and Rejection Feedback Design`](../superpowers/specs/2026-08-03-iphone-selfie-upload-and-feedback-design.md)
- Related architecture:
  [`Current architecture — implemented`](../architecture.md#current-architecture--implemented),
  [`Search`](../architecture.md#search), and
  [`Security, privacy, and legal boundaries`](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0019](../adr/0019-use-public-event-selfie-search.md)
- ADR impact: Resolved — conforms to accepted ADR 0019. The private temporary object and worker
  input remain bounded JPEG or PNG; HEIC/HEIF source decoding is a reversible Django input detail.

## Goal

Implement the approved source-image and rejection-feedback path so ordinary iPhone HEIC/HEIF
photos start the existing search, while every pre-search rejection is visible, actionable, and
diagnosable without retaining sensitive image data.

## Scope

Implement the approved specification without scope changes. In particular, do not change worker
models, ranking, search state, bearer authorization, result retention, lifecycle, upload limits, or
photographer ingestion.

## Acceptance criteria

Delivery must satisfy all
[10 specification acceptance criteria](../superpowers/specs/2026-08-03-iphone-selfie-upload-and-feedback-design.md#acceptance-criteria).
The implementation sequence additionally requires:

- every production behavior change to begin with a focused test that fails for the expected missing
  behavior;
- a committed privacy-safe HEIC fixture with hand-checked orientation and pixel expectations;
- a production web-image smoke that imports `pillow-heif==1.5.0` and decodes that fixture through
  the application boundary; and
- staging evidence for one accepted HEIC/HEIF submission, one rejected unsupported file, HTTP
  `302`/`422` behavior, safe rejection logging, worker completion, and temporary-selfie cleanup.

## File structure

- `src/backend/selfie_search/images.py`: authoritative source identification, bounded decode,
  HEIC/HEIF normalization, canonical representation, and typed rejection evidence.
- `src/backend/selfie_search/forms.py`: Django file-field integration and approved Russian messages;
  successful cleaning returns the canonical representation from `images.py`.
- `src/backend/selfie_search/services/submission.py`: consumes canonical bytes/content type only and
  preserves the existing storage/database compensation boundary.
- `src/backend/selfie_search/views.py`: maps validation/storage outcomes to `422`/`503`, emits one
  safe structured reason, and re-renders the focused event form.
- `src/backend/templates/catalog/event_detail.html`,
  `src/backend/static/ui/selfie-search.css`, and
  `src/backend/static/ui/selfie-search.js`: production error summary, fragment target, enabled retry
  state, and optional focus enhancement.
- `src/backend/selfie_search/tests/fixtures/iphone-oriented.heic`: small generated, privacy-safe real
  HEIC whose dimensions, orientation, and color quadrants are asserted literally.
- `src/backend/selfie_search/tests/test_images.py`, `test_forms.py`, `test_submission.py`, and
  `test_views.py`: source contract, side effects, HTTP outcomes, logging redaction, and regressions.
- `tests/js/selfie-search.test.js`: error-focus behavior without weakening duplicate-submit
  protection.
- `tests/visual/views.py`, `tests/visual/urls.py`, `tests/visual/visual.spec.js`, and the two new
  desktop/mobile snapshots: production-form rejection state.
- `src/backend/requirements.txt`: pin `pillow-heif==1.5.0`; the release requires Python `>=3.10`,
  Pillow `>=11.1.0`, and publishes CPython 3.12 manylinux wheels compatible with the current
  production image.
- `docs/architecture.md` and `docs/product-jobs.md`: update implemented source-format and delivered
  evidence only after behavior and staging verification succeed.

## Cross-task interfaces

Task 1 produces these interfaces for Tasks 2–4:

- `PreparedSelfie(content: bytes, content_type: Literal["image/jpeg", "image/png"], source_size: int, source_format: Literal["jpeg", "png", "heic", "heif"])`;
- `SelfieImageRejected(reason: SelfieRejectionReason, actual_format: str | None)` where
  `SelfieRejectionReason` is restricted to `missing_or_empty`, `unsupported_format`,
  `corrupt_image`, `source_too_large`, `normalized_too_large`, and `pixel_limit_exceeded`;
- `prepare_selfie_image(upload: UploadedFile) -> PreparedSelfie`; and
- `submit_selfie_search(*, event: Event, selfie: PreparedSelfie, storage: TemporarySelfieStorage) -> CreatedSearch`.

`PreparedSelfie` contains no filename, declared MIME type, EXIF, storage key, or bearer value. The
view may read the original upload size and declared MIME only to produce the bounded log fields
defined by the specification.

## Implementation

### Task 1: Decode and canonicalize supported source images

**Files:**

- Create `src/backend/selfie_search/images.py`
- Create `src/backend/selfie_search/tests/test_images.py`
- Create `src/backend/selfie_search/tests/fixtures/iphone-oriented.heic`
- Modify `src/backend/selfie_search/forms.py`
- Modify `src/backend/selfie_search/tests/test_forms.py`
- Modify `src/backend/requirements.txt`

- **Specification:** Source Image Contract, HEIC/HEIF Normalization, Failure Semantics, and
  acceptance criteria 1–3, 6, and 10.
- **Depends on:** Accepted specification and ADR 0019.
- **Produces:** `PreparedSelfie`, `SelfieImageRejected`, `SelfieRejectionReason`, and
  `prepare_selfie_image` exactly as listed under Cross-task interfaces.

- [ ] Add `pillow-heif==1.5.0` to the backend requirements and install the updated backend
  requirements into `.venv`; do not add `libheif` system packages unless the pinned wheel is proven
  unavailable on a supported build platform.
- [ ] Create the privacy-safe HEIC fixture once, inspect its decoded dimensions/orientation/color
  quadrants, and commit only the fixture; tests must use literal expected values rather than derive
  them with the encoder or production helper.
- [ ] Write failing `test_images.py` cases for real HEIC identification, orientation, RGB JPEG
  normalization at quality 90, metadata removal, canonical JPEG content type, unchanged dimensions,
  normalized-size enforcement, unsupported actual formats, corrupt containers, and excessive pixel
  dimensions before full-raster allocation.
- [ ] Extend `test_forms.py` with failing table cases proving that actual JPEG/PNG content is accepted
  with missing, generic, or inconsistent declared MIME/extension; HEIC/HEIF is accepted; and every
  rejected category carries the approved message and stable reason code.
- [ ] Run
  `.venv/bin/pytest -q src/backend/selfie_search/tests/test_images.py src/backend/selfie_search/tests/test_forms.py`
  and confirm failures are caused by the missing canonicalization interface and HEIC support, not by
  fixture corruption or dependency import errors.
- [ ] Implement the smallest canonicalization module and form integration. Register the HEIF opener
  at application import so a missing/invalid runtime dependency prevents Django startup rather than
  silently removing advertised support. Use actual decoder format as authority; preserve verified
  JPEG/PNG bytes, normalize HEIC/HEIF only, and close every decoder/image object deterministically.
- [ ] Rerun the targeted command and confirm all source-format, limit, orientation, metadata, and
  error-code tests pass.
- [ ] Run `.venv/bin/ruff check src/backend/selfie_search/images.py src/backend/selfie_search/forms.py src/backend/selfie_search/tests/test_images.py src/backend/selfie_search/tests/test_forms.py`
  and `.venv/bin/mypy` and confirm both pass.

### Task 2: Pass only canonical bytes into the existing search boundary

**Files:**

- Modify `src/backend/selfie_search/services/submission.py`
- Modify `src/backend/selfie_search/tests/test_submission.py`
- Modify `src/backend/selfie_search/tests/test_storage.py`
- Modify `src/backend/processing/tests/test_views.py`

- **Specification:** HTTP and Data Flow steps 2–8, Failure Semantics, and acceptance criteria 1, 2,
  4, and 7.
- **Depends on:** Task 1 `PreparedSelfie`.
- **Produces:** the revised `submit_selfie_search` signature listed under Cross-task interfaces;
  every new search configuration and temporary-object identity contains only canonical
  `image/jpeg` or `image/png`.

- [ ] Write failing submission tests proving HEIC-derived canonical JPEG bytes/content type reach
  `TemporarySelfieStorage.put`, source HEIC bytes and metadata do not, configuration records the
  canonical object size/type, and current JPEG/PNG storage behavior is unchanged.
- [ ] Update failure-path tests to prove no object/search/job exists after any Task 1 rejection and
  preserve the existing exact-object compensating delete when database creation fails after storage.
- [ ] Add a regression assertion at the worker claim boundary proving its download configuration
  still exposes only `image/jpeg` or `image/png` and no source-format or source-metadata field.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_storage.py src/backend/processing/tests/test_views.py`
  and confirm failures identify the old uploaded-file submission interface.
- [ ] Change the submission service and its callers to consume `PreparedSelfie` directly. Remove the
  obsolete path that rereads an arbitrary uploaded file or trusts its `content_type`; do not add a
  compatibility overload.
- [ ] Rerun the targeted command and confirm canonical storage, compensation, configuration, and
  worker-boundary tests pass.

### Task 3: Return actionable HTTP responses and safe rejection evidence

**Files:**

- Modify `src/backend/selfie_search/views.py`
- Modify `src/backend/selfie_search/tests/test_views.py`
- Modify `src/backend/selfie_search/tests/test_submission.py`
- Modify `src/backend/templates/catalog/event_detail.html`
- Modify `src/backend/static/ui/selfie-search.css`
- Modify `src/backend/static/ui/selfie-search.js`
- Modify `tests/js/selfie-search.test.js`

- **Specification:** User Experience, HTTP and Data Flow, Observability and Privacy, and acceptance
  criteria 3–6 and 8.
- **Depends on:** Tasks 1–2 typed rejections and canonical submission.
- **Produces:** public submit outcomes `302` accepted, `422` customer-correctable, and `503` storage
  unavailable; one structured `selfie_submission_rejected` event for each expected rejection.

- [ ] Write failing Django view tests for all approved messages and status codes, the
  `#selfie-search` response target, one pre-input `role="alert"` summary, an enabled retry button,
  and zero object/search/job side effects on every `422`/`503` branch.
- [ ] Use real logging capture to write failing tests for one event per rejection. Assert the exact
  bounded reason/source-size/actual-format/allowlisted-declared-type fields and assert the complete
  captured record contains no filename, raw MIME outside the allowlist, source bytes, decoded data,
  object key, token, signed URL, vector, raw exception, or traceback. Do not assert on a mocked
  logger.
- [ ] Extend the published-event markup test so `accept` advertises
  `image/jpeg,image/png,image/heic,image/heif,.heic,.heif` while server tests continue to prove the
  hint is non-authoritative.
- [ ] Write a failing JavaScript test in `tests/js/selfie-search.test.js` proving an existing error
  summary receives focus on page start; keep the no-JavaScript alert and fragment behavior covered
  by Django/Playwright rather than simulating a browser in the unit test.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_submission.py`
  and `npm run test:js`, and confirm the old `200` responses, hidden field-level error, missing log,
  and missing focus behavior cause the expected failures.
- [ ] Implement the minimal response mapping, safe structured logging helper, fragment/focus target,
  server-rendered alert, error styling, and optional JavaScript focus. Reuse the existing event view
  and gallery context; do not create a separate error page or client-side converter.
- [ ] Rerun both targeted commands and confirm all statuses, copy, side effects, logging redaction,
  no-JavaScript fallback, duplicate-submit prevention, and focus behavior pass.

### Task 4: Verify the production error state and image runtime

**Files:**

- Modify `tests/visual/views.py`
- Modify `tests/visual/urls.py`
- Modify `tests/visual/visual.spec.js`
- Create `tests/visual/visual.spec.js-snapshots/desktop-event-selfie-search-rejected.png`
- Create `tests/visual/visual.spec.js-snapshots/mobile-event-selfie-search-rejected.png`
- Modify existing selfie-entry snapshots only if dependency/font rendering or the approved input
  hint changes their pixels
- Modify `Dockerfile` only if the pinned manylinux wheel fails in the unchanged Python 3.12 image;
  do not add build tooling speculatively

- **Specification:** User Experience and acceptance criteria 8–10.
- **Depends on:** Task 3 production template/CSS/JavaScript and Task 1 dependency.
- **Produces:** a canonical production-screen rejection fixture and proof that the deployable web
  image decodes the real HEIC fixture.

- [ ] Add a visual route that renders the canonical production event template with a bound
  `SelfieSearchUploadForm` carrying the approved unsupported-format error; do not create a parallel
  design-reference template.
- [ ] Add desktop/mobile screenshot cases and a no-JavaScript behavioral assertion that the alert,
  file input, and enabled retry action are visible at the selfie section without script execution.
- [ ] Run the focused Playwright cases before updating snapshots and confirm failure because the new
  route/snapshots do not exist.
- [ ] Run `npm run test:visual:update`, inspect both new PNGs at
  original resolution, and confirm the alert does not obscure guidance, privacy copy, input, or
  action at 1440×1000 and 390×844.
- [ ] Run `npm run test:visual` and confirm the complete visual/behavioral suite passes without
  browser console, request, or non-document HTTP failures.
- [ ] Build the production web image with
  `docker build -t photo-prjct-selfie-heic-smoke .` and run
  `docker run --rm --entrypoint python -v "$PWD/src/backend/selfie_search/tests/fixtures:/fixtures:ro" photo-prjct-selfie-heic-smoke -c 'from pathlib import Path; from selfie_search.images import prepare_selfie_image; from django.core.files.uploadedfile import SimpleUploadedFile; content=Path("/fixtures/iphone-oriented.heic").read_bytes(); result=prepare_selfie_image(SimpleUploadedFile("iphone.heic", content, content_type="image/heic")); assert result.content_type == "image/jpeg" and result.source_format in {"heic", "heif"}'`
  and confirm exit status 0. This smoke must use the application decoder boundary, not only import
  the third-party package.

### Task 5: Run regression gates and reconcile repository truth

**Files:**

- Modify `docs/architecture.md`
- Modify `docs/product-jobs.md` only after staging evidence exists
- Modify `docs/superpowers/specs/2026-08-03-iphone-selfie-upload-and-feedback-design.md` status/evidence only after delivery
- Modify `docs/plans/2026-08-04-iphone-selfie-upload-and-feedback.md` status/evidence only after delivery

- **Specification:** complete approved specification, especially scope exclusions and acceptance
  criteria 7–10.
- **Depends on:** Tasks 1–4 green and inspected.
- **Produces:** CI-equivalent evidence, accurate architecture/job status, and one reviewable task
  diff ready for independent review.

- [ ] Run focused selfie coverage together:
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest -q src/backend/selfie_search/tests tests/processing/test_selfie_search_e2e.py src/backend/processing/tests/test_views.py`.
- [ ] Run repository static/Django gates:
  `.venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, `.venv/bin/mypy`,
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/python src/backend/manage.py check`, and the same environment with
  `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`.
- [ ] Run `npm run test:js` and the complete `npm run test:visual`; expect all JavaScript and visual
  cases to pass with only the two approved new snapshots or explicitly justified updates.
- [ ] Run the complete CI-like Python suite once, without overlapping pytest processes:
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest --cov --cov-report=term-missing`.
- [ ] Compare the complete diff with every specification acceptance criterion. Confirm no worker,
  ranking, state, lifecycle, bearer, result-retention, photographer-ingestion, or limit expansion
  entered the change.
- [ ] Update `docs/architecture.md` from JPEG/PNG-only source wording to the verified
  JPEG/PNG/HEIC/HEIF source-to-canonical-object boundary. Record conformance to ADR 0019; do not edit
  the accepted ADR because its stored-object/worker boundary remains unchanged.
- [ ] Prepare one reviewable working-tree diff including the untracked fixture, spec, plan, tests,
  snapshots, and implementation. Obtain independent review before staging or committing; return any
  fixes through the same TDD checks and reviewer.

### Task 6: Deliver through PR, CI, and staging verification

**Files:** exact approved task files from Tasks 1–5; no unrelated working-tree changes.

- **Specification:** Outcome, HTTP and Data Flow, Observability and Privacy, and all acceptance
  criteria.
- **Depends on:** Task 5 verification and independent approval.
- **Produces:** one final task commit, reviewed PR, green required checks, merged main, deployed
  staging image, and incident-path evidence.

- [ ] The root controller reruns final focused verification after review, stages only approved task
  files, and creates the single final task commit required by `AGENTS.md`; no implementation or
  review-fix commits precede it.
- [ ] Push a `codex/` branch, open the PR, wait for required checks, and resolve only failures caused
  by this task using the same failing-test-first discipline.
- [ ] Merge only after required checks and review are green, then wait for the standard main-to-
  staging deployment. Do not bypass `deploy/apply-deployment.sh` or mutate Yandex Cloud resources.
- [ ] Verify the live deployed commit/image, `/health/`, web/nginx/worker health, restart/OOM state,
  and absence of decoder import/startup failures.
- [ ] Submit one privacy-safe real HEIC/HEIF fixture through the public event form and verify `302`,
  bearer progress, worker completion, a terminal result, and deletion of its temporary object before
  terminal publication. The expected terminal domain outcome may be `no_face`; the purpose is the
  upload/decode/queue contract, not a face-match claim.
- [ ] Submit one privacy-safe unsupported fixture and verify HTTP `422`, the approved focused alert,
  enabled retry action, exactly one safe `unsupported_format` event, and no new search/job/object.
- [ ] Confirm sampled public submit logs distinguish `302` and `422` and contain no filename,
  object key, bearer token, raw MIME outside the allowlist, or image data. Do not manufacture an
  Object Storage outage merely to prove `503`; rely on automated failure injection for that branch.
- [ ] Update `docs/product-jobs.md`, the specification status/evidence, and this plan's status with
  the actual PR, commit, CI, deploy, and live verification facts. If those documentation edits were
  not in the reviewed commit, deliver them through the smallest separately reviewed documentation
  follow-up rather than amending deployed history.

### Final task: Architecture and ADR reconciliation

- [ ] Confirm the delivered browser-source boundary is JPEG/PNG/HEIC/HEIF while the stored object
  and worker input remain bounded canonical JPEG/PNG.
- [ ] Confirm ADR 0019 privacy invariants remain true: private temporary object, no persisted query
  embedding, cleanup before terminal publication, event isolation, and unchanged bearer access.
- [ ] Confirm `docs/architecture.md`, `docs/product-jobs.md`, the specification, and this plan describe
  only verified implementation and staging evidence.
- [ ] Record the final outcome as “Conforms to ADR 0019; no new or superseding ADR required” in the
  pull request.

## Verification

The task is complete only when all commands listed in Tasks 1–6 have fresh successful output. The
minimum final evidence set is:

- focused source-image, form, submission, view, storage, worker-boundary, and real selfie E2E tests;
- Ruff formatting/lint, configured `mypy`, Django system checks, and no migration drift;
- all JavaScript unit tests and complete containerized Playwright visual regression;
- one non-overlapping repository-wide pytest coverage run;
- successful production image build and real HEIC application-boundary smoke;
- green PR checks; and
- live staging `302` HEIC acceptance, `422` unsupported rejection, safe log evidence, worker
  completion, cleanup, and healthy containers.

## Operational impact and rollout

- No database migration, feature flag, environment variable, worker image change, Yandex Cloud
  resource change, or pricing-sensitive operation is required.
- The web and visual-test Python dependency layers change because `src/backend/requirements.txt`
  changes. CI will publish/use a new visual dependency image key automatically.
- The web container gains the bundled HEIC/HEIF decoder wheel and performs bounded synchronous
  decode/normalization during the existing upload request. Existing 20 MiB and 25,000,000-pixel
  limits remain the memory/CPU guard.
- Deployment uses the standard immutable application image and main-to-staging workflow. Verify
  decoder startup and one real HEIC upload before considering the incident path delivered.
- Monitoring uses existing Nginx/Docker logs plus the new bounded rejection event. No centralized
  log service or new metric backend is introduced.

## Rollback

Revert the application/dependency change and redeploy the prior immutable web image through the
standard workflow. No schema or durable-data rollback is needed. Existing queued/terminal searches
remain readable because their stored object and worker contracts never changed. Rollback removes
HEIC/HEIF acceptance and the new `422`/`503` feedback/logging behavior, so restore it only for a
decoder/runtime regression and retain incident evidence for the follow-up fix.

## Open questions

None.
