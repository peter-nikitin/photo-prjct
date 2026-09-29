# 0044: Deliver public event covers through the image CDN

- Status: Accepted
- Date: 2026-09-29
- Deciders: project maintainer (explicit public-cover source exception approved in chat)
- Supersedes: [ADR 0038](0038-deliver-gallery-grid-images-through-cdn-and-imgproxy.md) only for its exclusion of public event covers from the image origin
- Superseded by: none

## Context

The event catalog currently renders the full uploaded cover from the public Object Storage
bucket. Covers have immutable `event-covers/` keys under ADR 0006. The existing isolated image
origin and CDN can reduce their transfer size without creating another permanent derivative set.
ADR 0038 restricts that origin to private accepted previews; public covers require an explicit,
narrow source exception rather than a general-purpose public URL transformer.

## Decision drivers

- Reduce catalog-cover bytes without additional compute resources or permanent thumbnails.
- Preserve catalog publication rules and private gallery authorization.
- Retain immutable source identity, versioned transforms and reversible rollout.
- Add no credentials or private Object Storage access.

## Considered options

1. Extend the existing isolated image origin with one exact public cover prefix.
2. Persist an additional reduced cover during each upload.
3. Place a second CDN directly in front of full-resolution covers without resizing.

## Decision

Select option 1. The catalog retains Django HTML and its existing visible-event query. A separate
code-owned `event-cover-cdn-images` gate controls issuing signed CDN URLs for event covers,
independently of `gallery-cdn-images`. New definitions reconcile in `off`; staff acceptance and
public activation remain explicit Admin operations.

Add only `https://storage.yandexcloud.net/<public-bucket>/event-covers/` to the transformer's
allowed sources. Django signs an exact managed cover key, never a caller-provided URL. Redirects
remain disabled. The dedicated principal and its private preview-only S3 policy are unchanged;
HTTP cover reads use the object's existing public-read authority and no credentials.

The fixed `cover-v1` preset fits the source within 960 pixels in either dimension without
upscaling, uses progressive quality-78 JPEG and removes metadata. Existing CSS card cropping
remains authoritative. Only the first four covers are eager; subsequent covers are lazy. Changing
output-affecting settings requires a new version in the signed path.

Normalize new Admin cover uploads before storage to the same 960-pixel bound, progressive
quality-78 JPEG, without upscaling or metadata and with EXIF orientation applied. Store only that
reduced public cover under a new immutable UUID key; do not retain its full uploaded source or
create an additional derivative. Ordinary event edits do not read or rewrite existing covers.
Already stored objects remain unchanged; check their compatibility before activating CDN covers
and re-upload an incompatible cover through Admin under a new key rather than overwriting it.

Cover URLs use the existing six-hour CDN secure token and imgproxy HMAC. Successful cover
responses permit a one-year immutable browser cache and a 30-day CDN edge cache. These are public
objects: long browser retention is not a private-media revocation contract. Replacing a cover
creates a new key and URL; neither replacement nor unpublishing requires purging the old bytes.
Network requests still require a valid CDN token even when edge bytes are cached. Private gallery
sources, paths, capability lifetime and browser-cache policy remain unchanged.

Clear the shared CDN browser-cache override so the origin's per-preset response headers remain
authoritative: six hours for `gallery-v1`, one year for `cover-v1`. Keep the existing edge TTL,
secure-token validation, cache identity and CDN-to-origin authentication. Verify both headers on
actual cold and warm CDN responses before activating catalog covers.

No cover-storage migration, schema change, backfill, new VM, CDN resource, domain, IAM grant or
persistent result cache is introduced. Missing delivery prerequisites must not break catalog HTML;
the existing public cover remains usable. CDN/image failures never fall back to private originals.

## Consequences

### Positive

- Catalog covers reuse independently bounded image capacity and warm CDN caching.
- No new media lifecycle or durable processing state is required.
- The new public source does not broaden private-media permissions.

### Negative

- Cold covers consume the same finite transform capacity as gallery images.
- New cover uploads retain no full-resolution source for later reprocessing.
- Existing covers require compatibility checks before activation; normalization does not rewrite
  historical objects automatically.
- Browsers may retain old public covers long after replacement or unpublishing.
- Origin configuration and application URL emission need separate deployment and acceptance.

## Validation and rollback

Verify off/missing, staff and public flag behavior, unchanged publication filtering, placeholder
and eager/lazy behavior, exact managed source signing and stable image identity on page refresh.
Verify the actual Admin upload saves only the reduced JPEG, keeps small inputs small, applies
orientation and leaves the stored cover untouched when an event is edited without a new upload.
Exercise real Nginx/imgproxy for the cover transform, successful-only cache policy, and rejection
of foreign public prefixes, arbitrary transforms, altered signatures and private originals.
Retain the existing gallery acceptance case and verify its cache policy is unchanged.

Deploy application and origin with the gate off, validate staff cold/warm delivery, then activate
public exposure separately. Rollback sets the gate to `off`; new HTML uses the existing public
cover URL. No data rollback or cache purge is needed. Roll back origin only after disabling new
cover URLs; previously rendered cover capabilities can fail on an older origin.

## References

- [ADR 0006](0006-yandex-object-storage-media.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [ADR 0032](0032-reconcile-code-owned-feature-flags-at-startup.md)
- [ADR 0038](0038-deliver-gallery-grid-images-through-cdn-and-imgproxy.md)
- [Architecture](../architecture.md)
