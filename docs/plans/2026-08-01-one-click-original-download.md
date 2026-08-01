# One-click Original Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-08-01
- Status: Ready for implementation
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-01-one-click-original-download-design.md`](../superpowers/specs/2026-08-01-one-click-original-download-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), accepted media and download
  boundaries
- Related ADRs:
  [ADR 0019](../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md), and
  [ADR 0021](../adr/0021-allow-original-download-for-authorized-photos.md)
- ADR impact: Resolved — implementation must conform to accepted ADR 0021 while preserving ADR
  0019 result membership and ADR 0020 direct-delivery transport.

## Goal

Implement the approved specification's one-click original download from each rendered gallery or
ready-result card and from GLightbox.

## Global constraints

- Add no new free-versus-paid decision and do not implement commerce behavior.
- Reuse the current normal-gallery and saved-ready-result authorization lookups.
- Render only deterministic application URLs; never render or persist a signed URL or object key.
- Keep Django out of the media data path by redirecting to one short-lived exact-object signed GET.
- Remove the visible photo-ID caption; keep the photo ID in existing accessible image text.
- Use an icon-only, subdued card action with a minimum 44 by 44 CSS-pixel target.
- Use GLightbox's built-in bottom description area; do not create a custom toolbar or fork.
- Add only tests that protect the changed critical path and realistic regressions.
- Under `AGENTS.md`, implementer and reviewer subagents leave changes unstaged and do not alter Git
  history. The root controller creates one final implementation commit only after approval and
  final verification.

## Scope

Implement the approved specification without scope changes.

## Acceptance criteria

Use the specification's [Acceptance Criteria](../superpowers/specs/2026-08-01-one-click-original-download-design.md#acceptance-criteria).
Delivery additionally requires focused backend, template, JavaScript, and affected visual checks to
pass with no migration drift.

## File map

- `src/backend/ingestion/storage.py`: verify an exact final object and add an optional sanitized
  attachment response disposition to its signed GET.
- `src/backend/picflow/gallery.py`: own download URL presentation and original-download resolution.
- `src/backend/config/urls.py`, `src/backend/config/views.py`: expose the normal-gallery download
  route through existing gallery eligibility.
- `src/backend/selfie_search/urls.py`, `src/backend/selfie_search/views.py`: expose the bearer-result
  download route through existing saved-result membership.
- `src/backend/templates/catalog/event_detail.html` and
  `src/backend/selfie_search/templates/selfie_search/result.html`: remove captions and render card
  and GLightbox download actions.
- `src/backend/static/ui/icons.svg`, `src/backend/static/ui/catalog.css`, and
  `src/backend/static/ui/event-gallery.js`: provide the packaged icon, subdued accessible layout,
  and explicit built-in bottom description position.
- Existing focused tests under `src/backend/ingestion/tests/`, `src/backend/picflow/tests/`,
  `src/backend/selfie_search/tests/`, and `tests/js/`: protect only the new interfaces and reused
  authorization boundaries.
- Existing affected snapshots under `tests/visual/visual.spec.js-snapshots/`: record the approved
  removal of card IDs and addition of the subdued action without adding scenarios.
- `docs/future-work/2026-08-01-paid-photo-cart-action.md`: record the accepted commerce trigger.
- `docs/architecture.md`: change the accepted action from unimplemented to implemented only after
  behavior verification.

## Implementation

### Task 1: Exact-original attachment signing

**Files:**

- Modify: `src/backend/ingestion/storage.py`
- Modify: `src/backend/ingestion/tests/test_storage.py`
- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`

- **Specification:** Selected Design — Authorization and data flow; Failure semantics.
- **Depends on:** Accepted ADR 0021.
- **Produces:**
  - `FinalObjectStorage.sign_final(*, key: str, attachment_filename: str | None = None) -> str`
  - `PublicMediaResolver.resolve_download(*, photo: Photo) -> str`
  - deterministic `findme-photo-<photo-id>.<jpg|png>` attachment names.

- [ ] Extend the focused storage test so `sign_final` without a filename keeps its current Params,
  while a valid attachment filename adds exactly `ResponseContentDisposition: attachment;
  filename="..."`; confirm malformed filenames are rejected before signing.
- [ ] Run `.venv/bin/pytest -q src/backend/ingestion/tests/test_storage.py` and confirm the new
  attachment case fails because `sign_final` does not accept `attachment_filename`.
