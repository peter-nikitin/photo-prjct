# Direct Gallery Preview Links Design

## Status

Approved by the maintainer in conversation on 2026-09-14. ADR 0036 is accepted.

- Related architecture: [`docs/architecture.md`](../../architecture.md), public gallery and private
  Object Storage media delivery
- Related product job:
  [`PJ-005 — Visitor — Browse an event gallery`](../../product-jobs.md#pj-005--visitor--browse-an-event-gallery)
- Related specifications:
  [`2026-07-31-event-media-direct-delivery-and-pagination-design.md`](2026-07-31-event-media-direct-delivery-and-pagination-design.md)
  and
  [`2026-08-20-paid-watermarked-previews-design.md`](2026-08-20-paid-watermarked-previews-design.md)
- Related ADRs:
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md), and
  [ADR 0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md)
- ADR impact: **Requires a new ADR.** It must supersede ADR 0020 only for normal-gallery small
  presentation derivatives. Current authorization and redirect behavior remains authoritative for
  originals, downloads, ready selfie-search results, and legacy photos whose small presentation is
  the original.

## Outcome

One normal event-gallery HTML request authorizes its bounded page and issues direct, expiring Object
Storage GET URLs for the accepted small presentation derivatives already placed in that page's
grid. Loading those images requires no later Django request, PostgreSQL query, or Object Storage
metadata probe.

The page keeps lazy loading so opening a gallery does not start all 100 image transfers. Every
issued small-preview URL remains usable for six hours from page generation, allowing a visitor to
browse the complete page. Hiding a photo or unpublishing its event affects newly generated pages
immediately but does not revoke a capability already issued in an earlier page until that
capability expires. This bounded stale-presentation risk is explicitly accepted.

## Incident Evidence and Drivers

The 2026-09-14 production investigation found that one 100-card page contained 100 unique
`preview-small` application URLs. Face-crop images without an explicit loading policy caused every
one of those URLs to be requested eagerly. In a 50,000-request nginx sample, 44,206 requests were
for public `preview-small`; their mean application-edge time was 1.46 seconds, 1,892 took at least
five seconds, and 344 were abandoned by clients.

The current request path performs all of the following separately for every preview:

1. allocate a Django/Gunicorn request slot;
2. reload event and photo eligibility from PostgreSQL;
3. reload the selected derivative key;
4. call Object Storage `HEAD` to verify the object; and
5. create a signed URL and return a redirect.

The lazy-loading correction limits the initial fan-out but does not remove this repeated work as a
visitor scrolls. Issuing direct URLs during the already required page authorization removes the
fan-out from Django. A local, no-network benchmark created 1,000 S3-compatible signed URLs in about
213 milliseconds, so signing the maximum 100-card page is expected to add roughly 21 milliseconds
of local work before application-specific rendering overhead.

## Scope

### Included

- Normal published event-gallery pages, including their numbered and filtered pages.
- Direct signed small-preview URLs for accepted `preview-small-v1` derivatives in free galleries.
- Direct signed small-preview URLs for accepted `preview-watermarked-v1` derivatives in enabled
  paid galleries.
- One bounded authorization and presentation projection for all photos on the current page.
- A code-owned six-hour gallery-preview URL lifetime that does not affect another media capability.
- Local signing without request-time Object Storage inspection.
- Explicit lazy loading for every repeated face-crop image.
- Focused authorization, media-selection, query-bound, markup, expiry, and browser-request tests.
- A new ADR and updates to the architecture and evidence-backed job documentation.

### Excluded

- Direct original, lightbox, download, archive, or purchased-media URLs in HTML.
- Changes to ready selfie-search result media or bearer-result authorization.
- Direct links for a legacy photo whose small gallery presentation is its original.
- A public bucket, public object ACL, CDN, media gateway, Redis, or persisted page-grant model.
- Processing, derivative generation, backfill, face detection, embedding, ranking, or search changes.
- Private event-management pages and their summary or thumbnail paths.
- Revocation of an already issued small-preview capability before its six-hour expiry.
- Automatic refresh for a page kept open longer than six hours.
- A feature flag, runtime configuration parameter, or compatibility layer around the retired
  per-preview behavior.

