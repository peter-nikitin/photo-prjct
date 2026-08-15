# Event Photo Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add event-scoped photo folders to Django Admin, one mixed-folder resumable photographer upload queue, and stable multi-folder filtering in the public event gallery.

**Architecture:** `EventFolder` is event catalog metadata, while nullable protected foreign keys on `UploadItem` and `Photo` carry one durable assignment from registration through confirmation. The browser renders event-specific drop zones but retains one `UploadBatch` and coordinator; the gallery derives stable non-empty choices from its unfiltered eligibility queryset, validates repeated GET choices, applies folder `OR` plus time `AND`, then uses the existing numbered paginator.

**Tech Stack:** Django 6, PostgreSQL constraints and migrations, server-rendered Django templates, plain JavaScript, CSS, Node test runner, pytest-django, Playwright visual tests.

## Global Constraints

- Work only in `.worktrees/event-photo-folders` on `codex/event-photo-folders`.
- Follow [the approved design](../specs/2026-08-15-event-photo-folders-design.md) exactly; the mass editor remains excluded.
- Keep one event `UploadBatch`, one 10,000-item limit, one transfer queue, and existing request-driven direct Object Storage ingestion.
- A folder is metadata, not an Object Storage prefix; do not change incoming or final object keys.
- `NULL` means `Без папки`; never silently convert an invalid folder to `NULL`.
- Folder query parameters may only narrow the existing public gallery eligibility queryset.
- Folder choices are computed before active folder/time filters and never disappear because of those filters.
- Do not add packages, background jobs, nested folders, manual folder ordering, compatibility branches, or speculative editor permissions.
- Implementer and reviewer agents leave changes unstaged and never run Git mutation commands. The root controller alone stages the exact task files and creates one commit after approval and final verification.
- Use `make test TESTS="..."` and repository npm scripts; do not invoke global Python, pytest, Ruff, or mypy.

---

## File Map

- `src/backend/picflow/models.py`: owns `EventFolder`, normalized folder names, and nullable `Photo.folder`.
- `src/backend/picflow/migrations/`: introduces folder storage and protected relations without backfilling existing photos; existing rows remain `Без папки`.
- `src/backend/picflow/admin.py`: exposes event folders inline on the event change page.
- `src/backend/ingestion/models.py`: adds the durable nullable folder assignment to `UploadItem`.
- `src/backend/ingestion/forms.py`, `services/batches.py`, `services/confirmation.py`, and `services/resume.py`: validate, persist, publish, and resume the assignment.
- `src/backend/ingestion/views.py`: supplies event-folder data to the page and carries folder values through JSON contracts.
- `src/backend/templates/ingestion/upload.html`, `static/ui/upload-coordinator.js`, and `static/ui/upload.css`: render folder targets, retain one queue, show assignment labels, and provide unambiguous drop feedback.
- `src/backend/picflow/forms.py`, `picflow/gallery.py`, and `config/views.py`: parse folder GET choices, derive stable public choices, filter, and paginate.
- `src/backend/templates/catalog/event_detail.html`, `templates/ui/gallery_pagination.html`, and `static/ui/catalog.css`: render stable checkbox controls, preserve query state, and show reset/empty states.
- Existing unit, integration, JavaScript, and visual test files own regression evidence near each responsibility.
- `docs/architecture.md`, `docs/product-jobs.md`, and `docs/engineering-jobs.md`: record the delivered capability and evidence only after all behavior passes.

---

### Task 1: Event folder domain model and administration

**Files:**
- Modify: `src/backend/picflow/models.py`
- Create: `src/backend/picflow/migrations/0009_eventfolder_photo_folder.py`
- Modify: `src/backend/picflow/admin.py`
- Modify: `src/backend/picflow/tests/test_models.py`
- Modify: `src/backend/picflow/tests/test_admin.py`
- Modify: `src/backend/picflow/tests/test_photo_migrations.py`

**Interfaces:**
- Produces: `EventFolder(event: Event, name: str)`, `Event.folders`, and `Photo.folder: EventFolder | None`.
- Produces invariant: `EventFolder.name` is trimmed, non-empty, ordered by case-insensitive name, and unique per event under case-insensitive comparison.
- Produces invariant: `Photo.folder` uses `on_delete=models.PROTECT`, `null=True`, `blank=True`, `related_name="photos"`.

- [ ] **Step 1: Write failing model and migration tests**

Add focused cases equivalent to:

