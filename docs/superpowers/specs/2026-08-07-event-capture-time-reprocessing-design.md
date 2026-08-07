# Event Capture-Time Reprocessing Design

## Status

Approved in conversation on 2026-08-07; written review pending.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current photo-processing
  control plane and the event-scoped search direction
- Related ADRs:
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0004](../../adr/0004-repository-engineering-knowledge.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to ADR 0002, ADR 0004, and ADR 0017. Event timezone, EXIF traversal, and
  processor-version selection are reversible implementation details; no new ADR is required.
- Follow-up specification: event-gallery capture-time filtering, written only after the corrected
  event-9 corpus passes the acceptance gates in this specification

## Goal

Make capture-time extraction reliable enough to support customer filtering by the local time of an
event. Correct the published event `Cyclingrace Вечернее Садовое` (event ID `9`) without changing
other events, rewriting originals, or mutating the immutable evidence from previous processing
attempts.

## Evidence and Root Cause

The current staging snapshot contains 17,043 photos for event 9. Its accepted capture-metadata
results divide into:

- 10,699 successful results with non-null `capture_time`; and
- 6,344 terminal `unsupported_input` failures.

Direct inspection of representative failed originals found Canon MPO files despite their `.jpg`
names and `image/jpeg` declarations. Pillow identifies them as `MPO`. Their useful
`DateTimeOriginal`, `DateTimeDigitized`, and offset values are in the nested EXIF IFD, including an
explicit `+03:00` offset. The current worker accepts only `image.format == "JPEG"` and reads only the
mapping returned at the top level by `getexif()`, so it rejects these inputs before extraction.

The 10,699 accepted results reveal a second defect. Every result has
`timezone_state = "inferred_none"`; the worker interpreted the camera's timezone-less wall time as
UTC. PostgreSQL runs in `Etc/UTC`, and `capture_time` is currently stored as a canonical UTC string
ending in `Z` inside the accepted attempt's JSONB `result`, not as an indexed timestamp column.
Event 9 occurred in Moscow, so treating its timezone-less camera clock as UTC shifts the represented
instant by three hours. Merely accepting MPO would create an internally inconsistent corpus: MPO
values with explicit `+03:00` would normalize correctly while existing JPEG values would remain
three hours late.

## Outcome

After delivery:

- every published event has an explicit IANA timezone; event 9 uses `Europe/Moscow`;
- capture-metadata processor version 2 supports the JPEG and MPO representations observed in event
  9 and traverses the correct EXIF IFDs;
- explicit EXIF offsets are honored and timezone-less wall times are interpreted in the event's
  timezone rather than UTC;
- all accepted capture times remain canonical UTC instants while retaining bounded provenance;
- all 17,043 photos in event 9 receive a new version-2 processing outcome;
- old jobs, attempts, results, and reports remain immutable evidence; and
- a repeatable verification report proves the corrected corpus is complete and has no silent
  three-hour split before time filtering is specified.

## Scope

### Included

- An optional IANA timezone field on draft events and a publication invariant requiring it for a
  published event.
- `Europe/Moscow` as the explicit timezone of event 9 in the staging-derived data operation.
- Capture-metadata processor version 2 with event timezone in its immutable configuration.
- Bounded JPEG and MPO EXIF capture-time extraction.
- Nested EXIF IFD traversal for the standard capture fields and their paired offset fields.
- Explicit handling of malformed offsets and ambiguous or nonexistent local times.
- A guarded one-off Django management command scoped to exactly event ID 9.
- Reprocessing every event-9 photo, including the 10,699 previously accepted photos.
- Focused contract, parser, enrollment, command, and restored-snapshot verification.

### Excluded

- Reprocessing or exposing any event other than event 9.
- Publishing draft events or inferring their timezone from free-text city values.
- Customer-facing time controls, gallery query parameters, filtering, indexes, or UI.
- Editing EXIF, replacing originals, or generating corrected derivative files.
- Guessing capture time when no supported date exists.
- Supporting arbitrary image or metadata formats beyond the JPEG and MPO evidence in the current
  event.
- Rewriting, deleting, or marking old terminal attempts as if they had produced the new result.
- Activating a new worker build on production.

## Event-Timezone Contract

`Event` gains an IANA timezone identifier validated through `zoneinfo.ZoneInfo`. The database may
temporarily hold no timezone for a draft event so the migration does not invent facts for hidden
events such as London, Brighton, and synthetic fixtures. Model and admin validation reject a
published event without a valid timezone. New event creation exposes the field explicitly rather
than deriving it from `city`.

Event 9 is assigned `Europe/Moscow` before any version-2 capture job is enrolled. Enrollment rejects
an event with no valid timezone. The normalized capture-metadata configuration records the exact
event timezone, making a timezone correction a new processor identity rather than an in-place
reinterpretation of old attempts.

The customer-facing filtering specification must define entered clock values as local event time.
It will convert interval boundaries through the event's IANA timezone before comparing them with
canonical UTC capture instants. Browser timezone and server timezone must not alter that meaning.

## Capture-Metadata Processor Version 2

Version 2 retains ADR 0017's existing worker trust and transport boundary. It changes only the
versioned processor semantics and typed output.

### Supported container formats

The worker accepts Pillow-detected `JPEG` and `MPO` inputs after the existing byte and pixel bounds.
Filename suffix and submitted content type do not decide decoded format. Other decoded formats
remain `unsupported_input`; decode failures retain `decode_failed`.

### EXIF traversal and precedence

The configured field precedence remains:

1. `DateTimeOriginal` with `OffsetTimeOriginal`;
2. `DateTimeDigitized` with `OffsetTimeDigitized`;
3. `DateTime` with `OffsetTime`.

For each candidate, the parser reads its standards-defined IFD first: original and digitized fields
from the nested EXIF IFD and the generic image timestamp from the root IFD. A bounded root fallback
is permitted only for the same tag when Pillow exposes a valid duplicate there. Nested and root
values that disagree produce `capture_time_conflicting`; the standards-defined location wins.

The parser does not decode pixel arrays and does not enumerate MPO frames. It opens the primary
image, validates its dimensions, and reads bounded metadata only.

### Timezone resolution

For the first valid date candidate:

1. A valid paired EXIF offset defines the instant and yields `timezone_state = "explicit"`.
2. A missing paired offset uses the configured event timezone and yields
   `timezone_state = "event_timezone"`.
3. A malformed paired offset emits `capture_time_malformed_offset`, then uses the configured event
   timezone and yields `timezone_state = "event_timezone"`.
4. A local wall time that is nonexistent or ambiguous in the event timezone is not guessed. The
   candidate emits `capture_time_timezone_ambiguous` and the parser tries the next date field.
5. If no unambiguous candidate remains, `capture_time` is null and the result includes
   `capture_time_missing` plus the diagnostic warnings already accumulated.

Candidate conflicts are evaluated after normalization to UTC. Two source strings representing the
same instant do not conflict merely because their offsets differ.

### Typed result

A successful non-null version-2 result contains:

- `capture_time`: canonical RFC 3339 UTC text ending in `Z`;
- `source_field`: the selected EXIF date field;
- `timezone_state`: `explicit` or `event_timezone`;
- `source_value`: the bounded unmodified selected wall-time text;
- `source_offset`: the bounded explicit offset text, or null when the event timezone was used;
- `event_timezone`: the configured IANA identifier; and
- bounded stable `warnings`.

A missing result uses null source fields, `timezone_state = "not_applicable"`, retains the configured
`event_timezone`, and includes `capture_time_missing`. Version 2 never produces
`timezone_state = "inferred_none"`. The old version-1 schema remains readable only as immutable
historical evidence; no new version-1 job is created.

## Reprocessing Operation

A dedicated management command is the only supported backfill interface. It:

- requires `--event-id 9` and refuses any other event ID;
- requires an explicit apply confirmation, with dry-run as the default;
- verifies the event name, published status, timezone `Europe/Moscow`, photo count 17,043, and the
  exact capture-metadata version-2 configuration before writing;
- creates a new immutable event-scoped run and version-2 job for every event-9 photo, regardless of
  version-1 state;
- is idempotent for the same processor identity and does not duplicate already enrolled version-2
  jobs;
- uses the normal job, lease, exact-object authorization, worker completion, and accepted-result
  paths from ADR 0017; and
- prints only bounded counts and identifiers, never object keys, signed URLs, EXIF values, or
  filenames.

The command does not wait inside one database transaction for 17,043 remote jobs. It enrolls the
fixed cohort and exits. Existing run and job reporting provides progress; a separate read-only
verification mode evaluates completion.

## Failure Semantics

- Missing event timezone: refuse enrollment; do not assume UTC.
- Invalid IANA identifier: reject model publication/configuration and refuse enrollment.
- Unsupported decoded format: permanent `unsupported_input` failure.
- Unreadable or corrupt EXIF: deterministic warning or `decode_failed` according to whether the
  image itself can be opened safely.
- Missing supported time: successful typed result with null capture time and
  `capture_time_missing`; this fails the event-9 acceptance gate but remains a truthful domain
  result.
- Worker interruption, authorization expiry, or transient storage failure: normal bounded ADR 0017
  retry semantics.
- Partial backfill: retain completed version-2 evidence and rerun the idempotent enrollment command;
  do not roll accepted attempts back to version 1.
- Verification mismatch: do not enable or specify customer filtering as accepted behavior; inspect
  the bounded failure/warning distribution and representative originals first.

## Acceptance Criteria

### Parser and contract

1. A representative MPO fixture with nested `DateTimeOriginal` and `+03:00` produces the expected
   UTC instant with `timezone_state = "explicit"`.
2. A representative JPEG without an offset uses `Europe/Moscow` and produces a UTC instant three
   hours earlier than its wall-clock source with `timezone_state = "event_timezone"`.
3. Standards-defined nested fields win over conflicting root duplicates and emit a stable warning.
4. Malformed offsets, missing dates, field conflicts, size/pixel bounds, corrupt input, and
   unsupported decoded formats retain deterministic outcomes.
5. Ambiguous and nonexistent DST wall times are never silently assigned an arbitrary instant.
6. Django and worker agree exactly on processor identity, configuration, result schema, warning
   vocabulary, and timezone-state vocabulary.

### Event model and operation

7. Published events require a valid IANA timezone; drafts are not assigned one from city text.
8. Dry-run makes no changes, any event ID other than 9 is rejected, and repeated apply enrollment is
   idempotent.
9. Version-1 attempts and reports remain unchanged and queryable after version-2 enrollment.
10. Only photos belonging to event 9 receive version-2 capture-metadata jobs.

### Restored staging snapshot

11. The verified cohort is exactly 17,043 event-9 photos and exactly 17,043 terminal version-2
    jobs.
12. Exactly 17,043 current accepted version-2 results have non-null `capture_time`; there are zero
    terminal failures and zero missing-time outcomes.
13. Results contain no `inferred_none` timezone state.
14. Bounded aggregates compare source wall hour with normalized event-local hour and prove there is
    no mixed three-hour split between JPEG and MPO cohorts.
15. Minimum, maximum, hourly distribution, warning counts, explicit-offset counts, and
    event-timezone counts are reviewed for plausibility without exposing filenames, object keys, or
    raw EXIF payloads.
16. Only after criteria 11-15 pass may the gallery time-filter specification use this corpus as its
    data contract and acceptance fixture.

## Testing Strategy

- Pure worker tests use repository fixtures for JPEG root/nested EXIF, representative MPO nested
  EXIF, malformed offsets, conflicts, and DST boundary behavior.
- Worker-contract and Django result-validation tests use the same literal version-2 configuration
  and typed-result vocabulary.
- Django model/admin tests cover draft and published event timezone validation.
- Enrollment and command tests cover strict event scope, dry-run, confirmation, idempotency,
  immutable history, and exact cohort counts.
- Focused restored-snapshot verification runs against the current local staging clone and produces
  privacy-safe bounded aggregates.
- The final implementation verification runs the relevant focused suites followed by the project's
  complete `make check`; staging activation and live backfill require separate deployment evidence.

## Rollout and Rollback

Deliver the model, processor, worker, command, and tests through the normal immutable-image staging
workflow. Assign and verify event 9's timezone before applying the enrollment command. Run the
worker normally and monitor bounded run progress until the cohort is terminal; then execute the
read-only acceptance report.

Before customer filtering exists, rollback is to stop version-2 enrollment or worker claiming and
deploy the preceding image. Version-2 attempts already accepted remain immutable evidence. Do not
reactivate version-1 results as correct event-local instants. A processor defect requires a new
processor version and a new event-scoped run, not mutation of version-2 attempts.
