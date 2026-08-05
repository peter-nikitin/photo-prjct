# Gallery Search Immediate Result Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Repository `AGENTS.md` overrides generic intermediate-commit guidance: implementers and reviewers
> leave changes unstaged, and the root controller creates the final commit after approval.

**Goal:** Open a gallery-face search in a new tab immediately, show the existing processing result
page, and replace it with results automatically when exact event-scoped ranking finishes.

**Architecture:** Gallery submission validates the selected stored detection and creates a queued
bearer search without ranking in the submission request. The queued result page starts one
idempotent, CSRF-protected Django ranking request and continues using the existing status polling;
the ranking request locks the search, revalidates the stored detection, publishes immutable results
atomically, and stores no query vector or temporary media.

**Tech Stack:** Django 6, PostgreSQL, server-rendered templates, dependency-free JavaScript, Node
test runner.

## Global Constraints

- Keep ranking inside the source event with SFace, 128 dimensions, and threshold `0.363`.
- Never persist or expose the selected embedding, processing payload, storage key, or signed media URL.
- Create no temporary media, crop object, inference work, external selfie-worker job, or migration.
- Submission must return the existing bearer result redirect before ranking starts.
- Only a queued gallery-origin search may invoke the new processing endpoint.
- Processing must be idempotent and publish the source photo and ranked results atomically.
- Existing selfie upload, cleanup, polling, bearer authorization, result media, and feedback behavior
  must remain unchanged.
- Face-selection forms open the bearer result in a new tab; gallery scroll and chooser behavior stay
  unchanged.

---

### Task 1: Queued gallery ranking and immediate result-tab interaction

**Files:**

- Modify: `src/backend/selfie_search/services/submission.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/urls.py`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `src/backend/selfie_search/tests/test_submission.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `tests/js/selfie-search.test.js`
- Modify: `docs/adr/0024-use-gallery-face-as-search-query.md`
- Modify: `docs/superpowers/specs/2026-08-05-gallery-face-selector-design.md`

**Interfaces:**

- `submit_gallery_photo_search(...) -> CreatedSearch` validates the exact source and persists a
  `queued` gallery-origin search without results or `SelfieSearchJob`.
- `process_gallery_photo_search(*, search, now=None) -> SelfieSearch` locks and idempotently ranks
  only a queued `gallery_photo_query` search.
- `selfie_search:process_gallery_search` is a CSRF-protected POST nested under the bearer result URL.
- The result template exposes a small process form only for queued gallery-origin searches; the
  existing JavaScript submits it once and leaves status polling responsible for reload.

- [ ] Write failing service tests proving submission returns queued without ranking/results/job and
  processing publishes the exact source atomically, while repeated processing is a no-op and an
  invalid/stale source fails closed.
- [ ] Run the focused submission tests and verify they fail because submission is still synchronous
  and the processing service does not exist.
- [ ] Implement queued creation and locked idempotent processing by refactoring the existing exact
  source resolution and ranking code without persisting the query vector.
- [ ] Write failing view/template tests proving the face forms use `target="_blank"`, the redirect is
  immediate, only a queued gallery result renders the CSRF process form, and invalid/non-gallery
  bearer searches cannot invoke processing.
- [ ] Run the focused view tests and verify the expected failures.
- [ ] Add the protected process route/view and conditional result-page form.
- [ ] Write a failing JavaScript test proving the process form is submitted once on initialization
  while existing status polling remains active and handles success/failure without navigating away.
- [ ] Run `npm run test:js` and verify the new test fails before implementation.
- [ ] Extend the existing result controller to submit the process form once and preserve current
  polling/reload behavior.
- [ ] Update ADR 0024 and the approved selector specification to replace synchronous ready
  submission with the queued browser-triggered lifecycle and its new-tab behavior.
- [ ] Run focused Django and JavaScript tests, self-review the unstaged diff for CSRF, bearer,
  cross-event, idempotency, query-vector retention, and selfie-upload regressions.
