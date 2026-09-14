# Selfie Search In-Memory Ranking Cache

- **Status:** Proposed for written review
- **Date:** 2026-09-14
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), Search and photo
  processing; Security, privacy, and legal boundaries
- **Related ADRs:** [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md)
- **ADR impact:** Conforms to ADRs 0019, 0017, and 0028. This is a reversible implementation
  detail inside the accepted Django exact-ranking and single-deployment boundaries; no new ADR is
  required.

## Outcome

Reduce the warm-path Django cohort-load and direct-ranking time for the current 45,239-face event
from roughly 23–28 seconds to below five seconds without `pgvector`, a new service, a ranking or
threshold change, or a new biometric-data boundary.

The 2026-09-14 production measurement established that loading the authoritative cohort identities
without JSON vectors takes 1.176 seconds. A contiguous matrix comparison at the same cohort size is
not the dominant cost. The current latency instead comes from repeatedly transferring and decoding
all JSON vectors and then comparing every vector in a Python loop.

## Scope

This hotfix includes:

- one bounded, process-local cache of a validated compatible face cohort in each Django/Gunicorn
  process;
- an authoritative lightweight identity read before every ranking, so a cached cohort is reused
  only when its exact eligible membership is unchanged;
- a contiguous NumPy `float64` matrix for vectorized conservative candidate selection;
- the existing exact Python distance calculation for every retained candidate and the existing
  best-face-per-photo and deterministic result ordering rules;
- cache-specific timing and hit/miss observability without biometric payloads; and
- disabling Gunicorn request-count recycling so request volume does not continually discard the
  process-local cache.

Excluded:

- `pgvector`, ANN or approximate result membership, a vector database, or a broker;
- a dedicated Django selfie service or another VM;
- threshold, face-generation, cluster-expansion, result-order, or result-authorization changes;
- persistent gallery matrices, query vectors, selfies, intermediate candidates, or new result data;
- backfills, reprocessing, queue resets, feature-gate changes, or database schema changes; and
- optimization of preview redirects, which is owned by separate work.

## Selected design

### Authoritative cohort identity

The existing shared compatible-cohort query remains the single eligibility authority. It exposes a
lightweight ordered identity projection containing the fields required to prove the exact accepted
projection, attempt, generation, event, detection, and photo membership, but not the JSON vector.

Before each direct ranking, Django reads this identity projection and compares it with the cached
cohort fingerprint. The fingerprint is the complete ordered sequence of scalar identity values,
not only a count, timestamp, or TTL. A cache hit is allowed only on exact equality. Therefore a
published addition, removal, replacement, generation change, or eligibility change is observed
before the cached matrix can be reused.

On a miss, Django loads the current full compatible cohort through the same eligibility authority,
validates every row using the existing model, dimension, normalization, finiteness, event, attempt,
and photo-identity rules, and derives both the matrix and fingerprint from those loaded rows. That
loaded cohort is the attempt's snapshot, matching the existing one-read ranking semantics. A later
search validates it again against a fresh authoritative identity read.

### Cache lifetime and bounds

Each Gunicorn process owns at most one cohort entry. Its key includes event ID, query model,
dimensions, and the search's frozen compatible-generation configuration. The entry contains only:

- the full scalar cohort fingerprint;
- immutable detection/photo identity metadata needed by ranking; and
- one contiguous `float64` gallery matrix.

The cache contains no selfie bytes, query embedding, bearer token, search result, storage key, or
signed grant. Replacement drops the prior entry. At the supported maximum of 45,239 512-dimensional
faces, the matrix is about 177 MiB per warmed process; total entry memory must remain below 256 MiB.

A process-local lock prevents duplicate builds in one process. A contender rechecks the cache after
acquiring the lock and reuses the newly built entry when its already-read authoritative identity is
equal. Different Gunicorn processes remain independent; no filesystem, shared-memory, Redis, or
database cache artifact is introduced.

### Exact ranking contract

The query vector is validated by the existing exact contract before matrix comparison. NumPy
computes cosine scores for the complete validated matrix and discards only rows whose approximate
distance is greater than `threshold + 1e-10`. The `1e-10` margin is deliberately wider than the
floating-point reduction error for at most 512 normalized `float64` components.

Every retained row is then recomputed with the existing `math.fsum` distance calculation. Only
that exact distance decides threshold inclusion, the best detection per photo, the persisted score,
and sorting by `(cosine_distance, photo_id)`. Thus vectorization is only a conservative shortlist:
it may retain an extra near-boundary row, but it must not exclude a row that the current exact
algorithm would accept or alter any persisted distance or ordering.

