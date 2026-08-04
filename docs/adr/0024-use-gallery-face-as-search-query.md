# 0024: Use a gallery face as a search query

- Status: Proposed
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
and cleanup work without adding query evidence when the gallery photo contains exactly one accepted
face.

Using a stored gallery embedding as a public query is a durable biometric-access decision. It needs
an explicit boundary even though ranking, result persistence, event isolation, and result media
authorization can remain under ADR 0019.

## Decision drivers

- Remove the screenshot and upload step from the shortest testable customer path.
- Keep matching strictly inside the current published event.
- Reuse accepted model-compatible evidence instead of repeating ML inference.
- Store no query vector and introduce no temporary image or cleanup lifecycle.
- Preserve ADR 0019's immutable bearer result and existing media-authorization boundary.
- Avoid face selection until the one-face hypothesis has been tested.

## Considered options

1. Use the single current compatible accepted gallery-face embedding directly as the query vector.
2. Crop the single face server-side and submit it through the existing selfie worker path.
3. Crop the face in the browser and submit it as an implicit selfie upload.
4. Retain the screenshot-and-upload flow until multi-face selection is designed.

## Decision

Select option 1.

For any photo card rendered by an existing gallery surface, Django may expose a similarity-search
action only when the photo has exactly one current compatible accepted face embedding. Submission
revalidates the published event, current gallery eligibility, event membership, and unique source
embedding. Presentation is never sufficient authority.

Django uses that embedding transiently as the query vector for ADR 0019's exact cosine ranking.
The candidate cohort remains limited to the source photo's event, keeps one best detection per
photo, and uses the accepted model generations and distance threshold. The source photo must be a
member of the saved result; otherwise the transaction fails without publishing a result.

The search and ordered result rows are stored atomically as an immediately ready immutable
snapshot. Django stores the query source and ranking configuration but not the query vector. This
path creates no temporary media object, selfie-search worker job, worker attempt, or cleanup work.

The result uses ADR 0019's existing non-expiring bearer URL, probable-match presentation, current
publication checks, and result media authorization. This decision introduces no event access-type
branch and does not make a hidden gallery visible. It does not authorize cross-event matching,
named identity, face selection, result expiry, authentication, or new media access.

## Consequences

### Positive

- A customer can move from one known gallery photo to event-scoped probable matches with one
  action.
- Existing accepted evidence avoids redundant image transfer, storage, inference, and cleanup.
- The implementation stays inside Django/PostgreSQL and reuses the established result surface.
- Query-vector retention, ranking semantics, and result media authorization remain unchanged.

### Negative

- Any visitor who can access an eligible gallery card can create a bearer-linked face-search
  snapshot for its single recognized person.
- The first increment offers no action for group photos or photos whose accepted face evidence is
  missing, stale, incompatible, malformed, or ambiguous.
- Each activation creates a new durable snapshot; it does not deduplicate identical source-photo
  searches.
- Exact ranking runs synchronously in the submission request and inherits the measured cohort-size
  limits of the existing direct ranking implementation.

### Follow-up

- Add explicit face selection only after the one-face path validates the product hypothesis.
- Revisit synchronous exact ranking only when measured request latency or event size violates an
  accepted bound.
- Revisit bearer retention and abuse controls with the broader ADR 0019 governance triggers.

## Validation and rollback

Validate action eligibility for zero, one, and multiple faces; submission-time revalidation; event
isolation; deterministic ranking; mandatory source-photo membership; atomic immutable persistence;
absence of stored query vectors, temporary objects, and worker jobs; and unchanged result media
authorization.

Roll back by removing or disabling the gallery action and its submission route. Existing ready
snapshots remain readable under ADR 0019's bearer semantics. Reconsider the decision if public
gallery-origin searches cause material abuse, synchronous ranking exceeds the accepted request
budget, or one-face eligibility is too rare to test the hypothesis.

## References

- [Find similar photos from a gallery photo design](../superpowers/specs/2026-08-04-find-similar-from-gallery-design.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [Architecture: Search](../architecture.md#search)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
