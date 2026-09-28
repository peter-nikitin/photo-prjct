# Exact face search with pgvector

- **Status:** Approved in conversation on 2026-09-27
- **Date:** 2026-09-25
- **Owner:** FindMe Photo
- **Related architecture:** [Architecture: Search](../../architecture.md#search),
  [Open decisions](../../architecture.md#open-decisions)
- **Related ADRs:** [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md),
  [ADR 0025](../../adr/0025-expand-selfie-search-with-face-clusters.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- **ADR impact:** **Conforms to accepted [ADR 0040](../../adr/0040-use-pgvector-for-exact-face-search.md).**
  ADR 0040 supersedes ADR 0019's in-memory direct ranking and initial vector-infrastructure
  exclusion only. PostgreSQL authority, event/generation isolation, result and query privacy,
  and the temporary gate lifecycle remain governed by the cited accepted ADRs.
- **Related specifications:** [Public selfie search](2026-07-30-public-selfie-search-design.md),
  [Gallery face as query](2026-08-05-gallery-face-selector-design.md),
  [Face-cluster expansion](2026-08-05-selfie-search-face-cluster-expansion-design.md),
  [Production AdaFace](2026-08-16-production-adaface-new-events-design.md)

## Outcome

An uploaded-selfie search and a gallery-face search use the same eligible cohort and exact
full-scan search. Very borderline membership, near-tie order and anchor changes are accepted
under [ADR 0041](../../adr/0041-accept-pgvector-numerical-boundaries.md). A separate pgvector table is filled while the existing
JSON/Python reader continues serving customers. A runtime gate then selects the exact PostgreSQL
reader for staff review and, after side-by-side proof, all searches. This allows an immediate
return to the old reader while both representations are complete.
The customer flow, frozen search configuration, result snapshot, and media permissions stay the
same. The migration must improve measured search resource use on representative large events
without degrading unrelated web and processing work on the shared PostgreSQL host.

This design selects exact search. It does not authorize approximate nearest-neighbor search,
HNSW, IVFFlat, a separate vector service, a new embedding model, threshold recalibration, or
cross-event recognition during the pgvector read transition. A later SFace-to-AdaFace reprocessing
project changes recognition behavior and has its own approval and quality criteria.

## Current system and reason for change

`FaceEmbedding.vector` is JSON. Current `origin/main` validates a scalar cohort fingerprint and
reuses one process-local NumPy matrix where possible. Cache misses transfer compatible vectors
into Python; NumPy shortlists conservatively and exact Python arithmetic supplies final distance
evidence. Both sources choose the best face per photo and sort by distance and photo ID.
The offline face-cluster builder reads the
same accepted cohort. Existing events use 128-dimensional SFace; newer events use
512-dimensional AdaFace. These are existing recognition-model generations, not two versions
required by pgvector. The two generations must never mix in a search.

This makes each direct search load a potentially large biometric cohort and spend Django memory
and request/transaction time on a linear scan. A previous production read-only investigation
observed 45,241 embedding rows in a large event; this is historical evidence, not a current
capacity measurement. pgvector's default exact scan is suitable for testing whether compact
storage and database-side distance calculation improve this path before considering any loss of
recall.

## Selected design

### Parallel vector representation and authority

- Add a separate PostgreSQL table with one `vector` row per face detection that has an
  embedding, bound directly to that detection and its model identity. It must stand on its own
  when the legacy `FaceEmbedding` table is eventually removed. Its key and foreign keys must not
  depend on a legacy embedding row. A variable-dimension vector supports the existing 128D and 512D
  generations during this transition. The declared model and generation determine the required
  dimension.
- Keep the existing JSON `FaceEmbedding` rows and Python reader authoritative while the new
  table is populated and the gate is `off`. Reconcile historical rows by detection, model,
  dimension, event, and accepted generation. New accepted face processing must keep both
  representations complete until the old reader is retired. No search reads an incomplete
  pgvector cohort; write or backfill failure cannot be mistaken for a zero-match search.
- On write and read, require the existing finite, nonzero, L2-normalized embedding contract,
  exact expected dimension, model identity, accepted attempt, current projection, event/photo
  agreement, and gallery eligibility. A bad or incompatible vector must never become a match.
- Every eligible JSON embedding must map to the same detection and generation in the new table,
  or be explicitly accounted for as invalid and excluded under the existing rules. No photo,
  detection, projection, cluster membership, or saved result is silently deleted or re-enrolled.
- The temporary selfie query vector remains transient. A gallery-origin query reads its selected
  source embedding transiently. Neither source persists a query vector or sends it to logs,
  analytics, the browser, or the ML worker beyond the existing narrow callback contract.

### Read selection and side-by-side proof

- Register one temporary `pgvector-face-search-read` gate in the existing code-owned feature
  registry. Reconciliation creates it in `off`; only the operator changes it in Django Admin.
  `off` selects the legacy reader for everyone. `staff` selects pgvector only for authenticated,
  active staff and keeps the legacy reader for everyone else. `on` selects pgvector for all
  otherwise eligible searches. The gate grants no access to an event, selfie, gallery face, or
  result that the existing rules deny.
- Both uploaded-selfie and gallery-face searches use the same selection policy. Because the
  uploaded-selfie worker callback has no visitor identity, record the validated staff eligibility
  at submission and resolve the current gate state when ranking begins. Turning the gate `off`
  before ranking returns queued work to the legacy reader. A saved ready result remains immutable.
- A gallery-origin search reads its selected face from the selected representation. The comparison
  path checks that the legacy and pgvector source embeddings denote the same detection and model,
  then uses one validated query value for both rankers so a representation difference cannot be
  mistaken for a candidate-ranking difference.
- Side-by-side comparisons run only in a bounded, explicit review path using the same transient
  query vector and the same frozen search configuration against the same eligible corpus snapshot.
  Only the selected reader's results are published. Compare cohort membership, best detection per
  photo, threshold decisions, order, direct distances, strong cluster anchors, timing, and database
  load. Comparison must not persist the query vector, a second bearer result, or per-face
  biometric diagnostics. Aggregate mismatches and timings may be retained without identities.
- Missing rows, a stale projection, or a pgvector query error in a selected new-path search fail
  closed without silently falling back to JSON. The operator can change the gate to `off` for
  subsequent ranking while the two representations remain complete.

### Direct exact ranking

For each search, Django retains responsibility for validating the query and frozen search
configuration, resolving the permitted cohort, saving one immutable result per photo, and
enforcing authorization. PostgreSQL computes cosine distances with pgvector over **all** eligible
face rows in the event and compatible frozen generations. It returns only candidate identities,
photo identities, and distances needed for direct result construction; no gallery vector is
transferred for the online ranking scan.

The ranking contract is unchanged:

1. Apply event, photo visibility, accepted-attempt/current-projection, model, generation, and
   dimension constraints before accepting a distance.
2. Include every face at or below the search's frozen cosine threshold. There is no top-K cap,
   approximate index, or candidate prefilter that can omit an eligible match.
3. Choose one best eligible face per photo; resolve equal-distance faces deterministically by
   detection ID. Sort direct photos by `(distance, photo_id)` and retain exact detection and
   distance provenance.
4. Keep direct photos first. The existing optional cluster expansion consumes those direct rows
   and strong-anchor distances, then appends its own photos under its accepted rules.
5. Gallery-origin searches still require their selected source photo among the direct results;
   otherwise publication fails atomically. Uploaded-selfie cleanup still precedes terminal public
   publication. Already saved bearer results are never reranked.

The PostgreSQL reader uses native pgvector cosine distances, inclusive configured thresholds,
and deterministic `(distance, photo_id)` ordering with detection-ID tie breaking. It does not
recheck candidates in Python. pgvector single-precision components and normalization differ from
the old Python dot-product arithmetic. The maintainer explicitly accepts very borderline changes
under [ADR 0041](../../adr/0041-accept-pgvector-numerical-boundaries.md).

Retain an absolute distance comparison tolerance of `1e-6`. Classify threshold-membership changes
within that band, detection/order changes within near ties, and strong-anchor decisions near their
existing thresholds as accepted numerical differences. Report resulting expansion effects for
operator review. Any unexplained or non-boundary difference, delta above tolerance, cohort/source
identity drift, authorization change, or missing required source photo blocks activation. Do not
widen final thresholds, add Python candidate refinement, or persist extra float64 vectors.

### Shared cohort and offline clustering

The existing accepted-cohort eligibility rules remain one shared definition for online direct
search and offline cluster construction. The offline builder may still read bounded chunks of
vectors for its exact graph computation; this change does not redesign clustering or its
activated immutable corpus. It may continue reading JSON while that representation exists. Its
later change to the new table must preserve cluster membership rules and keep SFace and AdaFace
generations separate.

### Availability, migration, and rollback contract

The extension and database image must be available in development, CI, and the deployed
PostgreSQL environment before any schema relies on `vector`. The application must fail a
capability preflight clearly if pgvector is missing or incompatible; it must not serve a partial
cohort or silently fall back to a different ranker.

The new table can be filled and checked with the gate `off`, without changing the production
reader. New writes and old rows must remain consistently readable in both representations before
staff review and before public `on`. The implementation must prove ongoing reconciliation rather
than rely on a one-time row count. A missing or divergent current vector blocks pgvector
activation for that cohort. The read gate may move to `staff` only after completeness checks, and
to `on` only after side-by-side parity and representative load evidence are reviewed. While both
representations are complete, returning the gate to `off` restores the old reader without changing
saved results. The implementation plan must make extension availability, backup/restore,
backfill/reconciliation, cutover, and recovery mechanics explicit.

The later worker separation and SFace-to-AdaFace backfill form a separate approved change. The
backfill may write its new AdaFace evidence directly to the new vector table, but an event must
switch its pinned query generation only when its new accepted projection and threshold are ready.
Existing bearer snapshots remain immutable. Any activated face-cluster corpus tied to an old
generation must be rebuilt or deactivated under its existing rules. The model transition needs its
own quality evaluation because it can change result membership even when pgvector ranking has
numerical comparison with the old reader under ADR 0041.

**Rollback boundary:** Once a reprocessed event has AdaFace evidence only in the new table, the
legacy reader is no longer a complete fallback for that event. The later migration must retire or
block the `off` route for such events before new-only evidence is published, with an explicit
recovery method; it must not leave an Admin switch that silently serves partial results. After
all events and dependent offline readers use the new representation, remove the legacy JSON
embedding storage, legacy ranking reader, parallel-write logic, and temporary gate together. Keep
face detections, accepted projections, saved search results, and their evidence. This cleanup is
not part of the initial pgvector read cutover.

Database errors during ranking preserve the existing search-state semantics: no partial ready
snapshot, no cross-event fallback, and no exposure of biometric data in diagnostics. A failed
optional cluster lookup retains its accepted direct-only behavior; a failed direct scan does not
publish a misleading zero-match result.

## Alternatives considered

| Approach | Decision |
| --- | --- |
| Parallel table, then exact pgvector scan in PostgreSQL | Selected. Keeps full recall and provides an `off` rollback during controlled review, at the cost of temporary duplicated biometric storage and write/reconciliation work. |
| Keep JSON and optimize the Python scan or cache it | Does not remove repeated vector hydration or establish compact vector storage; a cache adds biometric copies and invalidation work. |
| HNSW or IVFFlat in PostgreSQL | Rejected for this transition. Approximate candidate selection can change result membership, including threshold and cluster-anchor matches. |
| Dedicated vector database | Rejected for this transition. Adds synchronization, operations, and another biometric store without evidence that exact PostgreSQL search misses an accepted target. |
| Move workers, then reprocess all SFace events into AdaFace before pgvector | Deferred. The agreed sequence introduces and validates pgvector first; later worker separation supports the independently approved AdaFace backfill. Reprocessing changes recognition evidence and historical search results. |

## Acceptance criteria

1. Both query sources, both production embedding dimensions, mixed historical/new events, hidden
   photos, rejected/stale attempts, and incompatible generations obey the current eligibility and
   privacy contracts.
2. For a frozen representative corpus and query set, direct result membership, selected face,
   order, threshold behavior, source-photo guarantee, saved distance, and cluster-expansion input
   meet the parity condition above. Zero-match and corrupt-data cases fail or complete exactly as
   their existing contracts require.
3. `off`, `staff`, and `on` route both query sources as specified, including queued selfie
   callbacks, missing flag rows, and a gate change before ranking. Ordinary visitors continue to
   use the old reader during staff review. A selected pgvector failure cannot publish partial or
   silently legacy-derived results.
4. Side-by-side proof uses one transient query and frozen cohort, publishes only one result, and
   records no biometric vectors or per-face diagnostics. An online pgvector search does not return
   gallery vector payloads to Django. Existing immutable bearer results and media authorization
   remain unchanged.
5. Every existing eligible embedding is reconciled in the new table, and ongoing accepted writes
   stay complete in both stores until the rollback boundary. Invalid historical rows are counted
   and investigated before activation. Backups restore a working vector-capable database.
6. On a representative large-event workload, record total search latency, cohort/scan time,
   Django and PostgreSQL CPU/RSS, transferred data, database concurrency, and unrelated web
   latency before and after. Activation requires an explicit improvement in the search bottleneck
   and no material regression to the shared production path; a numeric service target should be
   set from fresh baseline measurements in the implementation plan.
7. Before any later new-only AdaFace backfill, the old-reader rollback boundary is explicitly
   closed for affected events. Removing old storage and code cannot remove detections, projections,
   saved search results, or provenance.
8. No approximate vector index or dedicated vector service is installed as part of this change.

## Sources

- [pgvector project documentation](https://github.com/pgvector/pgvector): exact search is the
  default; HNSW and IVFFlat are approximate indexes; `vector` supports both current dimensions.
- [pgvector Python/Django documentation](https://github.com/pgvector/pgvector-python): Django
  extension migration, vector field, and cosine-distance integration.
