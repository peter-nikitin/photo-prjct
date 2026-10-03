# 0050: Decouple processing jobs and attempts from worker builds

- Status: Accepted
- Date: 2026-10-03
- Deciders: project maintainer; explicitly accepted in conversation on 2026-10-03
- Supersedes: none
- Superseded by: none

## Context

ADR 0017 makes Django and PostgreSQL authoritative for processing jobs, leases, retries and
accepted results. ADR 0042 moves worker execution to independently scaled pools. The current
implementation also records the worker release build on photo-processing attempts and checks it
in result callbacks; pool admission compares a worker build with cloud-observed VM and pool release
state. This couples durable work to deployment identity and complicates replacing a container on
an otherwise healthy VM. A worker release is not a property of the photo or the computation's
semantic generation.

## Decision drivers

- Keep the queue's contract limited to work, leases and results.
- Permit worker replacement and scale-from-zero without binding queued work to a VM or image.
- Preserve protection against unauthorized, duplicate, expired and stale results.
- Retain algorithm and model-generation provenance where it determines accepted result meaning.

## Considered options

1. Retain worker-build identity in attempts and use pool release state to admit each claim.
2. Keep worker release identity in deployment and diagnostics, but remove it from job, attempt,
   claim and result semantics.
3. Remove both worker-build and processor-generation identities from durable work.

## Decision

Select option 2. A processing job describes the input, processor type and semantic processor
contract needed to interpret its result. A processing attempt records the claim, lease, outcome
and accepted evidence. Neither stores or checks a worker image tag, image digest, release build,
container or VM identity. The same job may be retried by a different worker release without
rewriting the job or its earlier attempts.

The queue does not discover, register or validate worker releases or cloud instances. A worker
polls for work it can perform and submits the result for its current attempt. Worker readiness,
stop-claim/drain, image selection and replacement belong to deployment and runtime operations,
outside the queue's interface. Existing private transport and scoped caller authentication remain
mandatory, as do atomic claims, bounded leases, retry policy, idempotency, typed result validation
and rejection of stale or duplicate completion. An outstanding-job concurrency limit may remain a
workload rule; it must not depend on a worker build or instance identity.

`contract_version`, `processor_version`, model/configuration identity and immutable result
provenance are semantic processing data, not worker-release identifiers. Preserve them wherever
they determine whether a result is compatible with an event, photo or search generation. During
overlapping container releases, both old and new workers must implement the active processing
contracts. A breaking processing-contract change requires an explicit work transition, not a
worker-build equality check or an obsolete compatibility path.

Fleet inventory and diagnostic telemetry may retain worker build and VM identity for operations.
They do not become product-data attributes or eligibility conditions for queue claims and
completion. This decision does not choose a registry tag, image publication method, deployment
controller or new VM topology; those release details need their own design.

## Consequences

### Positive

- Queued jobs survive worker replacement, restart and preemption without release-specific routing.
- Processing and deployment can evolve behind a narrow claim/lease/result interface.
- Model-generation provenance remains available for safe publication and search.

### Negative

- A durable attempt no longer identifies its exact executing image; operational telemetry and
  release logs carry that diagnostic information instead.
- Deployment must ensure overlapping worker releases implement the active processing contracts;
  the queue will not use image identity as a compatibility gate.

### Follow-up

- Remove worker-build fields and equality checks from processing attempt and callback contracts.
- Remove worker-build and cloud-instance admission from queue claim decisions while preserving
  private authentication, lease fencing and workload limits.
- Keep the container-release and scale-from-zero design separate from this processing decision.

## Validation and rollback

Verify that two compatible worker releases can claim the same job type without release-specific
queue state; an interrupted attempt can expire and be retried by the other release; stale,
duplicate and unauthorized results remain rejected; and semantic processor/model generations
continue to govern accepted evidence. A rollback changes the running worker release, not queued
jobs, attempts, accepted artifacts or model-generation records. Revisit the decision only if a
concrete processing contract cannot safely span a required rolling replacement.

## References

- [ADR 0017: Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0042: Isolated worker pools](0042-isolate-autoscaled-photo-worker-pools.md)
- [Architecture: accepted constraints](../architecture.md#accepted-constraints)
