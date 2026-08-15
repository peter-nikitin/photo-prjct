# FindMe Photo screen inventory

This inventory is the canonical map from visual concepts to Django templates and Playwright baselines.

| Screen | Status | Canonical template | Production URL | Visual route | Playwright snapshots |
| --- | --- | --- | --- | --- | --- |
| Catalog | production | `src/backend/templates/catalog/event_catalog.html` | `/` | `/__visual__/catalog/populated/`, `/__visual__/catalog/empty/` | `desktop-catalog-populated.png`, `desktop-catalog-empty.png`, `mobile-catalog-populated.png`, `mobile-catalog-empty.png` |
| Event detail | production | `src/backend/templates/catalog/event_detail.html` | `/events/<slug>/` | `/__visual__/event/covered/`, `/__visual__/event/uncovered/` | `desktop-event-covered.png`, `desktop-event-uncovered.png`, `mobile-event-covered.png`, `mobile-event-uncovered.png` |
| Event gallery | production | `src/backend/templates/catalog/event_detail.html` | `/events/<slug>/` | `/__visual__/event/gallery-populated/`, `/__visual__/event/gallery-empty/`, `/__visual__/event/gallery-filtered-empty/`, `/__visual__/event/gallery-manual-invalid/` | `desktop-event-gallery-populated.png`, `desktop-event-gallery-empty.png`, `desktop-event-gallery-filtered-empty.png`, `desktop-event-gallery-manual-invalid.png`, `mobile-event-gallery-populated.png`, `mobile-event-gallery-empty.png`, `mobile-event-gallery-filtered-empty.png`, `mobile-event-gallery-manual-invalid.png`, `desktop-event-gallery-face-chooser.png`, `mobile-event-gallery-face-chooser.png`, `desktop-gallery-lightbox-download.png` |
| Selfie search entry | production | `src/backend/templates/catalog/event_detail.html` | `/events/<slug>/` | `/__visual__/event/selfie-search/` | `desktop-event-selfie-search.png`, `mobile-event-selfie-search.png`, `desktop-event-selfie-search-history.png`, `mobile-event-selfie-search-history.png` |
| Selfie search result | production | `src/backend/selfie_search/templates/selfie_search/result.html` | `/events/<slug>/selfie-search/<public-token>/` | `/__visual__/event/selfie-search/processing/`, `/__visual__/event/selfie-search/empty/`, `/__visual__/event/selfie-search/error/`, `/__visual__/event/selfie-search/ready/`, `/__visual__/event/selfie-search/feedback-problem/`, `/__visual__/event/selfie-search/feedback-marking/` | `desktop-selfie-search-processing.png`, `desktop-selfie-search-empty.png`, `desktop-selfie-search-error.png`, `desktop-selfie-search-ready.png`, `desktop-selfie-search-feedback-problem.png`, `desktop-selfie-search-feedback-marking.png`, `mobile-selfie-search-processing.png`, `mobile-selfie-search-empty.png`, `mobile-selfie-search-error.png`, `mobile-selfie-search-ready.png`, `mobile-selfie-search-feedback-problem.png`, `mobile-selfie-search-feedback-marking.png`, `mobile-selfie-search-result-lightbox-download.png` |
| Legal | production | `src/backend/templates/ui/legal.html` | `/legal/` | `/__visual__/legal/` | `desktop-legal.png`, `mobile-legal.png` |
| Shared public shell | production | `src/backend/templates/ui/base.html` | none | covered by production screen routes | covered by production screen snapshots |
| Search workspace | design-reference | `tests/visual/templates/design_reference/search.html` | none | `/__visual__/reference/search/` | `desktop-reference-search.png`, `mobile-reference-search.png` |
| Operator dashboard | design-reference | `tests/visual/templates/design_reference/dashboard.html` | none | `/__visual__/reference/dashboard/` | `desktop-reference-dashboard.png` |
| Event management | design-reference | `tests/visual/templates/design_reference/events.html` | none | `/__visual__/reference/events/` | `desktop-reference-events.png` |
| Upload | production | `src/backend/templates/ingestion/upload.html` | `/photographer/uploads/` | `/__visual__/upload/empty/`, `/__visual__/upload/active/`, `/__visual__/upload/partial/`, `/__visual__/upload/complete/`, `/__visual__/upload/folders/` | `desktop-upload-empty.png`, `desktop-upload-active.png`, `desktop-upload-partial.png`, `desktop-upload-complete.png`, `desktop-upload-folders.png`, `mobile-upload-empty.png`, `mobile-upload-active.png`, `mobile-upload-partial.png`, `mobile-upload-complete.png`, `mobile-upload-folders.png` |
| Orders | design-reference | `tests/visual/templates/design_reference/orders.html` | none | `/__visual__/reference/orders/` | `desktop-reference-orders.png` |
| Promotions | design-reference | `tests/visual/templates/design_reference/promotions.html` | none | `/__visual__/reference/promotions/` | `desktop-reference-promotions.png` |
| Purchased photos | design-reference | `tests/visual/templates/design_reference/purchased.html` | none | `/__visual__/reference/purchased/` | `desktop-reference-purchased.png` |

