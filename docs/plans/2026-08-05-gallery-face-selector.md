# Gallery Face Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`)
> syntax for tracking. Repository `AGENTS.md` overrides generic intermediate-commit guidance:
> implementers and reviewers leave changes unstaged, and the root controller creates one final
> implementation commit after approval.

- Date: 2026-08-05
- Status: Ready
- Owner: project maintainer
- Related specification:
  [Gallery Face Selector Design](../superpowers/specs/2026-08-05-gallery-face-selector-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), implemented gallery,
  accepted face-processing evidence, Search, and security/privacy boundaries
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md) and
  [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md)
- ADR impact: conforms to the expanded, accepted ADR 0024 in this unmerged delivery and preserves
  ADR 0019's event, immutable-result, bearer, and media-authorization boundaries.

**Goal:** Replace the unique-face text action with compact face crops that submit one usable face
directly or let the customer explicitly choose among several faces before starting the existing
event-scoped ready search.

**Architecture:** Django derives bounded presentation records from current accepted detections and
the existing preview coordinate space, while the POST service revalidates an exact detection and
uses its stored embedding transiently. Server-rendered `<details>` markup provides the functional
fallback; small JavaScript enhancements manage one anchored chooser and focus without changing
GLightbox.

**Tech stack:** Django 6, PostgreSQL JSON fields, server-rendered templates, existing CSS design
system, dependency-free JavaScript, Node test runner, Playwright visual tests.

## Global Constraints

- Use only current compatible accepted `kept` faces with valid `preview-small-v1` geometry.
- Keep ranking inside the source event with SFace, 128 dimensions, and threshold `0.363`.
- Never put an embedding, storage key, processing payload, or signed Object Storage URL in HTML.
- Add no model, migration, package, face-crop object, crop endpoint, inference, worker job, or
  temporary media.
- Keep one bounded face-presentation query for the paginated photo set; no per-card queries and no
  hydrated vectors for presentation.
- Preserve direct POST/CSRF behavior, source-photo membership, atomic ready results, empty
  temporary-object state, and absence of persisted query vectors.
- Keep the original-download action and GLightbox behavior independent from face controls.
- Preserve the native no-JavaScript path and keyboard operation on desktop and mobile.

## Scope

Implement the approved specification without scope changes. The ADR 0024 text and index update are
part of this same unmerged decision; no new ADR number is created.

## Acceptance Criteria

Use the specification's [Acceptance Criteria](../superpowers/specs/2026-08-05-gallery-face-selector-design.md#acceptance-criteria).
Delivery additionally requires inspected desktop/mobile visual snapshots and a successful local
smoke against the restored staging database for one-face, multi-face, and `+ N` cards.

## File Structure and Interfaces

- `src/backend/picflow/gallery.py` owns immutable face-crop and gallery-card presentation values plus
  the pure normalized crop calculation. It performs no face database lookup.
- `src/backend/selfie_search/services/submission.py` owns the shared accepted-generation predicate,
  bounded face-presentation lookup, exact selected-detection revalidation, and direct ranking.
- `src/backend/config/views.py` maps the current page's face records into `GalleryPhoto` values.
- `src/backend/selfie_search/views.py` and `urls.py` own the event/photo/detection-addressed POST.
- `src/backend/templates/catalog/event_detail.html` renders direct and disclosure forms.
- `src/backend/static/ui/catalog.css` owns the crop, stack, anchored chooser, and responsive layout.
- `src/backend/static/ui/event-gallery.js` owns chooser enhancement alongside its existing
  independent GLightbox initialization.
- Existing Django, Node, and Playwright test files remain the verification surfaces; no second
  prototype or production route is introduced.

## Implementation

### Task 1: Selected-face presentation and submission domain

**Deliverable:** One shared domain boundary returns ordered, vector-free face presentation records
for a page and atomically creates a ready result from an exact selected detection.

**Files:**

- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`

**Specification:** Usable face contract; Selected-face submission and ranking; Failure semantics.

**Depends on:** Expanded ADR 0024; existing compatible-generation filtering, `rank_embeddings`,
`GallerySearchUnavailable`, `GallerySearchFailed`, and atomic ready-result code.

**Produces:**

- immutable `GalleryFaceCrop` with `detection_id`, one-based `face_number`, and normalized square
  crop values;
- pure `gallery_face_crop(...) -> GalleryFaceCrop | None`, enforcing finite geometry, 20% padding,
  square clipping, and percentage normalization;
- `gallery_search_faces_by_photo(*, event, photos) -> dict[str, tuple[GalleryFaceCrop, ...]]` with
  deterministic face-index/detection-ID ordering and no selected vector column;
- `submit_gallery_photo_search(*, event, photo, detection_id, now=None) -> CreatedSearch`.

- [ ] Add failing pure crop tests for centered, edge-clipped, rectangular, malformed, non-finite,
  wrong-coordinate-space, and non-positive preview geometry. Assert exact normalized values and
  no source payload mutation.
- [ ] Add failing submission tests showing two compatible faces are both presentable, ordered by
  `face_index` then ID, while rejected, stale-generation, legacy-coordinate, malformed-shape,
  cross-event, and off-page faces are absent. Capture the bounded query count and selected fields so
  presentation cannot regress to N+1 or vector hydration.
- [ ] Change the former unique-face tests to submit the exact detection. Prove either face on one
  source photo produces its own query result/configuration evidence, and forged, foreign, stale, or
  incompatible detection IDs create no search.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/selfie_search/tests/test_submission.py::GalleryPhotoSubmissionTests"`.
  Expect the new type/functions/signature assertions to fail before implementation.
- [ ] Implement the immutable crop value and pure calculation in `picflow.gallery`. Keep the
  presentation type independent of ORM models and storage.
- [ ] Refactor compatible face filtering only enough to share the accepted state/generation rules.
  Make the presentation query select detection identity, photo identity, face index, and geometry;
  use database-side JSON type/length filtering where needed and never select `vector` into the
  presentation records.
- [ ] Evolve direct submission to resolve exactly the supplied detection through the same predicate,
  validate its vector only inside submission, record the source detection ID in search
  configuration, and retain the existing transaction and failure mapping.
- [ ] Re-run the Task 1 command and expect all selected tests to pass, then run
  `make test TESTS="src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_results.py"`
  and expect no direct-ranking/result regression.
- [ ] Self-review the unstaged Task 1 diff for vector leakage, cross-event joins, accepted-attempt
  drift, JSON edge cases, query inflation, and any compatibility branch for the obsolete
  unique-face path.

### Task 2: Event page and exact selected-face POST

**Deliverable:** Gallery cards receive zero or more face crop values, and every rendered face form
targets a protected event/photo/detection route whose view passes the exact detection to Task 1.

**Files:**

- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/selfie_search/urls.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`

**Specification:** Card footer controls; Selected-face submission and ranking; Failure semantics;
Privacy, authorization, and compatibility.

**Depends on:** Task 1's `GalleryFaceCrop`, `gallery_search_faces_by_photo`, and selected-detection
submission signature.

**Produces:**

- `GalleryPhoto.faces: tuple[GalleryFaceCrop, ...]` with no `similar_search_url` compatibility field;
- URL name `selfie_search:submit_gallery_face` containing event slug, photo ID, and detection UUID;
- exact face URLs attached to presentation values without a query inside the factory.

- [ ] Add failing factory/view tests for zero, one, two, three, and four faces. Assert deterministic
  card order, exact detection-addressed URLs, no old text action, two crops plus `+ 2` for four
  faces, bounded page queries, unchanged preview/lightbox/download markup, and no vector/storage key
  in response content.
- [ ] Update view tests to prove GET, missing CSRF, disabled feature, draft event, cross-event photo,
  non-gallery photo, forged detection, and service unavailability fail with the existing sanitized
  statuses, while a valid detection redirects to the existing bearer result.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py::GalleryPhotoSearchViewTests"`.
  Expect failures for the missing face tuple and selected-detection route.
- [ ] Replace the nullable URL builder with an immutable face tuple in `GalleryPhoto` and its
  factory. Remove the obsolete unique-face presentation path rather than retaining both APIs.
- [ ] In `event_detail`, call Task 1 once for the current page when selfie search is enabled, then
  map each face to its selected-detection POST URL. Keep the existing gallery visibility rule and
  add no access-type decision.
- [ ] Change the route/view to require the detection UUID, re-resolve the event and gallery photo,
  and pass the detection ID to Task 1. Preserve POST-only, CSRF, body-free 404/503, and bearer
  redirect behavior.