```python
folder = EventFolder(event=event, name="  Финиш  ")
folder.full_clean()
folder.save()
self.assertEqual(folder.name, "Финиш")

with self.assertRaises(ValidationError):
    EventFolder(event=event, name="   ").full_clean()

EventFolder.objects.create(event=event, name="Старт")
with self.assertRaises(IntegrityError), transaction.atomic():
    EventFolder.objects.create(event=event, name="старт")

photo = self.private_photo(folder=folder)
photo.full_clean()
photo.save()
with self.assertRaises(ProtectedError):
    folder.delete()
```

Extend the migration test to migrate from the current leaf migration, create legacy photos first,
migrate forward, and assert every pre-existing `Photo.folder_id is None`.

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/picflow/tests/test_photo_migrations.py"
```

Expected: failures because `EventFolder`, `Photo.folder`, and the admin inline do not exist.

- [ ] **Step 3: Implement the model boundary and generated migration**

Add a focused model with normalization in `clean()` and `save()` so ordinary model writes and
admin writes converge, plus database constraints using `Lower(Trim("name"))` scoped to `event` and
requiring stored names to equal their trimmed form:

```python
class EventFolder(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="folders")
    name = models.CharField(max_length=255)

    class Meta:
        ordering = [Lower("name"), "id"]
        constraints = [
            models.UniqueConstraint(
                Lower(Trim("name")),
                "event",
                name="picflow_folder_event_name_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(name=Trim("name")) & ~models.Q(name=""),
                name="picflow_folder_name_trimmed_chk",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Folder name cannot be empty."})

    def save(self, *args, **kwargs) -> None:
        self.name = self.name.strip()
        super().save(*args, **kwargs)
```

Import `Lower`, add `Photo.folder`, then run:

```bash
.venv/bin/python src/backend/manage.py makemigrations picflow
```

Inspect the migration: it must only create `EventFolder`, add nullable `Photo.folder`, and add the
constraint; it must not rewrite photo rows.

- [ ] **Step 4: Add the event-admin inline**

Implement `EventFolderInline(admin.TabularInline)` with `model = EventFolder`, `extra = 1`, and
attach it to `EventAdmin.inlines`. Keep the existing event delete prohibition. Test add, rename,
duplicate-name error, empty deletion, and protected deletion through the event change page.

- [ ] **Step 5: Verify Task 1**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_admin.py src/backend/picflow/tests/test_photo_migrations.py"
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
```

Expected: all selected tests pass and `No changes detected`.

- [ ] **Step 6: Root review and commit**

After implementer self-review and independent reviewer approval, the root controller runs
`git diff --check`, stages only the six Task 1 paths, and commits:

```bash
git commit -m "feat: add event photo folders"
```

---

### Task 2: Durable folder assignment through ingestion

**Files:**
- Modify: `src/backend/ingestion/models.py`
- Create: `src/backend/ingestion/migrations/0003_uploaditem_folder.py`
- Modify: `src/backend/ingestion/forms.py`
- Modify: `src/backend/ingestion/services/batches.py`
- Modify: `src/backend/ingestion/services/confirmation.py`
- Modify: `src/backend/ingestion/services/resume.py`
- Modify: `src/backend/ingestion/views.py`
- Modify: `src/backend/ingestion/tests/test_models.py`
- Modify: `src/backend/ingestion/tests/test_batch_services.py`
- Modify: `src/backend/ingestion/tests/test_confirmation.py`
- Modify: `src/backend/ingestion/tests/test_resume.py`
- Modify: `src/backend/ingestion/tests/test_views.py`

**Interfaces:**
- Consumes: `EventFolder`, `Event.folders`, and `Photo.folder` from Task 1.
- Produces: `UploadItem.folder: EventFolder | None` with `PROTECT`, `null=True`, `blank=True`, `related_name="upload_items"`.
- Produces: `ItemInput.folder_id: int | None`.
- Produces JSON input: each registration item contains `folder_id` as an integer or `null`.
- Produces resume JSON: each item contains `folder: {"id": int, "name": str} | null`.

- [ ] **Step 1: Write failing service, view, confirmation, and resume tests**

Cover one registration chunk containing `Старт`, `Финиш`, and `None`; idempotent re-registration
with the same folder; `item_metadata_conflict` when the same client ID changes folder; and
`folder_not_found` for missing or foreign event IDs. Assert confirmation creates:

```python
self.assertEqual(start_item.photo.folder, start_folder)
self.assertEqual(unfiled_item.photo.folder_id, None)
```

Assert the resume payload returns current folder names after rename and does not take a client-side
folder override. Add a defensive confirmation test that corrupts `UploadItem.folder_id` with SQL or
an allowed setup bypass and expects `ItemStateConflict("folder_event_mismatch", ...)` before a
`Photo` is created.

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
make test TESTS="src/backend/ingestion/tests/test_models.py src/backend/ingestion/tests/test_batch_services.py src/backend/ingestion/tests/test_confirmation.py src/backend/ingestion/tests/test_resume.py src/backend/ingestion/tests/test_views.py"
```

Expected: failures on the absent field and JSON contract.

- [ ] **Step 3: Add `UploadItem.folder` and migration**

Add the nullable protected foreign key and generate the ingestion migration. Existing items remain
`NULL`. Run `makemigrations --check --dry-run` after inspecting the generated migration.

- [ ] **Step 4: Extend registration validation and service inputs**

Add `folder_id = forms.IntegerField(required=False, min_value=1)` to `ItemForm`, with `None` as the
empty value. Extend the immutable item input:

```python
@dataclass(frozen=True)
class ItemInput:
    client_item_id: UUID
    filename: str
    content_type: str
    size: int
    folder_id: int | None
    last_modified_ms: int | None = None
    ambiguous_sha256: str | None = None
```

Inside the locked batch transaction, resolve all non-null IDs in one event-scoped query. If any ID
is absent, raise `BatchConflict("folder_not_found", "The selected event folder is no longer available.")`.
Persist `folder_id`, and include it in `_metadata_matches` so idempotency cannot change assignment.

- [ ] **Step 5: Publish and resume the durable assignment**

Before creating `Photo`, verify `item.folder_id is None or item.folder.event_id == batch.event_id`;
raise `ItemStateConflict("folder_event_mismatch", "The upload folder does not belong to this event.")`
on inconsistency. Pass `folder=item.folder` to `Photo.objects.create`.

Extend `ResumeManifestItem` with `folder_id: int | None` and `folder_name: str | None`; prefetch or
`select_related("folder")` without per-item queries. Serialize as:

```python
"folder": (
    {"id": item.folder_id, "name": item.folder_name}
    if item.folder_id is not None
    else None
)
```

- [ ] **Step 6: Verify Task 2**

Run the Task 2 test selector, then:

```bash
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
```

Expected: selected tests pass; no migration drift.

- [ ] **Step 7: Root review and commit**

After reviewer approval, the root controller stages only Task 2 files and commits:

```bash
git commit -m "feat: persist upload folder assignments"
```

---

### Task 3: Mixed-folder upload interface and drag feedback

**Files:**
- Modify: `src/backend/ingestion/views.py`
- Modify: `src/backend/templates/ingestion/upload.html`
- Modify: `src/backend/static/ui/upload-coordinator.js`
- Modify: `src/backend/static/ui/upload.css`
- Modify: `src/backend/ingestion/tests/test_templates.py`
- Modify: `tests/js/upload-coordinator.test.js`
- Modify: `tests/visual/visual_urls.py`
- Modify: `tests/visual/visual.spec.js`
- Update snapshots under: `tests/visual/__snapshots__/`

**Interfaces:**
- Consumes: registration and resume folder JSON from Task 2.
- Produces page data: event options expose their ordered folders without a new endpoint.
- Produces DOM: `[data-folder-target][data-folder-id]`, where an empty `data-folder-id` means
  `Без папки`, and a nested file input carries the same target.
- Produces client item property: `folder: { id: number, name: string } | null`.

- [ ] **Step 1: Write failing template and JavaScript tests**

Template tests must assert each event's folders are serialized safely and only become visible for
the selected event. JavaScript tests must prove:

```javascript
const selection = prepareSelection([startFile], limits, { folder: { id: 4, name: 'Старт' } });
assert.equal(selection.items[0].folder.id, 4);
assert.equal(registrationPayload.items[0].folder_id, 4);
```

Add tests for one queue receiving sequential selections from two named zones and `Без папки`, one
shared batch creation, the total max-file check across selections, folder labels in queue rows,
resume labels from server data, and no reassignment after insertion.

For drag state, simulate `dragenter`, nested `dragleave`, `drop`, and cancellation. Assert exactly
one target has `data-drag-active="true"`, siblings are subdued through the root state, the target
copy becomes `Загрузить в «Финиш»` or `Загрузить без папки`, and all state clears after completion.

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
make test TESTS="src/backend/ingestion/tests/test_templates.py"
npm test -- --test-name-pattern="folder|drop target|mixed queue"
```

Expected: failures because folder targets and per-selection folder state do not exist.

- [ ] **Step 3: Render event-specific folder targets**

Load `Event.objects.prefetch_related("folders")` in `upload_page`. Render one hidden target
collection per event or one safely encoded event-folder template, with `Без папки` first. Each
target must be a real label/button relationship to its own multiple JPEG input so keyboard and
file-picker selection work without drag-and-drop. Disable event switching once the first batch is
created, matching the existing event ownership behavior.

- [ ] **Step 4: Extend the coordinator without splitting the queue**

Change `prepareSelection` to accept a target:

```javascript
function prepareSelection(files, { maxFiles, maxFileBytes, crypto, folder = null }) {
  // existing validation
  return {
    items: selected.map((file) => ({
      clientItemId: crypto.randomUUID(),
      contentType: 'image/jpeg',
      file,
      folder,
    })),
  };
}
```

Keep one coordinator `items` array and one `batchId`. Registration emits `folder_id`; queue metadata
appends `Папка: <name>` or `Папка: Без папки`. Resume reconstructs folder exclusively from the
manifest response.

- [ ] **Step 5: Implement stable drag-target state and styling**

Use a per-target drag-depth counter or related-target containment check so movement across child
elements does not flicker. Set state on the target and root rather than relying only on `:hover`.
CSS must emphasize the full active target, subdue non-target siblings, preserve a visible focus
ring, and clear styles when no drag is active. Do not accept a drop on the target collection gap.

- [ ] **Step 6: Add visual and interaction coverage**

Update visual fixtures with two named folders and mixed queue items. Add desktop and 390px mobile
snapshots for the selected-event targets and mixed queue. Add an interaction case that dispatches a
drag over `Финиш`, asserts the exact destination copy and unique highlight, drops a JPEG, and checks
the queue folder label.

- [ ] **Step 7: Verify Task 3**

Run:

```bash
make test TESTS="src/backend/ingestion/tests/test_templates.py"
npm test
npm run test:visual -- --grep "upload"
```

Expected: all JS tests and upload visual cases pass; inspect changed snapshots for target clarity,
no overlap, and mobile picker accessibility.

- [ ] **Step 8: Root review and commit**

After reviewer approval and snapshot inspection, the root controller stages only Task 3 files and
commits:

```bash
git commit -m "feat: upload photos into event folders"
```

---

### Task 4: Stable public folder filtering and pagination

**Files:**
- Modify: `src/backend/picflow/forms.py`
- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/templates/ui/gallery_pagination.html`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `tests/visual/visual_urls.py`
- Modify: `tests/visual/visual.spec.js`
- Update snapshots under: `tests/visual/__snapshots__/`

**Interfaces:**
- Consumes: `Photo.folder`, `Event.folders`, and the existing base gallery eligibility predicate.
- Produces: `EventGalleryFolderFilterForm(event, available_choices, data)` with repeated `folder`
  values and explicit `unfiled=1`.
- Produces: `gallery_photo_queryset(..., folder_ids: Collection[int] | None = None,
  include_unfiled: bool = False)`.
- Produces context: `gallery_folder_choices`, `gallery_folder_filter_form`, and one combined
  pagination query representation that preserves valid time and folder inputs.

- [ ] **Step 1: Write failing form, queryset, view, pagination, and stability tests**

Build base-eligible photos in two folders, one empty folder, and `NULL`; also build ineligible
preview and another-event photos. Assert choices include only the two eligible named folders plus
`Без папки`, and exclude empty/ineligible/cross-event variants.

Assert no selection returns all base photos; two folder IDs use `OR`; named plus unfiled uses `OR`;
folder plus valid capture bounds uses `AND`. Request a time range with zero photos and assert the
same choices remain rendered. Assert malformed, repeated-invalid, deleted, and foreign IDs are
ignored rather than widening authorization. Assert page 2 links preserve valid repeated folder,
unfiled, from, and to values.

- [ ] **Step 2: Run focused tests and confirm the red state**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py"
```

Expected: failures because folder parsing and filtering do not exist.

- [ ] **Step 3: Separate base eligibility from active filters**

Keep `gallery_photo_queryset(event=event)` as the authority for base public eligibility. Add a
small query helper that aggregates non-empty named choices from that base queryset and checks
`folder_id__isnull=True` before any active filters. Do not derive choices from a paginated or
time-filtered queryset.

Extend filtering after base construction:

```python
folder_filter = Q()
if folder_ids:
    folder_filter |= Q(folder_id__in=folder_ids)
if include_unfiled:
    folder_filter |= Q(folder_id__isnull=True)
if folder_ids or include_unfiled:
    queryset = queryset.filter(folder_filter)
```

Then apply the existing validated UTC bounds and retain filename/ID ordering.

- [ ] **Step 4: Parse and normalize stable GET choices**

Use repeated `folder=<id>` values and `unfiled=1`. Validate IDs against the stable available named
choices for this event. Unknown, malformed, duplicate, deleted, and foreign IDs are omitted from
the normalized selection. Keep the existing time form behavior and errors unchanged. Build one
normalized query list (not a lossy dictionary) for pagination so repeated folder values survive.

- [ ] **Step 5: Render stable accessible controls**

Place the folder fieldset in the existing manual-search area. Use a visible legend, native
checkboxes, exact label `Без папки`, and checked state from normalized GET values. Render the folder
control only when at least one eligible named folder exists; within it, render `Без папки` only
when eligible unfiled photos exist. Keep all rendered choices on zero-result intersections.

The submit action applies time and folder together. The reset link removes both groups. Empty copy
must distinguish an active zero-result filter from an event with no published photos without
hiding controls.

- [ ] **Step 6: Preserve filters through numbered pagination**

Change the shared pagination include to accept ordered query pairs or a pre-encoded validated query
string, append only `page`, and retain the `#gallery` anchor. Add regression assertions for previous,
next, and numbered links with multiple folders.

- [ ] **Step 7: Add visual coverage**

Extend event visual fixtures with two non-empty folders, one hidden empty folder, and unfiled photos.
Capture desktop/mobile states for no selection, multi-selection combined with time, and stable
zero-result controls. Inspect that checkboxes do not shift or disappear and remain usable at 390px.

- [ ] **Step 8: Verify Task 4**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py"
npm run test:visual -- --grep "event gallery|manual time"
```

Expected: selected Python tests and gallery visual cases pass; changed snapshots match the approved
stable-control behavior.

- [ ] **Step 9: Root review and commit**

After reviewer approval and snapshot inspection, the root controller stages only Task 4 files and
commits:

```bash
git commit -m "feat: filter event galleries by folder"
```

---

### Task 5: Cross-path regression verification and documentation

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/product-jobs.md`
- Modify: `docs/engineering-jobs.md`

**Interfaces:**
- Consumes: all approved Task 1–4 behavior and local test evidence.
- Produces: evidence-backed documentation of admin folder management, mixed-folder ingestion, and
  public filtering; no claim of deployment or customer validation.

- [ ] **Step 1: Run the complete critical-path regression set**

Run:

```bash
make test TESTS="src/backend/picflow/tests src/backend/ingestion/tests src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_results.py"
npm test
npm run test:visual -- --grep "upload|event gallery|manual time"
```

Expected: all selected checks pass. Any failure must be classified against the accepted critical
path; fix only a real changed-path regression and return that diff through the same reviewer.

- [ ] **Step 2: Verify schema and Django configuration**

Run:

```bash
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check
sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
git diff --check
```

Expected: no system issues, no migration drift, and no whitespace errors.

- [ ] **Step 3: Run the repository quality suite**

Run:

```bash
make check
```

Expected: Ruff, mypy, non-slow tests, coverage gate, Django check, and migration check all pass. Do
not run the opt-in exhaustive staging-clone matrix unless a changed contract requires it.

- [ ] **Step 4: Update evidence-backed docs**

Document `EventFolder` as event-scoped catalog metadata, nullable durable assignment through
ingestion, stable public GET filtering, unchanged media authorization, and the exclusion of the mass
editor. Update product/engineering job evidence only to the locally verified state. Do not claim CI,
deployment, staging verification, or customer evidence.

- [ ] **Step 5: Final independent review and root commit**

Prepare the complete branch diff from the design commit through Task 5 for an independent final
review. After approval, rerun `git diff --check` and the narrow documentation-related checks, stage
only Task 5 files, and commit:

```bash
git commit -m "docs: record event folder delivery"
```

- [ ] **Step 6: Handoff**

Report exact commits, test commands and outcomes, any intentionally skipped opt-in checks, and that
CI/deployment/staging remain unverified unless the user separately asks to publish and deploy.
