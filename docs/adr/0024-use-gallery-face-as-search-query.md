# 0024: Use a selected gallery face as a search query

- Status: Accepted
- Date: 2026-08-04
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR 0019 accepts public event-scoped face search from an uploaded selfie. That path deliberately
creates a transient query embedding through the private worker, deletes the selfie before terminal
publication, and stores an immutable bearer-linked result snapshot.

A customer who has already found themself in an event gallery must currently take a screenshot and
submit it as a selfie to reach the same result. Accepted event-photo processing already stores the
compatible face embedding needed for exact ranking. Reprocessing a crop would add storage, worker,
and cleanup work without adding query evidence. A photo with one accepted face can use it
immediately; a photo with several accepted faces needs an explicit public choice so Django never
guesses which person the customer means.

Using a stored gallery embedding as a public query is a durable biometric-access decision. It needs
an explicit boundary even though ranking, result persistence, event isolation, and result media
authorization can remain under ADR 0019.

## Decision drivers

- Remove the screenshot and upload step from the customer path.
- Keep matching strictly inside the current published event.
- Reuse accepted model-compatible evidence instead of repeating ML inference.
- Store no query vector and introduce no temporary image or cleanup lifecycle.
- Preserve ADR 0019's immutable bearer result and existing media-authorization boundary.
- Make a multi-face query explicit without exposing vectors or adding crop storage.

## Considered options

1. Use the only accepted face directly, or let the customer explicitly select one accepted face
   when several exist, then use its stored embedding as the query vector.
2. Choose a multi-face source automatically by confidence or ordering.
3. Crop the selected face server-side and submit it through the existing selfie worker path.
4. Crop the face in the browser and submit it as an implicit selfie upload.

## Decision

Select option 1.

For any photo card rendered by an existing gallery surface, Django may expose a similarity-search
control for each current compatible accepted face whose bounding box is explicitly tied to the
published preview coordinate space. A photo with one usable face submits it directly. A photo with
several usable faces presents compact crops from the already-authorized preview and requires the
customer to select one; Django never infers identity or chooses a face automatically. No separate
face-crop object or media endpoint is created.

Submission identifies the selected detection and revalidates the published event, current gallery
eligibility, event membership, accepted processing generation, preview geometry, detection, and
embedding. Presentation is never sufficient authority. Legacy detections without explicit preview
coordinates are not presented by this control and receive no compatibility fallback.

Django validates the exact source at submission, persists a queued bearer result, then uses the
stored embedding transiently for ADR 0019's exact cosine ranking when the new result tab issues
its protected process request. The candidate cohort remains limited to the source photo's event,
keeps one best detection per photo, and uses the accepted model generations and distance threshold.
The source photo must be a member of the saved result; otherwise the transaction fails without
publishing results.

The selected embedding is used only transiently. The gallery form opens its bearer result in a new
tab, which renders a CSRF-protected POST process form only while its gallery-origin search remains
queued. Browser code retries rejected and non-success process requests without parallel calls until
a successful response, while the existing status poller reloads terminal results. The locked process
operation is idempotent: it publishes the ordered rows and ready snapshot atomically; an irreversible
stale-source or ranking failure instead publishes a terminal failure without rows, while an ambiguous
database failure remains queued for retry. The no-JavaScript form has an explicit submit control.
Django stores the query source and ranking configuration but not
the query vector. This path creates no temporary media object, selfie-search worker job, worker
attempt, or cleanup work.

The result uses ADR 0019's existing non-expiring bearer URL, probable-match presentation, current
publication checks, and result media authorization. This decision introduces no event access-type
branch and does not make a hidden gallery visible. It does not authorize cross-event matching,
named identity, automatic person identification, result expiry, authentication, or new media
access.

## Consequences

### Positive

- A customer can move from a known gallery photo to event-scoped probable matches without a
  screenshot, including when several people appear in the source photo.
- Existing accepted evidence avoids redundant image transfer, storage, inference, and cleanup.
- The implementation stays inside Django/PostgreSQL and reuses the established result surface.
- Query-vector retention, ranking semantics, and result media authorization remain unchanged.

### Negative

- Any visitor who can access an eligible gallery card can inspect small crops from that already
  visible preview and create a bearer-linked face-search snapshot for any usable person in it.
- Photos whose accepted face evidence or preview geometry is missing, stale, incompatible, or
  malformed still offer no control for the affected faces.
- Each activation creates a new durable snapshot; it does not deduplicate identical source-photo
  searches.
- Exact ranking runs from the result tab's process request and inherits the measured cohort-size
  limits of the existing direct ranking implementation.

### Follow-up

- Revisit the compact chooser only if real gallery use shows that dense group photos exceed its
  accepted 32-face presentation bound.
- Revisit browser-triggered exact ranking only when measured request latency or event size violates
  an accepted bound.
- Revisit bearer retention and abuse controls with the broader ADR 0019 governance triggers.

## Validation and rollback

Validate control eligibility for zero, one, and multiple faces; exact selected-detection submission
and processing-time revalidation; deterministic crop geometry; event isolation; deterministic
ranking; mandatory source-photo membership; queued creation without rows or jobs; locked idempotent
atomic publication; absence of stored query vectors, crop objects, temporary objects, and worker
jobs; unchanged result media authorization; and keyboard, no-JavaScript, desktop, and mobile
chooser behavior.

Roll back by removing or disabling the gallery action and its submission route. Existing ready
snapshots remain readable under ADR 0019's bearer semantics. Reconsider the decision if public
gallery-origin searches cause material abuse, synchronous ranking exceeds the accepted request
budget, or public selection among gallery faces causes material abuse.

## References

- [Gallery face selector design](../superpowers/specs/2026-08-05-gallery-face-selector-design.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [Architecture: Search](../architecture.md#search)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
