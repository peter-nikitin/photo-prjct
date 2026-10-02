# Historical AdaFace backfill and local photo-worker retirement

- **Status:** Proposed for written review; not approved for implementation or live enrollment
- **Date:** 2026-10-02
- **Owner:** FindMe Photo
- **Related architecture:** [Current worker placement](../../architecture.md#current-architecture--implemented),
  [Photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing),
  [Search](../../architecture.md#search), [Open decisions](../../architecture.md#open-decisions)
- **Related ADRs:** [0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [0019](../../adr/0019-use-public-event-selfie-search.md),
  [0024](../../adr/0024-use-gallery-face-as-search-query.md),
  [0025](../../adr/0025-expand-selfie-search-with-face-clusters.md),
  [0028](../../adr/0028-operate-one-canonical-deployment.md),
  [0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md),
  [0040](../../adr/0040-use-pgvector-for-exact-face-search.md),
  [0041](../../adr/0041-accept-pgvector-numerical-boundaries.md),
  [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md),
  [0046](../../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md)
- **Related work:** [Production AdaFace for new events](2026-08-16-production-adaface-new-events-design.md),
  [Exact pgvector search](2026-09-25-pgvector-exact-face-search-design.md),
  [blocked historical backfill](../../future-work/2026-09-27-new-only-adaface-event-backfill.md),
  [unified worker activation](../../plans/2026-10-01-unified-worker-activation.md)
- **ADR impact:** Conforms to ADRs 0017, 0019, 0024, 0025, 0028, 0032, 0040, 0041 and 0046
  for durable jobs, immutable results, exact PostgreSQL search and initial worker activation.
  **Supersedes ADR 0042 only for future post-acceptance rollback to on-host photo workers**:
  this first phase retires that fallback after remote processing is proven. A new accepted ADR
  must define the replacement recovery boundary before implementation planning. Initial
  activation's already-approved rollback remains in force until its `complete` action succeeds.

## Outcome and boundary

The first phase reprocesses every event still pinned to SFace v3 with the already approved
SCRFD-10G_KPS plus AdaFace IR18 WebFace4M generation, stores newly computed embeddings **only**
in `FaceEmbeddingVector`, validates the isolated bulk and selfie pools under real work, and then
retires photo/selfie execution on the canonical VM. Published and unavailable events, including
hidden photos with valid private sources, are in scope. The exact event and photo inventory is
captured immediately before enrollment; a count from an earlier date is not authority to omit
an event.

This is the first part of a two-part retirement. It does **not** delete SFace model support,
historical SFace evidence, `FaceEmbedding` JSON rows, the Python reader, parallel writes for
other work, or the temporary pgvector reader gate. Their coordinated removal is the subsequent
part, after every dependent online and offline reader has migrated. The first part must not
pretend that backfilling alone makes that cleanup safe.

Existing ready bearer-linked selfie and gallery-origin result snapshots retain their saved
photo membership, rank, media authorization and historical provenance. Opening those links
does not rerun recognition or ranking. Current event publication, photo visibility and media
eligibility continue to govern presentation; this migration does not broaden access.

## Current constraints

The current production AdaFace path still writes both `FaceEmbedding` JSON and
`FaceEmbeddingVector` through `persist_parallel_embedding`. Some eligibility, gallery-source,
offline corpus and reconciliation paths still consult JSON evidence. The existing
`backfill_pgvector_face_embeddings` command copies existing embeddings into pgvector; it does
not infer AdaFace from old photos. The `reprocess_event_face_embeddings --local-adaface` path
belongs to the isolated local experiment and is not a production historical-enrollment command.
The canonical deployment still declares local `worker-bulk` and `worker-selfie` services as
recovery-capable profiles even after remote claims open. None of these existing paths may be
mistaken for completion of this specification.

The worker rollout and model migration have different recovery boundaries. Before any
new-only AdaFace evidence is published, the remote fleet must have passed its supported
`complete` action, with a fresh read of the deployed revision, pool membership, claims, leases,
health and retained recovery state. A human acceptance waiver of a missing wakeup observation
is not telemetry proving it happened. No direct edit of production Compose or the database
may substitute for the release protocol.

## Selected design

### Event-scoped, bounded generation replacement

- Use the existing Django/PostgreSQL job, lease, retry and immutable-attempt protocol to enroll
  bounded historical face work into the isolated **bulk** pool. Do not add a broker, third pool,
  extra VM, or direct worker database/Object Storage credentials. The fixed bulk ceiling remains
  one VM unless a separately priced and approved capacity decision changes it.
- Freeze each event's exact candidate generation: existing approved AdaFace model/artifact,
  processor contract `3/face_embedding/5`, 512 dimensions, quality configuration and provisional
  threshold `0.42`. Do not silently tune threshold, substitute a model, or mix SFace and AdaFace
  in one query cohort.
- An explicit event cohort and bounded batch limit make enrollment dry-run-visible,
  idempotent and resumable. Each confirmed photo with a valid source receives an accounted-for
  terminal candidate outcome: accepted vector, no face/quality rejection, or actionable failure.
  Hidden and unavailable-event photos are accounted for without making them public. Missing
  source, unresolvable historical state or a photo whose old searchable face cannot be
  satisfactorily replaced is a reported blocker, not an implicit success or deletion.
- Candidate publication retains the accepted attempt, face detection, quality and projection
  evidence needed by the native reader. Its embedding destination is solely
  `FaceEmbeddingVector`; it must never create a candidate `FaceEmbedding` JSON row. Preview,
  watermark, capture-time, bib, ingestion and original objects are not replayed or altered.
  The candidate cohort and gallery-face source become independently eligible from native
  vector evidence, without requiring a parallel JSON row.
- Foreground photo and selfie requests retain priority. Historical enrollment is bounded and
  pausable; a batch never monopolizes the only bulk worker while new customer work waits.
  Backfill progress separately reports not-yet-enrolled and paused work; autoscaling continues
  to read only actual claimable jobs and active attempts. An empty queue is not interpreted as
  completed backfill while that separate backlog remains.

### Reconciliation and activation

- Old SFace remains the event's active generation while candidate evidence is incomplete.
  Reconcile exact photo/detection/attempt/projection/vector identities and terminal outcomes
  per event; queue zero alone is not completion. Review model-quality differences rather than
  applying pgvector's `1e-6` same-model numerical tolerance to SFace-versus-AdaFace results.
- Before the first event switches to new-only evidence, ensure no `off`/`staff` reader setting
  or rollback path can silently select an incomplete JSON cohort for that event. Native
  pgvector ranking remains exact, event-scoped and fail-closed. Recovery after this boundary
  uses the accepted AdaFace vector evidence and compatible application/worker release, not a
  switch back to SFace or an old image that lacks the new contract.
- Activate one complete event at a time. Both uploaded-selfie and gallery-face queries must use
  its AdaFace generation and native vector source. Already queued searches and leases are
  drained or resolved against their frozen generation before activation; no queued search is
  silently reinterpreted. Old-generation cluster corpora are deactivated or rebuilt under their
  existing acceptance rules before they can contribute to new results.
- Validate real customer-equivalent searches for published events and private processing
  evidence for unavailable events. Preserve existing result-link behavior and fail closed on
  missing/divergent vectors, source mismatch or unauthorized media.

### Remote-worker proof and on-host retirement

The backfill is also a sustained real-work acceptance of the isolated bulk pool. Evidence must
connect enrolled jobs to terminal accepted attempts, the expected immutable worker build,
queue/lease movement and actual remote VM membership. Observe a bulk wake from zero, bounded
processing, idle return to zero and boot-disk deletion when the queue drains. Exercise the warm
selfie pool with a new AdaFace search. Use durable job/result evidence and fresh provider and
Prometheus observations; a rate graph that misses its first counter sample is not proof of no
work, and a `RUNNING` VM alone is not proof of successful processing. Failures pause enrollment
and preserve the active event generation rather than expanding the approved VM cap.

Only after all SFace events are accounted for and activated, remote pools are healthy, and
new work no longer requires local photo-worker recovery may the canonical deployment retire
its local `worker-bulk`/`worker-selfie` services, local claim credentials/configuration and
release fallback. Inventory actual containers, references, secrets, images and recovery files
first; remove only confirmed obsolete photo/selfie resources through a reviewed deployment.
Keep PostgreSQL, Django, private worker API, queue coordinator/metrics, Nginx, import worker,
commerce worker, media and their credentials. Do not run a broad Docker prune or delete a
shared image merely because a local photo-worker container stopped.

After local retirement, future worker recovery must be through a pinned compatible remote
release/instance replacement with bounded claims and lease recovery; it must not depend on
recreating workers on the canonical VM. The replacement recovery contract and its rehearsal
belong in the superseding ADR and implementation plan. This cleanup does not downsize or move
the canonical VM and does not change cloud access, worker group sizes or paid services by itself.

## Alternatives rejected

1. Copy SFace vectors with the existing pgvector population command. This changes storage but
   does not convert recognition to AdaFace or make SFace removable.
2. Switch an event to AdaFace before its candidate cohort is complete, relying on old JSON or
   a model fallback for gaps. The representations are incompatible and a partial result would
   appear valid to customers.
3. Reprocess all events in one unbounded burst or add another worker pool for the replay.
   Neither is needed for bounded, resumable work within the accepted topology and ceiling.
4. Delete local workers immediately after the first remote success. That would discard the
   accepted release-recovery boundary before historical work proves sustained operation.

## Acceptance criteria

1. The exact live SFace event/photo cohort is frozen for the run, every selected photo has a
   reconciled terminal AdaFace candidate disposition, and interrupted/repeated enrollment
   neither loses work nor creates duplicate current vectors or projections.
2. Accepted AdaFace candidates have the pinned 512-dimensional model identity, accepted
   attempt/detection/projection and `FaceEmbeddingVector` row, with **no newly written**
   `FaceEmbedding` JSON row for those candidates. No unrelated processor, original, derivative
   or immutable result snapshot changes.
3. No event activates with missing, divergent or unexplained candidate evidence. Published
   event searches from both query sources and old ready-result links pass their authorization
   and media checks; unavailable events remain unavailable.
4. Remote bulk processing, zero-to-one wake, idle-zero/disk deletion and warm selfie processing
   have fresh provider, telemetry and durable result evidence. Foreground jobs make progress
   during the bounded replay without changing the approved capacity limit.
5. A supported completed fleet release and an accepted replacement recovery method precede
   removal of local photo/selfie execution. Post-cleanup inventory shows no local photo/selfie
   containers or enabled claim path, while web/database/import/commerce/private API/monitoring
   remain healthy and future Deploy cannot revive the old services.
6. The follow-on old-model/JSON-reader removal remains explicitly pending; this phase does not
   claim the whole two-part cleanup is finished.

## Failure and privacy boundaries

Backfill enrollment and cloud use require a fresh live inventory, explicit cost/impact review
and separate operational approval; this specification is not permission to dispatch jobs or
remove VM resources. A failed batch may pause and resume from durable state. Before an event
switches, its existing active generation remains serving; after it switches, an old-reader or
local-worker rollback is forbidden unless a complete compatible generation is independently
proved. Preserve database backups and restore evidence without exposing embeddings, raw selfies,
object keys or bearer links in monitoring and run receipts. Query vectors remain ephemeral.
