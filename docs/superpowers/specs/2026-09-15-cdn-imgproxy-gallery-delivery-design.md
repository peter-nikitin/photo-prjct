# CDN and imgproxy delivery for gallery grid images

Status: Approved
Date: 2026-09-15  
Related architecture: `docs/architecture.md` (gallery media delivery, Object Storage, production topology)  
Related ADRs: `docs/adr/0003-docker-compose-yandex-cloud.md`, `docs/adr/0006-yandex-object-storage-media.md`, `docs/adr/0007-nginx-certbot-https-edge.md`, `docs/adr/0020-use-signed-direct-object-storage-media-delivery.md`, `docs/adr/0022-use-numbered-gallery-pages.md`, `docs/adr/0028-operate-one-canonical-deployment.md`, `docs/adr/0029-use-watermarked-previews-for-paid-photos.md`, `docs/adr/0036-issue-direct-gallery-small-preview-capabilities.md`, `docs/adr/0038-deliver-gallery-grid-images-through-cdn-and-imgproxy.md`
ADR impact: Accepted by ADR 0038, which supersedes ADR 0036 for normal-gallery small-image delivery and amends the production topology from ADRs 0003 and 0007 by adding an isolated CDN/image origin while retaining the existing application edge.

## Summary

Normal event-gallery grids will load a smaller, request-time image representation through Yandex Cloud CDN and an isolated imgproxy origin. The source remains the already accepted, immutable preview in private Yandex Object Storage. imgproxy creates no persistent derivative: Yandex CDN keeps only a transient edge copy.

Django still decides which photos and which accepted preview variant a viewer may see. During the single bounded page query, it emits a six-hour CDN capability for each authorized source. Subsequent grid-image requests do not reach Django, PostgreSQL, the application Nginx, or photo-processing workers.

The first version uses one fixed `gallery-v1` transform: fit within a 960-pixel long edge, never upscale, JPEG quality 78, progressive encoding, metadata removed. The same URL is reused by a photo card and its face crops so a 100-card page still has at most 100 distinct grid-image resources.

The image origin runs on compute isolated from the canonical application VM. Expensive or adversarial transforms therefore cannot consume the capacity needed for galleries, selfie search, uploads, or background processing.

## Problem

The current grid uses accepted previews with a 1600-pixel long edge. On the representative `cyclingrace-moscow-2026` page, desktop cards render at roughly 285 by 213 CSS pixels, while the browser downloads 1600 by 1067 or 1067 by 1600 JPEGs. A sample showed approximately 370 KiB per image and 33.6 MiB for 100 unique images. The objects also had no browser cache lifetime, and sample time to first byte was approximately 500 ms.

Local representative re-encoding showed that a 960-pixel, quality-78 image reduces the estimated 100-image transfer to approximately 11.5 MiB, about 67 percent below the existing source set. Re-encoding at the original dimensions saved little; dimensional reduction is the material improvement.

Generating and retaining another derivative for every photo would increase storage state, processing work, backfill needs, and acceptance bookkeeping. Request-time transformation avoids that permanent set, but it is safe only when transformation capacity is isolated and repeated work is absorbed by a CDN.

## Goals

- Reduce bytes and load time for normal event-gallery grid images without persisting another derivative set.
- Keep gallery HTML generation bounded to the open numbered page and preserve one authorization decision per page render.
- Keep grid-image requests off Django, PostgreSQL, the application Nginx, processing workers, and the canonical application VM.
- Preserve free-event and paid-event media policy, including mandatory watermarking for paid public galleries.
- Preserve the six-hour page capability contract and allow every image placed into a rendered grid to finish loading during that interval.
- Bound the image processor's source access, accepted operations, resource consumption, and failure blast radius.
- Make cold-cache cost and warm-cache benefit directly observable.

## Non-goals

- Changing originals, accepted preview generation, processing jobs, or `PhotoDerivative` records.
- Adding a persistent thumbnail bucket, imgproxy result storage, local disk cache, or a backfill.
- Changing large/lightbox images, downloads, selfie-search results, private management views, cart/order media, or purchased-photo delivery.
- Moving Django, PostgreSQL, Nginx, or processing workers to new infrastructure.
- Introducing arbitrary client-selected sizes, formats, quality values, source URLs, or transformation pipelines.
- Solving private-event delivery; that path is being designed separately.
- Selecting final VM sizing or writing an implementation/deployment sequence. Those belong to the implementation plan after the design and ADR are approved.

## Product contract

### Eligible presentation

