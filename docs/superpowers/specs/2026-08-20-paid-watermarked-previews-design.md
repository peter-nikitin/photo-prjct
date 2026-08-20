# Paid Watermarked Previews Design

## Status

Approved in conversation by the project maintainer on 2026-08-20. Written specification pending
repository review.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current gallery-media and
  preview-first processing boundaries; purchase and download; security, privacy, and legal
  boundaries; evolution stage 3; and the paid-event media open decision
- Related product jobs:
  [`PJ-005 — Visitor — Browse an event gallery`](../../product-jobs.md#pj-005--visitor--browse-an-event-gallery),
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face),
  [`PJ-010 — Customer — Purchase selected photos`](../../product-jobs.md#pj-010--customer--purchase-selected-photos),
  and
  [`PJ-011 — Customer — Download purchased photos`](../../product-jobs.md#pj-011--customer--download-purchased-photos)
- Related specifications:
  [`2026-07-18-event-photo-gallery-design.md`](2026-07-18-event-photo-gallery-design.md),
  [`2026-07-30-preview-first-photo-processing-design.md`](2026-07-30-preview-first-photo-processing-design.md),
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md), and
  [`2026-07-31-event-media-direct-delivery-and-pagination-design.md`](2026-07-31-event-media-direct-delivery-and-pagination-design.md)
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0021](../../adr/0021-allow-original-download-for-authorized-photos.md), and
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md)
- ADR impact: **Requires new ADR.** The new ADR must authorize the normal published paid-event
  gallery for photos with an accepted watermarked preview; replace ADR 0019's paid-result original
  delivery with watermarked-preview delivery for the new explicit photo generation; replace ADR
  0020's paid-gallery denial and original media selection for those photos; and supersede ADR
  0021's original-download authorization for those paid photos. It must preserve bearer-result
  membership, private Object Storage, short-lived signed delivery, and the absence of commerce or
  purchase entitlement.

## Outcome

A newly confirmed photo in a paid event receives a clean private preview for face processing and a
second reduced preview carrying a watermark. Only the watermarked preview is publicly viewable.
The event's normal gallery and ready selfie-search results may show that photo without exposing its
clean preview or original and without offering an original download.

Free-event behavior remains unchanged. Existing photos are not enrolled, reclassified, or
backfilled. A paid event containing only older photos can therefore have an empty gallery until new
watermarked photos are uploaded; deleting obsolete test data is an operator action outside this
feature.

## Scope

### Included

- Explicit persisted applicability for newly confirmed paid-event photos.
- A versioned `generate_watermarked_preview` processor in the existing Django-polled processing
  system.
- One clean private `preview-small-v1` plus one public `preview-watermarked-v1` for each applicable
  paid photo.
- Separate watermark and face-processing states after clean-preview publication.
- Two repository-owned transparent PNG overlays, one landscape and one portrait.
- Paid-event gallery eligibility only after accepted watermarked-preview publication.
- Watermarked presentation in both the normal paid gallery and ready paid selfie-search results.
- Server-side denial of clean-preview and original presentation or download for the new paid-photo
  generation.
- The existing immutable `GalleryPhoto` presentation contract with nullable download capability.
- Immutability of an event's free/paid access type after its first `Photo` exists.
- Critical-path model, processing, authorization, template, and image-rendering tests.

### Excluded

- Backfill, automatic reconciliation, or bulk enrollment of existing photos.
- Mutation or replacement of an already published derivative.
- Per-event watermark assets or settings.
- Runtime controls for opacity, size, angle, spacing, repetition, or placement.
- Repeated watermark tiles, dynamic request-time compositing, CDN transformations, or a public
  media bucket.
- A distinct small and large watermarked derivative.
- Cart, prices, checkout, payment, purchase entitlement, or purchased-original delivery.
- A paid-photo download action, placeholder purchase message, or other commerce UI.
- Changes to face models, thresholds, embeddings, result membership, or ranking.
- Opening older paid photos through the normal gallery as a compatibility fallback.
- Deleting existing paid events or photos.
- Real-environment activation with placeholder watermark artwork.

