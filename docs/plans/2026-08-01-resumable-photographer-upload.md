# Resumable Photographer Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-08-01
- Status: Draft
- Owner: project maintainer
- Related specification:
  [Resumable photographer upload](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md)
- Related architecture: [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- Related ADRs: [0012](../adr/0012-use-django-photographer-permissions.md),
  [0013](../adr/0013-use-direct-private-object-storage-ingestion.md), and
  [0014](../adr/0014-keep-stage-2-ingestion-request-driven.md)
- ADR impact: Resolved — conforms to ADR 0012, ADR 0013, and ADR 0014

**Goal:** Implement the approved return, reselect, skip-confirmed, and continue-upload workflow,
with an actionable bounded queue and stable progress summary.

**Architecture:** PostgreSQL remains authoritative for owned batch/item progress. Django adds a
focused resume read model and safe manifest, while the browser reconstructs its in-memory queue
from reselected `File` objects and continues the existing direct Object Storage control flow.

**Tech Stack:** Django 5.2, PostgreSQL, vanilla JavaScript, Web Crypto SHA-256, Node test runner,
Playwright visual regression tests.

## Global constraints

- Implement the complete approved [scope](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#scope)
  without adding folder handles, persisted browser file access, closed-page transfer, multipart
  continuation, global deduplication, batch merging, or insertion of extra files into a batch.
- Keep upload authorization, ownership, direct private Object Storage transfer, confirmation, and
  immutable `Photo` creation inside ADR 0012–0014.
- Do not expose incoming/final keys, ETags, storage credentials, signed forms, or another uploader's
  records through resume reads.
- Hash only ambiguous filename-size-last-modified groups; never read every selected file merely to
  begin or resume the normal unique-file path.
- Preserve the current maximums: 10,000 JPEGs per batch, 50 MiB per file, registration chunks no
  larger than 100, and at most four active transfers.
- Use explicit queue states and bounded DOM rendering; do not restore the last-20-items behavior.

## Scope

Implement the approved specification without scope changes.

## Acceptance criteria

Use all approved
[acceptance criteria](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#acceptance-criteria).
Delivery additionally requires a migration-drift check, the CI-equivalent Python/JavaScript suite,
and updated desktop/mobile upload snapshots.

## File responsibility map

- `src/backend/ingestion/models.py` and `src/backend/ingestion/migrations/0002_upload_item_resume_metadata.py`:
  nullable, backward-compatible local matching metadata.
- `src/backend/ingestion/services/resume.py`: owned unfinished-batch query and safe manifest read
  model; no HTTP or storage-adapter responsibility.
- `src/backend/ingestion/forms.py`, `src/backend/ingestion/services/batches.py`, and
  `src/backend/ingestion/views.py`: registration contract, resume HTTP serialization, and existing
  mutation reuse.
- `src/backend/ingestion/urls.py`: named resume-manifest route.
- `src/backend/static/ui/upload-coordinator.js`: selection grouping, selective hashing, manifest
  matching, resumed transfer scheduling, group summaries, and bounded visible rows.
- `src/backend/templates/ingestion/upload.html` and `src/backend/static/ui/upload.css`: unfinished
  cards, grouped queue shell, and stable responsive summary geometry.
- `src/backend/ingestion/tests/`, `tests/js/upload-coordinator.test.js`, and `tests/visual/`: backend,
  coordinator, interaction, accessibility, layout, and snapshot evidence.
- `docs/architecture.md` and `docs/product-jobs.md`: implemented resume behavior and evidence after
  verification.

## Implementation

### Task 1: Persist backward-compatible matching metadata

**Files:**

- Modify: `src/backend/ingestion/models.py`
- Create: `src/backend/ingestion/migrations/0002_upload_item_resume_metadata.py`
- Modify: `src/backend/ingestion/forms.py`
- Modify: `src/backend/ingestion/services/batches.py`
- Modify: `src/backend/ingestion/tests/test_models.py`
- Modify: `src/backend/ingestion/tests/test_batch_services.py`
- Modify: `src/backend/ingestion/tests/test_views.py`

- **Specification:** [Minimal hybrid duplicate handling](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#minimal-hybrid-duplicate-handling)
  and [Data and interface contract](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#data-and-interface-contract).
- **Depends on:** None.
- **Produces:** nullable `UploadItem.client_last_modified_ms: BigIntegerField` and
  `UploadItem.ambiguous_sha256: CharField(max_length=64)`; registration input fields
  `last_modified_ms: int | None` and `ambiguous_sha256: str | None` carried by `ItemInput` and
  compared during idempotent replay.

- [ ] Add model and service tests proving new rows persist valid millisecond timestamps and
  lowercase 64-character SHA-256 values, ordinary unique rows keep `ambiguous_sha256=NULL`, legacy
  rows may keep both fields null, and idempotent registration rejects changed matching metadata.
- [ ] Extend view validation tests with valid optional values and rejected negative timestamps,
  malformed/non-lowercase hashes, and a hash supplied without a last-modified value.
- [ ] Run
  `.venv/bin/pytest -q src/backend/ingestion/tests/test_models.py src/backend/ingestion/tests/test_batch_services.py src/backend/ingestion/tests/test_views.py`
  and confirm the new assertions fail because the fields and form contract do not exist.
- [ ] Add nullable fields and a no-data migration. Extend `ItemForm`, `ItemInput`, registration row
  creation, and `_metadata_matches` without changing existing callers that omit both optional
  values. Validate SHA-256 at the Django form boundary and again in the service input validator.
- [ ] Run the same targeted pytest command and confirm all model, registration, replay, and
  validation cases pass.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` and confirm
  `No changes detected` after the checked-in migration.
- [ ] Prepare the complete unstaged task diff for independent review; after approval and root final
  verification, stage only the Task 1 files and create the single task commit
  `feat: persist upload resume metadata`.

### Task 2: Expose owned unfinished batches and a safe resume manifest

**Files:**

- Create: `src/backend/ingestion/services/resume.py`
- Create: `src/backend/ingestion/tests/test_resume.py`
- Modify: `src/backend/ingestion/views.py`
- Modify: `src/backend/ingestion/urls.py`
- Modify: `src/backend/ingestion/tests/test_views.py`
- Modify: `src/backend/ingestion/tests/test_permissions.py`
- Modify: `src/backend/ingestion/tests/test_templates.py`

- **Specification:** [Unfinished upload history](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#unfinished-upload-history),
  [Resume interaction](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#resume-interaction),
  and [Failure semantics](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#failure-semantics).
- **Depends on:** Task 1 matching fields.
- **Produces:** `list_unfinished_batches(uploader) -> tuple[UnfinishedBatchSummary, ...]`,
  `get_resume_manifest(uploader, batch_id) -> ResumeManifest`, and named GET route
  `upload_batch_resume_manifest` at `/photographer/uploads/<uuid:batch>/resume/`.

- [ ] Add service tests for newest-activity ordering, exclusion of completed/fully confirmed
  batches, inclusion of resumable `created/uploading/partial/failed` batches, aggregate counts, and
  bounded-query behavior for a representative multi-item fixture.
- [ ] Add view/permission tests proving the upload page renders only owned unfinished summaries;
  manifest GET returns item ID, filename, size, matching hints, status, and `confirmed`; and
  anonymous, permissionless, missing, and cross-owner requests fail closed. Assert serialized
  responses contain no `incoming_key`, `final_key`, ETag, `photo.original_key`, grant, or credential.
- [ ] Run
  `.venv/bin/pytest -q src/backend/ingestion/tests/test_resume.py src/backend/ingestion/tests/test_views.py src/backend/ingestion/tests/test_permissions.py src/backend/ingestion/tests/test_templates.py`
  and confirm failures identify the missing service, route, and page context.
- [ ] Implement immutable summary/manifest dataclasses and queryset aggregation in
  `services/resume.py`; keep ownership in every queryset. Render summaries in `upload_page` and
  serialize one owned manifest through the new GET view.
- [ ] Reuse the existing `_upload_access` permission boundary and return the existing sanitized
  `not_found` envelope for an inaccessible manifest rather than disclosing ownership.
- [ ] Run the same targeted pytest command and confirm list, manifest, privacy, query, and template
  assertions pass.
- [ ] Prepare the complete unstaged task diff for independent review; after approval and root final
  verification, stage only the Task 2 files and create the single task commit
  `feat: expose unfinished upload manifests`.

### Task 3: Reconstruct and continue the browser queue

**Files:**

- Modify: `src/backend/static/ui/upload-coordinator.js`
- Modify: `tests/js/upload-coordinator.test.js`
- Modify: `src/backend/templates/ingestion/upload.html`
- Modify: `src/backend/ingestion/tests/test_templates.py`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`

- **Specification:** [Resume interaction](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#resume-interaction),
  [Minimal hybrid duplicate handling](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#minimal-hybrid-duplicate-handling),
  and acceptance criteria 2–7.
- **Depends on:** Task 1 registration fields and Task 2 manifest shape.
- **Produces:** `matchingKey(file) -> string`,
  `prepareAmbiguousFingerprints(items, subtle) -> Promise<void>`,
  `matchResumeSelection(files, manifest, options) -> Promise<ResumeMatch>`, and
  `UploadCoordinator.resume(files, manifest) -> Promise<UploadCoordinator>`.

- [ ] Add Node tests proving unique new files are registered with last-modified metadata without
  hashing; only duplicate metadata groups call `crypto.subtle.digest`; lowercase SHA-256 is sent
  for those items; unique modern and legacy manifest entries match correctly; identical hash
  groups use multiset counts; unresolved ambiguity and extra selections become `needs_attention`;
  and confirmed items produce no authorize, retry, transfer, or confirm call.
- [ ] Add coordinator tests proving matched `failed` items use the retry route, matched
  `pending/authorized` items use the existing authorization route, missing manifest items remain
  waiting, resumed transfers retain the four-transfer limit, and finalization occurs only when the
  durable manifest cohort is terminal.
- [ ] Run `npm run test:js` and confirm the new tests fail because resume matching and selective
  hashing are absent while all pre-existing coordinator tests remain green.
- [ ] Implement selective hashing as a separate pure preparation stage; extend registration
  payloads with the Task 1 optional fields; add resume mode without changing new-batch behavior;
  and keep server-confirmed status authoritative over local progress.
- [ ] Add unfinished-batch cards and a dedicated resume file input/action to the template. Wire one
  card to fetch its Task 2 manifest, select files, call `resume`, and lock the displayed event to
  the manifest event without persisting `File` objects or grants.
- [ ] Update visual request stubs and add Playwright interaction coverage for returning to a page,
  choosing an unfinished card, reselecting a mixed confirmed/unconfirmed set, and observing that
  only the unfinished item reaches authorization and Object Storage.
- [ ] Run `npm run test:js` and confirm every JavaScript unit test passes.
- [ ] Run
  `.venv/bin/pytest -q src/backend/ingestion/tests/test_templates.py && npm run test:visual -- --grep "upload"`
  and confirm the template contract and resume interaction tests pass before snapshot updates.
- [ ] Prepare the complete unstaged task diff for independent review; after approval and root final
  verification, stage only the Task 3 files and create the single task commit
  `feat: resume interrupted browser uploads`.

### Task 4: Replace the last-20 list and stabilize upload layout

**Files:**

- Modify: `src/backend/static/ui/upload-coordinator.js`
- Modify: `tests/js/upload-coordinator.test.js`
- Modify: `src/backend/templates/ingestion/upload.html`
- Modify: `src/backend/static/ui/upload.css`
- Modify: `src/backend/ingestion/tests/test_templates.py`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`
- Update: `tests/visual/visual.spec.js-snapshots/desktop-upload-empty.png`
- Update: `tests/visual/visual.spec.js-snapshots/desktop-upload-active.png`
- Update: `tests/visual/visual.spec.js-snapshots/desktop-upload-complete.png`
- Update: `tests/visual/visual.spec.js-snapshots/desktop-upload-partial.png`
- Update: `tests/visual/visual.spec.js-snapshots/mobile-upload-empty.png`
- Update: `tests/visual/visual.spec.js-snapshots/mobile-upload-active.png`
- Update: `tests/visual/visual.spec.js-snapshots/mobile-upload-complete.png`
- Update: `tests/visual/visual.spec.js-snapshots/mobile-upload-partial.png`

- **Specification:** [Queue presentation](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#queue-presentation)
  and acceptance criteria 8–9.
- **Depends on:** Task 3 reconstructed queue states.
- **Produces:** `groupItems(items) -> QueueGroups` and
  `visibleGroupItems(group, offset, pageSize) -> UploadItem[]`; accessible grouped queue markup and
  fixed-width desktop summary styles.

- [ ] Replace tests for `visibleItems(last 20)` with Node/template tests for ordered groups:
  `needs_attention`, `uploading`, `waiting`, `uploaded`; only the first two expanded initially;
  explicit counts; and no more than 20 rendered rows per expanded group page for a 10,000-item
  fixture.
- [ ] Add Playwright assertions for group disclosure keyboard behavior, active/error priority,
  bounded DOM row count, no horizontal document overflow, and stable bounding boxes for the upload
  controls and summary while counters cross `9`, `10`, `999`, and `1 000`.
- [ ] Run `npm run test:js` and the targeted upload Playwright grep; confirm failures show the old
  last-20 renderer and shifting summary.
- [ ] Implement grouped rendering with semantic disclosure buttons and per-group bounded paging.
  Remove the `Показаны последние 20 файлов` copy and the root `queueWindowSize` contract.
- [ ] Give the desktop summary an explicit grid track/minimum geometry, reserve every metric/message
  row, and apply `font-variant-numeric: tabular-nums` to percentage and count values. Preserve the
  existing one-column responsive stack at `860px` and mobile controls at `640px`.
- [ ] Run `npm run test:js` and targeted template tests; confirm grouping, disclosure, and bounded
  rendering pass.
- [ ] Run `npm run test:visual:update -- --grep "upload"` through the documented visual container
  workflow, inspect every changed desktop/mobile PNG, then run
  `npm run test:visual -- --grep "upload"` and confirm interaction, accessibility, overflow, layout,
  and snapshot checks pass.
- [ ] Prepare the complete unstaged task diff, including all changed snapshots, for independent
  review; after approval and root final verification, stage only the Task 4 files and create the
  single task commit `feat: group and stabilize upload progress`.

### Task 5: Reconcile delivered behavior and run final verification

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify only if implementation decisions changed:
  `docs/superpowers/specs/2026-08-01-resumable-photographer-upload-design.md`

- **Specification:** [Architecture and ADR impact](../superpowers/specs/2026-08-01-resumable-photographer-upload-design.md#architecture-and-adr-impact)
  and all acceptance criteria.
- **Depends on:** Tasks 1–4 approved and individually verified.
- **Produces:** repository documentation that distinguishes implemented resume behavior from future
  background transfer, plus final CI-equivalent evidence.

- [ ] Compare the complete behavior and diff with the approved specification and ADR 0012–0014.
  Stop for a maintainer decision if delivery adds closed-browser transfer, new infrastructure,
  global media identity, or any other durable boundary outside those ADRs.
- [ ] Update `docs/architecture.md` photo-ingestion flow to state that PostgreSQL-backed unfinished
  batches can reconstruct the open-page browser queue after explicit reselection, while transfer
  still stops when the page closes.
- [ ] Update PJ-004 in `docs/product-jobs.md` with the implemented behavior and exact test/CI evidence;
  mark it validated only if the complete critical path and CI-equivalent suite pass.
- [ ] Run the targeted full feature set:
  `.venv/bin/pytest -q src/backend/ingestion tests/visual --disable-warnings` and expect zero failures;
  run `npm run test:js` and expect zero failures; run the complete containerized `npm run test:visual`
  and expect every desktop/mobile interaction and snapshot test to pass.
- [ ] Run CI-equivalent Python checks with CI environment values:
  `.venv/bin/ruff format --check .`, `.venv/bin/ruff check .`, `.venv/bin/mypy`,
  `.venv/bin/pytest --cov --cov-report=term-missing`,
  `.venv/bin/python src/backend/manage.py check`, and
  `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`;
  expect every command to exit 0 and branch coverage to remain at or above 75%.
- [ ] Run `git diff --check` and inspect `git status --short` so only intended task files and the
  user's pre-existing unrelated changes remain.
- [ ] Record the reconciliation outcome as `Conforms to ADR 0012, ADR 0013, and ADR 0014`; no ADR or
  deployment topology change is required.
- [ ] Prepare the documentation and complete feature diff for final independent review. After
  approval and root rerun of final verification, stage only Task 5 documentation and create the
  single task commit `docs: record resumable photographer uploads`.

## Verification

Run from the repository root with CI-equivalent `DB_*`, `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS`
values where Django settings require them.

1. `.venv/bin/ruff format --check .` — exits 0 with no formatting diff.
2. `.venv/bin/ruff check .` — exits 0 with no lint findings.
3. `.venv/bin/mypy` — exits 0 with no type errors.
4. `.venv/bin/pytest --cov --cov-report=term-missing` — exits 0; all tests pass and branch coverage
   is at least 75%.
5. `.venv/bin/python src/backend/manage.py check` — reports no Django system-check issues.
6. `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` — reports no model
   changes outside checked-in migrations.
7. `npm run test:js` — all coordinator and existing JavaScript tests pass.
8. `npm run test:visual` — all containerized Playwright interaction and snapshot tests pass for
   desktop and mobile states.
9. `git diff --check` — exits 0.

## Operational impact and rollout

- The release contains one additive nullable PostgreSQL migration. Apply it before serving the new
  backend and static assets; existing items remain readable and use the documented unique
  filename-size legacy fallback.
- No new environment variable, bucket policy, IAM permission, CORS rule, worker, service, or port is
  introduced.
- Deploy Django, the migration, template, CSS, and JavaScript from one release image so manifest
  fields and browser behavior stay compatible.
- Verify after deployment with one owned partial batch containing one confirmed and one failed item:
  the list shows the batch, reselection skips the confirmed item, only the failed item obtains a
  fresh grant, and completion creates no duplicate `Photo`.
- Monitor existing Django 4xx/5xx and Object Storage error signals during the smoke. No new
  monitoring subsystem is required.

## Rollback

- Roll back application/template/static assets together. The additive nullable columns may remain;
  the previous version ignores them, so a reverse migration is not required during an incident.
- Do not delete batches, items, confirmed photos, or private objects during rollback.
- If the new read or resume path misbehaves, disable the release by reverting the application
  commit(s); existing new-upload, retry, confirmation, and cleanup paths remain the recovery route.
- Remove the nullable columns only in a later separately verified maintenance change after all
  deployed versions no longer read or write them.

## Open questions

None.