- [ ] Re-run the Task 2 command and expect all selected tests to pass. Run
  `make test TESTS="src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_results.py"`
  and expect current gallery/result/media authorization regressions to pass.
- [ ] Self-review for forged IDs, N+1 queries, route ambiguity, feature-flag inconsistency, stale
  `similar_search_url` callers, and any widening of paid or hidden galleries.

### Task 3: Circular crops and anchored chooser interaction

**Deliverable:** The production event template implements the approved direct circle, overlapping
stack, `+ N`, anchored chooser, keyboard/focus behavior, and no-JavaScript fallback without
interfering with GLightbox.

**Files:**

- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/static/ui/event-gallery.js`
- Modify: `tests/js/event-gallery.test.js`
- Modify: `src/backend/picflow/tests/test_views.py`

**Specification:** Card footer controls; Anchored face chooser; Failure semantics.

**Depends on:** Task 2's `GalleryPhoto.faces` and exact face POST URLs.

**Produces:** Server-rendered face forms/disclosure plus an independently testable
`initializeFaceChoosers(root)` enhancement called by the existing gallery initializer.

- [ ] Add failing markup assertions for one direct circle; two/three overlapping summary crops;
  two crops plus exact remainder for more than three; all chooser tiles/forms; accessible names;
  `<details>`/`<summary>`/dialog labelling; and separation from `.gallery-card-link`.
- [ ] Extend Node tests with small DOM fakes for: opening one chooser closes another while focus
  stays on its trigger; Escape and outside click close and restore trigger focus; a face-form click
  is not treated as a lightbox trigger; and initialization remains safe when no chooser or GLightbox
  exists.
- [ ] Run
  `make test TESTS="src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_renders_gallery_face_controls"`
  and `npm run test:js`. Expect markup and chooser tests to fail before implementation.
- [ ] Render one face as a direct CSRF form button. Render multiple faces through card-local
  `<details>` with the approved visible stack and one form per full-grid tile. Use CSS custom
  properties only for normalized crop values; do not serialize JSON or vector data.
- [ ] Replace the text-action CSS with the 44-pixel target, circular crop viewport, overlap,
  focus ring, anchored dialog, three-column grid, three-row scroll bound, pointer, and viewport-safe
  mobile rules. Preserve footer/download alignment and respect reduced motion.
- [ ] Add the chooser enhancement to `event-gallery.js` without coupling it to the GLightbox
  return value. Preserve native disclosure semantics and the current lightbox focus restoration.
- [ ] Re-run the Task 3 commands and expect all Django markup and Node tests to pass, then run
  `npm run test:js` once more as the complete JavaScript regression gate.
- [ ] Self-review with keyboard-only and JavaScript-disabled reasoning: every face remains reachable,
  no nested interactive elements exist, and a chooser never obscures an unreachable close path.

### Task 4: Deterministic visual fixtures and product evidence

**Deliverable:** Desktop/mobile baselines show the real production template with one, multiple, and
overflow face controls plus an open chooser; documentation describes the delivered behavior
without claiming deployment evidence.

**Files:**

- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`
- Modify: `tests/visual/visual.spec.js-snapshots/*event-gallery*`
- Modify: `.agents/skills/update-visual-design/references/screen-inventory.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/architecture.md`
- Review: `docs/adr/0019-use-public-event-selfie-search.md`
- Modify: `docs/adr/0024-use-gallery-face-as-search-query.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/superpowers/specs/2026-08-05-gallery-face-selector-design.md`

**Specification:** Evidence and Existing Capability; Validation Contract; Product Job Update.

**Depends on:** Tasks 1–3 complete production markup and interaction.

**Produces:** Deterministic production-screen evidence and final ADR/architecture reconciliation.

- [ ] Extend the existing production event-gallery fixture with deterministic crop geometry and
  cards containing zero, one, two, and at least four faces. Add an open-chooser visual state through
  the test-only fixture/Playwright interaction, not fake production logic.
- [ ] Add Playwright assertions for no horizontal viewport overflow, anchored chooser containment,
  visible focus, exact stack/remainder counts, direct-face versus chooser behavior, and unchanged
  download/lightbox operation at desktop and 390-pixel mobile widths.
