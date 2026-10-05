# New-only AdaFace backfill for historical events

- Date: 2026-09-27
- Status: Blocked dependent task; not approved for execution
- Owner: project maintainer
- Depends on: [worker isolation](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/plans/2026-09-27-autoscaled-worker-isolation.md) and
  the deployed pgvector baseline under ADRs 0040/0041
- Governing recovery boundary: ADR 0040's reader/storage transition and ADR 0041's accepted numerical policy

## Observed gap

Historical SFace events retain old-generation evidence and legacy-dependent readers. Isolation
alone does not migrate them or make the old implementation removable. The new pgvector table and
reader are deployed at `866a894adf6b5ac1bba5bda2a4920cf88b661bbf`, with
`pgvector-face-search-read=on` in the 2026-09-28 integration inventory. Worker isolation remains
unactivated; this dated pgvector evidence does not authorize model backfill or legacy deletion.

## Why this is non-blocking now

Placement/autoscaling retains existing processor contracts and data. It can be prepared without
historical reprocessing. Keep existing event generations, readers and evidence until a separately
approved migration can use the completed vector-capable path.

## Concrete trigger to unblock

Both isolated pools have live functional acceptance; the pgvector task has deployed
`FaceEmbeddingVector`, reconciled existing data, reviewed side-by-side parity and enabled public
new reading. Record actual deployed SHA and gate state, not only an accepted ADR or merged PR.
Then approve a model-transition specification and implementation plan before enrollment.

## Required deliverable

Recompute explicitly selected historical SFace events with the approved AdaFace processor/model
and configuration. New vectors go only to `FaceEmbeddingVector`, never legacy `FaceEmbedding`.
Do not replay previews, watermarks, capture metadata or ingestion. Do not alter originals or
existing immutable bearer result snapshots. Persist the attempt/detection/projection evidence
needed for accepted vectors; “only the new table” describes the vector destination, not omission
of required processing evidence.

The plan must name the event cohort, model/artifact/configuration/threshold identities and use
dry-run-first bounded enrollment, pause/resume, deduplication, completion reconciliation and failure
recovery. Keep the old active generation until the candidate is complete and approved. Reuse
existing jobs/leases where their contract permits; do not add an unbounded SQL replay or secretly
turn on disabled processors. Define foreground-work priority before bulk historical enrollment.

Before new-only evidence becomes active, retire/block legacy-reader rollback for affected events
and establish a real recovery method. An Admin `off` switch must not expose an incomplete legacy
cohort. Rebuild or deactivate old-generation cluster corpora under their accepted rules. Keep
saved results/provenance and required detection/projection references intact.

## Completion and subsequent cleanup

All selected photos have accounted-for terminal outcomes; accepted vectors are model-compatible
and complete; event activation and real customer searches pass; rerunning a completed batch creates
no duplicates and interrupted batches resume. Then prepare one separate cleanup change migrating
every remaining dependent reader and removing legacy embedding storage, ranking reader, parallel
writes and temporary gate together. Do not delete legacy implementations during a partial backfill.
