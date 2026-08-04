# Gallery Page Jump Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one reusable page-number jump form to every server-paginated photo gallery.

**Architecture:** Extract the duplicated gallery pagination markup into one Django include. Pass each screen's `Page` object and accessible navigation label into the include; use a native GET number form so existing view validation handles navigation without JavaScript.

**Tech Stack:** Django templates, HTML, existing `catalog.css`, Django `TestCase`.

## Global Constraints

- Work only in `/Users/petrnikitin/Documents/Projects/photo-prjct/.worktrees/gallery-page-jump`.
- Keep the change minimal and UI-focused; do not change pagination backend logic, page sizes, ordering, authorization, or JavaScript.
- Cover event galleries and ready selfie-search results through one shared template partial.
- Do not modify the photographer upload queue.
- Use test-first development and run only focused tests.
- Leave all changes unstaged. Do not run Git index, commit, branch, tag, push, or PR commands.

---

### Task 1: Shared gallery page jump

**Files:**
- Create: `src/backend/templates/ui/gallery_pagination.html`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/catalog.css`
- Test: `src/backend/picflow/tests/test_views.py`
- Test: `src/backend/selfie_search/tests/test_views.py`

**Interfaces:**
- Consumes: a Django `Page` object as `pagination_page` and a Russian accessible navigation label as `pagination_label`.
- Produces: shared previous/status/page-jump/next markup; the GET form submits a numeric field named `page`.

- [ ] **Step 1: Add failing focused assertions**

  In each existing numbered-pagination test, assert the multi-page response contains a GET form, `name="page"`, `type="number"`, `min="1"`, the correct `max`, the current page value, and `Перейти`. Keep assertions behavior-oriented and do not add a new large fixture.

- [ ] **Step 2: Verify RED**

  Run the two exact existing test methods that cover numbered event and selfie-result pagination. Confirm failure is caused by the missing page-jump form.

- [ ] **Step 3: Implement the shared partial**

  Create the shared partial with the existing previous/status/next behavior and a compact GET form. Replace both duplicated `<nav class="gallery-pagination">` blocks with includes that pass the correct page object and navigation label. Preserve the condition that pagination is absent for a single page or a non-ready result.

- [ ] **Step 4: Style the native form**

  Extend the existing `.gallery-pagination` styles for wrapping, the compact number field, and button. Reuse existing colors, radii, font weights, and 44px touch-target conventions. Add only the responsive behavior needed to avoid overflow.

- [ ] **Step 5: Verify GREEN**

  Re-run the two focused test methods, then run the containing pagination test classes or files if exact selectors differ. Run `manage.py check` with the repository's configured environment and `git diff --check`.

- [ ] **Step 6: Self-review and report**

  Inspect the full unstaged diff for scope, template accessibility, query behavior, and mobile overflow. Write the implementation report to `/tmp/gallery-page-jump-luna-report.md`, including RED and GREEN commands/output and the changed-file list. Do not stage or commit.
