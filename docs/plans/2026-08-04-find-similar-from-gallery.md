# Find Similar Photos from a Gallery Photo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Repository `AGENTS.md` overrides generic per-task commit
> guidance: implementation and review fixes remain unstaged until one final root-controller commit.

- Date: 2026-08-04
- Status: Ready
- Owner: project maintainer
- Related specification:
  [Find Similar Photos from a Gallery Photo Design](../superpowers/specs/2026-08-04-find-similar-from-gallery-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), accepted constraints, Search,
  and security/privacy boundaries
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md) and
  [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md)
- ADR impact: resolved by accepted ADR 0024; implementation must preserve ADR 0019's event,
  immutable-result, bearer, and media-authorization boundaries.

## Goal

Implement the approved specification's critical path: start an event-scoped probable-match search
from an existing gallery photo containing exactly one current compatible accepted face, without a
screenshot, upload, temporary image, or worker job.

## Global constraints

- Search only within the source photo's current published event.
- Show the action only for exactly one current compatible accepted `kept` face.
- Revalidate all eligibility on POST; rendered HTML is not authority.
- Store no query vector and create no temporary media, `SelfieSearchJob`, or worker attempt.
- Publish one atomic, immediately ready, immutable bearer snapshot which contains the source photo.
- Reuse current result pagination, feedback, and media authorization without an `access_type`
  branch or opening a hidden gallery.
- Keep the existing SFace model, 128 dimensions, cosine threshold `0.363`, and exact deterministic
  ranking semantics.
- Use the existing `SELFIE_SEARCH_ENABLED` flag for both presentation and submission; add no new
  setting, dependency, service, migration, or compatibility path.

## Scope

Implement the approved specification without scope changes. Multi-face selection, cross-event
search, paid-gallery activation, ranking tuning, deduplication, and new observability infrastructure
remain excluded.

## Acceptance criteria

Use the specification's [Acceptance Criteria](../superpowers/specs/2026-08-04-find-similar-from-gallery-design.md#acceptance-criteria).
Delivery additionally requires the focused verification commands below to pass and a final review
to confirm that no media authorization or existing selfie-upload path was widened.

## File structure and interfaces

- `src/backend/selfie_search/services/submission.py` remains the single submission/cohort boundary.
  It will produce `gallery_search_eligible_photo_ids(...)`,
  `submit_gallery_photo_search(...)`, `GallerySearchUnavailable`, and `GallerySearchFailed` while
  preserving `submit_selfie_search(...)` and `compatible_search_candidates(...)`.
- `src/backend/picflow/gallery.py` remains presentation-only. `GalleryPhoto` gains nullable
  `similar_search_url`; `GalleryPhotoFactory.from_photo(...)` accepts a nullable URL builder and
  performs no face-data lookup.
- `src/backend/config/views.py` determines action eligibility for the already bounded gallery page
  and passes URLs into the presentation factory.
- `src/backend/selfie_search/views.py` and `urls.py` own the CSRF-protected POST endpoint and redirect
  to the existing result route.
- `src/backend/templates/catalog/event_detail.html` and
  `src/backend/static/ui/catalog.css` own the small secondary card action. Result templates and
  JavaScript remain unchanged.
- No model or migration file changes are expected. Query-source evidence stays in the bounded
  `SelfieSearch.configuration` JSON, and the direct ready row uses existing nullable/blank state.

## Implementation

### Task 1: Atomic direct search from one accepted gallery face

**Deliverable:** A service can validate one eligible source embedding, rank the current event cohort,
and atomically create an immediately ready result containing the source photo without storage or a
worker job.

**Files:**

- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/selfie_search/tests/test_submission.py`

- **Specification:** Submission and ranking; Result and authorization boundaries; Failure semantics.
- **Depends on:** Accepted ADR 0024; existing `rank_embeddings(...)`, `CreatedSearch`, compatible
  generation configuration, and `SelfieSearchResult` schema.
- **Produces:**
  - `GallerySearchUnavailable(LookupError)` for stale, absent, ambiguous, or incompatible source
    evidence;
  - `GallerySearchFailed(RuntimeError)` for sanitized ranking/invariant/database failures after
    transaction rollback;
  - `gallery_search_eligible_photo_ids(*, event: Event, photos: Iterable[Photo]) -> frozenset[str]`;
  - `submit_gallery_photo_search(*, event: Event, photo: Photo,
    now: datetime | None = None) -> CreatedSearch`.

- [ ] Add `GalleryPhotoSubmissionTests` which build accepted processing state and prove that zero,
  two, rejected, stale-generation, malformed-vector, and cross-event source faces are unavailable,
  while exactly one current compatible face is eligible.
- [ ] Run
  `sh scripts/run-in-test-env.sh .venv/bin/pytest -q src/backend/selfie_search/tests/test_submission.py::GalleryPhotoSubmissionTests`
  and confirm collection or assertions fail because the new service contract does not exist.
- [ ] Refactor the existing compatible-embedding queryset only as needed so eligibility counts,
  source resolution, and cohort loading share the identical accepted-state and model-generation
  predicate. Keep bounded field-only iteration and the existing selfie submission behavior.
- [ ] Add a gallery query configuration which records the source kind and source photo identity,
  plus the same embedding model, dimensions, threshold, and accepted gallery generations used by
  ranking. Do not place the vector, filenames, object keys, or public token in configuration.
- [ ] Implement the direct submission inside one `transaction.atomic()` block: re-resolve the
  published event and current gallery-eligible source photo; require exactly one source candidate;
  create the token digest and ready search; load and rank only that event's compatible cohort;
  require source-photo membership; bulk-create ranked result rows; set eligible/matched counts and
  terminal/cleanup timestamps; leave temporary key empty and create no job.
- [ ] Convert expected unavailable inputs to `GallerySearchUnavailable`; convert `RankingError`,
  missing-source invariant, and database persistence failures to `GallerySearchFailed` only after
  rollback. Do not catch or sanitize unrelated programming errors.
- [ ] Extend the tests to assert ascending distance/photo-ID ordering, one best face per matched
  photo, event isolation, source membership, immutable ready state, empty temporary key, cleanup
  completion, absent `SelfieSearchJob`/attempt/candidate rows, and no stored query vector.
- [ ] Inject ranking and bulk-persistence failures and assert `GallerySearchFailed` plus zero search
  and result rows from the attempted transaction.
- [ ] Re-run the Task 1 command and expect all `GalleryPhotoSubmissionTests` to pass.
- [ ] Run
  `sh scripts/run-in-test-env.sh .venv/bin/pytest -q src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_jobs.py`
  and expect the direct-search tests and all existing upload/cohort/ranking/job regressions to pass.
- [ ] Self-review the unstaged Task 1 diff for vector leakage, cross-event joins, query-count
  inflation, broad exception handling, and accidental worker/storage use. Leave changes unstaged for
  the root review workflow.

### Task 2: Existing gallery card action and protected POST endpoint

**Deliverable:** Eligible cards display `Найти похожие фото`; a protected POST creates the direct
search and redirects to the existing bearer result, while forged or stale requests fail closed.

**Files:**

- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/selfie_search/urls.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`

- **Specification:** Gallery presentation; Submission and ranking; Failure semantics.
- **Depends on:** Task 1's four public service interfaces.
- **Produces:** `GalleryPhoto.similar_search_url: str | None`, a nullable factory builder, URL name
  `selfie_search:submit_gallery_photo`, and view
  `submit_gallery_photo(request, event_slug: str, photo_id: str)`.

- [ ] Add presentation-contract tests proving the frozen `GalleryPhoto` carries a supplied search
  URL and defaults to `None` for current result-card and ordinary factory callers.
- [ ] Add event-page tests with current accepted fixtures for zero, one, and two compatible faces.
  Assert that only the one-face card receives a POST form with CSRF token and exact text
  `Найти похожие фото`, while existing lightbox and download actions remain unchanged.
