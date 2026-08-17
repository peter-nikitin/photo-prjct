# Brand Logo Implementation Plan

- Date: 2026-08-17
- Status: Approved
- Owner: project maintainer
- Related specification: [Brand logo design](../superpowers/specs/2026-08-17-brand-logo-design.md)
- Related architecture: [Architecture](../architecture.md)
- Related ADRs: none
- ADR impact: None — reversible implementation detail within the existing Django UI and static-asset boundary

## Goal

Implement the approved shared header logo, favicon, and supporting copy update.

## Scope

None beyond the approved specification.

## Acceptance criteria

One optimized SVG is the only production logo source; the shared production header and favicon use
it, the exact supporting copy is visible at desktop widths, existing mobile behavior is preserved,
and no event placeholder or design-reference branding changes.

## Implementation

Execute this plan with `$execute-implementation-plan`.

### Task 1: Deliver the optimized shared production logo

**Files:** create `src/backend/static/ui/logo.svg` and `tests/test_branding.py`; modify
`src/backend/templates/ui/base.html`, `src/backend/static/ui/design-system.css`,
`.agents/skills/update-visual-design/references/screen-inventory.md`, and affected files under
`tests/visual/visual.spec.js-snapshots/`; delete `src/backend/static/ui/favicon.svg`.

- **Specification:** Goal, Scope, Asset Preparation, Header Presentation and Accessibility,
  Verification.
- **Depends on:** Supplied source file `/Users/petrnikitin/Downloads/Logo_without_text_black_full.svg`.
- **Produces:** One optimized `ui/logo.svg` consumed by the shared production header and favicon.

- [ ] Add a focused Django Client branding test that renders the real catalog page and requires its
  response to expose exactly two identical `/static/ui/logo.svg` URLs: one favicon and one
  decorative `<img class="brand-mark" ... alt="">`. Require `найди моё фото`, the unchanged
  accessible brand label, and the absence of the old header mark and copy. Resolve `ui/logo.svg`
  through Django staticfiles, parse the resolved asset as XML, and require
  `viewBox="0 0 1500 1500"`.
- [ ] Run `make test TESTS="tests/test_branding.py"` and confirm failure because the new logo and
  template contract do not exist yet.
- [ ] Run
  `NPM_CONFIG_CACHE=/private/tmp/findme-logo-npm-cache npx --yes svgo@4.0.2 --multipass --output src/backend/static/ui/logo.svg /Users/petrnikitin/Downloads/Logo_without_text_black_full.svg`;
  expect a successful optimization and a smaller well-formed SVG that retains the approved
  `viewBox`.
- [ ] Render the supplied and optimized SVGs at the same 1500-pixel size, inspect them side by side,
  and accept the optimized asset only when geometry, color, gradients, and filled or transparent
  regions have no visible difference.
- [ ] Replace the header text mark with the decorative 42 by 42 image, point the favicon link to the
  same asset, change the supporting line to `найди моё фото`, remove obsolete text-mark CSS rules,
  and delete `ui/favicon.svg`. Do not change event-cover placeholders or the design-reference shell.
- [ ] Run `make test TESTS="tests/test_branding.py"`; expect the focused branding contract to pass.
- [ ] Run `npm run test:visual:update`, inspect every changed expected image, and confirm all and
  only production-shell snapshots reflect the new mark and applicable desktop copy. Append a dated
  note to the screen inventory; do not change route, status, or snapshot-name mappings.
- [ ] Run `npm run test:visual`; expect the complete visual suite to pass without new diffs.
- [ ] Run `git diff --check`; expect no output.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the delivered result with the approved specification and confirm the asset remains
  inside the implemented local static-asset and server-rendered Django UI boundary.
- [ ] Confirm the change remains a reversible presentation detail requiring no ADR or architecture
  update.
- [ ] Record the reconciliation outcome in delivery notes before push.

## Verification

- `make test TESTS="tests/test_branding.py"` — focused branding contract passes.
- `xmllint --noout src/backend/static/ui/logo.svg` — optimized SVG is well-formed XML.
- `npm run test:visual` — all Playwright visual tests pass with no unexpected diffs.
- `make check` — Ruff, mypy, the non-clone-staging Python suite with coverage, Django system checks,
  and migration drift checks all pass.
- `git diff --check` — no whitespace errors.

## Operational impact and rollout

No configuration, migration, data change, monitoring, or special deployment ordering. The normal
application deployment publishes the template, CSS, and collected static asset together.

## Rollback

Revert the change; there are no persistent data effects.

## Open questions

None.
