# 0048: Reuse Managed Prometheus for worker alerts

- Status: Accepted
- Date: 2026-09-30
- Deciders: project maintainer; confirmed that alerts use the existing Prometheus stack and requested completion of this integration in conversation
- Supersedes: [ADR 0043](0043-observe-isolated-workers-with-git-managed-alerts.md) only for its unresolved workspace/channel and routing read-back prerequisites; [ADR 0018](0018-use-managed-yandex-monitoring.md) only for the evaluator of worker demand/capacity alerts, not native autoscaling under ADR 0042
- Superseded by: none

## Context

The observability work has delivered a dedicated Managed Prometheus workspace, existing
operator notification routing, Git-owned rules and explicit exact-revision API reconciliation. Worker
activation documents still require a separate native cap-one alert and treat the earlier API
feasibility gap as unresolved. Repeating that investigation would not deliver worker coverage.

## Decision drivers

- Reuse the operational monitoring platform and approved notification destination.
- Keep queue-driven native autoscaling and processing authority unchanged.
- Distinguish fresh source evidence, missing observations and real saturation at ceiling one.
- Keep every alert change reviewable and reversible through Git, without manual UI setup.

## Considered options

1. Introduce a separate native alert lifecycle for worker queue and capacity alerts.
2. Export read-only worker observations into the existing Prometheus workspace and extend its
   owned rule package and existing receiver.

## Decision

Select option 2 for worker demand/capacity and diagnostic alerts. The private canonical scrape
exports queue observations and trusted current actual capacity; node diagnostics retain ADR
0043's bounded host/runtime transport. This observation path neither publishes authoritative
native autoscaling metrics nor advances successful native-publication evidence. No worker gains
cloud credentials or additional processing authority.

Reuse the existing dedicated workspace and operator channels. Follow the delivered monitoring-as-code
contract: owned rule read-back and fresh evaluator verification; routing drift explicitly
unverified where server-side GET is unconfirmed; routing rollback by reapplying a known Git
revision. Do not invent an API, claim a server-side routing backup, or replace shared routing.

The initial worker profile is cap one for both pools and is disabled until separately approved
fleet activation. Numeric source timestamps distinguish retained old samples from fresh evidence.
Missing queue, cloud, publisher or expected-node observations produce diagnostic alerts, not
fabricated zero capacity. Saturation requires fresh actual capacity at the ceiling and sustained
claimable-age growth; it never changes limits or authorizes remediation.

Native demand/capacity publishing, its WORKLOAD evaluator, bulk idle zero, selfie claim cap, folder
isolation, private authentication, durable jobs and pgvector remain unchanged. Existing public,
VM and commerce alerts are not migrated or retired by this change. This decision authorizes
repository integration, not paid creation, live rule activation or worker cutover.

## Consequences

### Positive

- One Git-owned alert delivery mechanism and existing operator receiver cover worker evidence.
- The approved cap-one policy no longer depends on an obsolete ceiling-two native predicate.
- Missing telemetry is distinguishable from a healthy idle pool.

### Negative

- Prometheus ingestion must be verified independently of native autoscaling publication.
- Server-side routing drift remains unverified; known-revision rollback is the accepted limit.
- Fixtures and CI cannot prove live cloud evaluation or email delivery.

### Follow-up

- Implement the [integration plan](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/plans/2026-09-30-worker-prometheus-alerts.md).
- Before customer cutover, prove fresh source samples, sustained firing, missing-series and
  retained-stale-source behavior, recovery and actual delivery for each pool.

## Validation and rollback

Validate both disabled and enabled rule packages, source freshness and private-only read-only
exposition. Live activation uses the existing protected exact-SHA monitoring workflow. Roll back
to the known Git rule/routing revision; retain local worker placement until fleet acceptance.
No data reset, database restore, queue mutation or capacity change follows from an alert.

## References

- [Monitoring-as-code design](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-28-monitoring-as-code-design.md)
- [Existing activation evidence](../operations/2026-09-28-monitoring-activation.md)
- [Folder activation specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md)
