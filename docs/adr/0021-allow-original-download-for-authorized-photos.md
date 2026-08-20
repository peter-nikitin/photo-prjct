# 0021: Allow original download for authorized photos

- Status: Accepted
- Date: 2026-08-01
- Deciders: project maintainers
- Supersedes: ADR 0019 and ADR 0020 only for their exclusion of attachment downloads
- Superseded by: [ADR 0029](0029-use-watermarked-previews-for-paid-photos.md) for original-download
  authorization by the new paid-photo generation only

## Context

The event gallery and ready selfie-search results already authorize full-resolution original
presentation through stable Django routes. ADR 0019 permits a ready bearer result to authorize a
saved free- or paid-event original but explicitly excludes attachment downloads. ADR 0020 moves
authorized media transfer from Django to short-lived signed Object Storage GET URLs and also keeps
attachment downloads outside its decision.

Visitors now need a one-click original download from every photo card and from the lightbox. There
is no implemented cart, purchase entitlement, or normal paid-event gallery path to govern a
different action. Adding a speculative free-versus-paid branch would not protect a current commerce
path and would make the immediate delivery larger.

## Decision drivers

- Deliver one-click original download through the current gallery and result surfaces.
- Reuse existing photo eligibility and saved-result membership instead of creating a parallel
  authorization model.
- Keep original bytes out of Django and preserve private Object Storage.
- Avoid commerce state or free-versus-paid branching before a real commerce path exists.
- Leave a concrete trigger for restoring a paid-media entitlement boundary.

## Considered options

1. Authorize attachment delivery whenever the existing gallery or ready-result media route already
   authorizes the original, then redirect to a short-lived signed exact-object GET.
2. Add a new free-only download policy while leaving paid ready-result originals viewable but not
   downloadable.
3. Use the HTML `download` attribute on the existing inline media URL without controlling Object
   Storage response disposition.

## Decision

Authorize attachment delivery of an original whenever the corresponding normal-gallery or ready
selfie-search result context already authorizes that photo. Django remains the authorization point
and Object Storage remains the data plane: Django rechecks current eligibility or saved-result
membership, signs one exact original with an attachment response disposition and a sanitized
filename, and redirects the browser to the short-lived URL.

This decision introduces no new event `access_type` condition. It does not open a normal paid-event
gallery or authorize unrelated photos. A ready-result bearer retains the exact membership boundary
from ADR 0019. Stable application URLs remain the only download URLs rendered in HTML; signed URLs,
permanent object keys, and credentials are not persisted or embedded.

The attachment filename is generated from the public photo identifier and validated media type,
not from a submitted filename or storage key.

This decision supersedes only the attachment-download exclusions in ADR 0019 and ADR 0020. Their
search, membership, privacy, transport, signing, expiry, and storage boundaries remain accepted.
It does not define cart behavior, purchases, payment state, entitlements, or purchased delivery.

## Consequences

### Positive

- Gallery and search-result photos gain the same small one-click download path.
- Authorization stays aligned with the media already visible to the visitor.
- Django does not proxy original bytes or acquire new persistent state.
- The implementation avoids a premature commerce abstraction and free-versus-paid matrix.

### Negative

- A valid paid-event ready-result bearer can download its existing original before commerce is
  implemented, as it can already view and save those original bytes.
- A short-lived signed attachment URL remains reusable until expiry.
- Download authorization cannot express purchase entitlement until a later commerce decision
  supersedes this temporary boundary.

### Follow-up

- Record the missing paid-photo cart and entitlement boundary in `docs/future-work/`.
- Revisit this decision when commerce implementation begins or before the first normal paid event
  is prepared for publication, whichever happens first.

## Validation and rollback

Validate that each authorized gallery and ready-result photo can obtain an exact-original signed
attachment redirect, that existing ineligible and non-member lookups still fail before signing,
and that rendered content contains no signed URL or object key. Confirm the response filename is
sanitized and derived from the photo identifier and validated media type.

Rollback removes the download actions and attachment routes while retaining existing inline
gallery and lightbox presentation. Already received bytes cannot be revoked; signed URL expiry
bounds reuse of an issued capability.

Reconsider immediately if a normal paid event is prepared for publication, commerce work begins,
or paid-original exposure is no longer acceptable.

## References

- [Architecture](../architecture.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [One-click original download design](../superpowers/specs/2026-08-01-one-click-original-download-design.md)