## Selected Design

### Authorization snapshot

The existing normal-gallery queryset remains authoritative. It proves current event visibility,
photo visibility, storage-backed photo identity, media policy, and accepted processing evidence
before a photo can enter a page. Pagination continues to bound that decision to at most 100 cards.

Materializing the page also materializes the accepted small-presentation source for each card:

| Gallery media policy | Small presentation source | HTML URL |
| --- | --- | --- |
| `preview_required` | Accepted `preview-small-v1` derivative | Direct signed Object Storage GET |
| `watermarked_preview_required` | Accepted `preview-watermarked-v1` derivative | Direct signed Object Storage GET |
| `legacy_original_allowed` | Original | Existing Django `preview-small` route |

The derivative projection must preserve the same mutually consistent accepted state, accepted
successful attempt, and published derivative checks used for gallery eligibility. It retrieves the
bounded page's final keys without one query per photo. The implementation may use one constrained
prefetch after pagination or an equivalent bounded projection; its observable contract is no N+1
query growth between a one-card and a 100-card page.

The authorization decision is a snapshot. A capability is valid because its photo was eligible
when this HTML response was generated. Subsequent hide, unpublish, processing-state, or derivative
changes do not revoke that capability. A new page request must reflect the new state and must not
issue a new URL for an ineligible photo.

### Preview grant issuer module

A gallery-preview grant issuer is the seam between authorized presentation and Object Storage
signing. Its interface accepts the bounded, already authorized gallery-page photos and returns the
small presentation URL for each photo. It hides media-policy selection, accepted derivative
selection, key validation, expiry, and signing from the view and template.

The issuer has one Object Storage adapter for the page, not one adapter per card. For accepted
presentation derivatives it creates a presigned exact-object GET locally with a code-owned lifetime
of 21,600 seconds. It must not call `head_object`, `get_object`, or any other network operation.
The adapter still validates that a supplied key is a managed final presentation-derivative key
before signing it.

The storage interface must make the trust distinction explicit: request-time "verify then sign"
and page-authorized "sign accepted derivative" are different operations. A raw unverified key must
not silently gain access to the latter path. Tests cross the issuer interface rather than recreating
its media-policy decisions in views or templates.

### Rendered presentation

`GalleryPhoto.preview_media_small.url` receives the direct URL only for accepted presentation
derivatives. This keeps the existing template-facing presentation shape. The following fields do
not change:

- `preview_media_large.url` remains the stable Django application route;
- `download_url` retains its current authorization behavior;
- face-search form actions retain their current event, photo, detection, and CSRF behavior; and
- paid cart presentation and watermarked-media policy remain unchanged.

Every `<img>` that reuses `preview_media_small.url`, including face crops inside direct-search and
chooser controls, must declare `loading="lazy"`. The first four main gallery tiles retain their
existing eager/high-priority policy. Repeated elements for the same card reuse exactly the same
direct URL; they do not mint additional capabilities.

The page's existing cross-origin `Referrer-Policy: same-origin` prevents its URL from being sent to
Object Storage. Application access logs must not record response bodies or signed URLs. Signed
query parameters will be visible to the recipient and to Object Storage as part of the accepted
bearer-capability transport.

## Capability Lifetime and Failure Semantics

The code-owned direct-gallery-preview lifetime is **six hours (21,600 seconds)**. It applies only to
the small derivative URLs embedded by the normal gallery. It must not reuse or change
`PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS`, because that setting also governs original, worker, or
other short-lived grants with different exposure boundaries. It is not a deployment parameter.

- A visitor opening or scrolling the page within six hours can load every derivative placed in
  that page's grid, even if the photo was subsequently hidden.
- Reloading or navigating to another numbered/filtered page performs a new authorization snapshot
  and issues fresh URLs only for currently eligible photos.
- A page retained beyond six hours may receive an Object Storage expiry response for an image not
  yet loaded. The first version adds no refresh endpoint or client retry protocol; reloading the
  page is the recovery path.
- If an accepted object is unexpectedly missing after HTML generation, Object Storage returns its
  sanitized missing-object response. Django does not probe or translate that failure.
