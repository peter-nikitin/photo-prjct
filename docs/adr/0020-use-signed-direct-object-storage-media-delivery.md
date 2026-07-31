# 0020: Use signed direct Object Storage media delivery

- Status: Accepted
- Date: 2026-07-31
- Deciders: project maintainers
- Supersedes: ADR 0019 only for the inline-Django transport of authorized gallery and selfie-result media
- Superseded by: none

## Context

The current gallery and ready selfie-search result routes authorize a photo in Django and then
stream its Object Storage body through Django/Gunicorn. The gallery tile uses the accepted 1600px
`preview-small-v1` derivative; the lightbox uses the private original. With roughly 20,000 photos
expected at the next event, this makes the current single VM the image data plane.

ADR 0019 permits a valid ready-result bearer link to authorize inline original delivery for a saved
free or paid result member. Its search, snapshot, bearer, event-isolation, selfie-cleanup, and
paid-result boundaries remain valid. This ADR changes only the transport of already authorized
media.

## Decision drivers

- Remove image-body transfer from Django and Gunicorn before the next event.
- Keep Django/PostgreSQL authoritative for every authorization decision.
- Keep objects private without adding a public bucket, CDN, or media service.
- Preserve the paid-result bearer exception without opening the normal paid gallery.
- Avoid a third preview size or preview backfill before measured need.

## Considered options

1. Authorize in Django and redirect to a short-lived signed exact-object Object Storage GET URL.
2. Retain inline Django streaming and increase VM capacity/workers.
3. Introduce a CDN or dedicated media gateway.

## Decision

For each eligible media request, Django rechecks event, photo, gallery, or ready-search-result
authorization in PostgreSQL, verifies the selected object with its existing private Object Storage
control-plane access, and returns a redirect to a short-lived signed GET for that exact object. The
browser retrieves the body directly from private Object Storage.

The deterministic Django routes remain the only media URLs embedded in HTML. For a
preview-required gallery photo, `preview-small` redirects to the accepted `preview-small-v1`
derivative and `preview-large` redirects to the original. A legacy gallery photo has no accepted
derivative, so both variants redirect to its original under existing published free-event rules.

A normal paid gallery remains unavailable. A paid result-media route redirects only for a valid
ready bearer token and a member of that immutable result. It grants no access to another photo or
event, normal paid-gallery media, attachment downloads, or purchases.

The signed URL is an intentionally short-lived bearer capability. It can reveal the exact object
identity to its recipient and remains usable until expiry. Django must not put signed URLs in HTML,
JSON, database fields, logs, attempts, or error responses. Storage credentials remain server-only;
worker credentials and bucket policy do not change.

This ADR supersedes only ADR 0019's requirement that Django stream already-authorized media inline.
ADR 0019 otherwise remains accepted and authoritative.

## Consequences

### Positive

- Django/Gunicorn handles authorization, HTML, APIs, and redirects instead of image bodies.
- The current 1600px derivative remains the gallery tile and face-embedding input; no new image
  size, reprocessing, or backfill is required for this event.
- Gallery and selfie-result media retain stable application URLs and authorization boundaries.

### Negative

- Recipients can reuse a temporary signed URL until expiry.
- The object identity is visible to that recipient.
- Object Storage serves final transfer errors and direct egress must be measured separately from VM
  capacity.

### Follow-up

- Deliver cursor pagination for normal galleries and ready selfie-search results.
- Reconsider a smaller derivative, CDN, or media gateway only after measured direct-delivery cost,
  latency, or error rate warrants it.
- On acceptance, update ADR 0019's cross-link and the architecture summary; update them again only
  after implementation verification changes the implemented-state description.

## Validation and rollback

Validate current authorization before signing, exact tile-derivative and lightbox-original
selection, paid normal-gallery denial, and paid-result membership restrictions. Confirm the
redirect has no image body or permanent credential, application logs contain no signed URL, and a
browser completes the direct Object Storage transfer.

Rollback redeploys the prior image and restores inline streaming. Revisiting the stable application
route renews an expired signed URL. Rollback cannot revoke bytes or a signed URL already received;
expiry bounds the latter.

## References

- [Event Media Direct Delivery and Pagination Design](../superpowers/specs/2026-07-31-event-media-direct-delivery-and-pagination-design.md)
- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [ADR 0013: Use direct private Object Storage ingestion](0013-use-direct-private-object-storage-ingestion.md)
- [ADR 0017: Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
