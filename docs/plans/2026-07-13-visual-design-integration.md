# Visual Design Integration Implementation Plan

- Date: 2026-07-13
- Status: Draft
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented)
- Related ADRs: `none`

## Goal

Make Django templates and static assets the canonical production visual source, preserve unfinished
screen designs in a test-only Playwright gallery, remove `src/proto`, and teach future agents to
update the visual layer without recreating a prototype archive or exposing unfinished routes.

## Scope

### In scope

- Production catalog, event detail, and legal page redesign using the visual language in `src/proto`.
- Local SVG icons, responsive/accessibility behavior, and removal of external UI CDNs.
- Test-only design-reference templates for search, dashboard, event management, upload, orders,
  promotions, and purchased photos.
- Chromium screenshot regression tests for deterministic desktop and mobile states.
- Project workflow skill, repository contracts, architecture guidance, and CI integration.
- Removal of all legacy prototype templates, scripts, assets, and `src/proto`.

### Out of scope

- New product behavior, production routes, models, migrations, APIs, authentication, search,
  ingestion, commerce, or changes to media storage.
- Customizing Django Admin beyond retaining the existing working link.
- Shipping test-only design references in the production image.

## Acceptance criteria

- `/`, `/events/<slug>/`, `/legal/`, and Django Admin keep their current production contracts.
- Draft events remain unavailable and all removed demo routes return 404.
- Public navigation contains no unfinished actions or links and loads no external icon CDN.
- Production templates use `FindMe Photo`, server-rendered `Event` data, responsive shared CSS, and
  a local Lucide SVG sprite.
- Future designs remain editable Django templates under `tests/visual` and are reachable only from
  the test settings/URLconf at `/__visual__/`.
- `src/proto`, duplicate top-level UI templates, demo JavaScript, and duplicate demo assets are gone.
- Playwright validates every production/reference desktop state plus mobile production, search, and
  upload states; it also catches console errors, failed resources, overflow, and broken live links.
- `$update-visual-design`, `AGENTS.md`, architecture, and repository tests prevent proto from returning.
- Required Python, Django, Playwright, and Docker checks pass.

## Implementation

### Task 1: Lock down routes and repository structure

**Files:** this plan, Django view tests, repository foundation tests.

- [ ] Add failing assertions for canonical UI paths, forbidden prototype paths, skill structure,
  unavailable demo routes, production URL isolation, navigation, and external CDN removal.
- [ ] Run targeted tests and record the expected RED result before implementation.

### Task 2: Deliver the production visual system

**Files:** shared UI templates, catalog/legal templates, UI CSS, local icon sprite, notices.

- [ ] Create a shared accessible shell with `FindMe Photo`, skip link, focus states, responsive
  navigation, and only catalog/legal/Admin destinations.
- [ ] Adapt catalog, empty state, event detail, missing cover, and legal content to the approved visual
  language while preserving server-rendered `Event` behavior.
- [ ] Split shared design-system CSS from live catalog/legal CSS, vendor only used Lucide symbols,
  and record the third-party license.
- [ ] Run focused template/view tests until GREEN.

### Task 3: Preserve unfinished screens outside production

**Files:** `tests/visual/templates/design_reference/`, test-only static assets/settings/URLs/views.

- [ ] Create deterministic static template states for search, dashboard, events, upload, orders,
  promotions, and purchased photos without localStorage, fake APIs, or mutation logic.
- [ ] Reuse the production design system; keep reference-only CSS/images under `tests/visual`.
- [ ] Expose `/__visual__/` only from `tests.visual.settings` and prove production settings/URLs do not
  reference the gallery.
- [ ] Remove `src/proto`, obsolete backend templates/static scripts, and duplicate demo assets.
- [ ] Run repository and route contracts until GREEN.

### Task 4: Add Playwright visual regression

**Files:** npm manifests, Playwright config/specs/snapshots, `.gitignore`, CI workflow.

- [ ] Pin `@playwright/test` in `package-lock.json`; expose `npm run test:visual` and
  `npm run test:visual:update`.
- [ ] Use Chromium with animations disabled, `Europe/Moscow`, desktop `1440x1000`, and mobile
  `390x844`.
- [ ] Snapshot all desktop states and mobile catalog populated/empty, event covered/uncovered, legal,
  search, and upload states.
- [ ] Assert no console errors, resource failures, horizontal overflow, or broken live links.
- [ ] Run Playwright in CI and upload reports/diffs on failure; keep snapshots tracked and ignore
  generated reports, results, and `node_modules`.

### Task 5: Make the workflow durable

**Files:** `.agents/skills/update-visual-design/`, `AGENTS.md`, architecture, repository contracts.

- [ ] Use the observed failing baseline (an agent proposed `src/proto/promotions/`) to write the
  minimal discipline skill and a production/design-reference screen inventory.
- [ ] Require direct edits to live templates, test-only references for future screens, promotion of a
  reference when behavior ships, reviewed snapshots, and complete checks.
- [ ] Update `AGENTS.md` and architecture so Django templates/static are the UI source of truth and
  `src/proto` is prohibited.
- [ ] Validate the skill structure and run the same fresh-agent scenario with the skill to confirm
  GREEN behavior.

## Verification

Run `ruff format --check .`, `ruff check .`, `mypy`, `pytest --cov --cov-report=term-missing`, Django
`check`, migration drift check, `npm ci`, `npm run test:visual`, and `docker build .`. All commands
must succeed. Inspect every new screenshot and confirm the production image contains no
`tests/visual` files.

## Operational impact and rollout

No configuration, schema, data, or runtime dependency change. Deploy through the normal staging
workflow and smoke-test the catalog, one published event, legal, and Django Admin on desktop/mobile.
Playwright remains a development/CI dependency and its test-only gallery is outside the Docker build
context copied into the application image.

## Rollback

Redeploy the previous immutable image or revert the pull request. No data or media rollback is
required.

## Open questions

- None.
