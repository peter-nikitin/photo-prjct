# 0049: Retire local photo-worker recovery after remote acceptance

- Status: Accepted
- Date: 2026-10-02
- Deciders: project maintainer; explicitly accepted in conversation on 2026-10-02
- Supersedes: ADR 0042 and ADR 0046 only for post-acceptance recovery through on-host photo workers
- Superseded by: [ADR 0051](0051-release-photo-worker-images-independently.md) for shared web/worker SHA and coupled image rollout only

## Context

ADR 0042 requires a local rollback while the isolated pools are being activated. Once
remote bulk and selfie workers have served real work, retaining on-host photo workers
as a second production placement keeps competing CPU and memory load on the database/web
VM, obsolete claim credentials and a rollback path that may not understand future
new-only vector evidence. The historical AdaFace backfill is a separate workload; it
does not need to decide where photo workers run.

The initial activation has a distinct acceptance boundary. Local recovery inputs remain
inert until that boundary is durably closed. Existing remote release tooling supports
pinned image rollout, verification and rollback within the worker groups, with bounded
replacement, durable receipts and lease recovery. A remote VM merely being `RUNNING`
does not establish processing correctness.

## Decision drivers

- Keep one compatible web/worker release and the approved remote bulk 0..1, selfie 1..1 ceilings.
- Preserve jobs, leases, accepted attempts, embeddings and immutable search results on failure.
- Do not route new-only AdaFace evidence through an older JSON-dependent reader or worker image.
- Remove photo-worker resource competition from the canonical database/web VM permanently.
- Avoid adding a third pool, broker, new VM or broader cloud authority for recovery.

## Considered options

1. Retain on-host photo/selfie workers and their claim path indefinitely as the fallback.
2. After remote acceptance, recover with a pinned compatible remote release or instance
   replacement, bounded claims and existing lease recovery.

## Decision

Select option 2 after the remote fleet has passed bounded real-work acceptance and its
initial release is durably committed. Retire local production photo/selfie execution
before historical AdaFace enrollment; do not use the backfill as a prerequisite for
choosing worker placement. The first new-only event activation must independently
close any legacy-reader or incompatible-image rollback for that event; fleet acceptance
alone does not do so.

After acceptance, remove the canonical deployment's local photo and selfie services, local
claim authorization and release fallback through a reviewed deployment. Remote bulk and selfie
groups remain the sole photo-processing and inference execution placement. Recovery uses the
existing canonical release path to verify or roll out a pinned compatible application/worker
build, or to replace a failed remote instance within the accepted group ceiling. Fence uncertain
members, preserve bounded claims and allow durable leases to expire/recover; do not reset job or
vector state. A prior image is a rollback candidate only if it supports every active event's
generation, vector evidence and processing protocol. Otherwise pause affected processing and
repair forward with a compatible release.

This decision does not remove the canonical VM, PostgreSQL, private worker API, queue publisher,
import/commerce workers or monitoring. It does not increase group ceilings, authorize paid
resources, change model thresholds or initiate the historical backfill. Removal of SFace, JSON
embeddings, Python ranking and the temporary reader gate remains a separate later phase.

## Consequences

### Positive

- The canonical host no longer needs spare CPU, memory, credentials or Compose paths for
  photo and selfie worker recovery.
- Recovery stays within one audited fleet release and durable processing protocol.
- No additional standing worker capacity is required beyond the accepted pools.

### Negative

- A remote-fleet or private-network outage may pause photo processing and selfie inference until
  a compatible remote release or instance is restored; there is no on-host emergency fallback.
- The application/worker compatibility boundary must be checked before any rollback, especially
  after an event activates AdaFace vectors without parallel JSON evidence.
- Removing retained local recovery inputs is irreversible through the ordinary `abort` action.

### Follow-up

- Verify remote-only recovery, including loss of the only warm selfie member, bulk
  zero-to-one wakeup, uncertain release state and lease recovery.
- Inventory and delete only confirmed obsolete local photo/selfie resources after
  fleet acceptance and before historical enrollment; retain all shared and stateful services.
- Complete the later legacy recognition/vector-reader retirement as separate work.

## Validation and rollback

Require fresh deployed-SHA, fleet, claim, lease, attempt and monitoring evidence; real
bulk processing, idle-zero/disk deletion and warm selfie search; and a compatible
remote release or instance-replacement recovery contract. A failed acceptance leaves
the release uncommitted and does not delete its recovery inputs. After acceptance,
recovery is remote-only and may pause processing while preserving durable state.
Reconsider this decision if remote-only recovery cannot meet accepted service
requirements within the fixed ceiling, rather than silently re-enabling local workers.

## References

- [Historical AdaFace backfill and local photo-worker retirement specification](../superpowers/specs/2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md)
- [Remote-only photo-worker operations specification](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md)
- [ADR 0042](0042-isolate-autoscaled-photo-worker-pools.md)
- [ADR 0046](0046-isolate-worker-pool-management-in-a-separate-folder.md)
- [Worker-pool runbook](../runbooks/worker-pools.md)
- [Current architecture](../architecture.md#current-architecture--implemented)