Only the small presentation used by the normal public event-gallery grid changes.

- A free event uses its accepted `preview-small-v1` object.
- A paid event uses its accepted `preview-watermarked-v1` object. imgproxy must never receive or substitute the clean preview for this presentation.
- A legacy photo without the required accepted derivative keeps its existing application-served presentation and is outside the new CDN path.
- A card and all face crops rendered from that card use the same `gallery-v1` image URL. CSS crop geometry remains in the `preview-small-v1` coordinate space.
- Existing lightbox, large-preview, download, purchase, and entitlement URLs remain unchanged.

The page continues to render the first four card images eagerly with high fetch priority and later cards lazily. Face-crop images remain lazy. This design changes bytes and delivery, not gallery ordering or loading semantics.

### Capability lifetime

Each rendered URL is usable for six hours from page generation. This intentionally favors completing the visible grid over rapidly revoking an already rendered card. If a photo is hidden after page generation, Django stops issuing new capabilities, but a capability already present in that page can continue to work until its expiry.

The CDN must validate expiry on every network request, including cache hits. Keeping transformed bytes at an edge after expiry does not authorize access to them.

## Delivery design

```text
gallery request
    |
    v
Django: authorize one page and choose accepted source keys
    |
    | HTML with six-hour CDN capabilities
    v
browser ---- cache hit ----> Yandex Cloud CDN
                              |
                              | cache miss, CDN-authenticated origin request
                              v
                    isolated origin Nginx
                              |
                              | signed fixed transform path
                              v
                           imgproxy
                              |
                              | GetObject: derivatives/previews/* only
                              v
                    private Object Storage
```

### URL structure and trust boundaries

All cache-relevant identity is in the path:

1. a versioned preset name, `gallery-v1`;
2. an imgproxy HMAC signature over the complete processing path;
3. an encoded, exact accepted source object key;
4. a fixed `.jpg` output extension.

Only the Yandex CDN authorization values are query parameters: the secure-token signature and expiry. Resize dimensions, quality, format, and source selection are not query parameters and cannot be chosen by the browser.

Django creates both signatures while rendering the already bounded page. It must not perform Object Storage metadata calls, HTTP calls to imgproxy/CDN, or additional per-photo database queries. The source key comes from the accepted derivative projection already used for authorization.

Yandex CDN validates its secure token before serving either a cache hit or a miss. Its cache key ignores only the CDN authentication query parameters. Thus separately rendered six-hour capabilities share the same cached object when their immutable source key and transform version match.

On a cache miss, CDN calls the dedicated image origin. Origin Nginx requires a secret CDN-to-origin request header, removes untrusted forwarding headers, applies request and connection limits, and forwards only valid image paths to imgproxy. imgproxy independently validates the path HMAC before fetching a source.

There is no unsigned image-origin route and no redirect to a raw Object Storage URL.

### Transform contract

`gallery-v1` is a server-owned preset with these fixed semantics:

- read only an accepted JPEG preview;
- apply orientation already represented by the accepted source;
- fit within a 960-pixel long edge while preserving aspect ratio;
- never upscale;
- encode JPEG at quality 78 using progressive/optimized output;
- remove metadata;
- return a deterministic content type and cache policy.

Changing any output-affecting setting requires a new preset version in the URL path. Existing cached representations therefore remain correct without purge or key mutation.

The 960-pixel size is chosen for the existing four-column desktop grid, high-density screens, and reuse by 44-pixel face crops. Acceptance must include the smallest representative face crops; if they are visibly worse, the design returns for review rather than silently adding a second face-specific transform.

### Cache policy

Source keys are immutable and transform versions are explicit, so transformed responses have a 30-day CDN edge TTL. This is transient delivery cache, not application-owned media state: eviction is harmless and a miss can reproduce the response.

Browser responses use a six-hour cache lifetime. CDN authentication is evaluated for every network request, but a browser may reuse its own previously authorized local copy without a network request after the URL itself expires. In the worst case, a copy fetched immediately before URL expiry can remain in that browser for another six hours. This bounded revocation delay is accepted: it helps every image already placed into the rendered grid finish and does not let a different viewer fetch the object. No application purge is required for hiding a photo.

Origin shielding or equivalent request coalescing is enabled so concurrent first requests for one object do not cause duplicate transforms. Errors, authorization failures, and placeholder responses are not cached as successful images.

## Infrastructure isolation

