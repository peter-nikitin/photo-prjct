# Optional Gallery Time and Photo Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow folder-only gallery filtering when time inputs are blank and show each known event-local photo time beside download.

**Architecture:** Normalize the existing time form so browser-submitted blank values mean “no time predicate,” leaving folder validation and queryset composition unchanged. Extend the immutable gallery presentation object with one optional, preformatted event-local time string, then render it in the production card action row with a small CSS grouping and deterministic visual fixtures.

**Tech Stack:** Django forms/views/templates, Python `zoneinfo`, Django `SimpleTestCase`/`TestCase`, CSS, Playwright snapshots in the pinned Docker environment.

## Global Constraints

- Empty `from` and `to` values mean no time filter; an end value without a start value remains invalid.
- Active folder and time predicates continue to combine with `AND`.
- Display capture time as zero-padded 24-hour `HH:MM` in `event.timezone_name`.
- Render no placeholder and no empty time element when `Photo.capture_time` is absent.
- Place the time immediately before download using the existing muted color token and a smaller font.
- Do not change metadata extraction, backfills, folder choices, gallery eligibility, pagination mechanics, media delivery, lightbox content, or selfie search.
- Do not add dependencies, migrations, JavaScript behavior, compatibility paths, or speculative abstractions.
- Implementer and reviewer agents must not modify Git index/history/remotes. The root controller creates one final commit per approved task after review and verification.

---

### Task 1: Make Blank Time Inputs Inactive

**Files:**
- Modify: `src/backend/picflow/forms.py:44-69`
- Test: `src/backend/picflow/tests/test_gallery.py:397-430`
- Test: `src/backend/picflow/tests/test_views.py:1070-1160`

**Interfaces:**
- Consumes: `EventGalleryTimeFilterForm(event: Event, data: QueryDict)` and the existing independent `EventGalleryFolderFilterForm`.
- Produces: `EventGalleryTimeFilterForm.is_requested: bool`, true only when `from` or `to` has a non-empty scalar submission; `utc_bounds` remains `None` when inactive.

- [ ] **Step 1: Add a failing form regression test for browser-submitted blanks**

Add to `EventGalleryTimeFilterFormTests`:

```python
def test_blank_browser_values_do_not_request_a_time_filter(self) -> None:
    form = self.form(self.make_event(), "from=&to=")

    self.assertFalse(form.is_requested)
    self.assertTrue(form.is_valid())
    self.assertIsNone(form.utc_bounds)
```

Extend `test_rejects_repeated_or_malformed_scalar_datetime_values` with both mixed blank/non-blank
orders so request detection cannot bypass the existing repeated-parameter rejection:

```python
"from=&from=2026-06-10T12:00",
"from=2026-06-10T12:00&from=",
```

- [ ] **Step 2: Add a failing event-page regression test for folder-only filtering**

Add to `EventDetailManualTimeFilterTests`:

```python
def test_folder_filter_works_when_browser_submits_blank_time_fields(self) -> None:
    folder = EventFolder.objects.create(event=self.event, name="Старт")
    included = self.photo("folder-only", filename="a.jpg")
    self.photo("folder-only-unfiled", filename="b.jpg")
    Photo.objects.filter(pk=included.pk).update(folder=folder)

    response = self.client.get(
        reverse("event_detail", kwargs={"slug": self.event.slug}),
        {"folder": str(folder.pk), "from": "", "to": ""},
    )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(
        [item.photo_id for item in response.context["gallery_photos"]],
        [included.pk],
    )
    self.assertFalse(response.context["manual_time_filter_form"].is_requested)
    self.assertFalse(response.context["manual_time_filter_invalid"])
    self.assertEqual(
        response.context["gallery_pagination_query_pairs"],
        (("folder", str(folder.pk)),),
    )
```

- [ ] **Step 3: Run the focused tests and confirm the regression**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::EventGalleryTimeFilterFormTests::test_blank_browser_values_do_not_request_a_time_filter src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests::test_folder_filter_works_when_browser_submits_blank_time_fields"
```

Expected: both tests fail because `from`/`to` key presence currently sets `is_requested=True`, causing “Укажите время начала.” and suppressing the gallery.

- [ ] **Step 4: Normalize request detection in the form**

In `EventGalleryTimeFilterForm.__init__`, replace key-presence detection with value-aware detection
across every submitted value while keeping repeated-value validation unchanged:

```python
self.is_requested = bool(
    data
    and any(
        value
        for name in ("from", "to")
        for value in (
            data.getlist(name)
            if hasattr(data, "getlist")
            else (data.get(name),)
        )
    )
)
```

Do not change `clean()`: only-to requests must still reach its existing missing-start validation, while two blank values now return through the existing inactive branch.

- [ ] **Step 5: Run the form and view filter suites**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::EventGalleryTimeFilterFormTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
```

