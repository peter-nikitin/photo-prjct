# 0045: Deliver commerce thumbnails through the gallery CDN

- Status: Accepted
- Date: 2026-09-30
- Deciders: project maintainer
- Approval: maintainer requested CDN delivery for cart and order thumbnails after the current paths and HTML-time order authorization were explained.
- Supersedes: [ADR 0038](0038-deliver-gallery-grid-images-through-cdn-and-imgproxy.md) only for its exclusion of commerce small-preview presentation.

## Context and decision

Cart and order thumbnails currently request Django media routes, which authorize each image and redirect to Object Storage. The normal gallery already emits six-hour CDN capabilities for accepted preview objects during HTML rendering. Reuse that delivery for commerce thumbnails rather than retaining application requests for each image or introducing another derivative and delivery service.

Use the existing `gallery-cdn-images` gate and fixed `gallery-v1` transform. Cart rendering first applies the existing cart and photo eligibility checks. Order rendering first authorizes the browser or access grant and selects only items belonging to that order. Only then may either page sign the exact accepted watermarked preview from the loaded gallery-media projection. A missing required preview must never select a clean preview or original.

Paid order presentation retains its existing eligibility semantics after a photo is hidden or an event unpublished. CDN signing must not reapply public-gallery visibility to an already authorized order. Order access denial prevents rendering capabilities; issued URLs retain their existing bounded six-hour lifetime. Revocation prevents new capabilities but does not invalidate an already received browser copy or unexpired capability.

Large/lightbox images, entitled originals, downloads, archives, selfie results and private management media retain their current authorization and delivery. With the gate off, commerce thumbnails retain their existing application routes. No new infrastructure, flag, derivative, schema or backfill is required.

## Consequences and verification

Small images avoid Django and database work after an authorized HTML response and reuse transformed CDN objects across gallery, cart and order. The same CDN availability dependency and bounded capability semantics apply as in ADR 0038.

Verify cart and browser/grant order HTML, accepted watermarked-source selection, cross-order/access denial, hidden and unpublished paid-order entitlement, missing-preview fail-closed behavior, gate off/staff/on, and unchanged large/download URLs. Verify the deployed generated small-image URL returns a JPEG from the CDN. Roll back the application release or disable the existing gallery gate; media and commercial records remain intact.