The image origin is a dedicated Yandex Cloud VM running only an origin Nginx and imgproxy under Docker Compose. It is not the canonical application VM and has no PostgreSQL, Django, application Redis/cache, worker API, or SSH dependency in the request path.

The VM is replaceable and holds no durable media or result cache. Its capacity is independently bounded and can be resized or replicated without changing application capacity. Yandex CDN is the sole intended public caller; network controls and the shared origin header enforce that boundary to the extent supported by the platform.

The implementation plan will choose the smallest validated CPU/RAM shape from a cold-cache load test. Production acceptance requires that saturation of this VM degrades uncached images only and does not reduce application or worker request capacity.

## Storage and credential boundary

imgproxy uses a dedicated service account and S3-compatible credentials. The Object Storage bucket policy grants that principal only `s3:GetObject` on:

```text
arn:aws:s3:::<media-bucket>/derivatives/previews/*
```

It grants no `ListBucket`, write, delete, multipart, ACL, bucket-management, original-object, or staging-prefix access. Yandex Object Storage supports policies scoped to an object-key prefix and a specific service-account principal. imgproxy's allowed-bucket configuration is additional defense, not a replacement for the prefix policy.

The imgproxy source loader accepts only the configured S3-compatible endpoint and bucket. Arbitrary HTTP/HTTPS/file sources and redirects to untrusted origins are disabled. The service enforces maximum source bytes, source dimensions, decoded pixels, processing time, output dimensions, and concurrent work. Requests outside `gallery-v1` fail closed.

The CDN secure-token secret, origin-header secret, imgproxy signing key/salt, and Object Storage credentials are separate Lockbox secrets projected only to the components that need them. They must not appear in URLs, images, Compose files, GitHub logs, application logs, or metrics labels. Rotation must support overlapping old/new verification long enough to avoid an outage.

## Failure behavior

- A CDN cache hit remains independent of the image-origin VM and Object Storage latency.
- A miss that times out or fails returns an image error; it must not fail the gallery HTML request or consume application capacity.
- The browser keeps the existing card placeholder when a grid image cannot load.
- The origin never falls back to an original, a clean paid preview, another derivative, or an application route.
- An invalid/expired CDN token fails before origin fetch. An invalid imgproxy signature, preset, source prefix, or resource bound fails before transformation.
- CDN/origin/imgproxy 4xx and 5xx responses are not cached as successful images.
- The CDN origin timeout remains below the platform's origin-response limit, and imgproxy work is cancelled when the downstream request is gone or timed out.

## Observability and privacy

The delivery path exposes aggregate, bounded-cardinality measurements for:

- CDN hit ratio, edge response status, bytes, and time to first byte;
- origin request rate, concurrency, queueing, and rejection count;
- imgproxy source-fetch time, processing time, output bytes, CPU, memory, and timeouts;
- secure-token, origin-authentication, path-signature, and source-policy rejection counts;
- Object Storage `GetObject` rate and failures for the preview prefix.

Dashboards separate cache hits, cache misses, client errors, origin errors, and source errors. Alerts cover sustained image 5xx, origin saturation, abnormal miss ratio, and credential/policy failures.

Logs must not retain CDN query strings, signatures, capability expiry values, Object Storage credentials, full source keys, photo identifiers, event identifiers, or viewer identifiers. Operational correlation uses generated request IDs and aggregate preset/status labels.

## Runtime rollout and rollback contract

A code-owned runtime feature gate controls emission of CDN/imgproxy URLs from the canonical deployment. While off, the current direct Object Storage capabilities remain the normal-gallery behavior. Staff-only mode supports production validation against representative free and paid events before public activation.

The gate is evaluated once for the page; a page never mixes direct and CDN delivery for otherwise eligible derivative-backed cards. If CDN capability construction fails while the gate is enabled, page generation fails explicitly rather than weakening media policy or substituting an unauthorized source.

Rollback turns the gate off and immediately restores current direct accepted-preview URLs for newly rendered pages. It requires no data migration, cache purge, or media backfill. After stable public operation, the implementation must remove the gate and obsolete direct normal-gallery issuer path instead of preserving two permanent architectures.

## Acceptance criteria

### Functional and policy

- A 100-card free gallery page emits no more than 100 distinct `gallery-v1` URLs and uses only accepted `preview-small-v1` sources.
- A paid gallery page uses only accepted `preview-watermarked-v1` sources; clean previews and originals never reach the public delivery path.
- The card and face crops for one photo reuse the same URL and preserve correct crop geometry.
- Legacy photos and every explicitly out-of-scope presentation behave exactly as before.
- A valid capability works until its six-hour expiry; an expired or altered capability cannot be served even from CDN cache.
- Hiding a photo prevents new page renders from issuing a capability while already rendered pages can finish loading until expiry.

