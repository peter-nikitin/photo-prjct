# 0017: Use Django-polled photo-processing jobs

- Status: Accepted
- Date: 2026-07-29
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

Stage 3 needs repeatable background image processing after confirmed photos become eligible. The
first processor only extracts capture time, but the execution boundary must later support
specialized ML runtimes and binary derivatives without giving workers PostgreSQL access or
permanent Object Storage credentials.

The current deployment has Django, PostgreSQL, and Object Storage but no broker. The first goal is
to prove a working, recoverable pipeline before adding infrastructure or activating work on the
smallest current VM.

## Decision drivers

- Deliver the smallest durable background-processing system on the current stack.
- Keep product state, job ownership, and accepted results authoritative in Django and PostgreSQL.
- Keep permanent database and Object Storage credentials out of worker containers.
- Recover explicitly from duplicate requests, worker termination, expired downloads, and stale
  results.
- Preserve an interface that can later support ML processors, derivatives, and a dedicated broker.
- Measure resource use before selecting VM size or worker concurrency.

## Considered options

1. A worker polls a private Django API backed by PostgreSQL jobs and leases.
2. Celery workers consume jobs from a new Redis or RabbitMQ broker.
3. Django streams original image bytes through worker-facing application responses.
4. A separate media gateway mediates all worker access to Object Storage.

## Decision

Use Django and PostgreSQL as the first photo-processing control plane and durable job store. A
separately runnable worker polls a private, versioned HTTP API to claim compatible jobs. Django
atomically assigns a bounded lease, owns retries and current state, validates typed results, and
keeps terminal attempts plus event-scoped run reports as immutable evidence.

The worker has a dedicated narrowly scoped API credential but no Django database configuration,
Django secret, or permanent Object Storage credential. At claim time Django issues short-lived
read authorization for the exact original. Queue waiting time therefore does not consume the
authorization lifetime. The worker may refresh authorization only for its current valid lease.

The first deployment uses one worker with concurrency one and no external broker. Polling uses
bounded backoff and jitter. A later broker may replace polling without changing processor, job,
attempt, lease, result, or event-run semantics.

Django remains responsible for media identity and publication. A future binary-output processor
may receive short-lived write authorization only for a unique attempt-scoped staging key. Django
must verify and promote that object without overwriting an immutable final key before recording it
as current.

This decision does not approve face processing, biometric retention, vector storage, preview
formats, production activation, or the capacity of the current VM.

## Consequences

### Positive

- The first pipeline adds no broker or broker recovery surface.
- Workers remain independently deployable and hold no permanent database or storage credentials.
- PostgreSQL transactions provide explicit claim, lease, retry, idempotency, and current-result
  state.
- Temporary media grants keep large image transfer out of Django response bodies.
- Immutable attempts and event-scoped runs preserve evidence for later processor comparison.

### Negative

- Polling adds bounded database and HTTP traffic while workers are idle.
- Django must implement job claiming, lease recovery, retries, result validation, and token
  authentication that a task framework would otherwise provide.
- A compromised worker can read the exact object authorized during its active lease.
- Moving a worker outside the private deployment network requires a new transport-security and
  network decision.
- A later broker migration must preserve the accepted semantics rather than adopting framework
  defaults silently.

### Follow-up

- Implement the first `capture_metadata` processor and local container pipeline.
- Measure worker and whole-VM CPU, memory, disk, latency, and throughput with concurrency one before
  any real-environment activation.
- Reconsider polling after measured load or scheduling requirements justify a broker.
- Approve separate specifications for face governance, vector search, and preview generation
  details before adding those capabilities.

## Validation and rollback

Validate the decision through contract, state-machine, idempotency, lease-expiry, stale-result,
authorization-redaction, immutable-report, and real-JPEG end-to-end tests. Confirm the worker
container receives neither database settings nor permanent Object Storage credentials.

Keep the worker disabled in real environments until capacity evidence supports explicit VM,
concurrency, and container limits. The local implementation can be rolled back by disabling job
creation and the worker service while retaining job, attempt, and report rows as evidence. Revisit
the decision if polling creates material application load, lease recovery proves unreliable, or
worker placement requires public API exposure.

## References

- [Event photo processing worker design](../superpowers/specs/2026-07-29-event-photo-processing-worker-design.md)
- [Architecture: photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- [Architecture: evolution stages](../architecture.md#evolution-stages)
- [ADR 0014: Keep Stage 2 ingestion request-driven](0014-keep-stage-2-ingestion-request-driven.md)
