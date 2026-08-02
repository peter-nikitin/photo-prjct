# Selfie Upload Guidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add accurate selfie-selection guidance to the public event search form and deploy it.

**Architecture:** Extend the canonical server-rendered event template only. Protect the copy with the existing event-detail view test and refresh the existing deterministic visual baselines.

**Tech Stack:** Django templates, Django TestCase, Playwright visual snapshots

## Global Constraints

- Preserve the existing search behavior and privacy disclosures.
- Do not claim that clothing affects face matching.
- Limit testing to the changed critical path.

---

### Task 1: Selfie upload guidance

**Files:**
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/visual/visual.spec.js-snapshots/desktop-event-selfie-search.png`
- Modify: `tests/visual/visual.spec.js-snapshots/mobile-event-selfie-search.png`

**Interfaces:**
- Consumes: the existing `event_detail` view and `selfie_search_form` context.
- Produces: explanatory text rendered above the upload form.

- [ ] **Step 1: Add a failing assertion**

Assert that both free and paid event responses contain the approved Russian recommendation.

- [ ] **Step 2: Verify the assertion fails**

Run the focused view test and confirm it fails because the recommendation is absent.

- [ ] **Step 3: Add the recommendation**

Insert the approved text in the existing selfie-search entry block without changing form behavior.

- [ ] **Step 4: Verify behavior and appearance**

Run the focused Django test, refresh only the two selfie-entry snapshots, inspect both images, and rerun their visual test.

- [ ] **Step 5: Deliver**

Review the diff, commit the exact task files once, open a PR, wait for required checks, merge, deploy staging, and verify the public page.
