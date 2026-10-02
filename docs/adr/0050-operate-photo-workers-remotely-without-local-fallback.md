# 0050: Operate photo workers remotely without local fallback

- Status: Proposed
- Date: 2026-10-02
- Deciders: project maintainer; remote-only direction stated in conversation on 2026-10-02
- Supersedes: ADR 0049's timing for local-worker retirement; ADR 0042 and ADR 0046
  only for local rollback after the remote fleet has served real work
- Superseded by: none

## Context

Bulk and selfie workers already serve from separate Instance Groups. The first release
receipt remains `verified` because its `complete` Deploy runs a legacy observability
check requiring an on-host photo worker. The check cannot validate the remote topology.
ADR 0049 would retain local-worker recovery until after historical AdaFace backfill,
but the maintainer has selected remote-only operation now. Keeping both placements
would perpetuate obsolete credentials, Compose services and recovery branches.

## Decision drivers

- Have one production worker placement and one compatible release path.
- Preserve durable jobs, leases, results, vectors and customer-serving web/database state.
- Finish the already serving remote release without a new cutover or misleading local check.
- Keep the accepted worker-group identities, capacity caps, folder isolation and costs.

## Considered options

1. Abort and repeat initial activation through local workers on a new release.
2. Keep local recovery and patch only the failing observability assertion.
3. Finalize the existing remote receipt once and remove local production execution.

## Decision

Select option 3. A one-time, reviewed, serialized finalization of the exact pending
receipt proves the serving remote fleet and commits it without redeploying the pinned
application or starting local workers. It removes the old local recovery inputs only
after the committed marker is durable. This exceptional transition does not become a
standing second release route.

Thereafter bulk and selfie execution exists only in the current remote Instance
Groups. The canonical VM keeps Django, PostgreSQL, Nginx, the private worker API,
queue/coordinator, monitoring, import and commerce, but not photo/selfie Compose
services or local claim credentials. Recovery of photo processing uses a compatible
pinned remote release, remote instance replacement and durable lease retry within
the accepted caps. If remote service is unavailable, processing waits; the system
does not fall back to on-host ML compute.

This decision changes neither historical recognition generation nor vector storage,
does not start AdaFace backfill, and does not authorize new paid resources, IAM or
network changes. The first vector-only event still closes its old-reader rollback
boundary independently.

## Consequences

### Positive

- Removes a false local-container deployment gate and a second production topology.
- Keeps worker CPU and memory off the stateful host permanently.
- Makes remote health and durable job outcomes the evidence for worker acceptance.

### Negative

- A remote fleet or private-network outage can delay uploads and selfie inference;
  there is no immediate on-host emergency worker.
- The one-time pending receipt needs a narrowly reviewed finalization action before
  the normal remote-only release can deploy.

### Follow-up

- Review the transition and deletion scope, then test and execute it with fresh
  evidence and separate operational approval.
- Remove obsolete local services, credentials, fallback code and assertions in the
  reviewed remote-only release before historical backfill.

## Validation and rollback

Require exact receipt/image/group identity, web and private health, provider/member
and disk settlement, current claims/leases, native telemetry and real processing
evidence before finalization. Verify committed read-back and absence of only the
owned local recovery inputs. A failure before commit retains the pending receipt;
after commit, recover remotely or repair forward without re-enabling local workers.
Reconsider remote-only placement only through a new explicit architecture decision.

## References

- [Remote-only photo-worker operations specification](../superpowers/specs/2026-10-02-remote-only-photo-worker-operations-design.md)
- [ADR 0042](0042-isolate-autoscaled-photo-worker-pools.md)
- [ADR 0046](0046-isolate-worker-pool-management-in-a-separate-folder.md)
- [ADR 0049](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
- [Current architecture](../architecture.md#current-architecture--implemented)
