# Compact Event Gallery Discovery Design

## Status

Expanded design approved on 2026-08-17.

- Related design: [`2026-08-15-event-photo-folders-design.md`](2026-08-15-event-photo-folders-design.md).
- Related architecture: [`docs/architecture.md`](../../architecture.md), public event gallery.
- ADR impact: none. This refines the existing server-rendered event gallery, filters, and
  presentation contracts without changing durable architecture.

## Goal

Let visitors reach event photos sooner, filter by any combination of folders and optional time
bounds without a delayed scroll jump, and read each known event-local photo time beside download.

## Scope

### Included

- Allow folder-only, start-only, end-only, bounded, and completely unfiltered gallery requests.
- Keep `AND` between active folder and time predicates.
- Remove the delayed fragment scroll after submitting the manual filter.
- Replace the tall event hero with one compact metadata bar on the event gallery and selfie-search
  result pages.
- Reduce desktop discovery height while keeping selfie and privacy copy available.
- Collapse the complete discovery block on mobile after a filter is applied and through pagination.
- Show known photo capture time as `HH:MM` beside download using small, muted text in the event
  gallery and on ready selfie-search result cards.

### Excluded

- Making card times clickable or copying them into the filter.
- Creating an automatic plus/minus-five-minute search from a card.
- Showing a date, seconds, timezone abbreviation, or capture time in the lightbox.
- Adding automatic padding around a supplied manual time bound.
- Inferring missing capture times or changing metadata extraction and backfill behavior.
- Changing folder choices, gallery eligibility, media delivery, selfie-search matching, ranking,
  result membership, or the customer-approved selfie/privacy wording.
- Replacing server-rendered filtering or pagination with JavaScript.

## Time Filtering

The time form is active only when at least one submitted `from` or `to` value is non-empty.
Browsers may submit both empty fields with a folder-only request; they must not trigger validation,
affect the queryset, or appear in pagination URLs.

Each bound is independent:

- `from` only applies an inclusive lower bound at the entered event-local time, with no upper time
  predicate;
- `to` only applies an inclusive upper bound at the entered event-local time, with no lower time
  predicate;
- both values apply the inclusive bounded interval between the entered times;
- neither value applies no time predicate.

Parsing, event-date validation, ambiguous/nonexistent local-time rejection, repeated-parameter
rejection, and event IANA timezone conversion remain unchanged. A supplied upper bound must still
be later than a supplied lower bound. One-sided time filters include only photos with a comparable
persisted capture time; missing capture times are not inferred.

Folder selections remain independently active and narrow the base public gallery. Active folder
and time predicates combine with `AND`. Pagination preserves only normalized active parameters.

## Stable Navigation

The manual filter remains a native GET form and works without JavaScript. Its action no longer
contains the `#gallery` fragment. The resulting page loads at the top and stays there instead of
starting at the top and then scrolling hundreds of pixels as the browser resolves the fragment
against a changing layout. No focus-management or scroll-restoration JavaScript is added.

Numbered pagination keeps `#gallery` so moving between result pages continues at the photo area.
This change targets filter submission only; it does not alter pagination mechanics.

## Compact Event Header

The tall event hero on the event-detail page is replaced by one thin horizontal metadata bar. It
contains the back action, event name, city, and event date or date range. The name truncates safely
on narrow widths while metadata remains readable. The event cover and long description are not
rendered on this page; catalog cards remain unchanged and continue to present the cover.

The bar uses the existing production design tokens, clear focus treatment, and a compact touch-safe
back action. It introduces no sticky behavior and consumes the minimum practical vertical space.

Selfie-search result pages reuse the same header include and classes as the event gallery. They show
the event name, city, and event date or date range above the existing result-specific privacy lead;
queued, processing, terminal, and ready states all use this shared header. This is a presentation
change only: bearer authorization, saved result membership, matching, and ranking remain unchanged.

## Compact Discovery

On desktop, `Найти свои фото` remains visible as two compact columns:

- the selfie column keeps the upload control and submit action on one row;
- the approved selfie guidance moves into a compact native disclosure;
- the approved deletion and link-access privacy meaning remains present without rewritten copy;
- the manual column places folder checkboxes on their own wrapping row;
- start, end, and `Показать` occupy a separate stable row, so folder count cannot shift the time
  fields or button sideways.

The existing selfie and manual-search backend contracts remain unchanged. The layout reduces
spacing and duplication and uses native disclosures rather than a custom disclosure widget.

On mobile, the entire `Найти свои фото` section—including selfie and manual search—is one native
disclosure:

