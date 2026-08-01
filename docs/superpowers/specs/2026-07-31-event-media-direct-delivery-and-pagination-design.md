# Event Media Direct Delivery and Pagination Design

## Status

Proposed for review on 2026-07-31.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current gallery-media
  delivery, Object Storage boundary, and public event-scoped selfie search.
- Related product jobs: `PJ-005 — Visitor — Browse an event gallery` and `PJ-008 — Customer — Find
  photos by face`.
- Related specifications:
  [`2026-07-18-event-photo-gallery-design.md`](2026-07-18-event-photo-gallery-design.md),
  [`2026-07-30-preview-first-photo-processing-design.md`](2026-07-30-preview-first-photo-processing-design.md),
  and [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md).
- Related ADRs: [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md).
- ADR impact: **Requires new ADR.** The new ADR must replace only the current inline-Django
  media-transport rule in ADR 0019 and its superseded predecessor ADR 0015. It must preserve the
  remaining selfie-search, event eligibility, paid-result authorization, and Object Storage
  boundaries.

## Goal

Keep the next approximately 20,000-photo event usable on the current single VM by removing image
bytes from the Django/Gunicorn data path and bounding HTML, database, and browser work per page.

The gallery tile uses the already published normalized `preview-small-v1` derivative with a 1600px
maximum long edge. Opening a tile in the lightbox loads the eligible original at full resolution.
Neither flow streams bytes through Django. Both the normal gallery and a ready selfie-search result
show a bounded first page and let the visitor request subsequent pages.

## Scope

### Included

- Short-lived direct Object Storage GET delivery after a Django authorization check.
- Tile delivery of the existing `preview-small-v1` derivative for a preview-required photo.
- Full-resolution original delivery when a visitor opens an eligible gallery or selfie-search card.
- Cursor pagination for published free-event galleries.
- Cursor pagination for ready public selfie-search result snapshots.
- A bounded Gunicorn process configuration for the HTML, API, authorization, and redirect paths.
- Critical-path tests for media authorization, redirect safety, pagination, and existing result order.

### Excluded

- A third image size, re-encoding the current 1600px preview, or a preview backfill.
- CDN, public bucket ACLs, public object URLs, Object Storage credential broadening, or a new media
  gateway service.
- Paid-event normal-gallery access, purchase/download entitlements, watermarks, and cache/CDN
  policy beyond the signed-object response.
- Changes to worker lease semantics, preview generation, face-model inputs, face-ranking logic,
  selfie retention, or result snapshot semantics.
- A new VM, automatic VM resizing, load balancer, database service, broker, or other infrastructure
  expansion.

## Current Constraints and Selected Design

The current 1600px derivative is the only reduced image version. It remains the gallery tile and
the accepted preview-backed face-embedding input. A new smaller derivative would add processing,
storage, and migration work without addressing the immediate VM failure mode: Django currently
opens Object Storage objects and streams every requested body to the browser.

Django remains the authorization point. It reloads the product state for every media request, then
returns a redirect to a short-lived, exact-object signed GET URL. The browser fetches the image
body directly from the private bucket. Django must never include a permanent object key, storage
credentials, or signed URL in an HTML page, JSON result, database record, event log, processing
attempt, or error message.

The signed URL is intentionally a temporary bearer capability. A recipient can save the delivered
image today through the inline response as well; the change moves the transport out of the VM. The
new ADR must state the signed URL lifetime and the accepted exposure semantics explicitly.

The existing stable application media routes remain the only URLs placed in HTML:

```text
GET /events/<event-slug>/photos/<photo-id>/media/preview-small/
GET /events/<event-slug>/photos/<photo-id>/media/preview-large/
```

`preview-small` authorizes and redirects to the accepted `preview-small-v1` object for a
preview-required photo. `preview-large` authorizes and redirects to that photo's private original.
The route names and lightbox integration remain stable. A legacy photo redirects both variants to
its original because it has no accepted preview derivative; it remains subject to the same published
free-event eligibility and never acquires a new gallery route.

### Gallery eligibility

The normal public gallery remains limited to a currently published `FREE` event and the existing
database-confirmed gallery-media queryset. For preview-required photos, a tile requires the
accepted `generate_preview` attempt and its published `preview-small-v1` derivative. A missing,
unpublished, failed, stale, or mismatched derivative returns the existing sanitized `404`; Object
Storage failures return the existing sanitized `503`.

`preview-large` is eligible only for a photo already eligible for the normal gallery. It does not
make an original available for a queued or failed preview, an unpublished event, or a normal paid
event gallery.

### Selfie-search eligibility

A ready selfie-search result is still a frozen ordered snapshot. Pagination may omit a result from
presentation when its event or photo is no longer eligible, but it must not reorder the remaining
rows or recompute matching.

For a free result, media follows the normal gallery authorization. For a paid result, only the
valid ready bearer token for the saved snapshot may authorize a result member's `preview-small` or
`preview-large` redirect. This preserves ADR 0019's narrow paid-result exception and never opens
the normal paid gallery or an unrelated photo URL.

## Pagination Contract

Both collection endpoints use opaque cursor pagination, not numeric offset pagination. The cursor
is a signed, versioned, bounded representation of the last emitted stable ordering key. It is
invalid outside its collection identity and route context.

