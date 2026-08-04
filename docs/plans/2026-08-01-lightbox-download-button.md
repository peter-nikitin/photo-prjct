# Compact Lightbox Download Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prominent GLightbox text download link and white description block with the approved compact icon-only action.

**Specification:** `docs/superpowers/specs/2026-08-01-lightbox-download-button-design.md`

**Architecture:** Reuse GLightbox's built-in description slot and the existing download endpoint. Change only server-rendered description markup and container-scoped CSS; no backend or JavaScript contract changes.

**Tech Stack:** Django templates, shared SVG sprite/CSS, GLightbox 3.3.1, Playwright.

**ADR impact:** None — reversible implementation detail. The change conforms to ADR 0021 without changing its authorization or delivery boundary.

## Task 1: Compact icon-only lightbox action

**Deliverable:** Gallery and ready selfie-result lightboxes render the shared download icon in a compact transparent right-aligned description row.

**Files:**

- Modify: `tests/visual/visual.spec.js`
- Modify: `src/backend/templates/catalog/event_detail.html`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/catalog.css`
- Update: affected files under `tests/visual/visual.spec.js-snapshots/`
- Update if snapshot list changes: `.agents/skills/update-visual-design/references/screen-inventory.md`

- [ ] Add a focused Playwright assertion that the open gallery and ready-result lightbox download links have the accessible name, contain the shared icon, and occupy a compact 44-pixel action without visible text.
- [ ] Run the focused visual test and confirm it fails because current markup is text-only and the description background remains prominent.
- [ ] Add the shared SVG icon and accessible attributes to both `data-description` fragments; scope CSS to make GLightbox description wrappers transparent, compact, and right-aligned while reusing subdued gallery-action states.
- [ ] Run the focused test and confirm it passes.
- [ ] Run `npm run test:visual:update`, inspect every changed expected image, then run `npm run test:visual` and `npm run test:js`.
- [ ] Run focused Django template/view tests plus `ruff format --check .`, `ruff check .`, `mypy`, Django system checks, migration drift, and the required pytest suite.
- [ ] Reconcile architecture and ADRs: confirm no architecture or ADR text changes are required because URLs, authorization, storage delivery, and runtime topology are unchanged.

## Rollout and rollback

No configuration, migration, or deployment ordering change. Roll back the template/CSS/snapshot commit to restore the previous GLightbox description styling.
