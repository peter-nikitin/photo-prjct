# 0024: Expand selfie search with conservative face clusters

- Status: Proposed
- Date: 2026-08-05
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR 0019 accepts event-scoped public selfie search with direct exact comparison against every
compatible gallery face embedding. Each appearance of a face in a photograph is a separate
embedding, so a direct match can miss other photographs of the same person when pose, occlusion,
lighting, scale, or equipment moves those face embeddings outside the direct threshold.

The product objective is to find more correct photographs without introducing named identity,
cross-event matching, persistent query embeddings, or automatic model changes from feedback. A
durable decision is required because cluster membership creates a new derived biometric
relationship, adds a second source of immutable result membership, and affects ranking evidence,
feedback interpretation, observability, and rollback.

## Decision drivers

- Improve photo-level recall while preserving the existing direct result.
- Make false merges more costly than fragmentation: an incorrect merge can add many unrelated
  photographs, while a split person cluster only limits expansion.
- Preserve ADR 0019's event isolation, transient query vector, cleanup-before-publication, bearer
  snapshot, and probable-match semantics.
- Keep PostgreSQL authoritative and avoid a new online vector service or identity system.
- Preserve immutable provenance so direct and expanded quality can be measured separately.
- Introduce contextual evidence only after the face-cluster contribution is measured in isolation.

## Considered options

1. Build immutable conservative event-scoped face clusters offline and expand only from calibrated
   strong direct anchors.
2. Keep direct-only search and accept that related photographs outside the direct threshold remain
   undiscovered.
3. Replace gallery face instances with one centroid or representative for each presumed person.
4. Build one combined identity graph from face, clothing, helmet, bicycle, bib, time, and sequence
   evidence in the first increment.

## Decision

Select option 1.

Build versioned anonymous face-cluster corpora from compatible accepted gallery face embeddings
within one event. Use deterministic thresholded graph clustering with a medoid-radius consistency
guard. Preserve singleton clusters and allow one person to remain split across several fragments;
do not force cluster count to match registration or attendance.

Cluster building is an offline derived-data operation governed by Django and PostgreSQL. Published
corpus versions are immutable and rebuildable. An operator explicitly activates one compatible
version for new searches. New photos or processing generations require a new corpus to participate
in expansion; they never mutate an active version or an existing bearer result.

Direct exact ranking remains the first and authoritative result source. Only a direct face match at
or below a separately calibrated threshold stronger than the ordinary display threshold can select
a cluster. The result snapshot keeps all direct photos in their existing order, then appends unique
cluster-expanded photos deterministically. A photo reached by both paths appears once as a direct
result while retaining both evidence sources.

PostgreSQL stores the corpus/configuration identity, direct and expanded counts, and immutable
per-result provenance. Structured ranking and terminal events plus the bounded daily summary expose
aggregate expansion volume and latency without photo, face, cluster, selfie, vector, contact, or
bearer identities. Durable product records support restricted aggregate retrospective reporting
after operational journal retention expires.

Customer feedback remains immutable evaluation evidence under ADR 0023. It can be aggregated by
frozen result source but never changes cluster membership, thresholds, models, activation, or saved
results automatically.

This decision does not introduce named identity, cross-event clustering, participant counting,
query-vector persistence, a vector database, an online clustering service, or clothing, helmet,
bicycle, bib, time, location, or sequence expansion. Future contextual factors remain separately
typed evidence and must not redefine a face cluster as a person identity.

## Consequences

### Positive

- Strong direct matches can recover additional photographs with difficult face pose or quality.
- Direct results remain unchanged and available when clustering is disabled or unavailable.
- Source-separated provenance and feedback make the incremental recall and false-positive cost
  measurable.
- Immutable event-scoped corpora and result snapshots preserve reproducibility.
- The design reuses Django, PostgreSQL, and existing gallery embeddings without new online
  infrastructure.

### Negative

- Durable cluster membership adds a derived biometric relationship that requires restricted access,
  versioning, and lifecycle discipline.
- False cluster merges can amplify one incorrect direct anchor across several photographs.
- Conservative guards intentionally leave fragmentation and singleton clusters, limiting maximum
  recall.
- Exact offline graph construction adds potentially substantial CPU, memory, and build time for
  large events.
- Result, feedback, and observability schemas become more complex because source evidence must
  remain immutable and distinguishable.
- Existing expanded bearer results remain readable after rollback because result membership is an
  immutable snapshot.

### Follow-up

- Calibrate cluster-edge, representative-distance, and strong-anchor thresholds on a person-split
  labelled closed benchmark with an explicit deletion boundary for any temporary biometric query
  artifacts.
- Implement corpus publication, direct-first expansion, provenance, source-separated feedback, and
  observability only after this ADR is accepted and an implementation plan is approved.
- Record explicit recall gain, precision cost, false merges, corpus build resources, and search
  latency before enabling an environment.
- Evaluate one contextual factor at a time only after the direct-plus-face-cluster baseline is
  measured and separately specified.

## Validation and rollback

Validate deterministic and atomic corpus publication, event/model/generation isolation, medoid
guard behavior, strong-anchor enforcement, unchanged direct ordering, unique direct-first result
membership, immutable provenance, selfie cleanup, non-persisted query vectors, direct-only fallback,
source-separated feedback, privacy-bounded events, daily aggregation, and durable retrospective
reporting.

Activate only after a held-out labelled evaluation has an explicitly approved minimum recall gain
and maximum precision regression, every observed false merge is reviewed, and measured resource use
fits the supported environment.

Roll back new expansion by disabling its environment gate or deactivating the event corpus. New
searches then use direct-only ranking. Existing bearer results and feedback remain immutable and
readable with their saved provenance. Reconsider this decision if false merges are not containable,
the recall gain is immaterial, offline building cannot fit supported resources, provenance cannot
be preserved, or the derived biometric relationship requires a broader consent or deletion model.

## References

- [Selfie Search Face-Cluster Expansion Design](../superpowers/specs/2026-08-05-selfie-search-face-cluster-expansion-design.md)
- [Architecture: Search](../architecture.md#search)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
- [ADR 0017: Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
- [ADR 0023: Store consented selfie-search quality feedback](0023-store-consented-selfie-search-feedback.md)
