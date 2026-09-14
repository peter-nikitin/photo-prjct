# Direct Gallery Preview Links Implementation Plan

- Date: 2026-09-14
- Status: Approved for implementation
- Owner: project maintainer
- Related specification:
  [Direct Gallery Preview Links Design](../superpowers/specs/2026-09-14-direct-gallery-preview-links-design.md)
- Related architecture: [Architecture](../architecture.md), public gallery and media delivery
- Related ADRs:
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md), and
  [ADR 0036](../adr/0036-issue-direct-gallery-small-preview-capabilities.md)
- ADR impact: accepted ADR 0036 narrowly supersedes ADR 0020 for normal-gallery small
  presentation derivatives

Use `$execute-implementation-plan` for implementation and review after the ADR gate is resolved.

## Goal

Implement the approved specification's outcome: one normal-gallery HTML request issues direct
six-hour Object Storage links for the accepted small derivatives in its bounded grid, and those
image loads do not return to Django, PostgreSQL, or Object Storage `HEAD`.

## Scope

The plan implements the specification without expanding its scope. Private event management,
selfie-result media, large media, originals, downloads, archives, purchased media, and legacy
original presentation remain unchanged.

The six-hour lifetime is a code-owned gallery policy. It is not a new environment variable or
deployment parameter.

## Critical path

```text
eligible 100-photo gallery page
  -> resolve accepted small derivative keys in one bounded query
  -> locally sign exact-object GET URLs for 6 hours, without HEAD
  -> render those URLs into preview-small image src attributes
  -> browser loads preview bytes directly from Object Storage
```

Only four main gallery images start eagerly. Every face-crop image and remaining main image stays
lazy. Clicking a card still uses the current Django-authorized large/original route.

## Acceptance criteria

- One accepted free `preview-small-v1` card renders a direct Object Storage small URL.
- One accepted paid `preview-watermarked-v1` card renders only its watermarked direct small URL.
- The same cards' large and permitted download URLs remain Django application routes.
- A legacy card whose small presentation is the original remains on the Django application route.
- Direct preview signing uses exactly 21,600 seconds and performs no Object Storage inspection.
- Resolving one or 100 accepted page previews uses one bounded derivative/evidence query, not one
  query per photo.
- Every face-crop image is lazy and only the first four main cards are eager/high priority.
- A signing failure fails the page with a sanitized `503`; it does not fall back to a more powerful
  object or leak a signed URL.
- A production 100-card load sends derivative-backed small image requests directly to Object
  Storage and produces no corresponding Django `preview-small` requests.
- Public event HTML, an eager preview, a below-fold preview, lightbox/original navigation, and
  `/health/` remain successful after deployment.

The existing gallery eligibility tests already protect hidden/unpublished/stale/foreign filtering.
The implementation must reuse that eligibility rather than add a new matrix of duplicate corner
tests.

## Worker/state/artifact release safeguards

Not applicable. This plan changes neither worker contracts nor durable processing state, derivative
identity, derivative bytes, Object Storage prefixes, or processing enrollment. It changes only how
an already accepted immutable derivative is delivered after the normal gallery authorizes it.

## Implementation

### Task 1: Add the accepted-preview signing and page-grant seam

**Files:**

- Modify: `src/backend/ingestion/storage.py`
- Modify: `src/backend/ingestion/tests/test_storage.py`
- Create: `src/backend/picflow/gallery_preview_grants.py`
- Create: `src/backend/picflow/tests/test_gallery_preview_grants.py`

- **Specification:** `Selected Design`, `Authorization snapshot`, `Preview grant issuer module`,
  and `Capability Lifetime and Failure Semantics`.
- **Depends on:** ADR 0036 (accepted).
- **Produces:**
  `issue_gallery_preview_urls(*, photos: Collection[Photo], signer: AcceptedPreviewSigner) ->
  dict[str, str]` and
  `PrivateUploadStorage.sign_accepted_preview(*, key: str, expires_in: int) -> str`.

- [ ] Add a failing storage test proving that an accepted preview key is signed for 21,600 seconds
  with no `head_object` or `get_object` call, plus one security assertion that an original key is
  rejected before the client is called.
