---
name: update-visual-design
description: Use when changing FindMe Photo Django templates, CSS, SVG icons, responsive layouts, Playwright visual snapshots, or visual concepts for features that are not implemented yet.
---

# Update Visual Design

Keep one visual source of truth while distinguishing working product UI from future concepts.

## Workflow

1. Read `references/screen-inventory.md`.
2. Inspect the relevant URLconf and view before editing. Classify the screen as `production` only when working backend behavior and a production URL exist; otherwise classify it as `design-reference`.
3. For a production screen, edit its canonical template under `src/backend/templates/` and shared assets under `src/backend/static/ui/` directly. Preserve server-rendered data, URL names, accessibility, and existing backend contracts.
4. For an unfinished screen, edit or add a static Django reference under `tests/visual/templates/design_reference/`, with deterministic context in `tests/visual/views.py` and reference-only assets in `tests/visual/static/`.
5. Update the inventory whenever a path, status, URL, or snapshot changes.
6. Run `npm run test:visual:update` only for intentional visual changes. It uses the pinned Docker visual-test environment. Inspect every changed expected/diff image, then run `npm run test:visual` without update mode.
7. Run focused Django tests, repository contracts, and the full required checks before completion.

## Non-negotiable boundaries

- Never create `src/proto`, a replacement prototype/archive directory, or standalone HTML outside Django templates.
- Never add fake business logic, localStorage, fake APIs, or demo mutations to preserve a concept.
- Never expose a production URL, navigation action, or enabled control for unfinished behavior.
- Never copy the production design system into the reference gallery; reference templates load `src/backend/static/ui/design-system.css`.
- Never accept snapshot changes without visual inspection.

Moving a concept “outside the Docker image” is not enough. The earlier baseline proposed `src/proto/promotions/`; that recreates a second source of truth even if it is not shipped. Future UI belongs only in the test-only gallery.

## Promoting a screen

When backend behavior becomes real:

1. Move the design into canonical backend templates/static and connect the existing production view.
2. Remove the corresponding reference template, fixture route/context, reference-only CSS, and obsolete snapshots.
3. Change the inventory status to `production` and record the production URL and replacement snapshots.
4. Verify that `config.settings`, `config.urls`, and the Docker image still exclude `tests/visual`.
