# 0027: Project current capture time onto Photo

- Status: Accepted
- Date: 2026-08-08
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

The event gallery can filter by canonical capture time stored in immutable accepted
`capture_metadata` attempts. Proving current version-2 evidence inside every pageable gallery query
requires joins through processing state, run, job, and attempt tables plus a JSON timestamp cast.
On the accepted 17,043-photo event-9 corpus, that query took 8.169-8.633 times the matching
unfiltered database baseline and the rendered page took 6.225-8.108 times its baseline. Both exceed
the approved 2x gate.

Immutable accepted attempts must remain authoritative and explainable. A faster read path must not
introduce stale time during reprocessing, accept late attempts, create an eventual-consistency
window, or turn a derived value into an independent correction authority.

## Decision drivers

- Keep the pageable gallery range predicate on a small indexed relation.
- Preserve immutable accepted processing evidence as the sole source of truth.
- Make projection freshness synchronous with current-state rotation and attempt acceptance.
- Retain exact provenance and a deterministic rebuild/reconciliation path.
- Avoid stale fallback behavior and asynchronous projection infrastructure.
- Permit a safe cutover without exposing partially backfilled projection data.

## Considered options

1. Store capture time and its source-attempt reference directly on `Photo`.
2. Store an event-scoped one-to-one capture-time projection in the processing domain.
3. Store a query timestamp on `PhotoProcessingState`.
4. Use a materialized view or asynchronous projection refresh.
5. Retain the direct processing-evidence gallery query.

## Decision

Store a nullable canonical UTC capture time and nullable immutable source-attempt reference directly
on `Photo`, with an event-and-time index. The pair is both null or both non-null. A non-null pair is
a rebuildable read projection of the same photo's exact current accepted succeeded
`capture_metadata` version-2 attempt and identical non-null result time.

Immutable processing attempts and current processing state remain authoritative. The projection
does not authorize manual correction, reconstruct processing state, or make version-1, stale,
failed, null, or unaccepted evidence eligible.

Django maintains projection freshness in the same PostgreSQL transaction as authoritative state
changes. Starting new current capture-metadata work clears the projection. Accepting a current
valid version-2 result publishes its non-null time and source or leaves the pair null for a valid
missing result. Stale, late, expired, duplicate, failed, cancelled, wrong-photo, wrong-processor,
and wrong-version attempts cannot publish it.

Every projection-aware lifecycle or repair path acquires the rows it needs in the complete order
`Event -> EventProcessingRun -> ProcessingJob -> Photo -> PhotoProcessingState ->
ProcessingAttempt`, omitting only rows it does not need and never acquiring leftward. Repair
discovers identifiers without publishing from them, acquires applicable locks in that order,
re-reads current identity and evidence, retries if identity changed, and updates the projection
before releasing locks.

Existing projections are populated by a strict, idempotent, dry-run-by-default command, not a data
migration. Global reconciliation covers every existing event with qualifying version-2 evidence;
event 9 retains exact 17,043-photo customer acceptance. Aggregate operations disclose no row-level
media or metadata values.

Cutover uses two releases. Release A deploys schema and synchronous projection writers while the
gallery continues reading direct evidence, then backfills and reconciles globally. Release B runs
final reconciliation and the first/midpoint/last 2x benchmark before switching gallery reads to the
projection and removing the direct JSON join/cast path.

A future processor-version correction must separately approve its evidence contract and explicitly
replace version 2 as the supported projection source. Manual capture-time overrides require a
separate audited authority decision.

## Consequences

### Positive

- Filtered gallery reads use a direct indexed timestamp range on `Photo`.
- Exact attempt provenance and immutable evidence remain available for audit and rebuild.
- Reprocessing cannot leave stale projected time visible.
- Backfill is retryable and does not mutate processing evidence.
- Two-release cutover prevents partially populated reads and closes the reconciliation-to-switch
  write gap.

### Negative

- `Photo` gains a persistent dependency on a processing attempt and duplicates one derived value.
- Every capture-metadata state transition must maintain an additional transactional invariant and
  common lock order.
- Deployment requires two releases plus global backfill, reconciliation, and performance evidence.
- The event-time index does not eliminate the gallery's existing filename-and-ID ordering step.
- A future processor version cannot become a projection source without an explicit decision and
  coordinated rebuild.

### Follow-up

- Implement and validate the schema, synchronous writer, backfill, reconciliation, and concurrency
  contracts in Release A.
- Measure the accepted local clone after backfill and retain aggregate evidence.
- Deliver Release B only after final reconciliation and all per-page database/render ratios pass
  the 2x gate.
- Update current architecture only after implementation and rollout evidence exist.

## Validation and rollback

Validate with state-transition, stale/late result, null/failure, transaction rollback, shared lock
order, idempotent backfill, global reconciliation, gallery regression, and privacy tests. On event 9
require exactly 17,043 current accepted version-2 source/value pairs and zero missing, stale, extra,
or mismatching projections. Benchmark first, midpoint, and last filtered pages against their
unfiltered baselines and require each database and rendered ratio to be no more than 2x.

Before Release B, rollback keeps direct reads active while Release A continues maintaining the
rebuildable projection. A Release-B failure leaves Release A active. Reconsider this decision if
the indexed projection still fails the 2x gate, transactional lock ordering causes material
contention, or a new correction authority or processor version requires different source
precedence.

## References

- [Photo capture-time projection design](../superpowers/specs/2026-08-08-photo-capture-time-projection-design.md)
- [Event gallery time filter design](../superpowers/specs/2026-08-08-event-gallery-time-filter-design.md)
- [Failed local-clone benchmark](../performance/2026-08-08-event-gallery-time-filter-local-clone.json)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
- [ADR 0017: Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0022: Use numbered gallery pages](0022-use-numbered-gallery-pages.md)