- [ ] Add `GalleryPhotoSearchViewTests` for POST-only behavior, disabled feature flag, unpublished
  event, cross-event or gallery-ineligible photo, stale/ambiguous source evidence, successful
  redirect to the existing bearer result, and sanitized `503` on `GallerySearchFailed`.
- [ ] Run
  `sh scripts/run-in-test-env.sh .venv/bin/pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py::PageTests::test_event_detail_renders_similar_search_only_for_one_face src/backend/selfie_search/tests/test_views.py::GalleryPhotoSearchViewTests`
  and confirm the new field, markup, route, or view assertions fail before implementation.
- [ ] Extend `GalleryPhoto` and its factory with the nullable URL value/builder. Keep all face
  eligibility outside `picflow.gallery` and keep ready-result card callers unchanged.
- [ ] In `event_detail`, only when `SELFIE_SEARCH_ENABLED` is true, evaluate Task 1 eligibility for
  the already paginated gallery photos and build the POST URL for eligible cards. Add no
  `access_type` condition beyond the existing gallery surface and no per-card database query.
- [ ] Add the event/photo-addressed URL and `@require_POST` view. Resolve the published event and
  current `gallery_photo_queryset` member, delegate unique-face revalidation to Task 1, return the
  existing sanitized not-found response for `GallerySearchUnavailable`, return a body-free `503`
  for `GallerySearchFailed`, and redirect successful submissions to the existing result URL.
- [ ] Render a separate CSRF-protected form in `.gallery-card-actions` only when
  `similar_search_url` is non-null. Style it as a compact secondary text action with a 44px minimum
  target; retain the separate right-aligned download icon and do not add JavaScript or lightbox
  description content.
- [ ] Re-run the Task 2 command and expect all selected presentation, event-page, and endpoint tests
  to pass.
- [ ] Run
  `sh scripts/run-in-test-env.sh .venv/bin/pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_results.py`
  and expect all current gallery/result/media view regressions to pass.
- [ ] Self-review the unstaged Task 2 diff for CSRF, method enforcement, feature-flag consistency,
  N+1 queries, forged photo IDs, accidental paid-gallery activation, token leakage, action nesting,
  keyboard accessibility, and result-card regressions.

### Task 3: Product evidence and implemented architecture reconciliation

**Deliverable:** Repository documentation describes the implemented gallery-photo query path without
claiming deployment or validation that did not occur.

**Files:**

- Modify: `docs/product-jobs.md`
- Modify: `docs/architecture.md`
- Review only: `docs/adr/0019-use-public-event-selfie-search.md`
- Review only: `docs/adr/0024-use-gallery-face-as-search-query.md`
- Review only: `docs/superpowers/specs/2026-08-04-find-similar-from-gallery-design.md`

- **Specification:** Product Job Update; Acceptance Criteria.
- **Depends on:** Passing Tasks 1 and 2 behavior.
- **Produces:** Accurate `PJ-008` wording/evidence and architecture facts linked to accepted ADR
  0024.

- [ ] Broaden the `PJ-008` job wording from only an uploaded reference image to either an
  appropriate selfie or an eligible one-person gallery photo within the selected event.
- [ ] Keep `PJ-008` `In progress` unless deployment evidence exists. Update its detail evidence and
  last-updated date with the exact focused local test results; append no status-history row when the
  status does not change.
- [ ] Update the architecture's implemented selfie-search summary and Search flow to distinguish
  the worker-backed selfie source from the immediately ready gallery-embedding source. Preserve
  event isolation, non-persisted query vectors, existing bearer/media rules, and the default-disabled
  feature fact.
- [ ] Compare the completed behavior with ADRs 0019 and 0024 and the approved specification. Stop
  for a new decision instead of editing an accepted ADR if implementation differs.
- [ ] Run `git diff --check` and expect no whitespace errors; run an exact link/path scan for every
  changed documentation reference and expect all local targets to exist.
