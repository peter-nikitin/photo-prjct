# 0037: Use a gallery-media read projection

- Status: Accepted
- Date: 2026-09-15
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Customer gallery, exact-media, selfie-result, and commerce reads selected current presentation
media by joining immutable processing attempts, current processing state, and published derivatives.
Those evidence tables are authoritative, but scanning them on customer requests couples a bounded
product read to growing operational history and caused material database contention on a large
event. The system needs a current, rebuildable media-selection relation without weakening accepted
processing provenance or media authorization.

## Decision drivers

- Remove processing-history relations from customer-facing gallery eligibility and media selection.
- Keep accepted processing attempts, state, and derivatives authoritative and auditable.
- Make publication atomic and fail closed when required projected media is absent.
- Rebuild and verify existing state without a data migration or a second writer.
- Cut over the only customer-serving deployment without exposing partially populated reads.

## Considered options

1. Store accepted clean and watermarked media directly on `Photo`.
2. Maintain a separate synchronous one-to-one gallery-media projection.
3. Build a PostgreSQL materialized view over processing evidence.

## Decision

Select option 2. `GalleryMediaProjection` is a sparse one-to-one relation keyed by `Photo`. It
stores only the accepted clean and watermarked presentation keys and their immutable source-attempt
references. It stores no event publication, visibility, folder, capture time, price, result
membership, order, entitlement, or original-media fact.

Processing attempts, current processing state, and derivatives remain authoritative. The existing
accepted-derivative publication transaction synchronously publishes the corresponding projection
slot after its evidence locks and before downstream enrollment or transaction completion. A slot
requires both key and source attempt, exact repeats are idempotent, and conflicting publication
fails the entire transaction. No signal, queue, cache, compatibility reader, or generic projection
framework is introduced.

One canonical derivation relation powers both the explicit all-events rebuild and symmetric-
difference verification. Rebuild is dry-run by default, mutates only with an explicit apply option,
and never rewrites processing evidence or Object Storage. Customer-facing collection and exact
photo interfaces read the projection and current product authority directly; required missing
slots fail closed. The independent face-crop subsystem remains outside this projection boundary.

The first reader cutover is one release. Deployment pulls the candidate while the previous web and
edge keep serving, stops only the existing processing worker topology, runs the additive migration,
then runs a bounded all-events publication drain. In one transaction, its PostgreSQL `UPDATE` locks
every in-progress clean/watermarked attempt regardless of lease deadline and sets each remaining
lease expiry to that attempt's immutable creation time. The update waits behind any publication
transaction already holding an attempt lock; after such a transaction commits, its now-terminal row
no longer matches. For every row still in progress, creation time is a lower bound for any heartbeat
timestamp captured after the attempt became observable, so neither an already-waiting heartbeat nor
a storage-blocked completion can restore publication ownership. Transaction-local lock and statement
timeouts bound the drain; either timeout rolls back every fence and enters deployment recovery.
Deployment then applies the all-events rebuild and requires a clean verification before candidate
Compose reconciliation. A bounded candidate smoke renders page one of the largest published
site-visible event with face lookup isolated, then resolves one exact eligible photo. It reports
aggregate timing, query counts, and executed-plan node names from `EXPLAIN ANALYZE` only. Any failure
uses the existing prior-package, environment, marker, and worker-topology recovery path; a first
deployment uses its still-private requested environment to clean candidate Compose before restoring
the no-environment state.

`Photo` fields are rejected because they would add processing provenance and media-slot concerns to
the product row and make the two optional slots harder to constrain independently. A materialized
view is rejected because refresh timing would open a publication-to-read gap and add a second
operational refresh lifecycle.

## Consequences

### Positive

- Customer media eligibility and selection no longer scan growing processing history.
- Authoritative evidence and exact source provenance remain available for audit and rebuild.
- Accepted derivative and projected readiness commit or roll back together.
- Rebuild, clean verification, bounded smoke, and prior-topology recovery make cutover explicit.

### Negative

- Publication maintains an additional row and lock after authoritative evidence locks.
- Every supported gallery-media variant requires an explicit projection slot and derivation rule.
- The first reader release pauses processing publication while migration and reconciliation run.
- In-progress clean/watermarked leases are fenced at their immutable creation time by the cutover
  drain and follow normal expired-attempt recovery after rollback or candidate startup rather than
  publishing across the verified snapshot.
- Rollback restores the previous reader and worker topology but retains the additive projection
  schema and rows.

### Follow-up

- Record pull-request CI, merge, canonical deployment, and live query evidence separately.
- Reconsider the shape only when a new customer media role cannot fit an explicit constrained slot.

## Validation and rollback

Validate model constraints, atomic publication rollback, lock order, set-oriented rebuild,
symmetric-difference cleanliness, collection and exact SQL shape, downstream authorization parity,
deployment ordering, failure recovery, and privacy-safe smoke output. Live validation must confirm
the deployed SHA, clean projection, worker health, public gallery and exact media responses, fresh
errors, and processing-relation counters under natural traffic.

Rollback deploys the prior immutable package and environment and reconciles its previous worker
topology through the existing single recovery path. It does not delete projection rows, processing
evidence, objects, database volumes, or certificate volumes.

## References

- [Gallery media projection design](../superpowers/specs/2026-09-15-gallery-media-projection-design.md)
- [Gallery media projection implementation plan](../plans/2026-09-15-gallery-media-projection.md)
- [Architecture](../architecture.md)
- [ADR 0002](0002-postgresql-system-of-record.md)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0017](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [ADR 0029](0029-use-watermarked-previews-for-paid-photos.md)
- [ADR 0036](0036-issue-direct-gallery-small-preview-capabilities.md)