## Selected Design

The existing clean preview remains the sole input to gallery face detection and embedding. A new
independent processor consumes that accepted clean derivative and creates the public watermarked
derivative. Face processing and watermark processing are siblings, so neither repeats or blocks
the other after clean-preview publication.

```text
new paid-event original
    -> generate_preview
    -> accepted private preview-small-v1
        +-> face_embedding
        `-> generate_watermarked_preview
            -> accepted public preview-watermarked-v1
                +-> normal paid gallery eligibility
                `-> paid selfie-result media eligibility
```

The worker does not decide whether a photo is paid, public, or downloadable. Django assigns an
explicit processing generation and media policy when the photo is confirmed, enrolls the required
processors, verifies output, publishes derivatives, and authorizes every media request.

## Domain and Data Model

### Event access type

`Event.access_type` remains the event-level `free` or `paid` classification. Once the event owns at
least one `Photo`, application writes must treat `access_type` as immutable. This applies to Django
Admin and every other supported write path. Publication status, descriptive fields, folders, and
other event metadata remain independently editable.

Photo confirmation is the authoritative point at which the event access type selects a photo's
generation. Confirmation must serialize against an access-type change for the first photo so a
photo cannot be created under an ambiguous policy.

### Explicit photo applicability

The system must not infer watermark applicability from upload time, current event type, missing
derivatives, Object Storage contents, or processing state. `Photo` gains one explicit allowed pair:

```text
processing_generation = preview_first_watermarked_v1
gallery_media_policy = watermarked_preview_required
```

The complete allowed-pair constraint becomes:

| Processing generation | Gallery media policy | Meaning |
| --- | --- | --- |
| `legacy_original_v1` | `legacy_original_allowed` | Existing explicit legacy behavior |
| `preview_first_v1` | `preview_required` | Existing clean-preview-first behavior |
| `preview_first_watermarked_v1` | `watermarked_preview_required` | New paid-photo behavior |

Newly confirmed paid-event photos receive the new pair. New free-event photos continue to receive
the existing preview-first pair. Every existing row retains its current values and receives no
watermark job merely because its event is paid.

`processing_generation` answers which processing graph applies. `gallery_media_policy` answers
which media may be presented publicly. Media authorization must read the latter rather than infer
presentation rights from processor state alone.

### Derivatives and processing evidence

`PhotoDerivative` remains the only persistent model for published binary photo derivatives. An
applicable paid photo has two rows:

| Variant | Producer | Purpose |
| --- | --- | --- |
| `preview-small-v1` | `generate_preview` | Private normalized ML input |
| `preview-watermarked-v1` | `generate_watermarked_preview` | Only public presentation image |

The existing `(photo, variant)` uniqueness and immutable-row behavior remain. Variant validation
must require `preview-small-v1` to reference an accepted successful `generate_preview` attempt and
`preview-watermarked-v1` to reference an accepted successful `generate_watermarked_preview`
attempt. No watermark field, derivative foreign key, or separate watermark model is added to
`Photo`.

`PhotoProcessingState` stores an independent row keyed by
`(photo, "generate_watermarked_preview")`. Existing `EventProcessingRun`, `ProcessingJob`, and
`ProcessingAttempt` records provide configuration identity, input fingerprint, retries, leases,
terminal evidence, and accepted attempt. The watermark job's immutable input fingerprint binds the
accepted clean derivative's object identity, byte size, SHA-256, dimensions, and accepted attempt.
The derivative's producer attempt therefore retains durable provenance without an additional
source-derivative foreign key.

## Watermark Media Contract

The watermark renderer uses exactly two versioned repository assets:

- `watermark-landscape-v1.png` when clean-preview width is greater than or equal to height; and
- `watermark-portrait-v1.png` when height is greater than width.

