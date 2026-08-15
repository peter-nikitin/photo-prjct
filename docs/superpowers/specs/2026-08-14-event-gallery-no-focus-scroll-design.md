# Event Gallery Without Programmatic Focus Scrolling

- Date: 2026-08-14
- Status: Approved

## Goal

Prevent the event gallery from changing the document scroll position when a face chooser or the
photo lightbox closes.

## Scope

Remove both gallery-owned programmatic focus restorations:

- closing a multi-face chooser outside the chooser or with Escape must close it without focusing
  its summary trigger;
- closing GLightbox must not focus the card that opened it.

Native pointer and keyboard focus behavior remains unchanged. The gallery keeps one face chooser
open at a time, continues to close it on outside click and Escape, and preserves all existing face
search and lightbox behavior.

## Acceptance criteria

- The gallery module contains no programmatic `.focus()` call.
- Closing a chooser does not invoke focus on its trigger.
- Closing GLightbox does not invoke focus on its opening card.
- Existing chooser exclusivity, close behavior, downloads, and face-search behavior remain intact.
- Focused JavaScript tests pass.

## Testing

Update `tests/js/event-gallery.test.js` first so the current focus-restoration implementation fails
the new contract. Then remove only the focus-restoration state and calls from
`src/backend/static/ui/event-gallery.js` and rerun the focused suite.

## ADR impact

None — this is a reversible interaction detail inside the existing event gallery. It remains within
ADR 0024's explicit multi-face selection and keyboard-behavior boundary.

## Operational impact

No configuration, data migration, deployment ordering, or rollback migration is required. Rollback
is a normal code revert.
