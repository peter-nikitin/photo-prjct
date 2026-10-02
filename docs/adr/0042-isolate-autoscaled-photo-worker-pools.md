# 0042: Isolate autoscaled photo worker pools

- Status: Accepted
- Date: 2026-09-27
- Deciders: project maintainer; explicitly accepted in conversation on 2026-09-27
- Supersedes: ADR 0003 and ADR 0028 for photo-worker placement on the designated VM only;
  ADR 0017 for a separate pre-activation capacity-measurement requirement for this relocation;
  ADR 0018 for observation-only monitoring metrics used by these worker autoscalers only
- Superseded by: [ADR 0049](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
  only for post-acceptance recovery through on-host photo workers

## Context

Bulk photo processing and selfie inference compete with Django and PostgreSQL on the canonical
VM. The maintainer selected independent queue-driven worker pools and explicitly excluded a
separate measurement or benchmark project. Later SFace-to-AdaFace reprocessing will write vectors
only to the new pgvector table introduced by ADR 0040; that reprocessing is not this relocation.

## Decision drivers

- Isolate worker CPU and memory from the public web/database host.
- Remove idle bulk capacity while retaining a warm selfie worker.
- Preserve existing authoritative jobs, leases, accepted evidence and media authorization.
- Keep one deployment, one deployed SHA and Docker Compose; do not introduce a broker.
- Deliver separation without a bespoke performance-measurement prerequisite.

## Considered options

1. Keep workers on the canonical VM and adjust container limits.
2. Use permanently allocated separate worker VMs.
3. Use independent Yandex Compute Instance Groups driven by authoritative queue metrics.

## Decision

Select option 3. Acceptance does not authorize paid provisioning or production cutover.

Move enabled bulk photo processing and selfie inference into separate worker groups. Keep
PostgreSQL, Django, the public edge, import and commerce on the canonical VM. A worker has one
active job and no database, Django secret or permanent media credentials. Retain existing job,
attempt, retry, artifact and result contracts. Do not activate new processor identities.

The bulk pool has bounds 0..2 and may use preemptible VMs. The selfie pool retains one warm regular
VM; its second instance is enabled only after the functional concurrency/recovery acceptance
checks show no web/API/DB failures. This is not a throughput or latency guarantee. Fixed VM
shapes and spend limits require approval before provisioning, not a bespoke sizing project.
Existing protocol limits and representative end-to-end correctness tests remain mandatory.

Use a dedicated private HTTPS worker endpoint on the canonical host. Only worker security-group
identities may connect. Clients validate the server certificate and still authenticate with scoped
bearer credentials. No public worker route, plaintext cross-VM fallback or direct PostgreSQL
access is permitted. Certificate issuance, renewal and trust delivery must be resolved before
the implementation plan is approved.

A publisher independent of worker count reports claimable jobs plus active attempts to Yandex
Monitoring. Instance Groups scales within hard limits; stale telemetry is an alert, not an empty
queue. Planned retirement is idle-first and preserves the warm selfie minimum. Forced VM loss
uses existing lease recovery. This narrowly permits monitoring-driven worker scaling, not automatic
changes to web, database, deployment, migrations or rollback.

The canonical Deploy workflow releases compatible web and worker images as one SHA. Workers
start claiming only after compatibility/readiness checks; a failed release cannot mark the fleet
successful. Relocation rollback drains remote workers and restores compatible on-host workers
without resetting rows, deleting artifacts or changing pgvector configuration.

Do not downsize the canonical VM, backfill events or remove legacy implementations in this change.
The later new-model backfill depends on completed pgvector deployment/public read activation and
working isolated workers. New vectors go only to `FaceEmbeddingVector`, not legacy `FaceEmbedding`.
Detections, attempts and projections required to make vectors accepted evidence are not forbidden
by this vector-store boundary. The backfill must explicitly close the old-reader rollback boundary
for affected events before activating new-only evidence. Legacy removal follows migration of all
dependent readers; saved results and provenance remain intact.

## Consequences

### Positive

- ML compute no longer consumes the canonical host's worker CPU/RAM budget.
- Each workload has an independent capacity floor and hard spend ceiling.
- No data migration or broker is necessary for worker placement.

### Negative

- Additional VM, network, certificate and multi-host deployment operations are required.
- Initial fixed shapes are not proven capacity; OOM/startup failures block cutover and require a
  revised approved shape, not silent increases in spend.
- Private API and PostgreSQL remain on a sole host; separation is not full availability HA.
- Cold starts and preemption may increase bulk queue delay.

### Follow-up

- Approve private certificate/trust ownership, fixed VM shapes and budget, then write the exact
  implementation plan with release safeguards and bounded operational commands.
- Prepare a separate blocked new-only AdaFace backfill task; keep pgvector transition ownership
  in its existing task and ADR 0040.

## Validation and rollback

Require a real bulk task and selfie query over private trusted HTTPS, absence of public worker
access and permanent worker data credentials, bulk wakeup from zero, idle scale-down, preservation
of a warm selfie VM, interruption/lease recovery, stale-metric behavior and whole-release rollback.
Do not treat local tests as live proof. Preserve durable attempts, accepted artifacts and saved
results on rollback. No benchmark or measurement tooling is a prerequisite of this change.

## References

- [Worker pool design](../superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md)
- [Current architecture](../architecture.md#current-architecture--implemented)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0017](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0018](0018-use-managed-yandex-monitoring.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- Deployed pgvector baseline's [ADR 0040](0040-use-pgvector-for-exact-face-search.md) and
  [ADR 0041](0041-accept-pgvector-numerical-boundaries.md); preserve their design/recovery boundary
  when reviewing the blocked backfill