### Performance and isolation

Using the same representative 100-card cohort and browser profile before and after the change:

- median transferred grid-image bytes fall by at least 60 percent;
- a complete cold-cache page transfers no more than 15 MiB of unique grid images;
- the first 12 unique grid images complete at least 50 percent faster than the recorded direct-delivery baseline;
- a repeated run produces CDN hits and no imgproxy/Object Storage fetches for already cached objects;
- gallery HTML generation adds no Object Storage/imgproxy/CDN requests and regresses by neither more than 200 ms nor more than 10 percent, whichever allowance is larger;
- CDN cache hits create no traffic to the image-origin VM, application VM, Django, PostgreSQL, or workers;
- a cold-cache saturation test can exhaust the bounded image origin without changing application HTTP capacity or processing-worker throughput.

### Visual and security

- Landscape and portrait cards remain sharp at supported mobile and desktop widths, including high-density screens.
- The smallest representative face crops remain recognizable at their current rendered size.
- Paid watermarks remain visible and materially equivalent to the existing source.
- Direct requests for originals, staging keys, non-preview prefixes, arbitrary source URLs, arbitrary transforms, oversized sources, and unsigned origin paths fail closed.
- The imgproxy credential cannot list the bucket, fetch an original, or write/delete any object when tested independently of application code.

## Required critical-path tests

- Unit tests for deterministic versioned path construction, imgproxy HMAC, CDN token expiry, and cache-key-safe parameter placement.
- Gallery view tests for the free, paid, legacy, hidden-after-render, feature-gate, and no-extra-query contracts.
- Origin integration tests showing valid CDN-authenticated/signed requests succeed and altered preset/source/signature/header requests fail.
- A credential probe proving prefix-only `GetObject` and denial of listing, originals, staging, writes, and deletes.
- Browser checks for eager/lazy behavior, card/face URL reuse, paid watermarking, crop geometry, and unchanged lightbox/download behavior.
- A production-like cold/warm load check against the acceptance cohort, including deliberate image-origin saturation while application and worker probes run.

Additional transform combinations, formats, browsers, and pathological media do not block delivery unless they represent an existing production path or violate a stated resource/security bound.

## Alternatives considered

### Persist another thumbnail derivative

This makes delivery simple but adds storage state, processing jobs, acceptance bookkeeping, and backfill. It conflicts with the requirement to avoid another retained preview set.

### Thumbor behind CDN

Thumbor is a valid and maintained choice, and Yandex documents this topology. imgproxy is selected because this use case is deliberately narrow: signed paths, an S3-compatible private source, fixed processing options, and explicit resource limits in a stateless service. This is a fit and operational-surface decision, not an unsupported claim that imgproxy is universally faster. The load test remains authoritative.

### Resize on the current application VM

This would couple cold-cache image CPU/memory and source latency to the same capacity whose availability protects galleries and search. It violates the isolation goal exposed by the production incident.

### Custom Django/Pillow or serverless transformer

This adds application code, security surface, cold-start/concurrency behavior, and transformation maintenance that imgproxy already provides. It also risks putting image work back on an application-owned request path.

### Client-side sizing only

CSS and HTML dimensions do not reduce source bytes. The current browser already displays much smaller cards while downloading 1600-pixel previews.

### imgproxy without CDN

Every view would repeat source fetch and resize work. This removes stored derivatives but does not create an economical or resilient delivery path.

## References

- [Yandex Cloud CDN secure tokens](https://yandex.cloud/en/docs/cdn/concepts/secure-tokens)
- [Yandex Cloud CDN caching configuration](https://yandex.cloud/en/docs/cdn/operations/resources/configure-caching)
- [Yandex Cloud CDN origins and origin shielding](https://yandex.cloud/en/docs/cdn/concepts/origins)
- [Yandex Object Storage bucket policies and prefix resources](https://yandex.cloud/en/docs/storage/concepts/policy)
- [imgproxy S3-compatible private sources](https://docs.imgproxy.net/latest/image_sources/amazon_s3)
- [imgproxy URL signing](https://docs.imgproxy.net/latest/usage/signing_url)
- [imgproxy resource and security configuration](https://docs.imgproxy.net/latest/configuration/options)

## Approval gate

Implementation planning may proceed under accepted ADR 0038.
