# Gallery Media Projection Design

## Status

Approved in conversation by the project maintainer on 2026-09-15. Repository review,
implementation planning, implementation, CI, deployment, and live verification remain pending.

- Related architecture: [`docs/architecture.md`](../../architecture.md), photo ingestion,
  processing, gallery presentation, selfie search, and commerce boundaries
- Related product jobs:
  [`PJ-005 — Visitor — Browse an event gallery`](../../product-jobs.md#pj-005--visitor--browse-an-event-gallery),
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face),
  and [`PJ-010 — Customer — Purchase selected photos`](../../product-jobs.md#pj-010--customer--purchase-selected-photos)
- Related specifications:
  [`2026-07-30-preview-first-photo-processing-design.md`](2026-07-30-preview-first-photo-processing-design.md),
  [`2026-07-31-event-media-direct-delivery-and-pagination-design.md`](2026-07-31-event-media-direct-delivery-and-pagination-design.md),
  [`2026-08-08-photo-capture-time-projection-design.md`](2026-08-08-photo-capture-time-projection-design.md),
  and [`2026-08-20-paid-watermarked-previews-design.md`](2026-08-20-paid-watermarked-previews-design.md)
- Related ADRs: [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md), and
  [ADR 0029](../../adr/0029-use-watermarked-previews-for-paid-photos.md)
- ADR impact: requires a new ADR because it selects a synchronous PostgreSQL read model as the
  canonical source of derived Gallery Media Readiness across gallery, selfie-search, and commerce
  reads. It does not replace immutable processing evidence as the source of truth.

## Outcome

Customer-facing reads no longer reconstruct Gallery Media Readiness by joining `Photo` to
`PhotoProcessingState`, `ProcessingAttempt`, and `PhotoDerivative`. Django synchronously projects
the accepted clean and watermarked media needed for presentation into one compact row per processed
photo. Gallery, exact media and download, selfie-result, cart, and checkout paths read `Photo` plus
that projection only.

The projection is derived, explainable, atomically current, and fully rebuildable. Processing
evidence remains authoritative. Event publication, photo visibility, gallery policy, folders,
capture time, original-media ownership, and purchase entitlement remain in their existing models
and are not copied into the projection.

## Problem and evidence

The current gallery eligibility predicate proves accepted derivative readiness at request time. It
joins current processing state to immutable attempts and derivatives and then applies `DISTINCT`.
The same predicate is reused by the gallery page, exact media and download routes, selfie-search
candidate and saved-result paths, and commerce eligibility checks.

During the 2026-09-15 production investigation, one approximately 38-second interval containing a
new selfie search increased sequential tuple reads by about 8.97 million on
`processing_photoprocessingstate` and 3.16 million on `processing_photoderivative`, while indexed
attempt fetches increased by about 2.27 million. A sampled gallery query used parallel workers and
matched the dynamic eligibility shape. The 16-core VM retained substantial aggregate CPU capacity,
so the immediate problem is repeated read amplification rather than insufficient VM CPU.

Exact media and download requests currently reuse the collection queryset and then filter it by
photo ID. Even when PostgreSQL can simplify that query, the interface makes an exact authorization
check depend on the collection query shape and its processing joins.

## Domain language

**Gallery Media Readiness** is the derived fact that a Photo has the exact accepted media required
by its `gallery_media_policy`. It does not mean that the Event is published, the Photo is visible,
the Photo belongs to a selfie result, or the customer may purchase or download the original.

**Gallery Media Projection** is the rebuildable read model that records Gallery Media Readiness and
the immutable evidence from which it was derived. It is never authoritative processing history.

## Scope

### Included

- A sparse one-to-one `GalleryMediaProjection` for processed photos.
- Exact clean-preview and watermarked-preview final keys with source-attempt provenance.
- Synchronous projection publication in the accepted derivative transaction.
- Projection-backed collection and exact-photo read interfaces.
- Cutover of gallery, media, download, selfie-search, cart, checkout, and purchase revalidation.
- A database-side rebuild and exact symmetric-difference verification.
- A deployment cutover that pauses processing publication while existing evidence is projected.
- Query-shape regression tests that prohibit processing-table access from customer-facing reads.
- Bounded live verification against the incident baseline after deployment.

### Excluded

- Copying `Event.publication_status`, `Photo.is_hidden`, folder, capture time, original key, price,
  search membership, order state, or entitlement into the projection.
- Changing gallery ordering, page size, filtering semantics, media policy, selfie ranking, purchase
  authorization, or signed-URL behavior.
- An asynchronous projection queue, scheduled refresh, materialized view, cache, or
  eventual-consistency window.
- Replacing, deleting, or rewriting immutable processing evidence or published derivatives.
- A compatibility fallback to the old processing joins after cutover.
- A general-purpose projection framework or speculative support for unknown derivative variants.
- Treating this change as a VM-sizing or worker-throughput change.

## Considered designs

### Selected: separate synchronous projection table

A separate `GalleryMediaProjection` keeps presentation readiness out of both immutable processing
history and the product-owned `Photo` row. Processing pays the projection cost once when a
derivative is accepted; all readers share a small interface and indexed row. The table can be
rebuilt from evidence and removed without losing authoritative data.

### Rejected: projection fields on `Photo`

This produces a marginally simpler join but makes processing publication mutate the primary product
model and mixes derived media facts with visibility, event ownership, filtering, and originals.
The separate table gives the read concern a clearer seam without increasing caller complexity.

### Rejected: PostgreSQL materialized view

Materialized refresh is not naturally per-photo or transactionally tied to accepted publication.
It introduces a freshness window or a heavier refresh protocol and makes exact publication failure
semantics less direct.

## Data model

`picflow.GalleryMediaProjection` is sparse and has a one-to-one primary-key relation to `Photo`.
Legacy photos need no row because their original-media readiness is already expressed by immutable
fields on `Photo`. For a preview-first photo, a missing projection or missing required slot means
not ready and fails closed.

The model contains:

- `photo`, a one-to-one primary key with protected deletion;
- `clean_preview_final_key`, nullable;
- `clean_preview_source_attempt`, nullable and protected;
- `watermarked_preview_final_key`, nullable;
- `watermarked_preview_source_attempt`, nullable and protected; and
- `updated_at` for operational diagnosis, not authorization.

Each final-key and source-attempt pair is either both null or both non-null, enforced by database
constraints. A populated pair is valid only when the referenced attempt belongs to the same photo,
has the exact producer type for the slot, succeeded, was accepted, owns the immutable matching
`PhotoDerivative`, and is the accepted succeeded state for that processor. Cross-table identity is
enforced by the publication transition and the verifier.

The final keys are intentionally copied from `PhotoDerivative`. A normal customer read must not
join any processing table merely to choose an object key. Provenance foreign keys preserve an
explainable link to immutable evidence but are not followed during normal reads.

No separate readiness booleans are stored. The required non-null key plus
`Photo.gallery_media_policy` determines Gallery Media Readiness:

| Gallery media policy | Required media fact |
| --- | --- |
| `legacy_original_allowed` | Existing eligible `Photo.original_key`; projection is irrelevant |
| `preview_required` | Non-null clean-preview projection pair |
| `watermarked_preview_required` | Non-null watermarked-preview projection pair |

Event publication, event access type, feature gates, photo visibility, private-source shape, and
commerce state remain additional caller-specific predicates over their authoritative models.

## Projection publication module

The projection module exposes one write interface:

```text
publish_gallery_media(derivative)
```

The interface validates the derivative's supported variant and accepted evidence, locks or creates
the projection by photo ID, and fills the corresponding slot. Its implementation remains internal
to the module. Callers do not set projection fields directly.

Production currently has one supported derivative publication point:
`complete_preview_attempt()`. After storage verification and promotion, its existing database
transaction accepts the attempt and processing state and creates `PhotoDerivative`. It then calls
`publish_gallery_media()` before commit.

Publication is monotonic. Publishing the clean slot does not clear the watermarked slot, and
publishing the watermarked slot does not change the clean slot. Repeating the exact final key and
attempt is idempotent. A different value for an occupied slot is a publication conflict and never
silently replaces accepted history.

The existing photo lock serializes clean and watermarked publication for one photo. The projection
lock is acquired after the existing event, run, job, photo, state, attempt, and derivative locks;
no path may acquire those earlier locks after holding the projection lock.

If projection publication fails, the database transaction rolls back attempt acceptance, state,
derivative, and projection together. A content-addressed object already promoted before the
database transaction may remain in final storage under the existing publication failure contract.
A retry verifies and reuses the same immutable object and completes the database publication.

Django signals are not used. The transition stays explicit, local, and directly testable.

## Read module and interfaces

The gallery-media read module owns the canonical eligibility implementation. Models, views,
selfie-search, and commerce callers must not reproduce processing or projection predicates.

It exposes two read interfaces:

```text
gallery_photos(event, filters, paid_watermarked_previews_enabled) -> QuerySet[Photo]
public_gallery_photo(event_id, photo_id, purpose, paid_watermarked_previews_enabled) -> Photo
```

`gallery_photos()` supports set-oriented consumers: numbered gallery pages, folder and capture-time
filters, selfie candidate cohorts and saved results, and bulk commerce eligibility. It filters
`Photo` on authoritative event and photo fields and left-joins at most one projection row. It does
not use `DISTINCT` to compensate for one-to-many processing joins.

`public_gallery_photo()` is the exact-photo interface for media, download, and other single-photo
authorization. It constrains the query by `Photo.pk` and `event_id`, applies the same canonical
eligibility semantics, and joins at most one projection row. It never invokes the collection
queryset and never constructs or scans a complete event gallery.

`purpose` distinguishes presentation-media selection, original download, and purchase validation
without exposing storage keys or processing variants to views. The module returns the authorized
Photo and selected presentation facts needed by `PublicMediaResolver`; the resolver no longer
queries `PhotoDerivative` itself.

All existing route behavior remains fail closed:

- unknown, cross-event, hidden, unpublished, policy-ineligible, or unprojected preview-first photos
  return the existing not-found response before storage access;
- storage absence and unavailability retain their existing response mapping;
- watermarked-policy photos never expose clean previews or original downloads;
- projection readiness never grants purchase entitlement or purchased-original access; and
- runtime feature gates remain independent of authorization.

## Rebuild and exact verification

Schema migration creates only the table, constraints, foreign keys, and indexes. Existing data is
projected by a dedicated command rather than row-by-row Python inside a Django data migration.

The rebuild command is deterministic, idempotent, database-side, and defaults to dry-run. Explicit
apply performs set-oriented upserts for both supported derivative variants and removes projection
state that is not derivable from accepted evidence. It never changes attempts, states,
derivatives, photos, object storage, search results, carts, orders, or entitlements.

The deploy verifier executes one database-side symmetric difference:

```text
(expected projection from accepted evidence EXCEPT actual projection)
UNION ALL
(actual projection EXCEPT expected projection from accepted evidence)
```

It counts the resulting rows without returning photo IDs, filenames, storage keys, attempt IDs, or
other per-photo data. `--require-clean` succeeds only when the count is zero. This proves missing,
extra, wrong-key, and wrong-source rows with one fixed-shape query rather than loading model rows or
iterating events in Python.

An event-scoped diagnostic mode may return aggregate mismatch counts by slot and category. It must
not expose row-level private media facts. Repair is always an explicit rebuild, never an implicit
side effect of verification.

## Deployment and rollback

The projection cutover uses one application release and no customer-facing compatibility path:

1. Validate and pull the candidate image while the old web and workers continue normally.
2. Stop processing workers and confirm that they have exited. Web requests and job enrollment may
   continue; only worker claims and result publication pause.
3. Run the candidate schema migration.
4. Run the candidate projection rebuild with explicit apply.
5. Run the exact symmetric-difference verifier with `--require-clean`.
6. Reconcile web and worker containers to the candidate image.
7. Verify local health, one public gallery page, one exact media route, and worker health.
8. Resume queued processing under projection-aware code.

The old web remains available through steps 2-5 and continues using the old query, so no photo
temporarily disappears. Enqueued work waits for the short processing pause. The stable evidence set
cannot gain a newly published derivative while workers are stopped.

A failure before container reconciliation leaves the old web serving. A failure after reconciliation
uses the existing deployment rollback. The additive schema does not prevent the prior release from
running; the prior release may temporarily resume old reads. Before any later projection-aware
cutover, rebuild and exact verification run again, so writes made by rolled-back workers cannot
leave a stale projection authoritative.

## Verification

### Functional and transactional tests

Tests cover:

- legacy-original, free clean-preview, and paid watermarked-preview readiness;
- hidden photos, unpublished events, cross-event IDs, missing projection rows, and incomplete slots;
- semantic parity across gallery, media, download, selfie-search, cart, checkout, and purchase
  revalidation;
- selected object keys without a resolver-side derivative query;
- atomic rollback when projection publication fails;
- exact-repeat idempotency and different-value conflict;
- clean and watermarked slot preservation;
- concurrent publication following the global lock order; and
- rebuild convergence, extra-row removal, mismatch detection, and privacy-safe reports.

### Query-shape regression tests

Representative calls for every customer-facing consumer capture executed SQL and fail if any query
references `processing_photoprocessingstate`, `processing_processingattempt`, or
`processing_photoderivative`. Exact-photo tests additionally require the photo primary-key and
event predicate and ensure that storage selection issues no hidden database query.

These structural tests are the durable performance gate. They directly prevent the observed query
amplification from returning without relying on timing in a shared test environment.

### Deployment smoke and live acceptance

Deployment performs only two bounded read smokes after a clean projection verification:

- render one page of the largest published gallery; and
- resolve one existing photo through the exact media path.

The smoke records sanitized `EXPLAIN ANALYZE` shape and elapsed time for diagnosis but has no
absolute millisecond threshold. Its hard conditions are successful responses, no processing
relations in the application SQL, and a clean projection.

After the first production cutover, a bounded live check compares the same gallery and exact-media
routes with the incident baseline. It also verifies that natural requests no longer increase tuple
reads on the three processing tables through gallery-media SQL fingerprints. This one-time evidence
confirms the optimization under real load; it is not repeated as an expensive deployment gate.

## Acceptance criteria

The design is complete when:

- every projection row is derived from exact accepted immutable evidence;
- accepted derivative publication and projection publication commit or roll back together;
- the pre-cutover symmetric difference is empty;
- all listed customer-facing consumers use the canonical read module;
- exact media and download paths use a primary-key and event-scoped lookup rather than the gallery
  collection query;
- no customer-facing eligibility or media-selection SQL references the three processing tables;
- no existing gallery, visibility, selfie-result, commerce, entitlement, or storage authorization
  behavior changes;
- deployment preserves gallery availability while pausing only processing publication; and
- bounded live evidence shows that gallery-media traffic no longer causes the observed processing-
  table read amplification.