- [ ] Add the optional storage argument, validate the generated filename against the fixed
  `findme-photo-<photo-id>.<jpg|png>` contract, and add the response override only for attachment
  signing. Preserve the existing exact-object inspection and inline call behavior.
- [ ] Add focused gallery resolver tests showing that `resolve_download` always selects
  `photo.original_key`, derives `jpg` or `png` from `photo.original_content_type`, passes the safe
  filename to storage, and rejects a missing key or unsupported persisted type before signing.
- [ ] Run `.venv/bin/pytest -q src/backend/ingestion/tests/test_storage.py
  src/backend/picflow/tests/test_gallery.py` and expect all selected tests to pass.
- [ ] Run `.venv/bin/ruff check src/backend/ingestion/storage.py src/backend/picflow/gallery.py
  src/backend/ingestion/tests/test_storage.py src/backend/picflow/tests/test_gallery.py` and expect no
  findings.
- [ ] Self-review the unstaged task diff for exact-object selection, filename injection, unchanged
  inline signing, and absence of free/paid branching; write the implementer report for root review.
- [ ] Root prepares a complete unstaged Task 1 diff including new files; one independent reviewer
  approves it before Task 2 begins, with any fixes returned to the same implementer and reviewer.

### Task 2: Stable download routes and shared presentation URL

**Files:**

- Modify: `src/backend/config/urls.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/selfie_search/urls.py`
- Modify: `src/backend/selfie_search/views.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`

- **Specification:** Presentation contract; Authorization and data flow; Failure semantics.
- **Depends on:** Task 1 `PublicMediaResolver.resolve_download`.
- **Produces:**
  - `GalleryPhoto.download_url: str`
  - `DownloadUrlBuilder = Callable[[Photo], str]`
  - `GET /events/<slug>/photos/<photo_id>/download/` named `photo_download`
  - `GET /events/<event_slug>/selfie-search/<public_token>/photos/<photo_id>/download/` named
    `selfie_search:result_download`.

- [ ] Extend `GalleryPhotoFactory` tests so the default builder reverses `photo_download` and the
  result builder receives one photo and returns the scoped bearer download URL without storage
  access.
- [ ] Add one normal-gallery view test and one ready-result view test which expect a bodyless 302
  from the new stable route and verify `resolve_download(photo=photo)`; extend the existing
  non-member result test to include the download route and confirm signing is not reached.
- [ ] Run `.venv/bin/pytest -q src/backend/picflow/tests/test_gallery.py
  src/backend/picflow/tests/test_views.py src/backend/selfie_search/tests/test_views.py` and confirm
  failures for the missing field, routes, and view calls.
- [ ] Add the two URL patterns and GET-only views by reusing `gallery_photo_queryset(event=event)`
  and `saved_ready_result_photo(search=search, photo_id=photo_id)` respectively. Map
  `ObjectMissing` to the existing not-found response and other `StorageError` to the existing 503.
- [ ] Add `download_url` to the frozen presentation value. Keep media and download builders
  separate; default to `photo_download`, and pass a scoped `_result_download_url_builder` from the
  result view.
- [ ] Run the three selected pytest modules again and expect all selected tests to pass.
- [ ] Run `.venv/bin/ruff check src/backend/config src/backend/picflow/gallery.py
  src/backend/picflow/tests src/backend/selfie_search` and expect no findings.
- [ ] Self-review the unstaged task diff for reuse of existing authorization, bearer response
  headers, GET-only behavior, sanitized failures, and absence of a new access-type decision; write
  the implementer report for root review.
- [ ] Root prepares the complete unstaged Task 2 diff; one independent reviewer approves it before
  Task 3 begins, with any fixes returned to the same implementer and reviewer.

### Task 3: Card and built-in GLightbox actions

**Files:**

- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/icons.svg`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/static/ui/event-gallery.js`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/js/event-gallery.test.js`
- Update only affected files: `tests/visual/visual.spec.js-snapshots/*.png`
- Create: `docs/future-work/2026-08-01-paid-photo-cart-action.md`

- **Specification:** Presentation contract; Minimal Validation Contract; Future Commerce Trigger.
- **Depends on:** Task 2 `GalleryPhoto.download_url`.
- **Produces:** complete one-click UI and the deferred commerce trigger.

- [ ] Change the existing normal-gallery and ready-result markup assertions to require no
  `gallery-photo-id`, one icon-only `gallery-download` link below each image with accessible name and
  title `Скачать оригинал`, and one GLightbox description link using the same stable URL.
- [ ] Extend the existing JavaScript initialization assertion with `descPosition === "bottom"`;
  do not add custom click handling because card download and description links are normal anchors.
- [ ] Run `.venv/bin/pytest -q src/backend/picflow/tests/test_views.py
  src/backend/selfie_search/tests/test_views.py && npm run test:js` and confirm the markup and
  GLightbox option assertions fail before implementation.
- [ ] Add a `download` symbol to the packaged sprite; replace each caption with a right-aligned
  action row; add the same stable download link to each lightbox description; and set GLightbox's
  built-in description position to bottom.
- [ ] Add focused CSS for the 44-by-44 transparent muted action, focus-visible outline, hover state,
  and built-in description link. Do not add a bright fill, custom lightbox toolbar, or empty row.
- [ ] Create the future-work artifact with the exact observed gap, non-blocking reason, revisit
  trigger, and likely scope approved in the specification.
- [ ] Run the selected pytest modules and `npm run test:js` again and expect all selected tests to
  pass.
- [ ] Run `npm run test:visual:update` once, retain only snapshots affected by the populated event
  gallery and ready selfie-result cards, then run `npm run test:visual` and expect the existing
  visual suite to pass without adding a scenario.
- [ ] Inspect affected desktop and mobile snapshots for right alignment, low visual emphasis,
  44-pixel target, no visible photo ID, and the native GLightbox description action.
- [ ] Self-review the unstaged task diff for pointer and keyboard behavior, mobile spacing,
  progressive pagination markup, no signed URL exposure, and no unrelated snapshot churn; write the
  implementer report for root review.
- [ ] Root prepares the complete unstaged Task 3 diff including the future-work file and affected
  snapshots; one independent reviewer approves it before final verification, with fixes returned to
  the same implementer and reviewer.

### Final task: Verification and architecture reconciliation

**Files:**

- Modify after successful verification: `docs/architecture.md`
- No migration or runtime configuration files.

- **Specification:** all acceptance criteria.
- **Depends on:** Tasks 1–3 approved by their reviewer gates.
- **Produces:** verified implementation, reconciled architecture, and one root-controller commit.

- [ ] Compare the complete unstaged diff with the approved specification and ADRs 0019, 0020, and
  0021. Stop instead of changing the accepted authorization or transport boundaries.
- [ ] Dispatch one independent reviewer for the complete working-tree diff; return fixes to the
  responsible implementer and re-review with the same reviewer until approved.
- [ ] Run `.venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, and `.venv/bin/mypy`; expect
  all commands to exit zero.
- [ ] Run `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/pytest --cov --cov-report=term-missing`; expect all
  tests to pass and branch coverage to remain at least 75 percent.
- [ ] Run `npm run test:js` and `npm run test:visual`; expect all JavaScript and existing visual
  scenarios to pass.
- [ ] Run `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  DEBUG=False ALLOWED_HOSTS=localhost .venv/bin/python src/backend/manage.py check` and the same
  environment with `makemigrations --check --dry-run`; expect no system errors and no migration
  changes.
- [ ] Update `docs/architecture.md` from “unimplemented” to the verified implementation fact, with
  no topology, deployment, or activation claim beyond the evidence obtained.
- [ ] Run `git diff --check`, stage only the approved task files, inspect `git diff --cached --stat`
  and `git diff --cached --check`, then create one implementation commit as the root controller.
- [ ] Record the reconciliation outcome: implementation conforms to ADR 0021, preserves ADR 0019
  membership and ADR 0020 transport, requires no migration, configuration, or deployment-order
  change.

## Verification

Targeted commands are embedded in each task. Final completion requires the CI-equivalent Python
quality/test/system-check sequence plus `npm run test:js` and the existing visual regression suite.
Expected outcome: every command exits zero, coverage stays at or above 75 percent, no migration is
generated, and visual changes are limited to the approved card and lightbox actions.

## Operational impact and rollout

No schema migration, setting, secret, service, worker, queue, or deployment-order change. A normal
application deployment activates the two stable download routes. Existing pages and inline media
remain compatible; rollback removes the new links and routes by redeploying the prior immutable
image. Monitoring uses existing Django status and Object Storage transfer/error signals.

## Rollback

Redeploy the prior application image to remove download actions and routes. Existing gallery and
lightbox presentation continues through current media routes. Already downloaded bytes cannot be
revoked; short signed URL expiry bounds reuse of an issued capability. No database rollback is
needed.

## Open questions

None.
