# Compact Event Gallery Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make event-gallery discovery compact and stable: allow either time boundary independently, remove the post-submit scroll jump, show event-local photo times, and collapse the whole discovery block on filtered mobile pages.

**Architecture:** Keep filtering server-rendered and event-scoped. Parse each optional time boundary independently into UTC, pass nullable bounds through the existing gallery query, and preserve only active parameters in pagination. Render one native outer `<details>` for the discovery area, with server-selected mobile state and a tiny desktop initializer; retain normal GET navigation and pagination anchors. Keep capture-time localization in the immutable gallery presentation factory.

**Tech Stack:** Django forms/views/templates, Python `zoneinfo`, existing gallery JavaScript, CSS, Node tests, Playwright visual snapshots in the pinned Docker environment.

## Global Constraints

- `from` only means `capture_time >= from - 10 minutes`; `to` only means `capture_time <= to + 10 minutes`; both means the existing tolerant bounded interval; neither means no time predicate.
- Photos without a comparable persisted `capture_time` do not match an active one-sided or two-sided time filter.
- Malformed, repeated, outside-event, DST-invalid, and inverted inputs remain invalid.
- Active folder and time predicates combine with `AND`; pagination preserves only non-empty active fields.
- The manual search form submits without `#gallery`; pagination keeps `#gallery`.
- Photo time is plain, non-clickable `HH:MM` in `event.timezone_name`, immediately beside download, with no placeholder when missing.
- Remove cover and long description only from event detail. Preserve catalog cards and approved selfie/privacy wording.
- Desktop: one-line event header and two-column discovery; folder chips occupy their own row, time controls their own row. Mobile: the entire discovery block uses one native disclosure, open initially when unfiltered and closed with summary `Фильтры применены` when a valid filter is active; reset remains available while closed.
- Do not change metadata extraction, backfills, gallery eligibility, media delivery, lightbox content, or selfie-search behavior. Add no dependency, migration, compatibility layer, or custom disclosure widget.
- Implementer and reviewer agents must not modify Git index/history/remotes. The root controller creates the final commit after independent review and fresh verification.

---

### Task 1: Make Browser-Submitted Blank Time Inputs Inactive — Completed

**Files:** `src/backend/picflow/forms.py`, `src/backend/picflow/tests/test_gallery.py`, `src/backend/picflow/tests/test_views.py`

- [x] Blank `from=&to=` does not request a time filter, while repeated mixed blank/nonblank values remain invalid.
- [x] Folder-only filtering works when the browser submits both empty time fields.
- [x] Independent review passed and root verification completed; commit `08f676b`.

---

### Task 2: Deliver Compact, Stable Gallery Discovery

**Files:**
- Modify: `src/backend/picflow/forms.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/picflow/gallery.py`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/static/ui/event-gallery.js`
- Modify: `src/backend/picflow/tests/test_gallery.py`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `tests/js/event-gallery.test.js`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`
- Modify: `.agents/skills/update-visual-design/references/screen-inventory.md`
- Update: affected event-detail snapshots under `tests/visual/visual.spec.js-snapshots/`

**Interfaces:**
- `EventGalleryTimeFilterForm.utc_bounds -> tuple[datetime | None, datetime | None] | None`.
- `GalleryPhoto.capture_time_display -> str | None`.
- Event detail exposes the existing valid-filter state to native discovery markup; the JS initializer only reconciles desktop presentation.

- [ ] **Step 1: Add failing form and view tests for both open-ended ranges**

Add form cases for start-only and end-only tolerant UTC bounds, and retain coverage for both empty, bounded, inverted, malformed, repeated, event-boundary, and DST-invalid submissions. Update the old only-`to` invalid expectation: it is now valid.

Add view cases proving:

- start-only includes known capture times after its lower tolerant bound and excludes earlier or missing times;
- end-only includes known capture times before its upper tolerant bound and excludes later or missing times;
- folder plus either one-sided boundary combines with `AND`;
- pagination query pairs include only non-empty `from`, `to`, and folder values;
- invalid UI coverage uses a genuinely malformed request rather than a missing boundary.

Run and confirm RED:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::EventGalleryTimeFilterFormTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
```

- [ ] **Step 2: Implement nullable time bounds end to end**

In the form, parse each supplied scalar independently. Only compare the values when both exist. Produce `(start - 10 minutes if present else None, end + 10 minutes if present else None)` and remove any behavior that substitutes the event end for a missing upper boundary.

Keep repeated-parameter and timezone/event-range validation. In the view, append only non-empty filter fields to pagination query pairs. Pass nullable bounds through the existing gallery page function, which already supports optional lower and upper predicates.

Rerun the Step 1 suite and confirm GREEN.

- [ ] **Step 3: Add failing capture-time presentation tests**

Extend `GalleryPresentationContractTests` to require `capture_time_display`: a known UTC value becomes zero-padded event-local `HH:MM`, and a missing value becomes `None`. Extend gallery template assertions so one known photo renders exactly one `.gallery-card-time` inside `.gallery-card-download`, while missing values render no empty time element.

Run and confirm RED:

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::GalleryPageTests::test_event_detail_gallery_markup_and_loading_policy"
```

