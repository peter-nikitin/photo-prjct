# 0029: Use watermarked previews for paid photo presentation

- Status: Accepted
- Date: 2026-08-20
- Deciders: project maintainers
- Supersedes: [ADR 0019](0019-use-public-event-selfie-search.md) for paid-result original
  presentation by the new paid-photo generation;
  [ADR 0020](0020-use-signed-direct-object-storage-media-delivery.md) for normal paid-gallery denial
  and original selection by that generation; and
  [ADR 0021](0021-allow-original-download-for-authorized-photos.md) for original-download
  authorization by that generation
- Superseded by:
  [ADR 0031](0031-use-orders-and-adapters-for-paid-original-delivery.md) only for original
  authorization after a qualifying Order becomes paid

## Context

The current public gallery supports only free events. A valid ready selfie-search bearer result
may present and download an original from a paid event because protected derivatives did not exist
when ADRs 0019 and 0021 established the shortest working search path. ADR 0020 preserves that
authorization while moving already authorized media transfer to short-lived signed Object Storage
URLs.

The implemented `preview-small-v1` derivative cannot become the paid presentation image by adding
a watermark: it is the immutable input to face detection and embedding, and stored face geometry
is expressed in its coordinate space. Paid presentation therefore needs a protected derivative
without changing recognition input or exposing another client-side media choice.

The product now needs newly uploaded paid photos to be inspectable in the normal event gallery and
ready selfie-search results before commerce exists. Existing photos do not need migration or
backfill.

## Decision drivers

- Keep face detection and embedding on the accepted clean preview.
- Make a watermarked image the only public representation of a newly uploaded paid photo.
- Open the normal paid gallery without opening originals or downloads.
- Keep media authorization and physical derivative selection in Django rather than the client.
- Reuse the existing immutable derivative and Django-polled processing boundaries.
- Avoid backfill, commerce state, per-event watermark configuration, and request-time image work.

## Considered options

1. Add the watermark to the existing clean preview before face processing.
2. Keep the clean preview private and generate one separate watermarked preview in an independent
   processor.
3. Keep only the clean preview and composite a watermark for every public request.
4. Keep the normal paid gallery closed until commerce and purchased delivery exist.

## Decision

Select option 2.

Assign every newly confirmed paid-event photo an explicit watermarked processing generation and
public-media policy. That generation keeps the accepted `preview-small-v1` as its private ML input
and independently creates one immutable `preview-watermarked-v1` derivative through the existing
ADR 0017 processing, exact-object grant, verification, and publication contract. Free photos and
existing photo rows retain their current explicit generations and policies; there is no automatic
backfill or inference from dates, event type, or missing objects.

Use two repository-owned transparent PNG overlays, one landscape and one portrait. Select by the
already oriented clean-preview dimensions, treating square as landscape. Scale the selected
overlay proportionally with centered `cover`, crop the excess, and alpha-composite it into a JPEG
with exactly the clean preview's dimensions. Layout is not runtime-configurable. The processor
identity binds the algorithm and asset checksums; real activation requires maintainer-approved
non-placeholder assets.

Treat an event's `free` or `paid` access type as immutable after its first photo exists. This keeps
each photo's assigned processing and public-media policy stable.

Allow the normal gallery of a published paid event to list only photos whose explicit watermarked
policy is backed by mutually consistent accepted processing state, accepted successful attempt,
and published watermarked derivative. Do not fall back to an original or clean preview. Older paid
photos remain absent from that gallery.

For a watermarked paid photo, both the gallery-card and lightbox application variants select the
same watermarked derivative. A ready selfie-search result applies the same selection after its
existing bearer and saved-membership checks. Original presentation and attachment download are
denied before storage signing in both contexts. The client receives semantic small and large media
URLs and no download capability; it receives no watermark flag or storage identity.

Keep Object Storage objects private and retain ADR 0020's stable application routes, current
database authorization, and short-lived signed exact-object delivery. This decision changes which
media is authorized, not the transport after authorization.

This decision does not define prices, cart behavior, payment, purchase entitlement, purchased
exports, or authenticated original delivery. Those remain future commerce decisions. Existing
photo rows and saved results retain their explicit current photo policy until separately removed or
superseded; this decision does not rewrite them.

This ADR conforms to ADR 0017's processing boundary and ADR 0022's numbered pagination. It
supersedes only the paid-media portions of ADRs 0019, 0020, and 0021 named above. Their event-scoped
selfie search, immutable result membership, bearer authorization, private-storage transport,
signing, expiry, and free-event behavior remain accepted.

## Consequences

### Positive

- Newly uploaded paid photos become browsable without exposing their originals.
- Face processing retains its accepted clean input and geometry contract.
- The browser cannot select between protected and unprotected objects.
- Only paid photos store two previews; free-photo storage and processing remain unchanged.
- Processing retries, immutable attempts, and derivative provenance reuse established models.

### Negative

- Each new paid photo stores both a clean and watermarked preview.
- A paid photo remains absent from public surfaces until watermark processing succeeds.
- Small and large presentation roles initially transfer the same 1600px watermarked object.
- Existing paid photos remain absent from the normal gallery unless separately deleted; no
  compatibility fallback or backfill is provided.
- A watermark discourages reuse but cannot prevent screenshots or copying of delivered bytes.

### Follow-up

- Supply and visually approve the final landscape and portrait PNG assets before real activation.
- Validate one real new paid photo through clean preview, face processing, watermark publication,
  normal gallery, and ready selfie-result delivery before public activation.
- Define purchase entitlement and protected original delivery in a later commerce ADR.

## Validation and rollback

Validate explicit new-photo applicability, event access-type immutability, clean-preview ML input,
asset selection and composition, exact-object worker grants, non-overwriting publication, and
accepted-evidence gallery eligibility. Verify normal paid-gallery and paid-result small and large
routes select only the watermarked derivative, while original and download requests fail before
storage signing. Confirm free events, existing photos, result membership, pagination, filters, and
face controls retain their current behavior.

Rollback disables new watermarked-photo enrollment and normal paid-gallery exposure while
retaining immutable originals, derivatives, jobs, attempts, and states as evidence. It does not
delete already received bytes or published storage objects. Reconsider this decision when commerce
needs entitled original delivery, a distinct paid lightbox size is justified by measurement, or
approved artwork requires a materially different composition contract.

## References

- [Paid watermarked previews
  design](../superpowers/specs/2026-08-20-paid-watermarked-previews-design.md)
- [Architecture: purchase and download](../architecture.md#purchase-and-download)
- [Architecture: security, privacy, and legal
  boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [Architecture: evolution stages](../architecture.md#evolution-stages)
- [Architecture: open decisions](../architecture.md#open-decisions)
- [ADR 0017](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0019](0019-use-public-event-selfie-search.md)
- [ADR 0020](0020-use-signed-direct-object-storage-media-delivery.md)
- [ADR 0021](0021-allow-original-download-for-authorized-photos.md)
- [ADR 0022](0022-use-numbered-gallery-pages.md)
