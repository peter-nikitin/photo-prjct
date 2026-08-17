# Brand Logo Design

## Goal

Replace the `FM` mark in the shared production header with the supplied logo and change the
supporting line from `фотографии событий` to the exact copy `найди моё фото`. Use the same logo
asset as the site favicon.

## Scope

- Add one optimized SVG at `src/backend/static/ui/logo.svg`.
- Use that file for both the header image and the favicon link in
  `src/backend/templates/ui/base.html`.
- Keep the `FindMe Photo` name, header link, accessible label, navigation, spacing, and responsive
  behavior unchanged.
- Leave event-cover `FM` placeholders and the test-only design-reference shell unchanged.
- Remove the obsolete `src/backend/static/ui/favicon.svg`; no compatibility path is retained.

## Asset Preparation

Optimize the supplied `Logo_without_text_black_full.svg` with SVGO 4.0.2 in multipass mode. The
optimized file must preserve the original `viewBox`, shapes, colors, gradients, and transparent or
filled areas. Compare rendered source and optimized images before accepting the asset. Any visible
difference is a failed optimization and requires safer SVGO settings.

The repository stores only the optimized result, not the source SVG or a second favicon copy.

## Header Presentation and Accessibility

Render the shared asset as a 42 by 42 pixel image in the existing brand row. The image is
decorative because the surrounding link already has the accessible label
`FindMe Photo — каталог событий`, so it uses an empty alternative text. CSS keeps the fixed square
footprint and removes the old text-mark background, color, and typography rules.

The supporting line is exactly `найди моё фото`, including lowercase letters and `ё`. Existing
mobile rules continue to hide that line below 760 pixels and hide all brand copy below 480 pixels.

## Architecture and ADR Impact

None — this is a reversible presentation detail within the existing server-rendered Django UI and
local static-asset boundary. It requires no ADR or `docs/architecture.md` update.

## Verification

- Validate the optimized SVG as well-formed XML and confirm the old header copy and production
  `FM` brand markup are gone.
- Render and compare the original and optimized SVGs.
- Refresh only snapshots affected by the shared production header, inspect every changed desktop
  and mobile image, then run the visual suite without update mode.
- Run focused Django/template checks and the repository-required final checks.
