# Admin Processing Progress Design

## Status

Approved in conversation on 2026-07-31.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current
  background photo-processing flow and proposed Operations module
- Related ADRs: [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0017

## Goal

Give staff a single, read-only place to see whether photo processing for each event is progressing
and when its current run is expected to finish. The page must use the existing Django/PostgreSQL
processing records; it must not add another queue, reporting store, worker protocol, or background
aggregation.

## Scope

### Included

- One staff-only Django page at `/admin/processing/`.
- One row for each `EventProcessingRun`, ordered with active runs first and then most recently
  created runs.
- The event identity, processor type and version, run status, cohort size, per-job-status counts,
  completed count, remaining count, and a simple ETA.
- Current aggregates calculated from the run's `ProcessingJob` rows at request time.
- A compact server-rendered template using the existing project styling conventions.

### Excluded

- Any mutation from the page: retry, cancel, claim, recover, requeue, or start work.
- Attempt-level browsing, results, errors, signed URLs, storage keys, worker credentials, or
  biometric payloads.
- JavaScript polling, charts, filters, export, pagination, background cache, new API, or a
  monitoring/alerting system.
- Throughput history, parallel-worker modelling, retry-duration prediction, or an accuracy claim
  for ETA.
- Visual regression tests.

## Selected Design

### Access and route

The view is part of the Django application and is mounted beneath the existing admin path as
`/admin/processing/`. It requires Django's normal staff access (`request.user.is_staff`). An
anonymous or authenticated non-staff request receives the same access control outcome as Django
Admin. The page is read-only.

Using staff access keeps operational visibility within the existing administrator boundary. This
does not give workers or public users access to processing state, and it does not change the
private worker API governed by ADR 0017.

### Read model

The page reads `EventProcessingRun` and its `ProcessingJob` rows. It does not use an immutable
closed-run report as the source for a live row: job state is the current authoritative projection.
For each run, the page presents:

| Field | Definition |
| --- | --- |
| Event | The run's event name, linked to its Django Admin change page. |
| Processor | `processor_type` and `processor_version`. |
| Run status | Existing `collecting`, `sealed`, or `closed` state. |
| Total photos | Number of jobs in the run cohort. |
| Statuses | Counts for every `ProcessingJob` status: `queued`, `processing`, `retry_wait`, `succeeded`, `failed`, and `cancelled`. A zero is shown explicitly. |
| Processed | `succeeded + failed + cancelled`; these are the terminal job states. |
| Remaining | `queued + processing + retry_wait`; these are the non-terminal job states. |
| ETA | The estimate defined below, `—` when it cannot be calculated, or `Completed` for a closed run. |

The page may query and aggregate per run for the first implementation. The current runs are
bounded cohorts, and no cache or denormalized counter is justified for this critical path. If an
operator later observes a slow page at a measured run volume, that is the trigger to introduce a
bounded aggregate query or projection.

### ETA

ETA is deliberately a transparent, instantaneous estimate, not a completion-time guarantee.

For an active run, the page finds the current job in `processing`. It displays an ETA only when
there is exactly one such job and that job has `claimed_at`:

```text
elapsed = max(now - processing_job.claimed_at, 0)
remaining = queued + processing + retry_wait
estimated_finish = now + elapsed * remaining
```

The displayed value is `estimated_finish`, rounded only for presentation. It is recalculated on
every page load.

If there is no processing job, more than one processing job, or `claimed_at` is absent, ETA is
`—`. This includes a queued run before its first claim and a run waiting for retry. The first
release assumes the currently accepted single-worker, concurrency-one operating model; it does not
infer a worker speed from historical attempts or estimate the duration of pending retries.

For a `closed` run, the ETA cell says `Completed`, regardless of any historical duration stored in
the immutable report.

### Failure and empty states

An event with no processing runs is absent from the table. If no runs exist at all, the page
explains that there are no processing runs yet. A run with zero jobs still renders consistently:
all status and processed counts are zero, and its ETA follows the rules above.

The page never derives a job state from nullable result fields and never changes stored state while
rendering. Database failures follow the application's normal Django error handling; the view must
not hide them with an invented zero-progress response.

## Alternatives Considered

### Extend model-specific Django Admin pages

This is mechanically smaller, but forces operators to navigate model records and does not provide
the requested event-level overview. It also couples presentation more tightly to internal admin
registration. Rejected for the dedicated operational use case.

### Full operations dashboard

Charts, retry actions, attempt drill-down, historical throughput, filtering, and automatic refresh
would support later operations, but add surface area and false precision before an operator has
used the basic view. Rejected for this critical-path increment.

### Historical-duration ETA

Closed attempts record durations, but averaging them would need decisions about processor version,
input size, retries, worker count, and outliers. The duration of the currently processing photo is
the simplest evidence available now. Rejected until measured operational use demonstrates that its
estimate is inadequate.

## Constraints and Invariants

- Django/PostgreSQL remain the control plane and source of truth for jobs, attempts, and runs.
- The page is an operator read model only; it must not alter lease, retry, attempt, report, or
  worker semantics.
- No secret, exact object-storage key, signed grant, raw result, error detail, face data, or
  embedding is exposed.
- Processing and run statuses retain their existing meanings; the page does not introduce a new
  state machine.
- The design remains compatible with future processors because rows identify the processor. It
  does not authorize a future processor or modify its data-governance boundary.

## Acceptance Criteria

1. A staff user can open `/admin/processing/` and see one row per processing run, with event,
   processor, run status, total, all six job-status counts, processed, remaining, and ETA.
2. A non-staff user cannot access the page.
3. Counts are calculated from the run's current jobs and classify terminal versus remaining states
   exactly as specified.
4. With exactly one claimed processing job, ETA equals the current job elapsed time multiplied by
   remaining jobs and is displayed as an estimated finish time.
5. With no uniquely eligible current processing job, the ETA is `—`; a closed run displays
   `Completed`.
6. Rendering the page does not write or mutate processing records.
7. The implementation has focused automated coverage for staff authorization, status aggregation,
   a calculable ETA, and the non-calculable ETA case. It has no visual tests.

## Verification Scope

The implementation plan must use focused Django view tests with fixed time. The tests need only
prove the access boundary, aggregate/count contract, and the two ETA branches above. Existing
processing model and service tests continue to cover the queue state machine; this page does not
duplicate them.