### Normal gallery

- Default and maximum page size: **100 photos**.
- Stable order: ascending photo ID, matching the existing gallery order.
- First request has no cursor; later requests use `?cursor=<opaque-token>`.
- A page contains its items and `next_cursor`, which is null only when no further eligible item
  exists.
- A malformed, expired, collection-mismatched, or tampered cursor returns a sanitized `404` rather
  than selecting a different collection.

### Ready selfie-search result

- Default and maximum page size: **100 photos**.
- Stable order: persisted result rank, then photo ID as the immutable tie-breaker.
- The cursor is bound to the search's public-token identity and cannot enumerate another result.
- The displayed matched-photo count remains the snapshot count; the page may separately state how
  many rows are currently displayable if publication changes remove saved members.
- Queued, processing, cleanup-pending, and terminal-failure result pages do not expose pagination.

The first page must preserve the current server-rendered page and progressive enhancement model.
Subsequent pages may be returned as an HTML fragment for an accessible "Show more" action. With
JavaScript disabled, the same action follows a normal link to the next cursor page. The document
must not create an unbounded client-side list or a separate client-only API.

## Browser Behavior

The event page and ready selfie-search page initially render at most 100 cards. Card image `src`
values keep the stable Django `preview-small` route and retain current eager/lazy loading behavior.
The lightbox `href` keeps the stable Django `preview-large` route. Navigation to either route
receives one authorization redirect and then transfers bytes directly from Object Storage.

The browser does not receive signed URLs in embedded markup and does not persist them. On expiry,
normal browser reload/navigation requests the stable Django route again and receives a fresh
authorization decision and signed URL.

## Gunicorn Bound

The production entrypoint must explicitly run a bounded multi-worker configuration appropriate for
the approved 8-vCPU VM: five workers, two threads per worker, bounded worker recycling, and a
finite request timeout. This protects the HTML/API/redirect path from one slow request without
turning Gunicorn into an image-transfer service. The exact flags are implementation details, but
the configuration must be observable in the running command and covered by an entrypoint contract
test.

## Failure Semantics

- Django evaluates access before creating a signed GET; it never creates a signed URL for a
  non-eligible event, photo, result, or route variant.
- Django verifies the selected exact object with its existing private-storage control-plane access
  before signing. A missing object is therefore a sanitized `404`, rather than a redirect to a
  bucket-side error.
- Signing or Object Storage availability failures return sanitized `503` and never fall back to
  Django streaming.
- A signed URL expiring after redirect is retried by navigating to the stable application URL; the
  redirect response itself must not embed a reusable retry credential.
- Missing object, derivative, or current accepted processing evidence returns sanitized `404`.
- The redirect carries no permanent credentials. Application logs redact the `Location` value and
  all signed-query parameters.
- A cursor failure never leaks a collection identifier, rank, photo ID, or another visitor's bearer
  token.

## Acceptance Criteria

- A free published event with 20,000 eligible photos renders no more than 100 cards in its first
  response and exposes a working next-page action.
- A ready selfie-search result with more than 100 current members renders the stored first 100
  ranks and can reach later ranks without changing their order.
- A preview-required tile request yields a redirect to only its accepted `preview-small-v1` object;
  its final response body is not read by Django.
- An eligible lightbox request yields a redirect to only that photo's original object; its final
  response body is not read by Django.
- A normal paid-gallery media request remains denied. A paid result member is reachable only with
  its valid ready search bearer link and only when the event remains published.
- A signed URL, object key, and signing credentials do not appear in rendered HTML, JSON,
  PostgreSQL persistent records, or application logs.
- Invalid variants, missing derivatives, ineligible publication, invalid/mismatched cursors, and
  Object Storage signing failures retain sanitized `404`/`503` behavior.
- Existing GLightbox keyboard, focus, no-JavaScript anchor fallback, and mobile behavior continue
  to work across page navigation.

## Alternatives Rejected

### Stream images through Django with more VM capacity

This preserves the current private-key concealment behavior but leaves the VM as the data plane for
every 1600px preview and full original. It does not meet the event-scale goal.

### Add a 640px tile derivative now

This would reduce per-tile transfer but creates a second derivative contract and a backfill before
measuring whether direct Object Storage delivery plus pagination is sufficient. The current 1600px
preview must remain for face embedding. Reconsider a smaller tile only if the measured post-rollout
page or Object Storage transfer cost is unacceptable.

### Add CDN or a dedicated media service

Either can be revisited after measured need, but both introduce infrastructure and operational
surface not required to remove Django from the transfer path before this event.

## Rollout and Revisit Trigger

The implementation plan must require a staged deployment of the immutable candidate, then a
real-object smoke test of tile and lightbox routes and a 20,000-row pagination fixture/load check.
On any failure, it must roll back to the previous immutable application image. The plan must not
treat a successful redirect as proof of a complete body transfer.

After the controlled 20,000-photo staging check, record its Object Storage egress, client tile-load
time, direct-delivery error rate, and authorization latency, then agree operating thresholds before
the next event. Revisit a smaller tile derivative or CDN only if a later staging check or event
violates one of those thresholds. These are future performance decisions, not prerequisites for
this delivery.
