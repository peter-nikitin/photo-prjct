# Gallery Image Type Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GLightbox render extensionless private-media URLs as images instead of external iframe content.

**Architecture:** Preserve the existing `GalleryPhoto` media boundary and stable same-origin URLs. Add an explicit GLightbox media-type hint to the server-rendered gallery link; do not change routing, storage resolution, response headers, or gallery JavaScript.

**Tech Stack:** Django templates, Django TestCase, GLightbox 3.3.1

**Working directory:** Run every command from `/Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/fix-gallery-image-type`.

---

## Chunk 1: Extensionless gallery media

### Task 1: Declare the large preview as an image

**Files:**
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/templates/catalog/event_detail.html`

- [x] **Step 1: Write the failing regression assertion**

Extend `GalleryPageTests.test_event_detail_gallery_markup_and_loading_policy` so every rendered gallery anchor must contain `data-type="image"` together with its extensionless `preview-large` URL.

- [x] **Step 2: Run the focused Django test and verify RED**

Run:

```bash
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=127.0.0.1 DB_PORT=55432 SECRET_KEY=test \
  /Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest \
  src/backend/picflow/tests/test_views.py::GalleryPageTests::test_event_detail_gallery_markup_and_loading_policy -q
```

Expected: FAIL because the gallery anchor lacks `data-type="image"`.

- [x] **Step 3: Add the explicit GLightbox type hint**

Add `data-type="image"` to `.gallery-card-link.glightbox` in `event_detail.html`.

- [x] **Step 4: Run focused verification and verify GREEN**

Run the focused Django test above, followed by:

```bash
npm run test:js
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff format --check \
  src/backend/picflow/tests/test_views.py
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff check \
  src/backend/picflow/tests/test_views.py
```

Expected: all commands exit successfully.

- [x] **Step 5: Commit the minimal fix**

```bash
git add docs/superpowers/plans/2026-07-19-gallery-image-type-fix.md \
  src/backend/picflow/tests/test_views.py \
  src/backend/templates/catalog/event_detail.html
git commit -m "fix: render gallery media as images"
```