- [ ] **Step 4: Implement photo-time presentation**

Add optional `capture_time_display` to frozen `GalleryPhoto`. In `GalleryPhotoFactory`, convert `Photo.capture_time` with `ZoneInfo(photo.event.timezone_name)` and format `%H:%M`; return `None` when absent. Group the optional `<time class="gallery-card-time">` and existing download link in `.gallery-card-download`, with small muted typography and no click behavior.

Rerun the Step 3 suite and confirm GREEN.

- [ ] **Step 5: Add failing interaction and markup tests for stable compact discovery**

Cover these contracts in Django/Node/Playwright tests as appropriate:

- manual form action contains no fragment, while pagination links still end in `#gallery`;
- submitting the manual form with JavaScript enabled leaves the final URL without a fragment, keeps `document.activeElement` as `BODY`, and leaves `scrollY` at zero after at least 1.2 seconds;
- event detail omits cover and long description, and renders one compact title/metadata line;
- one outer native disclosure wraps both selfie and manual discovery sections;
- unfiltered server markup is open; valid active folder/time markup is closed and says `Фильтры применены`; reset is outside the closed body;
- the desktop initializer opens the discovery disclosure using `matchMedia`, without focus or scrolling calls;
- no-JS GET filtering remains usable through native disclosure behavior.

Use malformed time input for the canonical manual-invalid visual fixture. Do not use blank or one-sided input as an invalid state.

Run the focused Django and Node tests and confirm RED:

```bash
make test TESTS="src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests src/backend/picflow/tests/test_views.py::GalleryPageTests"
node --test tests/js/event-gallery.test.js
```

- [ ] **Step 6: Implement the compact header and responsive discovery layout**

In `event_detail.html`:

- replace the detail hero with a minimal horizontal header containing title and concise metadata;
- omit event cover and long description from this page;
- wrap selfie and manual search in one native `<details data-event-discovery>` with a truthful summary and server-rendered `open` only for the unfiltered state;
- render reset outside the disclosure body when filters are active;
- keep approved selfie/privacy copy, moving detailed guidance into a nested native disclosure;
- keep folders on a dedicated row and time inputs/button on a dedicated row;
- remove `#gallery` from the manual form action only.

In CSS, make desktop discovery a compact two-column grid and the event header a single thin line. On mobile, keep the entire discovery area collapsible and minimize vertical spacing. Avoid visual techniques that expose content while native disclosure semantics remain closed.

In `event-gallery.js`, add only a progressive initializer that opens the outer discovery details on desktop via the existing breakpoint media query. It must not call `focus`, `scrollIntoView`, or mutate the URL. Leave pagination anchors unchanged.

Rerun the Step 5 suites and confirm GREEN.

- [ ] **Step 7: Update deterministic visual fixtures and snapshots**

Give the populated fixture two known event-local labels (`10:07`, `10:43`) and one missing value. Make the manual-invalid fixture genuinely malformed. Update the screen inventory for the compact desktop/mobile states.

Regenerate pinned snapshots:

```bash
npm run test:visual:update
```

Inspect every changed event-detail PNG at desktop and 390px. Confirm the compact one-line header, stable two-column desktop layout, whole-block mobile disclosure, visible reset in filtered mobile state, aligned muted times, and absence of unrelated page changes. Revert any unrelated snapshot churn, then run:

```bash
npm run test:visual
```

- [ ] **Step 8: Run focused regression and repository checks**

```bash
make test TESTS="src/backend/picflow/tests/test_gallery.py::EventGalleryTimeFilterFormTests src/backend/picflow/tests/test_gallery.py::GalleryPresentationContractTests src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests src/backend/picflow/tests/test_views.py::GalleryPageTests"
node --test tests/js/event-gallery.test.js
make test TESTS="tests/test_repository_foundation.py tests/test_visual_reference.py"
git diff --check
```

The implementer self-reviews the complete unstaged diff, checks that no cover/catalog/selfie behavior escaped scope, records exact test and snapshot evidence, and leaves Git untouched.

- [ ] **Step 9: Independent review, fixes, and root verification**

The root controller prepares one complete working-tree review package including untracked task files. An independent reviewer checks both the approved design and repository standards. Blocking fixes return to the same implementer; re-review returns to the same reviewer.

After approval, the root controller reruns Step 8 plus the pinned visual suite, stages only approved Task 2 files and snapshots, and creates one consolidated Task 2 commit.