Snapshot files live in `tests/visual/visual.spec.js-snapshots/`. The `/__visual__/` routes exist only with `tests.visual.settings`.

The existing covered/uncovered event fixtures intentionally hide `.event-gallery`; their four
snapshots remain focused regression surfaces for the event header and hero. Gallery layout and
states are covered separately by the populated/empty event-gallery fixtures.

On 2026-08-01, the existing populated gallery, selfie-search entry, and ready-result baselines were
refreshed for the original-download action: `desktop-event-gallery-populated.png`,
`mobile-event-gallery-populated.png`, `desktop-event-selfie-search.png`,
`mobile-event-selfie-search.png`, and `mobile-selfie-search-ready.png`. No visual route or scenario
was added.

On 2026-08-02, the existing desktop and mobile selfie-search entry baselines were refreshed for the
approved selfie-selection guidance. No visual route or scenario was added.

On 2026-08-04, the existing populated event gallery and ready selfie-result baselines were refreshed
to cover the shared page-jump control. No visual route or scenario was added.

On 2026-08-04, terminal selfie-search feedback added explicit compact-problem and in-gallery-marking
fixtures at desktop and mobile widths. The snapshots verify the consent form and the lower-left card
controls alongside the existing lower-right download action.

On 2026-08-05, the production event-gallery fixture added deterministic zero-, one-, two-, and
four-face cards. The desktop and 390px mobile baselines cover the direct circle, overlapping stack,
exact `+ 2` remainder, and an opened anchored chooser; the chooser screenshots are
`desktop-event-gallery-face-chooser.png` and `mobile-event-gallery-face-chooser.png`.

On 2026-08-05, saved device-local selfie-search history received a compact disclosure baseline:
the native disclosure is closed by default with a visible chevron; it sits beside the form on
desktop and below it on mobile. The existing `/__visual__/event/selfie-search/` route captures the
closed states in `desktop-event-selfie-search-history.png` and `mobile-event-selfie-search-history.png`.
Playwright interaction and geometry assertions separately open the disclosure to cover the
privacy copy, visible shared-sprite delete icon, one-line date rows, and 44px controls.

On 2026-08-15, the production upload fixture gained `/__visual__/upload/folders/` with the
selected event's `Без папки`, `Старт`, and `Финиш` targets plus a mixed-folder queue. Its desktop
and 390px mobile baselines are `desktop-upload-folders.png` and `mobile-upload-folders.png`.

On 2026-08-08, the production event-gallery fixtures added the permanent two-column discovery area
and dedicated valid-empty and invalid manual-time-filter states. Desktop and 390px mobile baselines
cover the normal, filtered-empty, and correction-only responses; interaction tests separately cover
no-JavaScript submit, filtered paging, reset, column order, and overflow.

On 2026-08-15, the production event-gallery fixtures added stable named-folder and `Без папки`
checkboxes. The populated and filtered-empty desktop and 390px mobile baselines cover no selection,
combined folder/time selection, and controls that remain visible for a zero-result intersection.
