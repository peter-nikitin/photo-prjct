# Selfie Result Full Width Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make selfie-search results use the shared 1180 px page width and remove the customer-facing cleanup retry action.

**Architecture:** Delete the retry control and its JavaScript state while retaining automatic local cleanup and error reporting. Let the shared page shell own result width, then refresh only affected visual contracts.

**Tech Stack:** Django templates, vanilla JavaScript, CSS, Node test runner, Playwright.

## Global Constraints

- Remove the cleanup retry button and all dedicated JavaScript behavior without compatibility code.
- Preserve automatic local selfie deletion and cleanup error reporting.
- Remove only the result-page 900 px maximum; keep the event search-entry maximum unchanged.
- Use minimal focused functional and affected visual tests.

---

### Task 1: Remove cleanup retry UI and widen results

**Files:**
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.css`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/js/selfie-search.test.js`
- Modify: `tests/visual/visual.spec.js`
- Modify: affected `tests/visual/visual.spec.js-snapshots/` files

**Interfaces:**
- Consumes: existing submitted-feedback cleanup controller and shared `.page-shell` width.
- Produces: no `[data-feedback-cleanup-retry]` contract and a result container governed by `--content-width: 1180px`.

- [ ] Add failing focused assertions that the retry control is absent and desktop result width matches the shared gallery width.
- [ ] Run those assertions and confirm failure against the current retry/900 px implementation.
- [ ] Delete retry markup, controller lookup/handler/state changes, and the result max-width rule.
- [ ] Run focused Node and Django assertions plus `git diff --check`.
- [ ] Update only affected desktop/mobile selfie-result snapshots, inspect each, and rerun visual tests.
- [ ] Search production code for obsolete retry selector/copy and write the task report.

