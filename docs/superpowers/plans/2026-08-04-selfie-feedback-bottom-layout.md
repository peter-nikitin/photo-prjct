# Selfie Feedback Bottom Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep selfie-search results full width and render terminal feedback immediately in a bottom 50/50 action-and-form region.

**Architecture:** Change only the production result template, its focused CSS/JavaScript, and the existing visual/test fixtures. Remove the obsolete disclosure and browser opt-out paths instead of retaining compatibility behavior.

**Tech Stack:** Django templates, vanilla JavaScript, CSS Grid, Django TestCase, Playwright visual snapshots.

## Global Constraints

- Desktop bottom region is equal 50/50 columns: new-search action left, expanded feedback right.
- Mobile order is new-search action first, feedback second, both full width.
- Gallery width never changes when feedback marking is active.
- Remove open, close, and opt-out controls and all dedicated JavaScript/localStorage behavior.
- Preserve result labels, consent, submission, cleanup, and unavailable behavior.
- Use only focused Django/JavaScript checks and the affected desktop/mobile visual snapshots.

---

### Task 1: Replace the feedback disclosure UI with the bottom layout

**Files:**
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.css`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/visual.spec.js`
- Modify: affected files under `tests/visual/visual.spec.js-snapshots/`
- Modify only if its descriptive note changes: `.agents/skills/update-visual-design/references/screen-inventory.md`

**Interfaces:**
- Consumes: existing `feedback`, `feedback_submitted`, result-label data attributes, and feedback submission endpoint.
- Produces: `.selfie-search-terminal-actions`, an always-visible `[data-feedback-form]`, and unchanged label/submission payloads.

- [ ] **Step 1: Add minimal failing markup and JavaScript assertions**

  Update focused tests so terminal feedback expects one `.selfie-search-terminal-actions` container,
  the new-search link before the feedback content, a visible form without `data-feedback-open`,
  `data-feedback-close`, or `data-feedback-opt-out`, and no `findme_selfie_feedback_prompt` storage
  access. Keep existing assertions for result labels and submission.

- [ ] **Step 2: Run the focused assertions and confirm the expected failure**

  Run the narrow Django feedback-view tests and the narrow JavaScript feedback tests selected by
  their existing test names. The failure must be caused by the old disclosure/opt-out markup or
  controller behavior.

- [ ] **Step 3: Implement the minimal template, CSS, and JavaScript change**

  Move the terminal new-search action and all feedback/unavailable/confirmation states into one
  bottom container. Render eligible feedback forms without `hidden`; keep result-label card controls
  visible whenever feedback is eligible. Delete the open/close/opt-out DOM nodes, controller fields,
  event handlers, storage constant, and branching. Use CSS Grid with `grid-template-columns:
  minmax(0, 1fr) minmax(0, 1fr)` and collapse it to one column at the existing mobile breakpoint.

- [ ] **Step 4: Run focused functional checks**

  Re-run only the touched Django feedback-view tests and focused JavaScript feedback tests. Run
  `git diff --check`.

- [ ] **Step 5: Update and inspect affected visual baselines**

  Run the existing visual update command narrowed to the feedback problem and feedback marking
  scenarios when supported; otherwise update the visual suite and retain only affected snapshots.
  Inspect every changed desktop/mobile image, then run the same focused scenarios without update.
  Confirm full-width gallery cards and desktop 50/50/mobile stacked terminal actions.

- [ ] **Step 6: Self-review**

  Search for obsolete selectors, copy, and `findme_selfie_feedback_prompt`; confirm no production or
  test path remains. Report changed files, exact commands/results, snapshot inspection, and concerns.

