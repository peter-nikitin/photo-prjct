# 0036: Issue direct gallery small-preview capabilities

- Status: Accepted
- Date: 2026-09-14
- Deciders: project maintainers
- Supersedes: [ADR 0020](0020-use-signed-direct-object-storage-media-delivery.md) only for
  normal-gallery small presentation derivatives
- Superseded by: none

## Context

A normal event-gallery page contains at most 100 cards. The current HTML gives every card a stable
Django `preview-small` URL. Each requested preview separately consumes a Django/Gunicorn slot,
rechecks event and photo eligibility in PostgreSQL, resolves the derivative, verifies the object
with Object Storage, and redirects to a signed GET.

Production evidence showed that these redirects dominated request volume and became slow under
contention. Lazy loading reduces the initial burst but does not remove the repeated authorization
and storage-control-plane work as a visitor browses the page.

The gallery page already makes one bounded current-eligibility decision before rendering each card.
The maintainer accepts that a small presentation derivative already placed in that grid may remain
visible for a bounded period after the photo is hidden or the event is unpublished.

## Decision drivers

- Remove normal-gallery small previews from scarce Django request capacity.
- Preserve current authorization for originals, paid entitlements, and selfie-result bearers.
- Keep Object Storage private and grant access only to an exact accepted derivative.
- Let every preview in a rendered 100-card grid load during a normal browsing session.
- Avoid a new service, cache, persistent grant model, or runtime configuration surface.

## Considered options

1. Issue exact-object signed small-preview URLs while rendering the authorized gallery page.
2. Put stateless application capabilities in HTML and retain one Django redirect per preview.
3. Batch-authorize visible preview URLs from JavaScript.
4. Make preview objects public or introduce a CDN/media gateway.

## Decision

Select option 1 for normal-gallery small presentation derivatives only.

When Django renders one bounded normal-gallery page, it authorizes current event and photo
eligibility and resolves the accepted presentation derivative. It embeds an exact-object signed GET
for `preview-small-v1` on an eligible free photo or `preview-watermarked-v1` on an eligible paid
photo. Signing is local and relies on mutually consistent accepted processing evidence; it does not
inspect Object Storage.

The code-owned capability lifetime is six hours. Hiding a photo or unpublishing its event prevents
the next HTML response from issuing a capability but does not revoke one already delivered. A page
kept open beyond six hours must be reloaded to renew an unloaded preview.

Keep current request-time authorization and delivery for `preview-large`, downloads, archives,
purchased media, ready selfie-search result media, private event-management media, and a legacy
photo whose small presentation is its original. ADR 0029 remains authoritative for selecting the
watermarked derivative in a paid gallery.

Every face-crop image reusing a small-preview URL loads lazily. Only the first four main gallery
tiles retain their eager, high-priority policy.

## Consequences

### Positive

- Small derivative loads after HTML generation do not reach Django or PostgreSQL.
- Object Storage receives image GETs directly and no longer receives a preceding `HEAD` for each
  page preview.
- One bounded eligibility decision selects every preview in the rendered grid.
- The change adds no schema, durable state, runtime service, or deployment parameter.

### Negative

- HTML exposes an exact derivative key and temporary bearer signature to its recipient.
- A previously issued derivative remains available for up to six hours after hide or unpublish.
- An unexpected missing object fails at Object Storage rather than as a Django-translated response.
- A page left open beyond six hours can require reload before an unloaded preview appears.

### Follow-up

- Measure gallery HTML latency, Django preview-request volume, direct image success, and public 5xx
  after deployment.
- Reconsider a CDN only if direct Object Storage transfer or signing becomes a measured bottleneck.

## Validation and rollback

Validate one accepted free derivative, one accepted paid watermarked derivative, one legacy photo,
unchanged large/download behavior, six-hour local signing without storage inspection, bounded query
count, and real browser lazy loading. On production, confirm that derivative-backed small previews
go directly to Object Storage and cause no corresponding nginx application requests.

Rollback deploys the previous application image and restores per-preview Django authorization for
new page loads. Rollback cannot revoke capabilities already issued; their six-hour expiry bounds
the residual access.

## References

- [Direct Gallery Preview Links Design](../superpowers/specs/2026-09-14-direct-gallery-preview-links-design.md)
- [Architecture](../architecture.md)
- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0022: Use numbered gallery pages](0022-use-numbered-gallery-pages.md)
- [ADR 0029: Use watermarked previews for paid photo presentation](0029-use-watermarked-previews-for-paid-photos.md)