- [ ] Add a failing issuer test containing one free derivative, one paid watermarked derivative,
  and one legacy card. Assert that only the two accepted derivatives are signed and that their
  exact policy-selected keys are used.
- [ ] Add one query-bound test proving the issuer performs the same single derivative/evidence
  query for one and 100 photos.
- [ ] Run
  `make test TESTS="src/backend/ingestion/tests/test_storage.py src/backend/picflow/tests/test_gallery_preview_grants.py -q"`
  and confirm the new assertions fail for the missing interfaces.
- [ ] Implement local-only preview presigning in `PrivateUploadStorage`: accept only managed final
  `preview-small-v1` and `preview-watermarked-v1` keys, validate a positive bounded expiry, call
  `generate_presigned_url`, and leave existing verify-then-sign behavior untouched.
- [ ] Implement the focused issuer with code-owned `GALLERY_PREVIEW_URL_TTL_SECONDS = 21_600`.
  Resolve expected variants from the explicit gallery media policy and query only the current page's
  `PhotoDerivative`, accepted successful `ProcessingAttempt`, and matching succeeded
  `PhotoProcessingState` evidence. Missing, duplicate, or mismatched evidence fails closed.
- [ ] Rerun the exact Task 1 command and expect all tests to pass.
- [ ] Run the exact changed-file hook and record final GREEN evidence after the last change:

  ```bash
  .venv/bin/pre-commit run --files \
    src/backend/ingestion/storage.py \
    src/backend/ingestion/tests/test_storage.py \
    src/backend/picflow/gallery_preview_grants.py \
    src/backend/picflow/tests/test_gallery_preview_grants.py
  ```

Testing intentionally stops at the selected source, no-HEAD contract, original-key rejection, and
query bound. It does not enumerate malformed key strings, every SDK exception, or every impossible
duplicate state beyond existing storage/model invariants.

### Task 2: Put direct links into the normal gallery and restore effective lazy loading

**Files:**

- Modify: `src/backend/config/views.py`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/picflow/tests/test_views.py`
- Regression: `src/backend/processing/tests/test_paid_watermarked_preview_flow.py`

- **Specification:** `Rendered presentation`, `Security and Privacy Boundaries`, and `Automated
  critical path`.
- **Depends on:** Task 1 interfaces.
- **Produces:** derivative-backed `GalleryPhoto.preview_media_small.url` values that point directly
  to Object Storage only on normal-gallery pages.

- [ ] Add one failing normal-gallery view test proving the complete free critical path: accepted
  small derivative becomes a direct URL while large and download remain application routes.
- [ ] Add one paid assertion proving only the accepted watermarked derivative is signed, and one
  legacy assertion proving an original-backed small image stays on the application route.
- [ ] Add one realistic failure assertion: signing failure returns sanitized `503` with no storage
  key or signature in the response.
- [ ] Strengthen the existing markup/loading test so every face-crop `<img>` is lazy while main
  cards 1–4 remain eager/high priority.
- [ ] Run the named new tests and existing loading-policy test individually; confirm they fail on
  the current implementation.
- [ ] In `event_detail`, materialize the bounded page, construct one storage adapter only when the
  page has derivative-backed cards, issue the direct small URL mapping once, and inject it through
  the existing `GalleryPhotoFactory.from_photo(..., media_url_builder=...)` seam. Preserve current
  reverse-generated URLs for every other variant and context.
- [ ] Add `loading="lazy"` to every face-crop image. Do not add JavaScript, change pagination, or
  change the first-four main-image policy.
- [ ] Run:

  ```bash
  make test TESTS="src/backend/picflow/tests/test_views.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/ingestion/tests/test_storage.py src/backend/processing/tests/test_paid_watermarked_preview_flow.py -q"
  ```

  Expected: the direct free/paid/legacy/failure/loading critical path and existing paid-watermark
  flow pass.
- [ ] Run the exact changed-file hook and record final GREEN evidence after the last change:

  ```bash
  .venv/bin/pre-commit run --files \
    src/backend/config/views.py \
    src/backend/picflow/tests/test_views.py
  ```

Do not add duplicate tests for selfie results, commerce presentation, every gallery filter, every
page number, or current hidden/unpublished eligibility unless a focused regression fails and proves
the changed seam reaches that path.

### Task 3: Reconcile architecture, run required gates, and deliver

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/superpowers/specs/2026-09-14-direct-gallery-preview-links-design.md`
- Modify after acceptance: `docs/adr/0036-issue-direct-gallery-small-preview-capabilities.md`
- Modify after acceptance: `docs/adr/README.md`
- Review: the complete diff from `origin/main`

