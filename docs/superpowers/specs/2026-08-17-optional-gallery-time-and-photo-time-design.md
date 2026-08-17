# Optional Gallery Time and Photo Time Design

## Status

Approved on 2026-08-17.

- Related design: [`2026-08-15-event-photo-folders-design.md`](2026-08-15-event-photo-folders-design.md).
- Related architecture: [`docs/architecture.md`](../../architecture.md), public event gallery.
- ADR impact: none. This change refines the existing gallery-filter and presentation contracts.

## Goal

Let a visitor filter an event gallery by folder without knowing a capture time, and show each
photo's known event-local capture time beside its download action without adding visual noise.

## Scope

### Included

- Treat empty gallery time fields as no time filter, including when a folder is selected.
- Preserve the existing combination of an active folder filter and an active time range with
  `AND`.
- Show a known photo capture time as `HH:MM` in the event's IANA timezone.
- Place the time immediately before the card's download action using small, muted text.
- Render no placeholder when a photo has no capture time.

### Excluded

- Changing the existing rule that an end time cannot be used without a start time.
- Showing a date, seconds, timezone abbreviation, or capture time in the lightbox.
- Inferring missing capture times or changing metadata extraction and backfill behavior.
- Changing folder choices, gallery eligibility, pagination, media delivery, or selfie search.

## Behavior

The time form is active only when at least one submitted time value is non-empty. Browsers may
still submit empty `from` and `to` parameters with a folder-only request; those empty parameters
must not trigger time validation, affect the queryset, or appear in pagination URLs.

If `from` is present, the existing open-ended and bounded range behavior is unchanged. If only
`to` is present, the form keeps reporting that a start time is required. Folder selections remain
independently active and continue to narrow the base public gallery.

Each gallery presentation object exposes an optional display value derived from the persisted
`Photo.capture_time`. The value is converted from its stored instant into `event.timezone_name`
and formatted as a zero-padded 24-hour `HH:MM` string. A missing capture time produces no display
value and no empty UI element.

In each gallery card's action row, the optional time and existing download link form the
right-hand action group. Face-search controls remain on the left. The time uses a smaller size and
the existing muted color token so it stays readable without competing with the photograph or
actions. The layout must remain stable on desktop and 390px mobile widths.

## Data Flow

1. The event detail view binds folder and time forms from the same GET request.
2. Empty time values normalize to an inactive time filter; valid folder selections remain active.
3. The gallery queryset applies only the active predicates and pagination preserves only their
   normalized parameters.
4. The gallery factory converts each known capture time to the event timezone and exposes its
   display string to the canonical production template.
5. The template renders the string before the download action, or renders only the download
   action when the string is absent.

## Validation Contract

Focused automated coverage must prove:

- a folder-only request containing empty browser-submitted time fields succeeds and filters by
  folder without time errors;
- empty time parameters are absent from preserved pagination state;
- existing start-only, bounded, invalid, and end-only time behavior does not regress;
- a known UTC capture instant is displayed as `HH:MM` in the event timezone;
- a photo without capture time renders no time label or empty placeholder;
- the desktop and mobile production gallery snapshots show the subdued time beside download while
  preserving face controls and card alignment.

## Acceptance Criteria

- A visitor can select one or more folders, leave both time fields empty, submit, paginate, and
  see only the selected folders' eligible photos.
- Entering a time continues to combine it with selected folders using `AND`.
- Every gallery card with known capture time shows its event-local `HH:MM` next to download.
- Cards without known capture time show no replacement text.
- The new metadata remains visually secondary and does not crowd the desktop or mobile gallery.
