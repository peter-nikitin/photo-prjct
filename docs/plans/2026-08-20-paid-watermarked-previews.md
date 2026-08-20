# Paid Watermarked Previews Implementation Plan

- Date: 2026-08-20
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-20-paid-watermarked-previews-design.md`](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md)
- Related ADRs:
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../adr/0021-allow-original-download-for-authorized-photos.md),
  [ADR 0022](../adr/0022-use-numbered-gallery-pages.md), and
  [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md)
- ADR impact: implements accepted ADR 0029. It preserves ADR 0017's job boundary, ADR 0020's
  private-storage delivery boundary, and ADR 0022's pagination while applying only the paid-media
  supersessions recorded by ADR 0029.

## Goal

Deliver the approved [paid watermarked preview
outcome](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#outcome): newly
confirmed paid photos keep a private clean ML preview and gain one accepted watermarked public
preview used by both the normal gallery and ready selfie-search results.

## Scope

Implement the approved specification without expanding into price, cart, checkout, payment,
purchase entitlement, or purchased-original delivery.

The later anonymous-cart task may consume the shared `GalleryPhoto.photo_id` and add a cart action
to existing card action markup. This plan does not add cart state or UI. It keeps
`PublicMediaResolver` as the only selector of public bytes, so a later cart action cannot become a
second media-selection path.

Execution must use `$execute-implementation-plan`. Each task below is one reviewed implementation
change; real-environment activation remains a separate maintainer-approved operation after final
artwork exists.

## Acceptance criteria

Use the specification's [acceptance
criteria](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#acceptance-criteria).
In addition:

- `GalleryPhoto.photo_id` remains the stable application identity shared by the normal gallery and
  selfie-result presentation; watermarked paid instances expose `download_url=None`;
- gallery and selfie templates retain one card action area and a stable photo-id data attribute so
  the later cart task can add an action without changing media authorization;
- no task adds price fields, cart models, browser cookies, entitlement checks, or an original
  purchase route;
- implementation may contain conspicuous placeholder PNGs for local verification, but the runtime
  gate remains off and the deployed worker identity remains inactive until the maintainer supplies
  and approves both final PNGs;
- every task passes its focused tests, and the assembled change passes `make check`, the worker
  suite, deployment-contract tests, and desktop/mobile visual regression before delivery.

## Implementation

### Task 1: Persist the paid-photo policy and freeze event access type

**Files:**

- Create `src/backend/picflow/photo_policy.py`.
- Create `src/backend/picflow/migrations/0012_paid_watermarked_photo_policy.py`.
- Modify `src/backend/picflow/models.py`.
- Modify `src/backend/picflow/admin.py`.
- Modify `src/backend/ingestion/services/confirmation.py`.
- Modify `src/backend/picflow/tests/test_models.py`.
- Modify `src/backend/picflow/tests/test_admin.py`.
- Modify `src/backend/ingestion/tests/test_confirmation.py`.

- **Specification:** [Domain and Data
  Model](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#domain-and-data-model)
  and [Compatibility and
  Activation](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#compatibility-and-activation).
- **Depends on:** None.
- **Produces:** `Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1`,
  `Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED`, and
  `picflow.photo_policy.policy_for_new_photo(event, user)` as the sole confirmation-time policy
  selector.

- [ ] Add failing model and migration tests for exactly three valid generation/policy pairs, the
  new 32-character values, and a schema-only migration that neither rewrites photos nor creates
  processing rows.
- [ ] Add failing policy tests for a free photo, a paid photo while the
  `paid-watermarked-previews` feature flag is missing/off, and a paid photo for an enabled caller.
  The enabled paid case must return the new pair; the other cases retain current behavior.
- [ ] Add failing admin tests proving `access_type` remains editable before the first photo and is
  rejected with a field error afterward while unrelated event fields remain editable.
- [ ] Add a transaction test with two database connections proving administrative access-type
  editing and first-photo confirmation serialize on the same `Event` row: either the edit commits
  first and confirmation uses the new type, or photo creation commits first and the edit is
  rejected.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/ingestion/tests/test_confirmation.py"`
  and confirm failures identify the missing values, selector, and lock.