Expected: PASS, including existing bounded, start-only, only-to, malformed, repeated, DST, folder-plus-time, pagination, and invalid-state cases.

- [ ] **Step 6: Self-review and prepare the review package**

Run:

```bash
git diff --check
git diff -- src/backend/picflow/forms.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py
```

Confirm there is no template/JavaScript workaround, empty time parameters are not preserved, and folder-only behavior is the only changed contract. Leave all files unstaged for the root controller's independent review.

- [ ] **Step 7: Root controller review gate and commit**

After reviewer approval, the root controller reruns Step 5, stages only the three Task 1 files, and commits:

```bash
git add src/backend/picflow/forms.py src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py
git commit -m "Allow gallery folder filters without time"
```

---

### Task 2: Show Event-Local Capture Time Beside Download

**Files:**
- Modify: `src/backend/picflow/gallery.py:108-158`
- Modify: `src/backend/templates/catalog/event_detail.html:140-150`
- Modify: `src/backend/static/ui/catalog.css:492-498,675-698`
- Modify: `src/backend/picflow/tests/test_gallery.py:33-125`
- Modify: `src/backend/picflow/tests/test_views.py:670-725`
- Modify: `tests/visual/views.py:116-123,230-280`
- Modify: `.agents/skills/update-visual-design/references/screen-inventory.md`
- Update: `tests/visual/visual.spec.js-snapshots/desktop-event-gallery-populated.png`
- Update: `tests/visual/visual.spec.js-snapshots/mobile-event-gallery-populated.png`

**Interfaces:**
- Consumes: persisted `Photo.capture_time: datetime | None` and `photo.event.timezone_name: str`.
- Produces: immutable `GalleryPhoto.capture_time_display: str | None`; canonical event-card markup `.gallery-card-download` containing optional `.gallery-card-time` followed by `.gallery-download`.

- [ ] **Step 1: Add failing presentation tests for localized and missing times**

Extend `GalleryPresentationContractTests` so the direct `GalleryPhoto` value includes and asserts `capture_time_display="11:03"`. Add a factory test:

```python
@patch("boto3.client")
def test_factory_formats_known_capture_time_in_the_event_timezone(self, boto3_client) -> None:
    event = Event(
        name="City Run",
        slug="city-run",
        timezone_name="Europe/London",
    )
    photo = Photo(
        id="photo-42",
        event=event,
        capture_time=datetime(2026, 6, 10, 10, 3, tzinfo=UTC),
    )

    gallery_photo = GalleryPhotoFactory.from_photo(photo=photo, event_slug=event.slug)

    self.assertEqual(gallery_photo.capture_time_display, "11:03")
    boto3_client.assert_not_called()
```

In the existing factory test whose photo has no capture time, add:

```python
self.assertIsNone(gallery_photo.capture_time_display)
```

- [ ] **Step 2: Add failing production-template assertions**

In `GalleryPageTests.test_event_detail_gallery_markup_and_loading_policy`, give one eligible photo a known `capture_time`, then assert the rendered markup includes:

```python
self.assertContains(response, '<div class="gallery-card-download">')
self.assertContains(response, '<time class="gallery-card-time">11:03</time>')
```

Keep at least one photo without capture time and assert the number of `gallery-card-time` elements equals the number of known values, proving no placeholder or empty element is rendered.

- [ ] **Step 3: Run focused tests and confirm the missing contract**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::GalleryPageTests::test_event_detail_gallery_markup_and_loading_policy"
```

Expected: FAIL because `GalleryPhoto` has no capture-time display field and the template has no grouped time/download markup.

- [ ] **Step 4: Extend the immutable gallery presentation object**

Import `ZoneInfo` in `src/backend/picflow/gallery.py`, add the optional field after `faces`, and populate it in the factory:

```python
@dataclass(frozen=True)
class GalleryPhoto:
    # existing fields unchanged
    faces: tuple[GalleryFaceCrop, ...] = ()
    capture_time_display: str | None = None
