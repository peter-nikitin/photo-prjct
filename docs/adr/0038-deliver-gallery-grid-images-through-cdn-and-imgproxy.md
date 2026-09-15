# 0038: Deliver gallery grid images through CDN and imgproxy

- Status: Accepted
- Date: 2026-09-15
- Deciders: project maintainers
- Supersedes: [ADR 0036](0036-issue-direct-gallery-small-preview-capabilities.md) only for
  normal-gallery small presentation derivatives
- Superseded by: none

## Context

ADR 0036 removed normal-gallery small-preview requests from Django and PostgreSQL by embedding a
six-hour exact-object Object Storage capability in each rendered card. Production measurement
confirmed that application control-plane work is no longer the remaining bottleneck, but direct
delivery still transfers a 1600-pixel preview for a card rendered at roughly 285 by 213 CSS pixels.

On the representative `cyclingrace-moscow-2026` page, 100 unique previews account for about
33.6 MiB. Sample time to first byte was about 500 ms, and the objects supplied no useful browser
cache lifetime. A representative 960-pixel quality-78 encoding reduced estimated page transfer to
about 11.5 MiB. Reducing dimensions produced the material saving; re-encoding at 1600 pixels did
not.

Persisting another derivative would add processing, storage, acceptance state, and backfill. An
on-demand transformer avoids that durable set, but placing transformation or a proxy cache on the
canonical application VM would make gallery image CPU, network, and disk load contend again with
Django, PostgreSQL, and workers. The delivery topology therefore needs a durable decision.

## Decision drivers

- Reduce cold-page image bytes as well as repeated origin fetches.
- Add no persistent derivative, processing job, database row, or media backfill.
- Keep grid-image requests and transformation load off the canonical application VM.
- Preserve bounded page authorization and six-hour capabilities.
- Preserve accepted clean previews for free galleries and accepted watermarked previews for paid
  galleries without fallback.
- Restrict the transformer to preview objects and a fixed, resource-bounded transform.
- Keep rollback independent of media migration or cache purge.
- Pay only for a small CDN resource and isolated image compute; optional premium CDN features are
  not required by this decision.

## Considered options

1. Put Yandex Cloud CDN in front of an isolated imgproxy origin that reads existing accepted
   previews and creates a fixed smaller representation on cache miss.
2. Put a hard Nginx proxy cache, with or without resize, on the canonical application VM.
3. Retain direct Object Storage delivery from ADR 0036 and change browser caching only.
4. Generate and persist another gallery-grid derivative for every photo.

## Decision

Select option 1.

For the small presentation of a normal public event-gallery grid, Django retains its current
bounded page authorization and accepted-derivative selection. It emits a six-hour Yandex Cloud CDN
capability whose signed, versioned path identifies one exact accepted preview and the fixed
`gallery-v1` transformation. CDN authentication applies to cache hits and misses. Authorization
query parameters do not fragment the cache key.

The source is `preview-small-v1` for an eligible free photo and `preview-watermarked-v1` for an
eligible paid photo. The first transform fits the accepted JPEG inside a 960-pixel long edge,
never upscales, encodes progressive JPEG at quality 78, and removes metadata. The card and its face
crops reuse the same transformed resource. Any output-affecting change requires a new transform
version in the path.

On a miss, CDN calls an image origin isolated from the canonical application VM. The origin runs
only a narrow Nginx boundary and imgproxy. It has no Django or PostgreSQL dependency and retains no
durable result cache. imgproxy uses a dedicated principal that can perform only `s3:GetObject` on
`derivatives/previews/*`; it cannot list the bucket, access originals or staging objects, or mutate
media. Signed paths, a fixed preset, source restrictions, resource limits, and CDN-to-origin
authentication fail closed.

CDN holds reproducible transformed bytes as a transient edge cache. The edge TTL may exceed the
viewer capability lifetime because every network request still requires an unexpired CDN token.
A browser may retain a previously authorized private copy for the bounded browser-cache period
defined by the design.

Keep ADR 0036 and current application routes authoritative for large/lightbox presentation,
downloads, archives, selfie-search result media, private management media, purchased media, and a
legacy photo whose small presentation remains its original. ADR 0029 remains authoritative for
paid watermarked-source selection.

The existing application Nginx does not proxy, cache, or transform these derivative-backed grid
images. It remains the application TLS edge from ADR 0007. ADR 0003 remains authoritative for the
canonical application deployment; the image origin is a separate replaceable delivery resource,
not a migration of application services.

## Consequences

### Positive

- Cold gallery pages transfer materially smaller images.
- Warm requests avoid both transformation and Object Storage reads.
- Gallery image load cannot consume application VM CPU, disk cache, or Django/PostgreSQL capacity.
- No derivative backfill, acceptance migration, or permanent image-result store is introduced.
- Immutable source keys and versioned transforms make long edge caching safe without purge.
- Rollback changes only newly rendered URLs.

### Negative

- Production gains a separately operated CDN resource, image-origin VM, domain, certificate,
  credentials, monitoring, and cost.
- A cold cache still depends on image-origin and Object Storage latency and capacity.
- Failure of the isolated origin can break uncached images even while gallery HTML remains healthy.
- CDN secure-token behavior, cache-key configuration, and origin isolation become security-critical.
- The fixed 960-pixel output may need a new version if representative smallest face crops fail
  visual acceptance.

### Follow-up

- Write an implementation plan only after this ADR is explicitly accepted.
- Validate the smallest viable image-origin VM through cold-cache load and saturation tests.
- Remove the rollout feature gate and obsolete direct derivative-backed small-preview issuer after
  stable public operation.
- Reconsider multiple transform sizes or formats only when measured browser cohorts justify the
  additional cache cardinality and visual contract.

## Validation and rollback

Validate the same representative 100-card cohort used for the direct-delivery baseline. Require at
least 60 percent lower median image bytes, no more than 15 MiB for a complete cold page, and at
least 50 percent faster completion of the first 12 unique grid images. A repeated run must be
served from CDN without imgproxy or Object Storage fetches. Saturating the image origin must not
change application HTTP capacity or worker throughput.

Verify free and paid accepted-source selection, face-crop geometry, watermark visibility, token
expiry, cache-key behavior, prefix-only Object Storage access, and rejection of unsigned,
arbitrary-source, arbitrary-transform, original, staging, oversized, and mutation requests.

Roll back the runtime gate so newly rendered pages use ADR 0036 direct exact-object capabilities.
No data rollback, cache purge, or media backfill is needed. Already rendered CDN capabilities and
browser copies retain their bounded lifetimes. Reconsider this decision if isolated origin cost or
reliability fails the measured acceptance criteria, or if CDN authentication cannot preserve the
private-media boundary.

## References

- [CDN and imgproxy gallery delivery design](../superpowers/specs/2026-09-15-cdn-imgproxy-gallery-delivery-design.md)
- [Architecture](../architecture.md)
- [ADR 0003: Use Docker Compose on a Yandex Cloud VM](0003-docker-compose-yandex-cloud.md)
- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [ADR 0007: Use Nginx and Certbot for the public HTTPS edge](0007-nginx-certbot-https-edge.md)
- [ADR 0020: Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0028: Operate one canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0029: Use watermarked previews for paid photo presentation](0029-use-watermarked-previews-for-paid-photos.md)
- [ADR 0036: Issue direct gallery small-preview capabilities](0036-issue-direct-gallery-small-preview-capabilities.md)
