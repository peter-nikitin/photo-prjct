# Photo Capture-Time Projection Design

- Date: 2026-08-08
- Status: Release B candidate implemented and locally verified; PR, CI, deployment, and customer
  acceptance remain pending
- Release A evidence: accepted staging deployment at `41e3068` with clean 17,043/17,043 global
  reconciliation and rollback-only lifecycle smoke. Release B local evidence is separate and does
  not substitute for its required live candidate gate.
- Owner: project maintainer
- Related architecture:
  [Current architecture — capture-metadata processor version 2](../../architecture.md#current-architecture--implemented),
  [Core data flows — photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing),
  and [Core data flows — search](../../architecture.md#search)
- Related ADRs: [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md)
- ADR impact: Requires new ADR 0027 to select a synchronous PostgreSQL read projection on `Photo`,
  retain immutable accepted processing evidence as its source of truth, and define freshness,
  clearing, rebuild, and correction boundaries. Conforms to ADR 0002, ADR 0017, and ADR 0022; it
  supersedes none of them.
- Triggering specification:
  [Event Gallery Time Filter Design](2026-08-08-event-gallery-time-filter-design.md)
- Triggering evidence:
  [Event gallery time-filter local-clone benchmark](../../performance/2026-08-08-event-gallery-time-filter-local-clone.json)

## Outcome

The event gallery can filter photos by capture time without joining the processing state, job,
run, and immutable-attempt tables or parsing JSON inside every pageable gallery query.

Each photo has a nullable, indexed UTC capture-time projection and an exact immutable source-attempt
reference. The current accepted `capture_metadata` processor-version-2 attempt remains the sole
source of truth. The projection is a synchronously maintained, rebuildable read model; it is not a
second authoritative capture-time record.

Every existing event with qualifying current version-2 evidence is backfilled and reconciled before
customer traffic uses the projection globally. The accepted 17,043-photo event-9 corpus remains the
customer-visible acceptance and performance cohort. The cutover is admitted only when every
existing qualifying event matches its source evidence exactly and event 9's filtered first,
midpoint, and last gallery pages each satisfy the existing 2x database and rendered-response gate.

## Problem and evidence

The approved manual-time filter currently proves eligibility by joining each gallery photo to its
mutable current processing state, current run and job, and immutable accepted version-2 attempt.
It extracts `capture_time` from JSON, validates its canonical shape, casts it to a timestamp, and
then applies the range predicate.

The accepted local event-9 corpus contains 17,043 current accepted non-null version-2 capture
times, zero missing times, and zero terminal failures. On that corpus, the direct filtered query
took 8.169-8.633 times its matching unfiltered database baseline, and the fully rendered response
took 6.225-8.108 times its baseline across the first, midpoint, and last pages. Every measurement
exceeded the approved 2x limit. This is the explicit trigger in the original filter specification
for a separately approved projection, freshness, correction, migration, and index design.

## Scope

This design includes:

- a nullable capture-time projection and source-attempt reference on `Photo`;
- a PostgreSQL index that supports event-scoped capture-time range filtering;
- synchronous projection clearing and publication inside the existing processing transactions;
- a strict, idempotent event-scoped backfill and reconciliation capability;
- gallery query cutover from direct processing evidence to the projection;
- aggregate privacy-safe acceptance and performance evidence; and
- a pre-switch rollout gate that leaves the current service active after any failure.

This design excludes:

- manual capture-time correction or an operator-entered override;
- mutation or deletion of immutable processing attempts;
- support for version-1, stale, failed, unaccepted, or null capture-time evidence;
- an asynchronous projection queue, scheduled refresh, materialized view, or eventual-consistency
  window;
- a projection table separate from `Photo`;
- browser-timezone behavior or changes to the approved manual-filter semantics;
- a new gallery ordering, page size, media-eligibility rule, or authorization rule; and
- speculative support for processors other than `capture_metadata` version 2.

Manual correction requires a separately approved audited source-of-truth design. A future parser or
timezone correction that requires processor version 3 is also outside this design: it must first
approve the new evidence contract and move projection eligibility from version 2 to that version.
Historical evidence is never rewritten and two processor versions are never silently treated as
simultaneously current projection sources.

## Data model

`Photo` gains two nullable fields:

- `capture_time`: an aware canonical instant stored by PostgreSQL/Django in UTC; and
- `capture_time_source_attempt`: a reference to the immutable `ProcessingAttempt` from which the
  value was projected.

The fields form one logical value. They are either both null or both non-null. A non-null pair is
valid only when the source attempt:

- belongs to the same photo;
- has processor type `capture_metadata` and processor version 2;
- has succeeded and is accepted;
- is the photo's current state's accepted attempt;
- is also the current attempt of the current run and job; and
- contains the same non-null canonical UTC `capture_time` as the projected value.

The exact source-attempt identity makes the projection explainable and allows reconciliation to
distinguish a merely equal timestamp from a projection derived from the current accepted evidence.
The source foreign key protects referenced immutable evidence from deletion; ordinary reads do not
join through it.

The database enforces the both-null-or-both-non-null shape. Cross-table currentness and result-value
equality are transaction and reconciliation invariants because ordinary relational constraints
cannot compare the JSON result and mutable processing state across tables.

An index on `(event, capture_time)` supports the approved event-scoped range predicate. The existing
gallery ordering remains `(original_filename, id)` and the page size remains 100; this design does
not claim that the range index removes the final ordering step.

## Source of truth and projection state

Immutable accepted processing evidence remains authoritative. Projection state is derived as
follows:

| Current processing condition | `Photo` projection |
| --- | --- |
| Current accepted succeeded v2 attempt with a valid non-null capture time | Exact UTC time and exact source attempt |
| No current accepted attempt | Both fields null |
| Current accepted v2 success with `capture_time=null` | Both fields null |
| Queued, processing, retry-wait, failed, or cancelled current work | Both fields null |
| Version 1, another processor, stale or late attempt | No value may be published from that evidence |

The projection is never used to reconstruct or mutate processing state. Deleting the projection
does not delete evidence; the accepted evidence can rebuild it deterministically.

## Synchronous freshness algorithm

Projection changes occur in the same PostgreSQL transaction as the authoritative state transition.

When capture-metadata work becomes current and clears the state's accepted attempt, the system also
clears both projection fields. This happens at enrollment, before a new result exists. The photo is
temporarily absent from filtered galleries rather than appearing under stale time.

When the current leased version-2 attempt completes successfully, Django first validates the typed
worker result under the existing capture-metadata contract. In the transaction that marks the
attempt accepted, the job succeeded, and the state succeeded/current/accepted:

- a non-null valid capture time replaces both projection fields with the exact UTC instant and
  accepted attempt; or
- a valid null capture time leaves both fields null.

Retryable failure and lease recovery retain null fields while work has no accepted current result.
Terminal failure or cancellation also leaves them null. A stale, expired, late, duplicate, wrong-
processor, wrong-version, or non-current attempt cannot change the projection.

Transaction rollback rolls back both the processing transition and projection change. There is no
committed state in which newly accepted evidence and the projection disagree because one write
succeeded independently of the other.

## Backfill and reconciliation contract

Existing data is populated by a dedicated command rather than a Django data migration. Schema
migration remains bounded to nullable fields, the shape constraint, foreign key, and index.

The command is strict, event-scoped, idempotent, and defaults to dry-run. Applying it requires an
explicit apply option and a confirmed event identity. An all-events orchestration enumerates every
existing event with qualifying current version-2 evidence and invokes the same event-scoped
contract; it does not broaden an individual write transaction across events. The command derives
every value from the exact current accepted version-2 state/evidence contract used above:

- a qualifying non-null current result sets both fields;
- every other state clears both fields;
- attempts, jobs, runs, states, and result JSON are never modified; and
- a repeated run converges to the same projection.

Each photo is derived inside its own transaction. Every projection-aware enrollment, reprocessing,
completion, cancellation, retry, repair, and reconciliation path uses one complete order for rows
that it needs to lock:

```text
Event (when required) -> EventProcessingRun -> ProcessingJob -> Photo
-> PhotoProcessingState -> ProcessingAttempt
```

A path may omit rows it does not need, but it never acquires a row to the left after holding a row
to the right. In particular, reprocessing no longer locks `Photo` before an existing run/job, and
completion never locks run/job after `Photo`. New run/job rows created by the current transaction do
not require a competing-row lock, but their later shared rows still follow the same order.

Backfill first reads only identifiers, then acquires the applicable event, current run, current job,
photo, state, and accepted-attempt locks in that order. It re-reads every current identity, version,
status, and result after all locks are held. If any identifier changed between discovery and lock
acquisition, it releases the transaction and retries that photo from discovery; it never publishes
the earlier value. It updates the projection before releasing the locks. Tests exercise concurrent
enrollment, completion, and repair and fail on any inverse lock acquisition.

The command reports only aggregate counts and bounded status information. It emits no filenames,
photo IDs, storage keys, EXIF source values, individual timestamps, or customer identifiers.

Reconciliation compares all photos to authoritative evidence. Before global query cutover, it
enumerates and passes every existing event with qualifying current version-2 evidence. For each
event it fails unless all applicable source/value/currentness invariants below hold. Event 9 has the
additional fixed customer-acceptance counts:

- event identity, publication state, timezone, photo count, and version-2 corpus preconditions are
  the accepted ones;
- 17,043 photos have current accepted non-null version-2 evidence;
- exactly 17,043 photos have non-null projection pairs;
- every projected time equals its accepted attempt's canonical capture time;
- every source reference is the exact current accepted attempt for the same photo;
- no qualifying photo is missing a projection; and
- no unqualified, stale, null, failed, other-version, or other-event evidence has a projection.

Events with no qualifying current version-2 evidence must have zero projection pairs. A new event
created after cutover is maintained by the synchronous lifecycle and needs no historical backfill;
global reconciliation still detects any projection outside the supported version-2 contract.

Any mismatch is a delivery failure. The command may repair projection rows on an explicit apply,
but it never repairs or rewrites authoritative evidence.

## Gallery query behavior

After successful reconciliation, the manual filter retains all approved form semantics: event-local
interpretation, inclusive ten-minute widening, optional end time through the event end, validation,
pagination, empty/error states, privacy, and no query persistence.

The filtered gallery starts with the existing media-eligibility queryset and applies only:

```text
photo.capture_time >= widened_start_utc
AND photo.capture_time <= widened_end_utc
```

Null projections are excluded by the range predicate. The user-facing request does not inspect or
join processing evidence. Unfiltered galleries do not require capture time and remain unchanged.
The direct JSON-processing join and cast path is removed after cutover rather than retained as a
fallback or compatibility path.

## Failure and correction semantics

- **Enrollment clears projection but the worker is slow or unavailable:** the photo is absent only
  from time-filtered results until current accepted evidence exists; unfiltered gallery eligibility
  is unchanged.
- **Worker returns missing or invalid time:** valid typed missing output leaves the projection null;
  invalid output is rejected by the existing worker contract and cannot publish a projection.
- **Late or stale completion arrives:** existing lease/current-attempt checks reject it as a source
  of current state and projection.
- **Projection write fails:** the enclosing processing transaction rolls back; evidence is not
  accepted without its required projection transition.
- **Backfill is interrupted:** committed batches remain derived and safe; rerunning the idempotent
  command converges. Cutover remains blocked until full reconciliation passes.
- **Projection drift is detected:** customer cutover or delivery is blocked. Operators rerun the
  aggregate reconciliation and explicit projection rebuild; they do not edit attempts or time
  values manually.
- **Parser or timezone interpretation is wrong:** do not edit the projection or historical attempt.
  A separately approved processor-version change must define its evidence rollout and explicitly
  replace version 2 as the one supported projection source before corrected work can publish.

## Rollout and cutover contract

The change uses two application releases so projection writes are continuously maintained before
projection reads become customer-authoritative. No additional event-ID or UI restriction is
introduced.

**Release A — projection writer, direct reader:**

- applies the nullable schema, shape constraint, source foreign key, and index;
- changes every capture-metadata lifecycle transition to maintain the projection atomically under
  the common `Photo`-then-state lock order;
- retains the existing direct-evidence gallery query as the only reader for this release;
- enumerates and backfills every existing event with qualifying current version-2 evidence,
  including the accepted 17,043-photo event-9 corpus; and
- passes global reconciliation while the deployed Release A continues dual-write/direct-read
  operation.

Release A does not expose partially populated projection data to gallery reads. New enrollment and
completion after backfill are projection-aware, so the projection cannot silently fall behind while
the application remains on Release A.

**Release B — projection reader:**

- performs a final global reconciliation immediately before service switch;
- runs the privacy-safe projection benchmark for event 9's first, midpoint, and last pages;
- requires both database-execution and fully rendered response ratios at no more than 2x their
  matching unfiltered baseline for every page, without timeout or health degradation;
- switches the gallery to `Photo.capture_time`; and
- removes the direct processing JSON join/cast path rather than retaining a fallback.

A Release-A schema, writer, backfill, or reconciliation failure leaves the preceding service active.
A Release-B reconciliation, benchmark, candidate-health, or switch failure leaves Release A active
as the projection-maintaining direct reader. Backfilled fields are rebuildable and do not alter
immutable evidence, so an unsuccessful candidate can be discarded without reversing data
authority.

After switching, smoke checks cover unfiltered gallery, valid/invalid manual filters, filtered
empty state, page navigation/reset, selfie search, gallery-origin search, media authorization, and
health. Customer acceptance additionally requires the live event-9 current-v2 report and projection
reconciliation to remain terminal and exact.

## Release A and Release B evidence status — 2026-08-08

Release A is accepted on staging at `41e3068`: it remains the projection-maintaining direct
current-v2 JSON/cast reader, with final global reconciliation at 17,043/17,043 event-9
source/value pairs and a rollback-only lifecycle smoke that clears then republishes the projection.
Release B implements the projection-only filtered reader and removes the direct JSON/cast fallback
locally; it has not switched a live service.

The integrated Release A suite passed 539 tests with 2 skipped and 43 deselected. The visual suite
passed 92 tests in 1.2 minutes after the `<=30` index fix. `make check` passed with Ruff/format/MyPy clean,
1,591 tests passed, 3 skipped, 43 deselected, 83.53% coverage, a clean Django check, and no
migration drift.

On the accepted local staging clone (9 events, 17,310 photos; event 9 has 17,043), the
authoritative pre-backfill event-9 report had 17,043 accepted results, 17,043 non-null results,
17,043 terminal jobs, and 17,043 version-2 jobs, with zero missing or terminal failures, status
`accepted`, and timezone `Europe/Moscow`.
The global dry run reported `would_change=17043`, `unchanged=267`, `events=9`, `photos=17310`, and
zero exhausted/retries/skipped. Apply changed 17,043 and left 267 unchanged. The required-clean
report was clean with exact/projection/qualifying non-null counts of 17,043 and zero missing,
mismatching, stale, extra, partial, or unsupported rows; event 9 was accepted at exactly
17,043/17,043. The idempotent apply changed 0 and left 17,310 unchanged, and the authoritative
after-report was identical and accepted.

On the immutable accepted local clone, Release B's final global reconciliation was clean before and
after the read-only candidate benchmark. Event 9 retained 17,043 exact source/value pairs and every
first/midpoint/last database and rendered ratio passed the 2x gate in the [sanitized aggregate
report](../../performance/2026-08-08-event-gallery-time-filter-local-clone.json). This local
candidate evidence does not replace Release B review, PR/CI, normal staging deployment, exact
image/health, live reconciliation, live benchmark, post-switch smoke, or customer acceptance.

## Privacy and authorization

The projection contains one event-scoped timestamp and an internal immutable-attempt reference
already present in PostgreSQL. It stores no new image, EXIF source string, user query, biometric
vector, identity, or search history. Aggregate commands preserve the existing bounded-report
privacy contract.

The projection changes only gallery selection. It does not grant media access, open paid galleries,
change bearer-result authorization, retain selfie uploads, or bypass existing gallery media and
download policies.

## Rejected alternatives

- **Keep the direct processing-evidence join.** Rejected because the accepted corpus measured
  8.169-8.633x database and 6.225-8.108x rendered regressions against a 2x gate.
- **Separate projection table.** Rejected because the selected query needs one scalar property of a
  photo; a separate table adds a read join and duplicates event identity without improving the
  current product boundary.
- **Store the timestamp on `PhotoProcessingState`.** Rejected because the event-range index would
  still require a join or duplicated event identity and would mix search projection with processor
  lifecycle state.
- **Materialized view or asynchronous refresh.** Rejected because freshness would lag current-state
  rotation and permit stale customer results.
- **Timestamp without source-attempt identity.** Rejected because equality alone cannot prove which
  immutable accepted result produced the projection.
- **Populate inside a Django data migration.** Rejected because a large evidence scan and repairable
  operational work do not belong in an all-or-nothing schema migration.
- **Retain the direct query as a fallback.** Rejected because it silently restores the measured
  failing path and masks projection drift.
- **Manual time override.** Rejected from this scope because it introduces a new audited authority,
  conflict precedence, and operator-permission design.

## Acceptance criteria

1. `Photo` has nullable `capture_time` and `capture_time_source_attempt` fields whose database shape
   is both null or both non-null, plus an `(event, capture_time)` index.
2. A non-null pair is derived only from the same photo's exact current accepted succeeded
   `capture_metadata` version-2 attempt with the identical canonical non-null UTC time.
3. Starting new current capture-metadata work clears both fields in the same transaction that clears
   the accepted pointer.
4. Accepting a valid current version-2 success atomically publishes or clears the projection
   according to its non-null or null result.
5. Stale, late, expired, duplicate, wrong-photo, wrong-processor, wrong-version, failed, cancelled,
   and unaccepted attempts cannot publish projection values.
6. Every projection-aware lifecycle and repair path follows
   `Event -> Run -> Job -> Photo -> State -> Attempt` for the rows it needs; transaction failures
   cannot commit authoritative evidence and a mismatching projection independently, and concurrent
   repair/transition tests detect lock inversion.
7. The strict event-scoped backfill defaults to dry-run, requires explicit apply, is idempotent,
   mutates only projection fields, and emits aggregate privacy-safe output.
8. Global reconciliation covers every existing event with qualifying current-v2 evidence and proves
   zero missing, stale, or extra projections; event 9 additionally proves exactly 17,043 source/value
   pairs and no authoritative-evidence mutation.
9. Filtered galleries use only the indexed `Photo.capture_time` range after existing media
   eligibility; the direct processing JSON join/cast path is removed and unfiltered behavior is
   unchanged.
10. Event-local form semantics, inclusive ten-minute widening, 100-item numbered pages,
    filename-plus-ID order, no-JavaScript operation, empty/error states, and query privacy remain
    unchanged.
11. The local accepted-clone and live candidate benchmarks both cover first, midpoint, and last
    pages and pass database and rendered ratios of at most 2x for every page.
12. Release A deploys projection writing while retaining direct reads; Release B performs final
    reconciliation and the 2x benchmark before projection-read cutover. Failure leaves the preceding
    service active and does not alter immutable processing evidence.
13. No manual correction, future processor-version correction, asynchronous refresh, separate
    projection table, version-1 fallback, or retained direct-query fallback is introduced.
14. Existing media authorization, paid-gallery, selfie privacy, gallery-origin, feedback, cluster,
    bearer-result, and health regressions continue to pass.

## Architecture and ADR reconciliation

The design conforms to ADR 0002 by keeping authoritative and derived durable state in PostgreSQL,
and to ADR 0017 by preserving Django-owned processing transitions, immutable accepted attempts,
worker isolation, typed result validation, and PostgreSQL transactions. It conforms to ADR 0022 by
retaining bounded numbered gallery pages and their ordering contract.

It requires ADR 0027 because selecting a durable `Photo` read projection, exact source-attempt
provenance, synchronous clearing/publication, rebuild semantics, and a persistent event-time index
is a long-term data-boundary decision not governed by existing ADRs. ADR 0027 supersedes no existing
decision. Current implemented architecture must not describe the projection as live until the
implementation, reconciliation, benchmark, deployment, and acceptance evidence exist.