- [ ] Self-review documentation for false production/staging claims, duplicated product jobs,
  cross-event implications, or a new free-versus-paid policy.

### Final task: Verification, independent review, and one implementation commit

**Deliverable:** One approved implementation diff with fresh verification evidence and one final
root-controller commit, ready for publication when requested.

- **Specification:** Entire acceptance contract.
- **Depends on:** Tasks 1–3 complete and unstaged.
- **Produces:** Reviewable working-tree diff, final evidence, architecture/ADR reconciliation, and
  one consolidated implementation commit.

- [ ] Prepare a complete working-tree diff including untracked task files without staging it, and
  classify review findings as `blocking` or `future` under `AGENTS.md`.
- [ ] Run an independent reviewer against the complete diff. Return blocking fixes to the same
  implementer and re-review them with the same reviewer when subagents are used; interrupt any
  unplanned nested agent before using its result.
- [ ] Run the full focused suite:
  `sh scripts/run-in-test-env.sh .venv/bin/pytest -q src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_ranking.py src/backend/selfie_search/tests/test_jobs.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_views.py`.
  Expect zero failures and record the exact test/subtest counts.
- [ ] Run `.venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, `.venv/bin/mypy`,
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check`, and
  `sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run`.
  Expect every command to exit zero and migration drift to report no changes.
- [ ] Run `git diff --check` and inspect `git status --short` plus the exact task diff. Confirm no
  `.env`, `.venv`, storage key, token, unrelated user file, migration, or unapproved scope entered
  the patch.
- [ ] Reconcile the final diff explicitly as conforming to accepted ADR 0024 and preserving ADR
  0019; record this outcome in the eventual pull request.
- [ ] Only after approval and fresh verification, stage the exact implementation/docs files and
  create one root-controller implementation commit. Do not create intermediate implementation or
  review-fix commits.

## Verification

Baseline before implementation in the isolated worktree:

```text
115 passed, 59 warnings, 64 subtests passed
```

Command used:

```bash
sh scripts/run-in-test-env.sh .venv/bin/pytest -q \
  src/backend/picflow/tests/test_gallery.py \
  src/backend/picflow/tests/test_views.py \
  src/backend/selfie_search/tests/test_submission.py \
  src/backend/selfie_search/tests/test_ranking.py \
  src/backend/selfie_search/tests/test_results.py \
  src/backend/selfie_search/tests/test_views.py
```

Implementation completion requires the Final task's focused suite, Ruff format/lint, configured
mypy, Django system check, migration-drift check, and diff check. The full repository pytest suite
is not required by this critical-path plan; CI remains the repository-wide regression gate if the
branch is published.

## Operational impact and rollout

- **Configuration:** no new setting; the action and POST endpoint use `SELFIE_SEARCH_ENABLED`.
- **Database:** no migration or backfill. Successful clicks add ordinary ready `SelfieSearch` and
  `SelfieSearchResult` rows but no job, attempt, candidate, temporary-object, or query-vector state.
- **Storage/worker:** no new Object Storage or worker traffic.
- **Deployment order:** normal application image deployment only. No lifecycle, IAM, model, worker,
  or database ordering step.
- **Smoke:** on an existing published event whose rendered gallery contains a photo with exactly
  one accepted face, confirm the action is present; click it; confirm an immediate ready bearer page,
  the source photo in results, event-only membership, and unchanged media/download behavior. Also
  confirm a zero/multi-face card lacks the action.
- **Monitoring:** inspect the POST response status and request duration plus application errors.
  Revisit synchronous ranking only if measured latency violates the current request budget.
- **Compatibility:** none. Existing selfie uploads and existing bearer results retain their current
  path and schema; no compatibility layer or fallback is added.

## Rollback

Revert the gallery action, route/view, presentation value, and direct submission service. Existing
ready snapshots created through the feature remain readable under ADR 0019's bearer semantics and
need no cleanup because they contain no temporary object or query vector. No database, storage,
worker, IAM, or lifecycle rollback is required.

## Open questions

None.
