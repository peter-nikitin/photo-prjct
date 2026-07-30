# Preview-First Photo Processing Design

## Status

Approved by the project maintainer on 2026-07-30.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current event-gallery and
  photo-processing boundaries; proposed Media and Recognition modules; photo ingestion and
  indexing flow; evolution stages 3-4
- Related ADRs:
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0015](../../adr/0015-allow-anonymous-free-event-original-delivery.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017 and ADR 0015
- Related specifications:
  [Event photo gallery](2026-07-18-event-photo-gallery-design.md) and
  [Event photo processing worker](2026-07-29-event-photo-processing-worker-design.md)

## Goal

Generate one reduced, normalized preview for every newly confirmed photo before starting its ML
processing. Use that preview for event-gallery tiles and as the input to the first face-detection
and embedding pass. Continue to use the private original when a visitor opens the enlarged image.

The feature reduces gallery transfer size and ML decode cost without adding watermarks, changing
originals, or retroactively processing existing photos.

## Observable Outcome

After the feature is activated:

- a newly confirmed photo enters an explicit preview-first processing generation;
- Django queues `generate_preview` before any `face_embedding` work for that photo;
- the worker creates a bounded normalized JPEG and uploads it through an attempt-scoped grant;
- Django verifies and publishes the immutable preview before marking preview processing successful;
- the photo becomes visible in a gallery tile only after that accepted success;
- the tile uses the preview while the enlarged view continues to use the original;
- Django queues `face_embedding` only after preview publication, and ML reads that published
  preview rather than the original; and
- photos that predate activation retain their explicitly recorded legacy gallery behavior and are
  not backfilled.

## Scope

### Included

- A versioned `generate_preview` processor in the existing separately runnable worker.
- One `preview-small-v1` derivative for each newly confirmed eligible photo.
- Explicit processing-generation and gallery-media-policy data.
- A separate explicit `PhotoProcessingState` for preview generation.
- Strict Django-owned dependency from accepted preview success to `face_embedding` enrollment.
- Attempt-scoped staging upload, Django-owned verification, non-overwriting promotion, and
  PostgreSQL publication.
- Gallery selection of the preview for tiles and the original for enlarged viewing.
- Face detection and embedding over the published preview.
- Legacy compatibility without automatic backfill.
- Existing lease, retry, stale-attempt, immutable-report, and credential-isolation guarantees.

### Excluded

- Watermarks or paid-event preview publication.
- A large derived preview; `preview-large` continues to resolve to the original within the current
  eligible free-event policy.
- Backfill, migration processing, or manual bulk enrollment of existing photos.
- On-demand generation from a gallery request.
- Changes to face models, thresholds, embedding format, similarity search, or recognition quality
  policy.
- Reprocessing previews merely because a later processor version exists.
- Responsive image sets, multiple thumbnail sizes, AVIF, WebP, PNG output, or client-side resize.
- Original rewriting, deletion, retention changes, or purchased export generation.
- An operator UI for states, retries, or processing-generation changes.
- A new broker, queue service, media gateway, or permanent worker storage credential.

## Selected Design

Preview generation is an independent processor in the existing Django-polled processing system.
Django remains the trusted control plane and PostgreSQL remains the system of record. The worker
performs bounded image computation and direct transfer through exact-object short-lived grants but
does not choose storage keys, publish application state, or decide which processor runs next.

The selected dependency is:

```text
new confirmed original
    -> generate_preview queued
    -> worker creates and uploads attempt-scoped preview
    -> Django verifies and publishes preview
    -> preview state succeeded
        +-> photo becomes eligible for gallery tile
        `-> face_embedding queued with published preview as input
```

Preview publication and downstream enrollment are controlled by accepted current state. A worker
success response by itself is not sufficient to expose media or start ML.

### Rejected alternatives

#### Combined `preview_and_face_embedding` processor

A combined task would reduce one claim transition, but it would mix media-publication failures with
ML failures, repeat ML when only preview publication needs recovery, and prevent independent
versioning. Separate processors preserve the existing explicit state and attempt model.

#### On-demand preview generation

Starting work from the public gallery would add user-visible first-request latency and make ML
enrollment depend on gallery traffic. Public reads must consume published state rather than create
heavy processing work.

#### Face processing from the original in parallel

Parallel enrollment would not guarantee the approved preview-first order and would retain the
original decode cost. ML therefore has a hard dependency on accepted preview publication.

## Explicit Applicability and State

The system must not infer processing applicability, readiness, or failure from upload timestamps,
missing derivative rows, storage-object existence, face counts, embeddings, or other indirect
evidence.

Every photo has explicit persisted values sufficient to distinguish:

- `legacy_original_allowed`: the photo predates activation and may continue using the original for
  its tile;
- `preview_required`: the photo belongs to the preview-first generation and requires an accepted
  preview before gallery inclusion; and
- the processing generation/configuration that selected this policy.

Activation changes the policy assigned to photos confirmed afterward. Existing photos retain the
legacy policy and receive no automatic preview job. Reconciliation reads the persisted policy and
generation; it does not compare dates or treat an absent preview as proof of legacy status.

`generate_preview` and `face_embedding` each have their own `PhotoProcessingState`. They use the
existing explicit vocabulary:

- `not_requested`;
- `queued`;
- `processing`;
- `retry_wait`;
- `succeeded`;
- `failed`; and
- `cancelled`.

The preview state records its current run, job, attempt, accepted attempt, retry timing, and
transition timestamps according to the existing processing contract. The face state remains
`not_requested` until Django accepts the preview. No face, no embedding, or no face-state row may be
interpreted as "waiting for preview" or as a terminal ML outcome.

For a `preview_required` photo, gallery readiness is an explicit application decision backed by
the accepted preview state and published derivative record. Storage probing is not a substitute
for database state.

## Preview Media Contract

The first derivative variant is named `preview-small-v1`. Its normalized processor configuration
includes every output-affecting rule:

- output format: JPEG;
- maximum long edge: 1600 pixels;
- upscaling: forbidden;
- aspect ratio: preserved;
- orientation: apply the source EXIF orientation to pixels before resizing;
- color space: sRGB;
- JPEG quality: 85;
- metadata: do not copy EXIF, GPS, comments, thumbnails, or other source metadata; and
- watermark: none.

An image whose long edge is at most 1600 pixels is not enlarged, but it is still normalized for
orientation, color space, output encoding, and metadata removal. The worker reports the actual
width and height after orientation and resize.

The exact resize kernel, encoder library, and internal memory strategy are reversible
implementation details, provided output remains deterministic enough for the declared checksum and
satisfies this contract. Any change to an output-affecting rule above requires a new processor or
configuration version and a separately named immutable derivative. A new version does not mutate
`preview-small-v1` or its processing evidence.

## Binary Publication Contract

The implementation uses the binary-derivative boundary already reserved by the worker design:

1. Django owns the final immutable derivative identity and creates a unique unused staging key for
   the current attempt.
2. The claimed job declares one bounded output slot with variant, allowed content type, byte limit,
   dimension limits, and required checksum algorithm.
3. Django grants short-lived write access only to that attempt's exact staging key. The grant
   permits no list, other-object read, copy, delete, or final-key write operation.
4. The worker downloads the exact original through its existing temporary read grant, creates the
   preview in bounded memory, uploads it to the authorized staging slot, and returns bounded
   metadata: variant, content type, byte size, dimensions, checksum, duration, and warnings.
5. Django checks the staging object and returned metadata against the job contract. Verification
   includes JPEG type, byte bounds, dimensions, checksum, and current attempt ownership.
6. Django promotes the verified object to the immutable final key without overwriting an existing
   object.
7. Only the accepted current attempt may publish the derivative record and transition preview
   processing to `succeeded`.

The worker never receives reusable Object Storage credentials and never chooses the bucket,
staging key, or final key. Signed URLs and fields follow the existing secret-redaction rules.

An unaccepted staging object is not application-visible. Attempt-scoped staging objects use a
bounded Object Storage lifecycle/TTL so abandoned uploads do not accumulate indefinitely. Cleanup
never targets originals or published final derivatives and is not part of the success transaction.

## Preview-First Enrollment

Confirmation of a new eligible original persists the preview-first applicability and requests
`generate_preview` through the established transactional enrollment boundary. It does not request
`face_embedding` at that time.

After successful object verification and promotion, Django performs one atomic application
transition that:

- publishes the derivative record;
- accepts the current preview attempt;
- changes the preview state to `succeeded`;
- makes the photo eligible for preview-backed gallery presentation; and
- idempotently requests `face_embedding` using the published preview fingerprint.

If the transaction fails, none of those application-visible changes is accepted. A retry may reuse
the already promoted immutable object only after verifying it matches the same declared output; it
must never overwrite a conflicting object.

Reconciliation may repair an interrupted downstream enrollment only when persisted data says:

- the photo is `preview_required`;
- preview state is `succeeded`;
- a verified published preview is current; and
- face processing is explicitly `not_requested`.

It must not enqueue ML merely because an object exists in storage.

## Gallery Behavior

The existing presentation contract remains stable:

- `preview_media_small` is the tile/list image;
- `preview_media_large` is the enlarged lightbox image.

The resolver applies the photo's explicit gallery-media policy:

| Policy | `preview-small` | `preview-large` | Gallery eligibility |
| --- | --- | --- | --- |
| `legacy_original_allowed` | Original | Original | Existing legacy rules |
| `preview_required` | Published `preview-small-v1` | Original | Accepted preview required |

A `preview_required` photo with preview state `not_requested`, `queued`, `processing`,
`retry_wait`, `failed`, or `cancelled` is absent from the gallery. The resolver never falls back to
the original for its tile. A missing or unavailable published preview returns the existing
sanitized media failure; it does not change database state or switch media variants.

The enlarged original remains governed by the existing published free-event policy in ADR 0015.
This design does not expose paid-event originals and does not define paid-event gallery behavior
without watermarks.

## ML Input Contract

`face_embedding` for a preview-first photo receives an input fingerprint and short-lived read grant
for the accepted published `preview-small-v1`, not for the original. Claim and refresh operations
must preserve that exact derivative identity.

Detection, landmarks, face quality decisions, and embeddings are computed from the normalized
preview pixels. Stored detection geometry declares the preview dimensions and coordinate space.
The system also preserves enough immutable source/derivative dimension metadata to map bounding
boxes between preview and oriented-original coordinates without guessing.

This feature changes ML input size and pixels, not the model, thresholds, face-result schema, or
meaning of an embedding. Recognition-quality comparison against the current original-based
pipeline belongs to implementation validation; a material quality regression blocks activation.

Legacy photos are not re-enrolled or recomputed by this feature. Existing accepted ML results
remain valid historical evidence for their declared processor version and input fingerprint.

## Failure and Retry Semantics

Stable permanent preview failures include:

- unsupported or invalid input type;
- input exceeding declared byte or pixel limits;
- input fingerprint mismatch;
- deterministic decode failure;
- invalid dimensions;
- failure to apply orientation or produce the required normalized JPEG; and
- output violating the declared type, size, dimension, or checksum contract.

Retryable failures include bounded network interruption, temporary Django or Object Storage
unavailability, HTTP 5xx responses, and an expired temporary grant while the attempt lease remains
valid.

Warnings may record non-fatal normalization facts, such as an absent color profile handled by the
declared default. Warning codes are bounded and versioned; raw metadata and untrusted exception
text are not persisted.

Existing lease and idempotency semantics apply:

- each claim creates a distinct immutable attempt;
- each attempt writes only to its own staging key;
- repeated identical completion is idempotent;
- conflicting completion is rejected;
- expired or stale attempts cannot publish media, change current accepted state, make the photo
  gallery-visible, or request ML; and
- bounded retry exhaustion explicitly changes preview state to `failed`.

A preview failure leaves face processing explicitly `not_requested`. It does not mark face
processing successful, failed, or implicitly blocked. A later accepted preview retry is the only
automatic path that requests ML.

## Privacy and Security

- Originals and previews remain private Object Storage objects delivered through controlled
  application routes.
- The worker has no database access, Django secret, permanent Object Storage credential, bucket
  list permission, or reusable write permission.
- Preview output contains no EXIF, GPS, embedded source thumbnail, comment, or other copied source
  metadata.
- HTML and public responses contain no permanent object key, signed URL, bucket credential,
  checksum used as an internal identifier, or internal exception detail.
- Attempt results and event reports store bounded metadata, not image bytes or temporary grants.
- Face-processing privacy and biometric-governance requirements remain unchanged.

## Reporting

Every preview attempt belongs to an event-scoped immutable `generate_preview` run. Its report
records:

- processor and configuration version;
- exact photo cohort;
- explicit counts by terminal state;
- retries and stale attempts;
- download, compute, upload, verification, and total durations where available;
- output byte and dimension summaries;
- bounded warnings and stable failure codes; and
- the accepted attempt identity for each successful photo.

The report does not contain image bytes, original or derivative storage keys, signed grants, EXIF
values, or face results. Preview and face reports remain separate so failure rates and resource
costs are attributable to the correct processor.

## Acceptance Criteria

1. A photo confirmed after activation explicitly receives the preview-first generation and
   `preview_required` policy; an existing photo explicitly retains `legacy_original_allowed`.
2. Confirmation queues `generate_preview` and leaves `face_embedding` explicitly
   `not_requested`.
3. A real representative JPEG produces a JPEG preview whose long edge is at most 1600 pixels,
   whose aspect ratio is preserved, and which is never upscaled.
4. EXIF orientation is applied before resize, output is sRGB at quality 85, and output contains no
   copied EXIF/GPS or other source metadata.
5. The worker uploads only to the exact attempt staging slot and cannot select or overwrite a
   final object.
6. Django verifies type, size, dimensions, checksum, and current-attempt ownership before
   non-overwriting promotion and publication.
7. Only accepted preview success makes a preview-required photo gallery-visible and queues
   `face_embedding`.
8. A new gallery tile reads the published preview, while enlarged viewing reads the original.
9. No preview state or storage failure causes a new tile to fall back to the original.
10. A legacy photo remains visible under its existing rules without a preview job or backfill.
11. ML claims the accepted published preview by fingerprint and does not download the original for
    a preview-first photo.
12. Preview dimensions and coordinate-space metadata make detection geometry mapping explicit.
13. Retryable, permanent, exhausted, cancelled, and stale preview outcomes remain explicit and do
    not start ML.
14. Duplicate completion is idempotent; a conflicting or stale completion cannot publish media or
    change current state.
15. Reconciliation uses explicit policy and processor states, never dates, storage probes,
    embeddings, or face counts.
16. Event-run evidence reports preview outcomes separately without secrets or media bytes.
17. Representative comparison finds no material face-detection or embedding-quality regression
    caused by changing the ML input from original to preview.

## Verification Boundaries

Verification must cover the critical and realistic regression paths:

- processor-contract validation and real-JPEG end-to-end generation;
- orientation, resize, no-upscale, color, encoding, and metadata removal;
- staging upload, Django verification, immutable promotion, and publication;
- explicit new-versus-legacy applicability;
- strict preview-to-ML ordering and idempotent reconciliation;
- gallery eligibility and small-versus-large media selection;
- retry, lease expiry, duplicate completion, stale completion, and storage failure;
- secret/key redaction and temporary-object isolation;
- immutable per-event preview reporting; and
- a representative ML comparison between the prior original input and `preview-small-v1`.

Visual snapshots need updating only if the rendered appearance changes materially. Changing the
backing media source without a visual change is covered by resolver and browser-network
assertions.

## Architecture and ADR Reconciliation

This design conforms to ADR 0017. It adds the binary-output extension anticipated by the existing
worker specification while preserving Django/PostgreSQL control, HTTP polling, explicit states,
leases, immutable attempts, exact-object temporary grants, and the worker's lack of permanent
credentials.

It conforms to ADR 0015 by retaining controlled original delivery only for the enlarged view of an
eligible free-event photo and by introducing a reduced derivative for new gallery tiles. It does
not broaden anonymous original access or define paid-event delivery.

ADR 0006 and ADR 0013 continue to govern private storage and original ingestion. The versioned
preview encoding and processor dependency are reversible implementation choices within the
accepted worker architecture, so this specification does not require a new ADR.