Malformed gallery rows remain fail-closed. The full cohort is validated when built, rather than
allowing vectorized selection to hide a row that the existing implementation would reject.

### Gunicorn process lifetime

The canonical web service sets `GUNICORN_MAX_REQUESTS=0` and
`GUNICORN_MAX_REQUESTS_JITTER=0`. Request-count recycling currently destroys warm caches under high
preview traffic and has independently produced failed requests at worker replacement boundaries.
Normal deployment replacement, process failure, and operator rollback remain the ways a cache is
discarded. Existing health checks and container restart policy remain unchanged.

## Data flow

1. The selfie worker returns one transient normalized query embedding through the protected
   callback.
2. Django revalidates the attempt snapshot and reads the current lightweight compatible-cohort
   identity outside the final publication lock.
3. On an exact cache hit, Django reuses the matrix and metadata without selecting or decoding JSON
   vectors. On a miss, one thread builds and validates a replacement entry from the current full
   cohort.
4. Django performs vectorized conservative selection and exact Python recomputation for retained
   rows.
5. The existing cluster-expansion decision, short final lock/revalidation, immutable result write,
   and selfie-cleanup-before-publication flow continue unchanged.

The gallery-photo query source uses the same compatible-cohort and direct-ranking service and
therefore receives the same optimization without changing its synchronous state or authorization
contract.

## Concurrency and failure semantics

- Cohort validation and cache construction remain outside `SelfieSearch`, job, attempt, and event
  publication locks.
- A concurrent eligibility change may occur after the identity read just as it may occur after the
  current full cohort query; the accepted result remains the immutable snapshot ranked by that
  attempt.
- A cache miss never waits on another Gunicorn process. Within one process, only construction is
  serialized; hit ranking uses the immutable entry after it is published atomically.
- Build, validation, allocation, or ranking failure follows the existing fail-closed incompatible
  ranking path. There is no stale-cache or approximate-result fallback.
- Lease loss, duplicate callback, cleanup retry, terminal publication, and result immutability
  semantics remain unchanged.
- Rollback deploys the prior immutable application image and restores the prior Gunicorn settings;
  no durable data or cache cleanup is required.

## Observability

The existing bounded ranking event adds cache outcome, identity-read duration, cache-build duration,
exactly validated face count, shortlist count, and ranking duration. Logs and metrics must not
contain vectors, selfies, bearer tokens, storage keys, signed grants, or fingerprint contents.

## Acceptance criteria

1. SFace and AdaFace fixtures, including equal scores and candidates within `1e-10` of the
   threshold, produce exactly the same photo IDs, detection IDs, persisted cosine distances, and
   final ordering as the current implementation.
2. Invalid dimension, model, normalization, finiteness, event, attempt, and photo identity inputs
   retain the current fail-closed behavior on both cold and warm paths.
3. A warm cache hit performs the authoritative identity query but does not select, transfer, or
   decode the JSON vector column.
4. Any change to the complete authoritative cohort identity causes a miss and replacement before
   reuse; count-only collisions do not pass validation.
5. Concurrent same-process misses build one immutable entry, do not publish a partial entry, and do
   not change callback lease or result-publication behavior.
6. One 45,239-face 512-dimensional cache entry consumes less than 256 MiB, and each process retains
   no more than one entry.
7. On the production-equivalent 45,239-face cohort, cold `load_ms + rank_ms` remains below 60
   seconds and a warm `identity_ms + rank_ms` is below five seconds with exact baseline equality.
8. Canonical deployment reports `GUNICORN_MAX_REQUESTS=0`, stable web restart counts, healthy public
   HTTP, no accumulating PostgreSQL lock waiters, concurrent bulk-queue progress, and at least one
   warm selfie completion meeting the five-second ranking-path bound.
9. No migration, queue mutation, backfill, reprocessing, selfie-search feature-state change, or
   persistent biometric artifact is introduced.

## Rejected alternatives

- **NumPy without a cohort cache:** removes the Python ranking cost but preserves the dominant
  repeated JSON-vector load and decode, so it cannot meet the warm-path target.
- **TTL-only cache validation:** is faster than a complete identity read but can reuse eligibility
  state after a publication or cohort change, which is not an acceptable privacy or correctness
  trade.
- **Dedicated Django selfie service:** centralizes one cache and isolates callback CPU, but expands
  Compose, routing, health, and rollback scope. It remains a follow-up only if bounded process-local
  caching cannot meet the measured target.
- **`pgvector`:** is the intended later scaling direction after traffic falls, but requires a
  schema/index and rollout decision outside this urgent hotfix.