```

```python
capture_time_display=(
    photo.capture_time.astimezone(ZoneInfo(photo.event.timezone_name)).strftime("%H:%M")
    if photo.capture_time is not None
    else None
),
```

Keep conversion in the factory so templates receive presentation-ready text and do not own timezone logic.

- [ ] **Step 5: Render and style the right-hand action group**

Replace the standalone download link in `event_detail.html` with:

```django
<div class="gallery-card-download">
  {% if photo.capture_time_display %}<time class="gallery-card-time">{{ photo.capture_time_display }}</time>{% endif %}
  <a class="gallery-download" href="{{ photo.download_url }}" aria-label="Скачать оригинал" title="Скачать оригинал">
    <svg class="icon" aria-hidden="true"><use href="{{ ui_icons }}#download"></use></svg>
  </a>
</div>
```

In `catalog.css`, replace the direct-child auto-margin rule with the group layout and add the muted label:

```css
.gallery-card-download {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
}

.gallery-card-time {
  color: var(--muted);
  font-size: 0.78rem;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  white-space: nowrap;
}
```

Retain the existing 44px download target, hover/focus behavior, icon sizing, and left-side face controls.

- [ ] **Step 6: Run focused Django tests**

Run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::GalleryPageTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
```

Expected: PASS with localized known time, absent unknown time, unchanged gallery authorization/filtering, and valid markup.

- [ ] **Step 7: Update deterministic visual fixtures and inventory**

Add `capture_time_display: str | None = None` to `FixtureGalleryPhoto` and an optional keyword argument to `_gallery_photo`. Give the second and third cards on `GALLERY_FACE_PHOTOS` values `"10:07"` and `"10:43"`; leave the first card `None` so one screenshot proves both present and absent states.

Append a dated inventory note:

```markdown
On 2026-08-17, the populated production event-gallery fixture added optional event-local photo
times beside download. The existing desktop and 390px mobile populated snapshots cover known and
missing capture-time states without adding a new route.
```

- [ ] **Step 8: Update and inspect intentional visual baselines**

Run:

```bash
npm run test:visual:update
```

Inspect every changed PNG, specifically the populated desktop and mobile gallery snapshots, and verify: `10:07`/`10:43` sit immediately before download; the first card has no gap or placeholder; face controls stay left-aligned; 44px download targets and card widths are unchanged; text does not wrap or crowd at 390px. Reject and fix any unrelated snapshot change.

- [ ] **Step 9: Verify the pinned visual suite**

Run:

```bash
npm run test:visual
```

Expected: PASS without diff images. Confirm `git status --short` lists only the two intended populated snapshots, not unrelated visual baselines.

- [ ] **Step 10: Run repository contracts and self-review**

Run:

```bash
make test TESTS="tests/test_repository_foundation.py tests/test_visual_reference.py src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::GalleryPageTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
git diff --check
git diff --stat
```

Inspect the complete Task 2 diff. Confirm the template remains the canonical production screen, the inventory is current, missing metadata renders nothing, and no lightbox/selfie-result behavior changed. Leave changes unstaged for the root controller's independent review.

- [ ] **Step 11: Root controller review gate and commit**

After reviewer approval, the root controller reruns Steps 6, 9, and 10, stages exactly the Task 2 files and intended snapshots, and commits:

```bash
git add src/backend/picflow/gallery.py src/backend/templates/catalog/event_detail.html src/backend/static/ui/catalog.css src/backend/picflow/tests/test_gallery.py src/backend/picflow/tests/test_views.py tests/visual/views.py .agents/skills/update-visual-design/references/screen-inventory.md tests/visual/visual.spec.js-snapshots/desktop-event-gallery-populated.png tests/visual/visual.spec.js-snapshots/mobile-event-gallery-populated.png
git commit -m "Show capture time in event gallery"
```

---

## Final Verification

After both task commits, the root controller must use `superpowers:verification-before-completion` and run:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::EventGalleryTimeFilterFormTests src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests src/backend/picflow/tests/test_views.py::GalleryPageTests tests/test_repository_foundation.py tests/test_visual_reference.py"
npm run test:visual
git diff origin/main...HEAD --check
git status --short --branch
```

Expected: all focused Django/repository tests and the full pinned visual suite pass; no diff errors; the branch contains the approved design commit plus one reviewed commit per implementation task and has a clean working tree.