A square preview uses the landscape asset. Orientation is selected from the already normalized
clean preview, not from source EXIF metadata.

Each PNG is a complete transparent overlay, not a repeated tile. The renderer scales the selected
overlay proportionally with `cover`, centers it, crops equal excess from opposite edges, and alpha
composites it over the clean preview. It does not expose layout parameters. Opacity, artwork,
placement, and any internal angle are properties of the PNG itself.

The output contract is:

- variant `preview-watermarked-v1`;
- JPEG in sRGB;
- exactly the clean preview's width and height;
- no upscaling or additional resizing of the clean preview;
- no copied metadata;
- bounded bytes under the declared output slot; and
- a reported SHA-256 verified by Django before publication.

The processor configuration identifies the algorithm version and both asset SHA-256 values. A
worker must reject a claim when its packaged asset does not match the declared checksum. Replacing
the artwork changes the processor configuration/version and affects only subsequently confirmed
photos. It does not rewrite an existing derivative.

Placeholder assets may prove the repository behavior locally, but real-environment activation is
blocked until the maintainer supplies and visually approves the actual landscape and portrait PNG
assets.

## Enrollment and Publication

Confirmation of a new watermarked-generation photo requests only the existing clean-preview work.
It does not request watermark or face work directly from the original.

After Django verifies, promotes, and atomically accepts `preview-small-v1`, it independently and
idempotently requests:

- the event's selected `face_embedding` generation with the clean derivative fingerprint; and
- `generate_watermarked_preview` with that same clean derivative fingerprint.

The two downstream processors have no dependency on each other. A watermark failure does not
cancel, repeat, or reinterpret face processing. A face failure does not prevent watermark
publication. Public paid-photo eligibility nevertheless requires accepted watermark evidence; a
successful worker callback or an unverified object alone is insufficient.

The watermark worker receives a short-lived read grant only for the accepted clean derivative and
a short-lived write grant only for its attempt-scoped staging key. It does not receive the
original, another derivative, a permanent storage credential, or authority to choose a final key.
Django verifies and promotes the staged object non-overwriting to an immutable final key before it
publishes `PhotoDerivative` and accepts the processing attempt in one application transaction.

## Public Presentation Interface

The browser and templates do not receive a watermark flag, processing generation, derivative
variant, object key, or asset identity. `GalleryPhoto` remains the immutable application
presentation value with semantic media roles:

- `preview_media_small` for the gallery card;
- `preview_media_large` for the lightbox;
- `download_url`, changed to an optional value; and
- existing identity, alternative text, and face controls.

For a watermarked paid photo, both media roles use stable application routes that resolve to the
same `preview-watermarked-v1` object, and `download_url` is `None`. Templates render no download
control when the capability is absent. This keeps storage and watermark decisions inside the
server-side media module and leaves the client unable to request a clean variant by changing a
presentation flag.

`PublicMediaResolver` is the single media-selection module:

| Gallery media policy | `preview-small` | `preview-large` | Original download |
| --- | --- | --- | --- |
| `legacy_original_allowed` | Original | Original | Existing authorization |
| `preview_required` | Accepted `preview-small-v1` | Original | Existing authorization |
| `watermarked_preview_required` | Accepted `preview-watermarked-v1` | Accepted `preview-watermarked-v1` | Denied |

Authorization remains separate from physical selection. A deterministic application route first
proves a currently published event and eligible photo or a valid ready-result bearer plus saved
result membership. Only then may the resolver sign the selected exact private object. Unknown
variants, ineligible photos, missing accepted evidence, and forbidden downloads return a sanitized
404 before Object Storage signing.

## Gallery and Selfie-Result Behavior

A published event detail page remains available for either event access type. Its normal gallery
query applies an event-specific eligibility policy:

- a free event retains the existing eligible legacy and clean-preview photo behavior; and
- a paid event includes only `watermarked_preview_required` photos with mutually consistent
  accepted watermark state, accepted successful attempt, and published
  `preview-watermarked-v1` derivative.

