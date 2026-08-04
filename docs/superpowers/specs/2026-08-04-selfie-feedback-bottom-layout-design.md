# Selfie feedback bottom layout

## Status

Approved interaction design. This document replaces only the feedback layout and opt-out portions
of `2026-08-04-selfie-search-quality-feedback-design.md`; its submission, consent, storage, and
result-label contracts remain unchanged.

## Product outcome

After viewing a completed selfie-search result, a customer can review the full-width result gallery
and submit feedback without opening a separate panel or losing gallery width.

## Desktop layout

The result gallery keeps its normal width while feedback marking is available. Below the gallery,
the terminal-result actions form one full-width, two-column region with equal columns:

- the left column contains the existing `Искать по другому селфи` action;
- the right column contains the feedback form, expanded immediately when feedback is available.

The form is not opened, closed, overlaid, sticky, or positioned beside the gallery. The result-card
controls `Я есть` and `Меня нет` remain on the cards while the expanded result-label form is visible.

## Other states

Error and empty-result feedback use the same bottom region and expanded form. They do not display
photo-label controls. When feedback is unavailable or already submitted, the existing unavailable
or confirmation state occupies the right column while the new-search action remains on the left.

## Responsive behavior

At the existing mobile breakpoint, the two columns become one vertical stack. The new-search action
comes first and spans the available width; the feedback form follows and also spans the available
width. No horizontal overflow or gallery-width reduction is allowed at supported desktop and mobile
snapshot widths.

## Removed interaction

The UI no longer renders `Оценить качество поиска`, `Закрыть`, or
`Не спрашивать больше на этом устройстве`. The browser opt-out key and its read/write behavior are
removed. Existing one-search-one-feedback enforcement and browser-local selfie handling are not
changed.

## Accessibility and failure behavior

The form remains a labelled section in normal document order. Existing validation errors, consent
requirements, keyboard-accessible result labels, submission states, and local-selfie-unavailable
message remain intact. Removing disclosure controls must not leave hidden form or card controls.

## Verification

- Focused Django assertions cover the new always-expanded markup and removed controls.
- JavaScript tests cover initialization without open, close, or opt-out controls and retain marking,
  validation, submission, and cleanup coverage.
- Desktop and mobile Playwright snapshots cover result-label, empty/error feedback, and the 50/50 to
  stacked responsive transition.
- Every changed snapshot is inspected before the non-update visual suite runs.

