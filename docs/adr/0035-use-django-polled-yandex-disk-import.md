# 0035: Use Django-polled Yandex Disk import

- Status: Accepted
- Date: 2026-09-07
- Deciders: project maintainer
- Supersedes: [ADR 0013](0013-use-direct-private-object-storage-ingestion.md)
  only for the source-byte transport of public Yandex Disk imports, and
  [ADR 0014](0014-keep-stage-2-ingestion-request-driven.md) only for background control and
  transfer of those imports. Both remain authoritative for local browser uploads.
- Superseded by: none

## Context

The approved public Yandex Disk import specification lets a photographer submit a public folder
link for an event folder and close the browser once the server has accepted the task. The server
must enumerate direct files, copy valid originals to private Object Storage, and enroll each
confirmed photo into the existing processing pipeline.

ADR 0013 keeps source-byte transfer off the application VM through direct browser uploads.
ADR 0014 makes upload control request-driven and introduces no ingestion worker. The new source
requires execution independent of a browser and before a Photo exists. ADR 0017 provides a proven
Django/PostgreSQL job-and-lease pattern, but its accepted scope is post-ingestion photo processing.
The maintainer explicitly accepted ADR 0035 after reviewing the architectural decision.
This acceptance does not authorize deployment.

## Decision drivers

- Complete ingestion after the submitting browser closes.
- Keep PostgreSQL authoritative for ownership, progress, retries, identity, and publication.
- Preserve one shared original-publication and processing-enrollment contract.
- Keep long transfers out of ordinary web request bodies and avoid a new broker.
- Bound network, temporary disk, and worker privileges on the canonical deployment.
- Recover from interrupted transfers and uncertain publication without duplicate photos.

## Considered options

1. Add a dedicated ingestion worker polling a private Django API backed by PostgreSQL import state.
2. Extend the photo-processing worker and its jobs to represent imports before Photo creation.
3. Use a broker-backed ingestion queue, such as Celery with Redis or RabbitMQ.

Option 1 preserves the ingestion/processing boundary and uses the current deployment model.
Option 2 couples two different lifecycles and requires changing the existing Photo-based job
contract. Option 3 can provide durable scheduling but adds an operational dependency without a
current scheduling requirement that justifies it. A long user-facing HTTP request cannot meet
the accepted background-completion requirement and is not a viable alternative.

## Decision

Select option 1.

Django persists an owned import task before acknowledging submission. Listing the source and
transferring files both run in a separately runnable ingestion worker. PostgreSQL stores import
manifests, item results, checked content fingerprints, retry state, and bounded leases. Django
owns claims, authoritative validation, duplicate decisions, and publication. The first deployment
uses one import worker with one file transfer at a time and no external broker.

The worker receives a dedicated credential for a private Django API. It has no PostgreSQL access,
Django secret, or permanent Object Storage credentials. It calls the public Yandex Disk API for
the assigned source, handles only direct JPEG candidates, and requests a fresh per-file download
URL for each attempt. Source URL validation, redirect validation, public-address enforcement,
timeouts, response bounds, and temporary-file limits apply to outbound transfers. Download URLs
and public source keys are not exposed through progress responses or routine logs.

After validating downloaded bytes and computing their identity, the worker receives a short-lived
write grant for one server-generated private incoming key. Django binds verification and promotion
to object identity and publishes an immutable original. Both browser and imported uploads use the
same authoritative Photo publication and processing-enrollment rules, including folder ownership,
paid-media policy, and current processor eligibility. An import success does not mean processing
has finished.

Content duplicate handling uses the approved specification's owner/source/event/folder scope and
verified SHA-256 plus byte size. A retry converges on the same item outcome; concurrent equivalent
imports cannot publish duplicate photos in that scope. This does not introduce global media
deduplication or compare another photographer's records. Changed content creates a new photo
without replacing an existing immutable original.

When the worker runs on the canonical VM, source bytes consume that VM's network and bounded
temporary disk. This is an explicit exception to ADR 0013's off-VM source transfer, not a native
Object Storage copy from a remote URL. Source files on Yandex Disk are never modified or deleted.

The code-owned `yandex-disk-import` release gate starts in `off` under ADRs 0028 and 0032.
Task creation, retries, work claims, and publication enforce the owner's current eligibility.
Closing the gate pauses new work and publication without discarding durable progress. Already
published originals and ordinary processing retain their existing lifecycle. Workers do not
reconcile feature definitions. Infrastructure capacity and activation remain separate operational
decisions; this ADR does not authorize either.

## Consequences

### Positive

- Browser closure no longer interrupts this ingestion mode.
- Import recovery uses durable state without introducing a broker or changing processing jobs.
- Worker permissions stay narrow; Django retains media identity and publication authority.
- Existing local uploads and processing policies remain reusable and independently testable.

### Negative

- The deployment gains another worker, API credential, and recoverable job lifecycle.
- Import competes for VM network and temporary disk when placed on the current VM.
- Source availability and API throttling can delay or partially fail imports.
- Content fingerprints and import history require durable storage for repeat-import behavior.
- Sharing publication requires focused refactoring and regression evidence for the existing path.

### Follow-up

- Record precise tasks, schema changes, protocol limits, deployment ordering,
  previous-version rehearsal, and bounded recovery operations in the implementation plan.
- Before activation, verify actual download endpoints and the full real-JPEG transfer chain.
- Measure representative maximum-file and maximum-manifest behavior before choosing deployed
  resource limits; do not infer capacity from small fixtures.
- Keep ADRs 0013 and 0014's browser-upload decisions unchanged. Update implemented architecture
  only after the behavior is delivered.

## Validation and rollback

Validate browser-independent completion, direct-child listing, content deduplication, current
authorization and gate checks, stale-lease rejection, retry bounds, uncertain-publication recovery,
and normal processing enrollment. Include existing browser-upload regression evidence and a real
public JPEG through Disk API, incoming storage, final original, Photo, and standard processing.
Verify credentials and ensure malicious source or redirect addresses cannot reach private services.

To stop new import side effects, set the gate to off and stop the import worker. Preserve import
rows, fingerprints, confirmed photos, and object identities. Do not reset processing state, purge
originals, or reverse schema changes as a rollback shortcut. The implementation plan must establish
the safe prior-version application/worker ordering and treatment of interrupted attempts before
execution. Reconsider worker placement or scheduling only when measured contention warrants it.

## References

- [Implementation plan](../plans/2026-09-07-yandex-disk-photo-import.md)
- [Approved public Yandex Disk import specification](../superpowers/specs/2026-09-07-yandex-disk-photo-import-design.md)
- [Architecture: photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- [ADR 0006: Object Storage](0006-yandex-object-storage-media.md)
- [ADR 0012: Photographer permissions](0012-use-django-photographer-permissions.md)
- [ADR 0013: Private ingestion](0013-use-direct-private-object-storage-ingestion.md)
- [ADR 0014: Request-driven ingestion](0014-keep-stage-2-ingestion-request-driven.md)
- [ADR 0017: Django-polled processing](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0028: Canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0032: Feature registry](0032-reconcile-code-owned-feature-flags-at-startup.md)