A paid photo whose watermark state is absent, `not_requested`, `queued`, `processing`,
`retry_wait`, `failed`, or `cancelled` is absent from the normal gallery. One unavailable photo
does not hide ready sibling photos or the event page. Existing paid photos with legacy or ordinary
preview policy remain absent; the gallery never falls back to their original or clean preview.

The paid gallery reuses the existing numbered pagination, ordering, filters, face controls,
lightbox, empty state, and gallery markup. It adds no purchase controls or new customer copy.

Ready selfie-search result identity and membership remain immutable. Current presentation
eligibility is rechecked as before. A new watermarked-generation paid result member is shown only
after accepted watermark publication, and both of its media roles resolve to the watermark.
Original presentation and attachment download are denied even with a valid ready-result bearer.
Older saved paid-result members retain their explicit existing photo policy; this feature does not
rewrite them.

## Failure, Retry, and Consistency Semantics

The existing processing state vocabulary, leases, retries, immutable attempts, duplicate callback
handling, stale-result rejection, and attempt-scoped staging cleanup apply unchanged.

- Clean-preview failure prevents both face and watermark enrollment.
- Watermark failure follows the declared bounded retry policy and leaves the photo absent from
  public paid surfaces.
- Face failure does not invalidate an accepted watermark, although the photo cannot appear in a
  selfie result unless it independently has compatible accepted face evidence.
- Duplicate completion cannot publish a second derivative for the same photo and variant.
- A storage object without matching accepted database evidence is never public.
- A database success transition cannot commit before exact object verification and non-overwriting
  promotion.
- A missing final object after publication produces the existing sanitized media failure and does
  not authorize fallback.

Reconciliation may repair watermark enrollment only for a photo explicitly assigned the
watermarked generation whose clean preview is accepted and whose watermark state is explicitly
`not_requested`. It must not enroll existing paid photos based only on event type or a missing
watermarked derivative.

## Security and Privacy Boundaries

Originals, clean previews, and watermarked previews remain private Object Storage objects. Rendered
HTML contains only stable application routes. Django remains the authorization point and returns a
short-lived signed exact-object redirect only after current database checks, consistent with ADR
0020's transport boundary.

For the new paid generation, no normal-gallery route, ready-result route, download route, variant
substitution, or missing-derivative fallback may sign the original or clean preview. Signed URLs
remain bearer capabilities until expiry and the watermark cannot prevent screenshots or reuse of
bytes already received; it is a presentation and commerce boundary, not digital-rights
management.

This feature stores no customer identity, purchase, entitlement, price, or watermark-specific
personal data. Face processing continues to consume only the clean accepted preview and retains
its existing event isolation and biometric boundaries.

## Compatibility and Activation

There is no automatic backfill and no date-based compatibility branch. Existing photos retain
their persisted generation and media policy. The migration only expands allowed enum values and
the database constraint; it does not rewrite rows or create processing jobs.

The new processing and public behavior applies only to photos confirmed after the feature is
activated. Activation must enable Django enrollment, worker claiming, and public selection for the
same supported processor identity. A partial activation must fail closed: no watermarked photo is
public until accepted evidence exists, and the normal paid gallery must not expose old policies.

Implementation and real-environment activation require the new ADR identified above. Real
activation additionally requires the approved non-placeholder PNG pair, packaged checksum
verification, the existing attempt-staging lifecycle coverage, and a real-photo smoke proving that
face processing still reads the clean preview while both paid presentation routes select the
watermarked derivative. This specification authorizes no cloud mutation or deployment by itself.

## Rejected Alternatives

### Watermark the existing clean preview

Rejected because `preview-small-v1` is the immutable input to face detection and embedding.
Changing its pixels would alter current recognition behavior and persisted geometry.

### Store only a temporary clean preview

Rejected because later face retries and explicit reprocessing would lose their accepted input.
Regenerating it from the original would add a second implicit processing path and weaker
provenance. Two derivatives are stored only for the new paid generation.