- **Specification:** `Verification`, `Rollout and Rollback`.
- **Depends on:** reviewed Tasks 1–2 and ADR 0036 (accepted).
- **Produces:** one review-approved package, one consolidated implementation commit, green CI,
  deployed exact image, and bounded production evidence.

- [ ] Update architecture with HTML-time small-preview authorization and the six-hour snapshot
  lifetime. Update PJ-005 and the applicable engineering job with repository evidence only; do not
  claim CI or production evidence before it exists.
- [ ] Reconcile the delivered behavior against accepted ADR 0036 before push. The result must be
  exact conformance; stop rather than silently changing its media or revocation scope.
- [ ] Obtain independent review through `$execute-implementation-plan`. Fix only blocking findings
  affecting the accepted critical path, an existing production path, security/privacy, realistic
  failure, or deployment. Record useful non-blocking corner findings separately only when they have
  a concrete revisit trigger.
- [ ] Run the selector with every changed path and record its reasons and package fingerprint.
  With the focused file set above, core and visual are expected; selector output is authoritative.
- [ ] Run final-package verification after the last change:

  ```bash
  make check
  npm run test:visual
  ```

  Expected: full core checks pass and existing visual baselines remain green without snapshot
  updates. Run another expensive layer only if the selector requires it.
- [ ] Create the repository-required single consolidated implementation commit, push the branch,
  open the PR without signed URLs or keys, and wait for all required CI checks.
- [ ] Merge after green CI and wait for automatic deployment of the exact merge SHA.
- [ ] Perform one bounded browser load of the representative published 100-card event. Aggregate,
  without raw URLs, keys, IDs, signatures, IPs, or log lines:
  event HTML status/time/size; 100 cards; four eager main images; zero eager face crops; direct host
  for one eager and one below-fold preview; unchanged lightbox route; Django `preview-small` count
  caused by the load; fresh 5xx distribution; and `/health/` status.
- [ ] Stop and roll back to the previous image if HTML, direct previews, lightbox, health, or 5xx
  behavior regresses. Do not take a database dump, hide/unpublish a live photo, submit a selfie,
  mutate queues, or exercise payment during this validation.
- [ ] After successful production verification, add dated PR/CI/deploy/live evidence without
  claiming long-term customer outcome.

## Verification

Focused implementation evidence:

```bash
make test TESTS="src/backend/ingestion/tests/test_storage.py src/backend/picflow/tests/test_gallery_preview_grants.py -q"
make test TESTS="src/backend/picflow/tests/test_views.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/ingestion/tests/test_storage.py src/backend/processing/tests/test_paid_watermarked_preview_flow.py -q"
```

Final required evidence:

```bash
.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD
.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main
make check
npm run test:visual
git diff --check
```

The selector, not this plan, decides whether another expensive suite is required. No coverage
percentage target or new corner-case matrix is part of acceptance.

## Operational impact and rollout

No new environment variable, secret, service, schema, migration, worker image, queue, storage
prefix, or feature flag is introduced. The normal web image changes the small-preview URL embedded
in normal-gallery HTML. Deployment follows the existing immutable-image pipeline.

Monitor event HTML time, aggregate Django `preview-small` volume, direct preview success, health,
and 499/502/504 counts. The expected primary signal is that derivative-backed gallery preview loads
disappear from Django/nginx application traffic.

## Rollback

Redeploy the previous known-good image. New page loads return to per-preview Django authorization.
Already issued direct URLs remain usable until their six-hour expiry; rollback cannot revoke them.
There is no data or schema rollback.

## Open questions

None.
