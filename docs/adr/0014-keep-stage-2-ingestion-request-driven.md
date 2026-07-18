# 0014: Keep Stage 2 ingestion request-driven

- Status: Accepted
- Date: 2026-07-13
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

Stage 2 needs durable upload state and direct media transfer, but it does not decode images, create
derivatives, or run recognition. Introducing a task broker and ingestion worker for its short
control and confirmation operations would add infrastructure before background processing is in
scope.

## Decision drivers

- Deliver the smallest reliable ingestion control plane on the current deployment.
- Keep large file bytes out of Django request handlers.
- Bound browser resource use for large selections.
- Defer worker technology until image-processing requirements can shape it.

## Considered options

1. Request-driven Django control and confirmation with browser-managed transfer concurrency.
2. Queue every upload confirmation for an ingestion worker.
3. Proxy upload and confirmation through a synchronous Django request.

## Decision

Keep Stage 2 ingestion control and confirmation request-driven in Django. The browser owns an
in-memory queue and transfers files directly to private Object Storage with bounded concurrency;
the default is four active transfers. Django requests create and update durable state, issue upload
authorization, verify and promote completed objects, and confirm results.

Stage 2 introduces no ingestion worker, task broker, or background task framework. Image decoding,
metadata extraction, derivative generation, and other background image processing remain deferred
to later roadmap stages and require their own execution decision.

## Consequences

### Positive

- No new always-on infrastructure is required for the upload stage.
- Durable state remains in Django and PostgreSQL while the browser bounds parallel transfer work.
- Later processing technology can be selected from measured image workloads.

### Negative

- Closing or reloading the page stops unfinished browser transfers.
- Confirmation must remain short and bounded enough for request handling.
- This decision does not provide background image processing or automatic queue recovery.

### Follow-up

- Measure confirmation latency and browser behavior for representative and maximum-sized batches.
- Decide the worker, broker, retry, and processing contract before Stage 3 background image work.

## Validation and rollback

Validate that the browser never exceeds its configured concurrency, Django control requests stay
bounded, and partial failures remain recoverable without a worker. Reconsider if measured
confirmation work cannot reliably fit request limits or if upload completion must continue after
the browser session ends.

## References

- [Stage 2 photographer upload design](../superpowers/specs/2026-07-13-stage-2-photographer-upload-design.md)
- [Architecture: photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