### Produce both derivatives in `generate_preview`

Rejected because clean-preview publication, ML enrollment, watermark rendering, and watermark
failure would become one coupled transition. Separate processors preserve independent retries and
versioning behind the existing processing interface.

### Composite on each public request

Rejected because it puts image computation, failure, and caching on the customer request path and
would require a new transformation service to remain reliable at gallery scale.

### Add a watermark flag to the client contract

Rejected because clients must not select between protected and unprotected media. The server owns
authorization and physical derivative selection; the client receives only semantic small, large,
and optional download capabilities.

### Add a watermark-specific Django model

Rejected because existing derivative, processing-state, job, and attempt models already hold the
required binary identity, lifecycle, and provenance. A new table would duplicate those concepts
without creating a deeper interface.

### Parameterize watermark layout

Rejected for the first version. Two complete PNG overlays hold the approved artwork and alpha;
fixed `cover` composition is sufficient. Per-event or runtime layout controls add state and test
surface without a current product requirement.

### Keep the normal paid gallery closed

Rejected after design review. Once a photo has an accepted watermark, the normal paid gallery
should expose that protected preview immediately so the complete list can be inspected. This does
not imply original access or commerce.

## Acceptance Criteria

1. A newly confirmed free-event photo retains the existing preview-first generation, creates only
   `preview-small-v1`, and preserves current gallery and download behavior.
2. A newly confirmed paid-event photo persists
   `preview_first_watermarked_v1` plus `watermarked_preview_required` without inspecting dates or
   future derivative presence.
3. An event's `access_type` cannot change through supported application writes after its first
   `Photo` exists, including a first-photo confirmation racing an administrative edit.
4. Existing photos retain their generation and policy and receive no watermark work automatically.
5. Acceptance of `preview-small-v1` independently requests face and watermark processors with the
   exact same clean-preview fingerprint.
6. Face processing receives only the accepted clean preview and produces the same coordinate-space
   contract as before; it never reads `preview-watermarked-v1`.
7. The watermark processor reads only the accepted clean preview and writes only its current
   attempt-scoped staging object through short-lived exact-object grants.
8. Landscape and square previews select the landscape PNG; portrait previews select the portrait
   PNG.
9. Composition uses centered proportional `cover`, crops symmetrically, respects PNG alpha, and
   produces a JPEG with exactly the clean preview's dimensions.
10. Asset checksum mismatch, invalid overlay, invalid clean input, or invalid output fails closed
    without publishing a derivative.
11. Django verifies and non-overwritingly promotes the staged object before accepting the attempt
    and publishing one immutable `preview-watermarked-v1` row.
12. Watermark failure and retry do not cancel, repeat, or reinterpret face processing; face failure
    does not invalidate an accepted watermark.
13. A published paid event lists only photos with the new policy and mutually consistent accepted
    watermark state, attempt, and derivative evidence; older paid photos remain absent.
14. Paid gallery small and large routes and paid ready-result small and large routes all resolve to
    the accepted `preview-watermarked-v1` object.
15. No public route for a new watermarked paid photo can sign its original or clean preview, and its
    normal-gallery and ready-result download routes return 404 before storage signing.
16. `GalleryPhoto` exposes semantic small and large media without a watermark flag and exposes no
    download URL for a watermarked paid photo; templates render no download action in that case.
17. Draft events, unrelated photos, invalid bearer tokens, non-result members, invalid variants,
    missing accepted evidence, and storage failures preserve sanitized denial or failure behavior.
18. Existing numbered pagination, gallery filters, lightbox interaction, face controls, free-event
    behavior, result ordering, and selfie-result membership remain unchanged.
19. Focused model, migration, processing contract, state-machine, authorization, template,
    JavaScript, and desktop/mobile visual tests cover the changed critical paths.
20. Real-environment activation is blocked until the required ADR is accepted and the maintainer
    approves the non-placeholder landscape and portrait PNG assets.
