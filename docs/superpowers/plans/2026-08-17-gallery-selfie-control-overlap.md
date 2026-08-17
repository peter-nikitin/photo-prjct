# Gallery Selfie Control Overlap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the selfie upload button below the native file input at every width, including after device-local search history appears.

**Architecture:** Keep the discovery area's two-column layout intact and make only the selfie form an unconditional single-column grid. Lock the behavior with Playwright geometry assertions both before and after device-local history becomes visible at `1440px`.

**Tech Stack:** Django templates, CSS Grid, Playwright 1.61, pinned Docker visual-test environment.

## Global Constraints

- Do not change the selfie-search form action, fields, labels, or accessibility contract.
- Do not move the manual folder/time search below the selfie column on desktop.
- Keep the discovery columns side by side at `1440px` and keep the existing mobile discovery layout at `390px`.
- Do not update visual baselines unless the rendered layout changes intentionally; keep updates
  limited to snapshots that contain the changed selfie form.

---

### Task 1: Unconditionally stacked selfie controls

**Files:**
- Modify: `tests/visual/visual.spec.js`
- Modify: `src/backend/static/ui/catalog.css`

**Interfaces:**
- Consumes: the production `/__visual__/event/gallery-populated/` fixture and the existing `#selfie-search` form markup.
- Produces: a CSS layout contract in which the file input always precedes the submit button vertically.

- [ ] **Step 1: Write the failing desktop geometry tests**

Change `desktop discovery keeps upload and time controls aligned without overlap` to require vertical
ordering at `DESKTOP_VIEWPORT`:

```javascript
expect(layout.selfieFile.bottom).toBeLessThanOrEqual(layout.selfieSubmit.top);
```

Extend `saved selfie-search history sits beside the form on desktop and below it on mobile` to
capture the file input and submit button after history is visible and assert the same vertical
ordering on desktop.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
VISUAL_TEST_IMAGE=photo-prjct-visual-deps:6f2f4690f11ee90c153f2d1f7afa91bd8a3ed014 \
  docker compose -f docker-compose.visual.yml run --rm visual-tests \
  npm run test:visual:inside -- --grep "desktop discovery keeps upload and time controls aligned without overlap"
docker compose -f docker-compose.visual.yml down --volumes --remove-orphans
```

Expected: FAIL because the input and submit button currently share a row at `1440px`, and overlap
after history narrows the form.

- [ ] **Step 3: Make the selfie form unconditionally single-column**

Replace the wide two-column declaration with:

```css
.event-discovery .selfie-search form {
  grid-template-columns: minmax(0, 1fr);
  max-width: none;
}
```

Keep the label and error in normal grid flow and remove the obsolete `1200px` override.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
VISUAL_TEST_IMAGE=photo-prjct-visual-deps:6f2f4690f11ee90c153f2d1f7afa91bd8a3ed014 \
  docker compose -f docker-compose.visual.yml run --rm visual-tests \
  npm run test:visual:inside -- --grep "desktop discovery keeps upload and time controls aligned without overlap"
docker compose -f docker-compose.visual.yml down --volumes --remove-orphans
```

Expected: PASS before and after history appears at `1440px`.

- [ ] **Step 5: Verify the visual and backend regression surfaces**

Run:

```bash
npm run test:visual
make test TESTS="src/backend/picflow/tests/test_views.py::EventDetailManualTimeFilterTests"
git diff --check
```

Inspect `/__visual__/event/selfie-search/` at `1440x1000` with saved history and confirm the file input is fully visible above the button, history remains beside the form, the manual-search column remains to the right, and the document has no horizontal overflow. Update only snapshots whose selfie form intentionally changes.

- [ ] **Step 6: Commit the reviewed fix**

```bash
git add tests/visual/visual.spec.js src/backend/static/ui/catalog.css
git commit -m "fix: prevent gallery selfie control overlap"
```