- [ ] Add the new choices and replace `picflow_photo_processing_policy_pair_chk` with a constraint
  accepting exactly the three documented pairs. Do not add a watermark field, derivative FK,
  date cutoff, data migration, or compatibility inference.
- [ ] Define the stable release key `paid-watermarked-previews` in `photo_policy.py` and delegate
  evaluation to `feature_flags.services.is_enabled`. A missing row remains fail-closed. Once a
  `Photo` exists, all background processing follows its persisted pair and never reevaluates the
  feature flag.
- [ ] On upload confirmation, lock and reload the batch's `Event` before choosing the policy and
  creating the first photo. Keep preview geometry inspection outside the transaction; use the
  locked event only for the authoritative policy decision and persisted relation.
- [ ] Wrap the supported Django Admin event-change POST in one atomic section and have its form
  lock the persisted event before accepting an `access_type` change. Reject the change if any
  photo exists, holding the row lock through save so it races safely with confirmation.
- [ ] Run the focused command above; expect all policy, migration, admin, confirmation, and race
  tests to pass.

### Task 2: Add the bounded watermark renderer and worker contract

**Files:**

- Create `src/worker/photo_worker/watermark.py`.
- Create `src/worker/photo_worker/assets/watermark-landscape-v1.png`.
- Create `src/worker/photo_worker/assets/watermark-portrait-v1.png`.
- Create `src/worker/tests/test_watermark.py`.
- Modify `src/worker/photo_worker/contracts.py`.
- Modify `src/worker/photo_worker/runner.py`.
- Modify `src/worker/photo_worker/runtime_contract.py`.
- Modify `src/worker/tests/test_contracts.py`.
- Modify `src/worker/tests/test_runner.py`.
- Modify `src/worker/tests/test_model_smoke.py`.

