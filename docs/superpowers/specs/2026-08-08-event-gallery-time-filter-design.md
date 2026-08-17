# Event Gallery Time Filter Design

- Date: 2026-08-08
- Status: Approved design; customer delivery remains gated by the final live staging capture-time acceptance report.
- Related architecture: [Current architecture — implemented: event timezone and capture metadata](../../architecture.md#current-architecture--implemented), [Core data flows — proposed: Search](../../architecture.md#search), and [Security, privacy, and legal boundaries](../../architecture.md#security-privacy-and-legal-boundaries).
- Related ADRs: [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md), [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md), and [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md).
- ADR impact: Conforms to ADR 0017, ADR 0019, ADR 0022.

## Outcome

A customer can narrow a published free-event gallery by the time their photographs were taken,
without relying on the browser or server timezone.  The event detail remains a server-rendered GET
page and works with JavaScript disabled.  The page presents one permanent **«Найти свои фото»**
area above the gallery: the existing privacy-safe selfie search and a new manual time search sit
side by side on wide screens and stack on mobile.

The manual search uses trustworthy, current capture-time evidence only.  A customer enters an
event-local starting time and may enter an event-local ending time.  The server converts each
supplied wall-clock value using the event's own IANA timezone, uses it as an inclusive UTC bound,
and returns only otherwise eligible gallery photos.  It neither creates a search record nor stores
the customer query.

This delivery also removes `SELFIE_SEARCH_ENABLED` completely.  Public selfie search is a normal
available capability for every published free event when its existing processing prerequisites are
healthy; it is no longer a deployment-controlled hide/404 switch.

## Scope

### Included

- A server-rendered manual time form and filtered event-gallery GET contract.
- Event-local validation, DST-safe conversion, database filtering, existing 100-item numbered
  pagination, and a distinct filtered-empty state.
- The always-present two-column discovery area on published free-event detail pages, including
  responsive and no-JavaScript behavior.
- Removal of `SELFIE_SEARCH_ENABLED` from active settings, environment configuration, deployment
  validation/rendering, workflows, runtime branches, tests, and current documentation.
- Measurement of the direct version-2 evidence query against the 17,043-photo event-9 corpus
  before considering any denormalized photo projection.

### Excluded

- Reprocessing, repairing, guessing, or exposing missing capture metadata.
- A `Photo` capture-time column, a projection table, a backfill, or a new index merely in
  anticipation of scale.
- JavaScript-only filtering, client-side timezone conversion, asynchronous search jobs, saved
  manual searches, query history, analytics that retain entered times, and changes to selfie
  retention.
- A time filter for paid-gallery access, selfie-result pages, gallery-origin face searches, or
  other search surfaces.
- Any change to the independent `SELFIE_FEEDBACK_ENABLED` or
  `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED` feature flags, their privacy contracts, or ADR 0019's
  bearer-result authorization.

Historical accepted ADRs remain immutable evidence of the conditions under which earlier work was
delivered.  Removing the gate means removing it from executable and current-facing documentation;
it does not rewrite historical evidence to claim that the gate never existed.

## Preconditions and evidence status

The filter may use only the corrected corpus contract established by
[`2026-08-07-event-capture-time-reprocessing-design.md`](2026-08-07-event-capture-time-reprocessing-design.md).
Its local full run on the current staging clone has passed the restored-snapshot acceptance: all
17,043 event-9 photos have current accepted non-null version-2 capture-time evidence with the
required bounded checks.  The live staging reprocessing/backfill is in progress.  Its terminal
acceptance is not yet evidenced and no transient progress count is part of this specification.

Before customer delivery of this feature, the live staging run must produce the same final accepted
report: event 9 has exactly 17,043 terminal current accepted version-2 results with a non-null
capture time, no terminal failures or missing times, no `inferred_none` timezone state, and
reviewed bounded distributions with no JPEG/MPO three-hour split.  The accepted local-clone run
proves the corpus contract; it does not substitute for the final live staging report.

Every other published event is eligible only when it has the already-required valid
`Event.timezone_name` and individual photos have qualifying current version-2 evidence.  A photo
without that evidence is simply absent from a filtered gallery; the feature never falls back to
version 1, filename times, upload time, browser time, or a guessed timestamp.

## User experience and GET interface

The canonical event detail remains `GET /events/<slug>/`.  Opening its canonical URL with no
manual-search parameters shows the existing unfiltered free-event gallery.

The manual form submits the same URL with these optional query parameters:

| Parameter | Meaning |
| --- | --- |
| `from` | Optional `datetime-local` wall-clock lower bound in the event timezone. |
| `to` | Optional `datetime-local` wall-clock upper bound in the same timezone. |
| `page` | The existing one-based numbered gallery page. |

The event's date range is available to the form as its local minimum and maximum.  Each supplied
bound must be between `start_date 00:00:00` and `end_date 23:59:59.999999` in
`Event.timezone_name`; an omitted bound leaves that side of the query unbounded.

`from` and `to` are scalar parameters and may occur at most once.  A repeated value is an invalid
manual search rather than an implicit choice of first or last value.

The page has these three mutually exclusive gallery states:

1. **Unfiltered:** neither `from` nor `to` is supplied; the existing gallery and its existing
   empty state behave unchanged.
2. **Valid filtered:** at least one of `from` or `to` is supplied and valid; when both are
   supplied, `to` is later than `from`.  The resulting gallery or its distinct filtered-empty
   state is rendered below `#gallery`.
3. **Invalid manual search:** either parameter is supplied but the request does not meet the
   validation contract below.  The manual form displays the field error in the normal page
   response and no gallery cards, pager, or ordinary empty-gallery message are rendered.  This
   prevents an invalid request from looking like a broader unfiltered result.

The form has no `page` field, so submitting a new time selection always starts at page 1.  Pager
links and the numbered-page GET form preserve whichever validated `from` and `to` values were
supplied alongside `page`; an unfiltered pager continues to use only `page`.  The reset control
links to the canonical event-detail URL with no query parameters, including no `page`, `from`, or
`to`.

## Manual-time semantics

`Event.timezone_name`, validated as an IANA identifier on publication, is the sole authority for
interpreting `from` and `to`.  The browser's timezone, server process timezone, locale, and current
UTC offset cannot alter the result.

For each supplied value, the server must:

1. parse one complete local date-time value rather than a date or a timestamp with an arbitrary
   offset;
2. require it to be inside the event-local date range above;
3. reject a nonexistent local time (a DST forward gap) and an ambiguous local time (a DST backward
   overlap) rather than choosing either offset; and
4. convert the unambiguous local wall time to its canonical UTC instant using
   `Event.timezone_name`.

At least one non-empty `from` or `to` requests a manual search.  Each supplied value must be
complete, inside the event-local date range, nonexistent/ambiguous-time safe, and convertible to
an unambiguous UTC instant.  An omitted bound leaves that side of the query unbounded.  When both
are supplied, `to` must be strictly later than `from`; equal values and values earlier than `from`
are errors.  Errors are attached to the manual-search UI, use the normal successful page response
rather than a redirect or a partial gallery, and retain the customer's safe-to-re-render form
values.

For a valid search, let `start` be the UTC `from` instant when supplied and let `end` be the UTC
`to` instant when supplied.  The inclusive query bounds are:

```text
capture_time >= start       (when `from` is supplied)
capture_time <= end         (when `to` is supplied)
```

There is no automatic widening or tolerance around a supplied bound.  Each comparison is
inclusive: evidence exactly on the entered lower or upper boundary belongs in the result, while a
photo even one minute outside that boundary does not.

## Evidence selection and gallery behavior

The filter begins with the existing gallery-media eligibility predicate.  It therefore preserves
event scope, publication/access rules, preview readiness where required, media authorization, and
the existing `original_filename, id` ordering.  It then intersects that queryset with direct
current accepted `capture_metadata` evidence whose processor version is 2 and whose accepted
result has a non-null canonical UTC `capture_time` within the requested bounds.

“Current accepted version 2” is a single contract, not a convenient JSON lookup: the current
capture-metadata state and its accepted successful attempt must agree on the version-2 identity and
the result used for comparison.  Stale attempts, failed attempts, unaccepted attempts, other
processor types, version-1 results, malformed/missing capture times, and photos from another event
do not qualify.  The query reads this accepted evidence directly; this increment adds no denormalized
`Photo` projection or alternate source of truth.

Eligible photos retain the existing 100-photo page size, filename-plus-ID order, gallery cards,
lightbox, download/media authorization, and page-number semantics from ADR 0022.  Filtering only
decides which photo rows reach that established presentation boundary.  It does not alter the
signed-direct media delivery or authorized-download boundary governed by ADRs 0020 and 0021.

A valid time filter that matches no eligible photo is not an error.  It renders a dedicated
filtered-empty message, distinct from the unfiltered **«Фотографии пока не опубликованы.»** state,
and keeps the manual form and reset control available.  An invalid request is distinct from both
empty states and hides the gallery entirely.

## Discovery area and selfie availability

On every published free-event detail page, the former optional **«Найти мои фото»** section becomes
an always-present **«Найти свои фото»** container before `#gallery`.

- Its left column, **«Поиск по селфи»**, retains the existing upload behavior, validation, privacy
  disclosures, deletion contract, result URLs, device-local history behavior, feedback behavior,
  and gallery-origin face controls.  This change does not persist an ordinary selfie query, image,
  or embedding beyond the already approved path.
- Its right column, **«Ручной поиск»**, contains the GET `from`/`to` form, event-local date-range
  guidance, field errors, and reset control described above.
- At the mobile layout width the columns become one vertical reading order: selfie search first,
  manual search second, then the gallery.  The same controls and result states remain available
  without JavaScript.

The selfie capability is no longer conditional on `SELFIE_SEARCH_ENABLED`.  The setting, its
environment variable, defaults, deployment validations/rendered environment, CI/workflow inputs,
runtime disabled branches, and tests that model a disabled mode are removed.  Current architecture
and active operational documentation likewise stop instructing an operator to enable it.

This is not a weakening of prerequisites: photo processing and face embeddings remain fail-fast
application/deployment prerequisites for the always-available selfie path.  If those prerequisites
are not correctly configured, the application or deployment must fail its existing checks rather
than start a page which silently hides selfie search or returns a feature-disabled 404.  The
independent feedback and cluster-expansion flags remain flags; only their former dependency on the
removed gate is eliminated.  ADR 0019's privacy, immutable-result, event-isolation, bearer-link,
and media-authorization boundaries are unchanged.

## Performance decision

The first implementation must measure the direct evidence join and pageable gallery request against
the accepted 17,043-photo event-9 local-clone corpus, then confirm the result during the final live
staging acceptance.  The report must identify the corpus size, database query plan, database
execution time, and rendered page latency for representative first and later pages, without
exposing filenames, EXIF values, storage keys, or customer data.

For each representative page, filtered database execution time and rendered-page latency must each
be no worse than twice the corresponding unfiltered staging baseline, with no request timeout or
health degradation.  No projection is introduced because a projection is technically possible.  If
this comparison fails, a separately approved follow-up must choose and specify a projection, its
freshness and correction semantics, and any migration/index work.  The current delivery retains
the direct evidence source of truth.

## Rejected alternatives

- **Use browser-local time or the server timezone.** Rejected because the same URL would mean
  different instants for different customers or deployments.
- **Treat ambiguous/nonexistent DST times as one arbitrary offset.** Rejected because it silently
  changes the customer’s selected interval; the form must request a different time instead.
- **Show the normal unfiltered gallery after a malformed filter.** Rejected because it can conceal
  an error as a successful but much broader search.
- **Project capture time onto `Photo` now.** Rejected because the accepted processing attempt is
  already the authoritative evidence and the corpus is small enough to measure first.
- **Use a POST, a persisted manual search, or JavaScript filtering.** Rejected because a shareable,
  reloadable, privacy-minimal GET satisfies the user job and preserves no-JavaScript operation.
- **Retain a feature flag as an emergency disable switch.** Rejected by the approved product
  decision: availability is now a prerequisite-checked product capability, not an optional UI.

## Acceptance criteria

1. A published free-event detail page with no `from`/`to` request remains an unfiltered
   server-rendered gallery with the existing eligible photos, filename-plus-ID order, 100-item
   pages, empty-state behavior, media links, and normal page validation.
2. The page always renders **«Найти свои фото»** with the **«Поиск по селфи»** and
   **«Ручной поиск»** columns for a published free event.  Desktop renders two columns and the
   supported mobile layout stacks them before the gallery; visual coverage protects both layouts.
3. The manual form works when JavaScript is disabled: valid GET submission reaches `#gallery`,
   errors are visible in the returned HTML, and all gallery/pager/reset controls remain operable.
4. Either `from` or `to` may be supplied; valid values are interpreted in `Event.timezone_name`,
   not browser/server timezone.  Tests cover a deliberate browser/server timezone mismatch.
5. Tests cover a multi-day event, midnight crossing, each one-sided bound, an explicit bounded
   range, and rejection when `to <= from`.
6. Tests prove exact UTC bounds, inclusive lower/upper boundaries, and exclusion immediately
   outside each boundary.
7. Only current accepted non-null `capture_metadata` version-2 evidence can include a photo.
   Tests exclude version 1, stale/unaccepted/failed evidence, missing capture time, other events,
   and otherwise ineligible gallery media.
8. Malformed, blank, out-of-event-range, ambiguous-DST, and nonexistent-DST inputs display their
   manual-form errors with status 200 and hide gallery cards, pager, and ordinary empty states.
   Repeated `from` or `to` parameters are errors as well.
9. A valid zero-match filter renders the distinct filtered-empty state; it is neither an error nor
   the unfiltered empty-gallery message.
10. Filtered previous/next links and page-number submission preserve `from` and `to`; new form
    submission omits `page` and begins at page 1; reset returns the canonical query-free event URL.
11. No page or query path creates a selfie search, processing job, object, photo projection, or
    stored manual-query history merely to filter the gallery.
12. `SELFIE_SEARCH_ENABLED` has no remaining active setting, environment, workflow, deployment,
    runtime branch, or disabled-mode test.  Selfie submission and gallery-origin search remain
    available on published free events without it, while missing processing/face-embedding
    prerequisites still fail fast.  Feedback and cluster-expansion flags retain their independent
    behavior.
13. Existing ADR 0019 privacy and result authorization regressions continue to pass: ordinary
    selfie uploads are not retained as query history, event scope remains strict, bearer-result
    behavior is unchanged, and manual time search stores no biometric or manual-query data.
14. Performance evidence on the accepted 17,043-photo local clone and final live staging acceptance
    records the direct current-v2 query and pageable gallery behavior.  For each representative
    page, filtered database execution and rendered-page latency are each at most twice the matching
    unfiltered staging baseline, with no timeout or health degradation.  A photo projection remains
    out of scope unless this gate fails and a separate approved specification admits it.

## Architecture and ADR reconciliation

The direct query is a read-side use of the immutable, versioned processing evidence supplied by
ADR 0017; it neither changes worker ownership nor mutates attempts.  Event-local interpretation
uses the already implemented event-timezone and capture-metadata-v2 contract.  The manual form is
a normal server-rendered gallery refinement and preserves the numbered pagination decision in
ADR 0022.

Removing the availability gate changes configuration and presentation policy, not ADR 0019's
durable biometric privacy and authorization decision.  The existing gallery media selection still
passes through the delivery/download boundaries of ADRs 0020 and 0021 without changing their
authorization semantics.  No new or superseding ADR is required.