- it is open by default when no gallery filter is active, so first-time visitors discover search;
- it is closed by default when any valid folder or time filter is active, including numbered pages;
- its closed summary says `Фильтры применены` when applicable;
- the active-filter reset remains accessible without requiring the disclosure to be opened;
- opening the disclosure reveals the same controls and approved copy, stacked for the viewport.

The server renders an active-filter disclosure closed and an unfiltered disclosure open. A small
progressive-enhancement initializer opens it on desktop, where discovery always remains exposed;
native disclosure and GET-form behavior remain usable without JavaScript. This avoids duplicate
forms or client-owned filter state.

## Photo Time Presentation

Each immutable gallery presentation object used by the event gallery and ready selfie-search result
cards exposes an optional display value derived from `Photo.capture_time`. The value is converted
from its stored instant into `event.timezone_name` and formatted as zero-padded 24-hour `HH:MM`.
Missing capture time produces no display value.

In each event-gallery or ready selfie-search result card action row, the optional time and existing
download link form the right-hand group. Face-search feedback controls remain on the left. The time
uses a smaller size, tabular numerals, and the existing muted color token. When time is missing, no
placeholder, empty element, or reserved gap is rendered. Card time remains plain text in this
increment; the lightbox description remains download-only.

## Data Flow

1. The event detail view binds folder and time forms from the same GET request.
2. Each supplied time value independently becomes an optional UTC bound; empty values stay absent.
3. The gallery queryset applies active folder and time predicates, then numbered pagination.
4. Valid normalized parameters are preserved; filter submission omits a fragment while pagination
   retains `#gallery`.
5. The gallery factory converts each known capture instant into an event-local display string.
6. The canonical event-detail template renders the compact header, responsive discovery, gallery,
   and optional gallery card times.
7. Selfie-search result templates reuse the same compact header include and classes, while the ready
   result view reuses the same presentation objects and renders the same optional card times without
   changing saved result membership or ranking.

## Failure and Accessibility Semantics

- Malformed, repeated, outside-event, nonexistent, ambiguous, and inverted supplied times retain
  the existing accessible validation behavior and do not fall back to broad results.
- A single valid bound is not an error and never invents the missing opposite bound.
- Folder and time query parameters can only narrow the already-authorized base gallery.
- Native GET submission, native disclosures, labels, legends, keyboard access, focus visibility,
  and 44px interactive targets remain available without JavaScript.
- Responsive collapsing must not duplicate form controls, lose selected values, or create horizontal
  overflow at 390px.

## Validation Contract

Focused automated coverage must prove:

- blank browser-submitted time fields allow folder-only filtering and are omitted from pagination;
- start-only, end-only, and bounded requests produce the correct one-sided or two-sided UTC bounds,
  using each supplied value as an exact inclusive bound;
- malformed, repeated, outside-event, DST-invalid, and inverted ranges remain invalid;
- active one-sided bounds combine with folders using `AND` and persist through numbered pages;
- manual filter submission produces a query URL without `#gallery` and keeps `scrollY` stable with
  `BODY` as the active element after page load;
- pagination links still target `#gallery`;
- a known UTC capture instant displays as event-local `HH:MM` in both the event gallery and ready
  selfie-search result cards, while a missing value renders no label or gap in either surface;
- server-rendered selfie-search result pages expose the same compact event metadata header as the
  event gallery without changing the approved result privacy lead or bearer behavior;
- desktop visual coverage shows the one-line event bar, compact two-column discovery, stable folder
  and time rows, and subdued card times in the gallery and ready selfie-search result;
- mobile visual and interaction coverage shows initial-open, filtered-closed, user-reopen, reset,
  pagination, known-time, and missing-time states without overflow;
- existing selfie upload, history, privacy copy, face controls, lightbox, download, and public gallery
  authorization contracts remain intact.

## Acceptance Criteria

- A visitor can use folders alone, either time boundary alone, both boundaries, or neither.
- One-sided filters extend through the rest of the event in the missing-bound direction.
- Applying a filter reloads at the top without a delayed jump or focus change.
- The event and selfie-search result headers are one compact line and no longer spend vertical space
  on cover or description.
- Desktop folders never push the time inputs or submit action sideways.
- Mobile visitors see search on first entry and a compact closed summary while paging filtered photos.
- Every known photo time appears as quiet event-local `HH:MM` beside download in the event gallery
  and ready selfie-search results; unknown time shows nothing.
- Photos become visible materially earlier on both desktop and mobile without weakening existing
  privacy, authorization, accessibility, or no-JavaScript behavior.