- [ ] Run `npm run test:visual:update`. Expect only the intentional event-gallery desktop/mobile
  snapshots and any explicitly added open-chooser snapshots to change.
- [ ] Inspect every changed PNG at original resolution. Reject clipping, distorted crops, hidden
  controls, unreadable overlap, download displacement, or a chooser outside the card/viewport;
  adjust production CSS and repeat update/inspection until correct.
- [ ] Run `npm run test:visual` and expect all visual tests to pass without diff artifacts.
- [ ] Update the screen inventory with the new production states. Broaden `PJ-008` and implemented
  architecture only with actual local test evidence; keep deployment/customer status unchanged.
- [ ] Reconcile the final behavior with expanded ADR 0024 and ADR 0019. Stop rather than weakening
  event isolation, query-vector privacy, bearer semantics, or media authorization.
- [ ] Run `git diff --check` and an exact local-link scan for changed documentation; expect no
  whitespace errors or missing targets.

### Final Task: Local staging-data smoke, independent review, and one commit

**Deliverable:** One approved implementation diff with fresh automated and real-data visual
evidence, committed once by the root controller and ready for the existing PR.

- [ ] Inspect the agent tree and interrupt any implementer/reviewer that delegated contrary to
  `AGENTS.md`. Prepare a complete unstaged working-tree diff including new files.
- [ ] Dispatch one independent reviewer for the complete diff. Classify findings as `blocking` or
  `future`; return blocking fixes to the owning implementer and re-review with the same reviewer.
- [ ] Run the focused Python suite:
  `make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py"`.
  Expect zero failures and record exact test/subtest counts.
- [ ] Run `npm run test:js` and `npm run test:visual`; expect zero failures.
- [ ] Run `make check`; expect Ruff format/lint, mypy, full pytest with coverage, Django system
  check, and migration-drift check to exit zero with `No changes detected`.
- [ ] Start the branch against the restored staging database with Object Storage credentials
  provided only at process launch. On the Cyclingrace gallery, verify a one-face circle submits
  directly, a multi-face stack opens all crops, a card with more than three faces shows two crops
  plus the correct remainder, selecting different faces creates different ready bearer results,
  and images still resolve successfully. Do not print credentials or signed URLs.
- [ ] Inspect desktop and mobile layouts in the real local gallery. Record screenshots as review
  evidence outside Git unless they are intentional deterministic baselines.
- [ ] Run `git diff --check`, `git status --short`, and inspect the exact task diff. Confirm no
  `.env`, secret, signed URL, vector, storage key, migration, dependency, unrelated change, or
  obsolete unique-face compatibility path entered the patch.
- [ ] After reviewer approval and fresh verification, the root controller stages the exact task
  files and creates one implementation commit. Push it to the existing branch/PR and report CI as
  pending until remotely verified.

## Verification

Required successful commands are the focused Python suite, `npm run test:js`,
`npm run test:visual`, `make check`, and `git diff --check`. Visual snapshot update mode is not a
verification result; every updated image must be inspected before the non-update run.

## Operational Impact and Rollout

- **Configuration:** none; presentation and POST remain gated by `SELFIE_SEARCH_ENABLED`.
- **Database:** no migration or backfill. Successful selections create the existing ready search
  and result rows with source detection evidence; no crop or vector state is added.
- **Storage/worker:** no new traffic contract or worker work. Repeated crop elements reuse the
  existing authorized preview application URL.
- **Deployment:** normal immutable application image through the existing PR/CI/staging flow.
- **Smoke:** use a published event with one-face, multi-face, and four-plus-face cards; validate exact
  selected-person results, event-only membership, source inclusion, and unchanged media/download.
- **Monitoring:** existing POST status/duration and application errors. Revisit synchronous ranking
  only under ADR 0019/0024's measured latency trigger.
- **Compatibility:** none. Existing bearer results remain readable; the old unique-face-only
  service/presentation interface is removed in the same unmerged delivery.

## Rollback

Revert the face presentation, detection-addressed route, chooser markup/CSS/JS, selected-detection
service change, and expanded ADR text together. Existing ready snapshots remain readable under ADR
0019 and need no cleanup because they contain no query vector or temporary/crop object. No database,
storage, worker, IAM, or lifecycle rollback is required.

## Open Questions

None.