- Failure to construct the storage adapter or issue the complete current page fails the HTML
  request with the existing sanitized server-error behavior. The page must not mix direct and
  fallback application URLs for eligible derivative-backed cards.
- Legacy original-backed cards continue through the current application endpoint and retain its
  current `404`/`503` behavior.

## Security and Privacy Boundaries

- A signed URL is an exact-object bearer capability and may be copied or reused until expiry.
- HTML now reveals the accepted derivative's Object Storage identity and signature to the page
  recipient. It reveals no permanent access key or secret.
- Only reduced free previews and watermarked paid previews may use this path. A clean paid preview,
  free original, paid original, purchased original, or attachment disposition is forbidden.
- Event and photo eligibility must be proven before signing; arbitrary photo IDs, derivative keys,
  or variants are not issuer inputs.
- Current authorization still applies to every original, download, result-media, and legacy-original
  request.
- Hiding or unpublishing prevents issuance in every later HTML response but does not revoke an
  already delivered capability during its six-hour lifetime.

## Verification

### Automated critical path

- A published free event emits a direct signed small URL only for an accepted
  `preview-small-v1`; the same card's large and download URLs remain application routes.
- An enabled published paid gallery emits a direct signed small URL only for its accepted
  `preview-watermarked-v1`; neither the clean preview nor original appears in HTML.
- A legacy free card keeps the existing application small-media URL.
- Hidden photos, unpublished events, failed/stale/mismatched attempts, and missing derivatives do
  not receive a capability because they do not enter the gallery page.
- The issuer uses exactly 21,600 seconds and performs no Object Storage metadata or body request.
- One-card and 100-card pages have a bounded query count with no per-card query growth.
- All face-crop images are lazy; only the first four main cards remain eager/high priority.
- Signed URLs do not appear in application log messages or error responses.
- Existing large-media, download, paid-media, cart, face-search, pagination, and filter tests remain
  green.

### Browser and production verification

- On a representative 100-card production page, initial browser traffic requests only viewport or
  near-viewport small previews.
- Those small-preview requests go directly to Object Storage; nginx receives no corresponding
  normal-gallery `media/preview-small` requests for derivative-backed cards.
- The page HTML, first four images, a below-fold image, face-crop controls, lightbox, and permitted
  download all work.
- Reloading after a photo is hidden removes it and issues no new URL, while an already captured URL
  remains usable only within its accepted expiry window.
- Public event HTML and `/health/` remain successful during the bounded smoke check.

## Alternatives Rejected

### Stateless application capability followed by a redirect

HTML could contain an application token carrying the authorized key. Django could validate its
signature and issue an Object Storage URL without PostgreSQL or `HEAD`. This avoids database and
storage-control-plane work but still creates up to 100 Django/Gunicorn requests, consumes worker
request counters, and retains a redirect hop. It does not meet the primary request-fan-out goal.

### Batch signing endpoint

JavaScript could request fresh URLs for visible cards in batches. This bounds PostgreSQL checks and
supports long-open pages, but introduces a client protocol, JSON capabilities, failure recovery,
and repeated authorization that are unnecessary once six-hour snapshot access is accepted.

### Public or CDN-backed preview objects

Public derivative URLs or CDN signed delivery could also remove Django, but require a new storage
or edge revocation model and materially broader infrastructure. Exact-object private-bucket signed
GETs already satisfy the selected snapshot contract.

### Direct URLs for every media variant

Embedding originals, downloads, legacy-original small media, or result-media grants would issue
more powerful capabilities before user intent and would cross paid entitlement and bearer-result
boundaries. The performance evidence concerns small gallery previews, so those paths remain
unchanged.

## Rollout and Rollback

Implementation conforms to accepted ADR 0036. It ships in the normal immutable application image
with no new deployment configuration, schema, or data migration.

After deployment, verify the original public gallery symptom and compare normal-gallery
`preview-small` request volume with the pre-change sample. Rollback redeploys the prior image and
restores per-preview Django authorization for new requests. Rollback cannot revoke signed URLs
already issued by the changed release; their six-hour expiry bounds that residual exposure.
