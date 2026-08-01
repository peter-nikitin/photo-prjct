# Event Processing Worker Summary Design

## Status

Approved in conversation on 2026-07-31; written review pending.

- Related architecture: [`docs/architecture.md`](../../architecture.md), background-processing flow
  and Operations module
- Related ADRs: [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017
- Replaces for implementation planning: the event-run-oriented presentation in
  [`2026-07-31-admin-processing-progress-design.md`](2026-07-31-admin-processing-progress-design.md)

## Goal

Make `/admin/processing/` answer the operator's question: "Has this event finished processing?"
One row represents one event, with one common total and three fixed worker columns: Preview,
Embedding, and Metadata. The row makes Preview's bottleneck visible without falsely reporting a
blocked embedding worker as an unknown ETA.

## Scope

### Included

- One staff-only, read-only row per event having at least one job for `generate_preview`,
  `face_embedding`, or `capture_metadata`.
- A single event photo total, shared by all three worker columns.
- Exactly three worker columns: Preview (`generate_preview`), Embedding (`face_embedding`), and
  Metadata (`capture_metadata`).
- Per-worker completed/total, status, and ETA of that worker's own queue.
- An event-level completion status derived from the three worker summaries.

### Excluded

- Job mutation, attempts/results/errors, filters, history, charts, JavaScript polling, cache,
  API, schema changes, or visual tests.
- New worker dependencies or a scheduler change. The existing preview-first enrollment and worker
  contract remain authoritative.
- A forecast for embedding work that cannot begin until Preview is accepted.

## Selected Design

### Event row and common total

The page groups current `ProcessingJob` records by `event`. `Total photos` is the count of distinct
photo IDs in the union of the three displayed processor job sets for that event. It is intentionally
shown once, rather than presenting three different run cohort sizes. An absent job is not inferred
to be complete.

An event status is:

- `Completed` only when each of Preview, Embedding, and Metadata has no remaining job and its
  denominator equals the event total; or
- `In progress` otherwise.

This deliberately answers whole-event completion, rather than closure of an individual immutable
run.

### Worker columns

For each fixed processor type, the column shows:

| Field | Definition |
| --- | --- |
| Progress | terminal jobs / event total. Terminal means `succeeded`, `failed`, or `cancelled`. |
| Status | `Completed` if progress equals total and no non-terminal job exists; `Processing` if exactly one current job is processing; `Queued` if runnable queued/retry work exists; `Waiting for preview` for Embedding when no runnable/current embedding job exists but Preview has remaining work; otherwise `Not started`. |
| ETA | the worker's own estimated finish; `Completed` when complete; `Waiting for preview` for the dependency case; `—` only for a genuinely not-yet-measurable runnable worker. |

`queued`, `processing`, and `retry_wait` are remaining. A current job means a `processing` job with
`claimed_at`.

### ETA

For a worker with exactly one current job, calculate its queue completion as:

```text
elapsed = max(now - current_job.claimed_at, 0)
eta = now + elapsed * worker_remaining
```

The display labels the timestamp `UTC`. The count multiplies only that processor's remaining jobs,
not the event total and not another worker's queue.

For Preview, this is the immediately actionable bottleneck estimate. For Metadata it is independent.
For Embedding, if its own current job exists, calculate it independently even while Preview still has
other photos; if it has no current/runnable job and Preview is incomplete, show `Waiting for preview`
instead of `—`. The page never adds Preview ETA to Embedding ETA or claims a whole-event finish time.

### Dependency rule

Preview-first enrollment is already the control-plane rule: a face embedding job becomes eligible
only after its photo has an accepted preview. The page reads this as a presentation dependency only.
It does not create, unblock, retry, or reclassify a job. Thus a Preview bottleneck is visible in the
Preview column, and a dependent Embedding column truthfully explains its wait state.

## Constraints

- Django/PostgreSQL processing jobs remain the source of truth.
- The page is GET-only and staff-only, with no secret, storage, attempt, face, embedding, result,
  or error exposure.
- The three processor names are fixed in this delivery; a generic dynamically expanding dashboard
  is out of scope.
- A missing job never proves a photo complete.

## Acceptance Criteria

1. Staff sees one row per qualifying event with one distinct-photo total and exactly Preview,
   Embedding, and Metadata columns.
2. Each column displays terminal/event-total progress, status, and the worker's own ETA outcome.
3. The event says `Completed` only when all three columns complete all event photos; otherwise it
   says `In progress`.
4. An incomplete Preview plus unavailable Embedding work displays `Waiting for preview`, not `—`.
5. ETA uses only that worker's queue and an active claimed job; timestamps say `UTC`.
6. The page remains staff-only, GET-only, read-only, and has no visual tests.

## Verification Scope

Focused Django view tests cover staff authorization, distinct common total, event completion,
worker-specific progress/ETA, and the Embedding waiting-for-preview case. Existing processor tests
remain responsible for job creation, dependency enforcement, and state transitions.