- **Specification:** [Watermark Media
  Contract](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#watermark-media-contract)
  and worker portions of [Security and Privacy
  Boundaries](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#security-and-privacy-boundaries).
- **Depends on:** None.
- **Produces:** exact worker identity `2/generate_watermarked_preview/1`, configuration kind
  `generate_watermarked_preview`, and a `WatermarkedPreviewResult` compatible with the existing
  attempt result/upload lifecycle.

- [ ] Add failing image tests using asymmetric, pixel-addressable clean images and overlays. Prove
  landscape and square select the landscape PNG, portrait selects the portrait PNG, proportional
  `cover` is centered, opposite-edge excess is cropped equally, alpha is respected, dimensions are
  unchanged, output is sRGB JPEG, and metadata is absent.
- [ ] Add failing bounded-failure tests for corrupt/unsupported clean input, corrupt/non-RGBA or
  empty overlays, asset checksum mismatch, pixel and byte limits, and an output exceeding its exact
  slot. Each failure must return a declared sanitized code without uploading bytes.
- [ ] Add failing contract and runner tests for identity `2/generate_watermarked_preview/1`, an
  accepted-clean-preview input fingerprint, both asset SHA-256 values, exact
  `preview-watermarked-v1` output slot, lease handling, duplicate/stale completion behavior, and
  secret-safe logs.
- [ ] Run
  `make test TESTS="src/worker/tests/test_watermark.py src/worker/tests/test_contracts.py src/worker/tests/test_runner.py src/worker/tests/test_model_smoke.py"`
  and confirm failures are limited to the missing processor and assets.
- [ ] Implement a Pillow renderer that opens the already oriented clean JPEG, verifies the selected
  packaged PNG checksum before composition, applies centered `cover`, alpha-composites once, and
  encodes with the declared fixed JPEG settings. Do not add layout parameters, tiling, EXIF work,
  or another resize of the clean input.
- [ ] Commit two visually obvious non-production transparent PNGs solely to exercise the complete
  path. Keep their filenames stable; replacing either asset requires updating its declared SHA-256
  and processor configuration/version before activation.
- [ ] Extend the versioned claim/configuration/output validators and runner dispatch. The watermark
  worker must accept only the clean derivative grant supplied by Django and the one attempt-scoped
  upload slot; it must not derive keys or request an original.
- [ ] Extend the worker build-time runtime check to verify both packaged asset files and their
  declared checksums so a mismatched image cannot start a compatible identity.
- [ ] Run the focused worker command above; expect all renderer, contract, dispatch, build-time,
  and failure tests to pass.

### Task 3: Publish the watermark through the existing processing state machine

**Files:**

- Modify `src/backend/processing/models.py`.
- Modify `src/backend/processing/contracts.py`.
- Modify `src/backend/processing/storage.py`.
- Modify `src/backend/processing/services/enrollment.py`.
- Modify `src/backend/processing/services/previews.py`.
- Modify `src/backend/processing/services/jobs.py`.
- Modify `src/backend/processing/services/reports.py`.
- Modify `src/backend/processing/views.py`.
- Modify `src/backend/processing/checks.py`.
- Modify `src/backend/processing/admin_progress.py` only as needed to label and count the new
  existing-state-machine processor.
- Modify `src/backend/processing/tests/test_models.py`.
- Modify `src/backend/processing/tests/test_storage.py`.
- Modify `src/backend/processing/tests/test_enrollment.py`.
- Modify `src/backend/processing/tests/test_previews.py`.
- Modify `src/backend/processing/tests/test_views.py`.
- Modify `src/backend/processing/tests/test_reports.py`.
- Modify `src/backend/processing/tests/test_admin_progress.py` if `admin_progress.py` changes.

- **Specification:** [Derivatives and processing
  evidence](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#derivatives-and-processing-evidence),
  [Enrollment and
  Publication](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#enrollment-and-publication),
  and [Failure, Retry, and Consistency
  Semantics](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#failure-retry-and-consistency-semantics).
- **Depends on:** Tasks 1 and 2.
- **Produces:** `GENERATE_WATERMARKED_PREVIEW_PROCESSOR`,
  `GENERATE_WATERMARKED_PREVIEW_CONTRACT`, `request_generate_watermarked_preview(photo,
  clean_preview)`, and immutable `preview-watermarked-v1` publication.

- [ ] Add failing `PhotoDerivative.clean()` tests proving each supported variant accepts only its
  matching accepted successful producer and rejects cross-photo, cross-processor, unsuccessful,
  stale, or unaccepted attempts.
- [ ] Add failing enrollment tests proving confirmation requests only `generate_preview`; accepting
  the clean preview then independently requests face and watermark work with equal immutable clean
  fingerprints including key, bytes, SHA-256, dimensions, media kind, and accepted attempt. Free
  and older paid policies must not request watermark work.
- [ ] Add failing API/storage tests proving a watermark attempt gets a short-lived GET grant only
  for its accepted `preview-small-v1` and a PUT grant only for
  `processing-staging/previews/<attempt>/preview-watermarked-v1.jpg`. Unknown keys, variants,
  fingerprints, identities, and expired leases fail before a grant is signed.
- [ ] Add failing completion tests for independent HEAD/stream hash and dimensions, reported hash
  agreement, non-overwriting promotion to
  `derivatives/previews/<photo>/preview-watermarked-v1/<attempt>-<sha>.jpg`, atomic accepted state
  plus immutable derivative publication, retries, stale attempts, idempotent duplicate callbacks,
  and conflicting duplicate callbacks.
- [ ] Add a regression test proving watermark failure/retry neither changes nor reenqueues the
  sibling face job, and face failure does not invalidate an accepted watermark derivative.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_models.py src/backend/processing/tests/test_storage.py src/backend/processing/tests/test_enrollment.py src/backend/processing/tests/test_previews.py src/backend/processing/tests/test_views.py src/backend/processing/tests/test_reports.py src/backend/processing/tests/test_admin_progress.py"`
  and confirm the new processor expectations fail while existing preview/face cases remain green.
- [ ] Extend the current preview publication service with two explicit processor/variant profiles
  rather than copying its state machine. Preserve the clean-preview behavior byte for byte; select
  staging/final key, producer, declared limits, and post-publication enrollments from the matched
  profile.
- [ ] After accepting `preview-small-v1` for the new generation, request face and watermark siblings
  in the same authoritative completion transaction. Watermark acceptance publishes no downstream
  ML work.
- [ ] Extend exact-key storage validation, claim dispatch, result validation, failure/warning
  allowlists, reports, system checks, and the existing admin progress projection only where they
  enumerate supported processors. Do not add a watermark table, a second state vocabulary, or
  event-type inference.
- [ ] Run the focused command above; expect all lifecycle and regression tests to pass.

### Task 4: Make watermarked media the only public bytes for the new paid policy

**Files:**

- Modify `src/backend/picflow/gallery.py`.
- Modify `src/backend/config/views.py`.
- Modify `src/backend/selfie_search/services/results.py`.
- Modify `src/backend/selfie_search/views.py`.
- Modify `src/backend/templates/catalog/event_detail.html`.
- Modify `src/backend/selfie_search/templates/selfie_search/result.html`.
- Modify `src/backend/picflow/tests/test_gallery.py`.
- Modify `src/backend/picflow/tests/test_views.py`.
- Modify `src/backend/selfie_search/tests/test_results.py`.
- Modify `src/backend/selfie_search/tests/test_views.py`.
- Modify `tests/js/event-gallery.test.js`.
- Modify `tests/visual/visual.spec.js` and add only the new paid-gallery and paid-result snapshots.

- **Specification:** [Public Presentation
  Interface](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#public-presentation-interface),
  [Gallery and Selfie-Result
  Behavior](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#gallery-and-selfie-result-behavior),
  and request-facing [Security and Privacy
  Boundaries](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#security-and-privacy-boundaries).
- **Depends on:** Tasks 1 and 3.
- **Produces:** `GalleryPhoto.download_url: str | None`, event-surface and saved-result eligibility
  querysets with distinct compatibility contracts, and one policy-aware `PublicMediaResolver`.

- [ ] Add failing queryset tests proving a free normal gallery is unchanged; an enabled published
  paid gallery contains only new-policy photos with mutually consistent accepted watermark state,
  attempt, and derivative; one failed/pending photo does not hide ready siblings; and old paid
  policies never appear in the normal gallery.
- [ ] Keep saved-result compatibility separate from normal-gallery eligibility. Add failing tests
  proving old paid saved members retain their current explicit policy behavior, while a new-policy
  member is eligible only with an accepted watermark and only while the runtime gate permits that
  request user.
- [ ] Add failing resolver and endpoint tests for the exact policy matrix: both semantic variants of
  a new paid photo sign the same `preview-watermarked-v1`; original and clean-preview substitution
  cannot be requested; normal-gallery and bearer-result downloads return sanitized `404` before
  any storage signer call; missing objects remain sanitized; free behavior is unchanged.
- [ ] Add failing factory/template tests proving both normal and selfie result flows use the same
  `GalleryPhoto.photo_id`, semantic small/large media, and `download_url=None`. Assert no download
  anchor or lightbox download HTML is emitted when absent.
- [ ] Add a JavaScript characterization test proving GLightbox initialization and slide changes
  tolerate a card with no description download, without inventing a hidden fallback action.
- [ ] Add `data-photo-id="{{ photo.photo_id }}"` to the shared card/figure boundary on both
  surfaces and retain the existing action container when download is absent. This is the complete
  integration contract for the later cart task; do not add a cart action or expose policy,
  derivative, object-key, watermark, price, or entitlement fields.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py"`
  and confirm failures cover only the new eligibility, authorization, and nullable capability.
- [ ] Refactor gallery eligibility into an event-surface query and a saved-result presentation
  query instead of making selfie results reuse the paid normal-gallery compatibility rule. Pass
  the evaluated request gate explicitly to the query boundary; do not infer it from event type.
- [ ] Make `GalleryPhotoFactory` omit original download capability for only
  `watermarked_preview_required`. Keep stable application media URLs; make
  `PublicMediaResolver` map both semantic variants to the accepted watermark and reject download
  before signing.
- [ ] Allow the normal paid gallery only when `paid-watermarked-previews` is enabled for the current
  caller. With the gate off/missing, keep the paid normal gallery closed and hide new-policy paid
  result members; do not reinterpret or expose their stored objects. Old paid bearer-result
  behavior remains governed by its persisted legacy policy until commerce supersedes it.
- [ ] Update both templates with conditional download markup and no new customer copy. Reuse the
  existing pagination, filters, lightbox, capture time, face controls, and empty state.
- [ ] Run the focused Python command above, `npm run test:js`, and `npm run test:visual`; expect
  Python authorization tests, the no-download lightbox contract, and desktop/mobile paid/free
  gallery and result snapshots to pass.

### Task 5: Package the processor without activating placeholder artwork

**Files:**

- Modify `.env.example`.
- Modify `deploy/apply-deployment.sh`.
- Modify `tests/deployment/test_deployment_scripts.py`.
- Modify `tests/processing/test_worker_container_contract.py`.
- Modify `tests/test_repository_foundation.py`.
- Modify `docs/architecture.md` only for implemented runtime facts not deferred to the final task.

- **Specification:** [Compatibility and
  Activation](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md#compatibility-and-activation).
- **Depends on:** Tasks 1 through 4.
- **Produces:** a worker image capable of the new exact identity and a reviewed, fail-closed future
  activation sequence. It does not activate the identity or flag.

- [ ] Add failing deployment-contract tests proving
  `2/generate_watermarked_preview/1` is an allowed exact identity, but is absent from current
  `PHOTO_WORKER_PROCESSOR_IDENTITIES` defaults and from the required preview-processing identity
  set while placeholder assets remain.
- [ ] Add a failing repository contract proving `.env.example` documents the optional exact
  identity and the required order: approved assets and checksums, worker activation, one real
  staff-only smoke, then public gate activation.
- [ ] Run
  `make test TESTS="tests/deployment/test_deployment_scripts.py tests/processing/test_worker_container_contract.py tests/test_repository_foundation.py"`
  and confirm failures identify only the missing supported identity and activation documentation.
- [ ] Extend deploy validation to accept the exact new identity when explicitly configured. Do not
  append it to Compose, workflow, `.env`, or deployment-script defaults and do not require it for
  ordinary preview processing yet.
- [ ] Document that the database feature-flag row is absent/off by default and no data migration
  creates or enables it. A code deploy may package placeholder assets, but cannot enqueue the new
  policy or expose its public gallery for anonymous users.
- [ ] Run the focused deployment-contract command above; expect all tests to pass.

### Task 6: Prove the assembled critical path

**Files:**

- Create `src/backend/processing/tests/test_paid_watermarked_preview_flow.py`.
- Modify only existing test helpers/fixtures directly required by that end-to-end application
  test.

- **Specification:** all approved acceptance criteria, especially criteria 1–19.
- **Depends on:** Tasks 1 through 5.
- **Produces:** one human-auditable application-level proof from paid upload confirmation through
  clean ML input, watermark publication, normal gallery, and ready selfie-result authorization.

- [ ] Write one integration test that enables the gate for staff, confirms a new paid JPEG, accepts
  the clean preview, proves face and watermark sibling fingerprints, accepts the watermark, and
  verifies normal-gallery and saved-result DTOs/routes expose only the watermark with no download.
- [ ] In the same test module, prove a free sibling stores only the clean derivative and retains
  its existing original download, and an existing paid photo receives no watermark enrollment or
  normal-gallery fallback.
- [ ] Exercise retry/failure at the watermark boundary and prove the paid photo disappears without
  changing the accepted clean derivative or face state.
- [ ] Run
  `make test TESTS="src/backend/processing/tests/test_paid_watermarked_preview_flow.py"`; expect the
  complete critical path and fail-closed regression cases to pass.
- [ ] Run `make check`; expect the complete Django quality and test suite to pass.
- [ ] Run `make test TESTS="src/worker/tests"`; expect the complete worker suite to pass.
- [ ] Run `make test TESTS="tests/deployment tests/processing/test_worker_container_contract.py tests/test_repository_foundation.py"`;
  expect all packaging and deployment contracts to pass.
- [ ] Run `npm run test:visual`; expect all desktop and mobile snapshots to pass.
- [ ] Run `npm run test:js`; expect the complete browser-behavior unit suite to pass.
- [ ] Build the worker image and run its baked `photo_worker.runtime_contract` and
  `photo_worker.model_smoke`; expect checksum, CPU-only runtime, and model checks to pass. This is
  packaging evidence, not permission to activate placeholder artwork.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADR 0029, the still-applicable
  boundaries of ADRs 0017/0019/0020/0021/0022, and `docs/architecture.md`.
- [ ] Update `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md` only with
  evidence actually delivered by the implementation; keep price, cart, purchase, entitlement,
  and purchased-original delivery explicitly unimplemented.
- [ ] Record the shared follow-on cart seam: `GalleryPhoto.photo_id`, nullable `download_url`, the
  existing card action container, and `PublicMediaResolver` ownership of media selection. Do not
  prescribe the cart's model or cookie design in watermark documentation.
- [ ] Confirm ADR 0029 remains accurate. Stop for a decision instead of contradicting it;
  supersede rather than edit an accepted decision.
- [ ] Record focused commands, full-suite results, worker-image evidence, visual snapshots, and the
  still-blocked real-activation gate in the pull request.

## Verification

Run after the complete implementation:

```sh
make test TESTS="src/backend/picflow/tests src/backend/ingestion/tests/test_confirmation.py src/backend/processing/tests src/backend/selfie_search/tests"
make test TESTS="src/worker/tests"
make test TESTS="tests/deployment tests/processing/test_worker_container_contract.py tests/test_repository_foundation.py"
make check
npm run test:js
npm run test:visual
docker build --file Dockerfile.worker --tag findme-photo-worker:watermark-local .
docker run --rm findme-photo-worker:watermark-local python -m photo_worker.runtime_contract
docker run --rm findme-photo-worker:watermark-local python -m photo_worker.model_smoke
```

Expected outcomes:

- all focused and complete Python suites pass with no migration drift;
- visual regression passes for free and paid normal galleries and selfie results at desktop and
  mobile sizes;
- the worker image verifies exact watermark assets and remains CPU-only;
- no test observes storage signing for a forbidden new-policy original, clean preview, or download;
- defaults still omit the new worker identity and the runtime gate remains absent/off.

## Operational impact and rollout

The schema migration adds enum-compatible values and replaces one check constraint; it performs no
row rewrite, enrollment, backfill, or deletion. Existing photos and saved results retain their
current generation and policy.

Code rollout is fail-closed: the `paid-watermarked-previews` row is absent/off and the deployed
worker identity list excludes `2/generate_watermarked_preview/1`. Free uploads and existing paid
behavior continue. No deployment or cloud mutation is authorized by this plan.

Real activation is a later operational change with this exact order:

1. Replace both placeholder PNGs with maintainer-approved artwork, update their declared SHA-256
   values and processor version/configuration, rerun image and visual tests, and rebuild both app
   and worker images.
2. Deploy code/schema first with the runtime gate off; verify migration, health, and that public
   paid galleries remain closed.
3. Explicitly add `2/generate_watermarked_preview/<approved-version>` to the active worker identity
   list and verify the running worker advertises that exact identity.
4. Create/set `paid-watermarked-previews=staff`; upload one new paid photo as staff and verify the
   original -> clean preview -> face plus watermark graph, private storage keys, normal staff
   gallery, ready staff selfie result, and 404 download behavior.
5. After maintainer approval of the real rendered image, set the same flag to `on` and verify one
   anonymous normal-gallery route and one anonymous bearer-result route. Then monitor watermark
   queue failures, retries, latency, and public 404/5xx rates.

Do not enable the feature flag before a compatible worker identity is active. Do not reuse
placeholder-produced derivatives after final artwork is supplied; remove the disposable test event
or its photos as a separately approved operator action.

## Rollback

Set `paid-watermarked-previews=off` first. This immediately closes the normal paid gallery, hides
new-policy members from paid result presentation, and stops assignment of the new policy to later
confirmations. It does not reinterpret already persisted photos.

After the queue drains or is intentionally left as evidence, remove the watermark processor from
the active worker identity list. Reverting application code is safe only while the gate is off and
no older code can encounter the new enum values; otherwise retain the forward-compatible code and
schema. Do not delete originals, derivatives, jobs, attempts, states, or migration rows during
rollback. Placeholder test data may be deleted only as a separately named, approved maintenance
operation.

## Open questions

None. Final artwork is an explicit activation prerequisite, not an implementation-design question.
