# Gallery Selfie Control Overlap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the selfie upload button from crowding the native file input at intermediate desktop widths.

**Architecture:** Keep the discovery area's two-column layout intact and add one responsive rule that stacks only the selfie form controls through `1200px`. Lock the behavior with a Playwright geometry assertion at `1072px` while retaining the existing `1440px` side-by-side contract.

**Tech Stack:** Django templates, CSS Grid, Playwright 1.61, pinned Docker visual-test environment.

## Global Constraints

- Do not change the selfie-search form action, fields, labels, or accessibility contract.
- Do not move the manual folder/time search below the selfie column on desktop.
- Keep the existing layout at `1440px` and the existing mobile layout at `390px`.
- Do not update visual baselines unless a baseline viewport changes intentionally.

---

### Task 1: Responsive selfie controls

**Files:**
- Modify: `tests/visual/visual.spec.js:3-4,963-992`
- Modify: `src/backend/static/ui/catalog.css:380-406,760-810`

**Interfaces:**
- Consumes: the production `/__visual__/event/gallery-populated/` fixture and the existing `#selfie-search` form markup.
- Produces: a CSS layout contract in which the file input precedes the submit button vertically at `1072px` and remains beside it at `1440px`.

- [ ] **Step 1: Write the failing intermediate-desktop geometry test**

Add the viewport constant:

```javascript
const INTERMEDIATE_DESKTOP_VIEWPORT = { width: 1072, height: 780 };
```

Extend `desktop discovery keeps upload and time controls aligned without overlap` so it checks the existing horizontal geometry at `DESKTOP_VIEWPORT`, then reloads the populated gallery at `INTERMEDIATE_DESKTOP_VIEWPORT` and asserts:

```javascript
expect(intermediate.selfieFile.bottom).toBeLessThanOrEqual(intermediate.selfieSubmit.top);
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
VISUAL_TEST_IMAGE=photo-prjct-visual-deps:6f2f4690f11ee90c153f2d1f7afa91bd8a3ed014 \
  docker compose -f docker-compose.visual.yml run --rm visual-tests \
  npm run test:visual:inside -- --grep "desktop discovery keeps upload and time controls aligned without overlap"
docker compose -f docker-compose.visual.yml down --volumes --remove-orphans
```

Expected: FAIL because the input and submit button currently have the same `top` value at `1072px`.

- [ ] **Step 3: Add the minimal responsive CSS rule**

Before the existing `@media (max-width: 620px)` block, add:

```css
@media (max-width: 1200px) {
  .event-discovery .selfie-search form {
    grid-template-columns: minmax(0, 1fr);
  }

  .event-discovery .selfie-search form > label,
  .event-discovery .selfie-search form > .selfie-search-error {
    grid-column: auto;
  }
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
VISUAL_TEST_IMAGE=photo-prjct-visual-deps:6f2f4690f11ee90c153f2d1f7afa91bd8a3ed014 \
  docker compose -f docker-compose.visual.yml run --rm visual-tests \
  npm run test:visual:inside -- --grep "desktop discovery keeps upload and time controls aligned without overlap"
docker compose -f docker-compose.visual.yml down --volumes --remove-orphans
```

Expected: PASS at both `1440px` and `1072px`.

- [ ] **Step 5: Verify the visual and backend regression surfaces**

Run:

```bash
npm run test:visual
make test TESTS="src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
git diff --check
```

Inspect `/__visual__/event/gallery-populated/` at `1072x780` and confirm the file input is fully visible above the button, the manual-search column remains to its right, and the document has no horizontal overflow. Existing `1440px` and `390px` snapshots must remain unchanged.

- [ ] **Step 6: Commit the reviewed fix**

```bash
git add tests/visual/visual.spec.js src/backend/static/ui/catalog.css
git commit -m "fix: prevent gallery selfie control overlap"
```
