# Selfie Search Observability Design

- **Status:** Approved in conversation on 2026-08-04; pending written review
- **Date:** 2026-08-04
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), implemented public
  selfie-search flow, Django-polled worker boundary, and proposed Operations capability
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md),
  [`2026-08-02-asynchronous-selfie-search-submission-design.md`](2026-08-02-asynchronous-selfie-search-submission-design.md),
  and [`2026-08-02-selfie-upload-guidance-design.md`](2026-08-02-selfie-upload-guidance-design.md)
- **Related ADRs:** [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md) and
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md)
- **ADR impact:** None — reversible implementation detail. This design adds bounded operational
  evidence without changing selfie processing, retention, ranking, result publication, or the
  Django/worker authority boundary governed by ADRs 0017 and 0019.

## Incident Evidence

Read-only inspection of the active environment for 2026-08-03 Europe/Moscow found 1,616 selfie
submission POSTs. Only 333 submissions produced at least one result. The losses occurred at three
different boundaries:

- 501 POSTs returned the event page with HTTP 200 and created no search, but historical logs could
  not distinguish upload-validation reasons from temporary storage failure;
- 508 of 1,062 created searches ended in bounded selfie-face rejection; and
- 221 of 554 searches with a valid query embedding completed with zero matches.

Durable search and attempt rows showed that the gallery cohort, worker timings, retry behavior, and
cleanup were healthy. Nginx logs allowed device-level inference for the initial rejection boundary,
but retained raw IP address, referrer, and user-agent on the submission route. The `web` and worker
containers were later recreated by a normal deployment, deleting their earlier stdout history.

The evidence exposed two operational gaps: no bounded reason exists before a search row is created,
and application/worker logs do not survive a container replacement. It also showed that raw edge
request identity is more privacy-sensitive than the diagnosis requires.

## Outcome

An operator can explain the previous 14 complete days of selfie-search outcomes without inspecting
raw selfies, bearer URLs, query vectors, filenames, EXIF, full user-agents, referrers, or IP
addresses. Each relevant Django and worker boundary emits one versioned JSON object with a bounded
schema. Container output survives an ordinary Compose replacement in the host system journal.

Once per day, the host emits one aggregate JSON report for the previous Europe/Moscow calendar day.
The report distinguishes pre-search rejection, accepted submission, selfie-face rejection, ready
with zero matches, ready with positive matches, retry/failure, and missing-event discrepancies. It
is diagnostic evidence, not product analytics or a biometric-quality benchmark.

## Success Criteria

The design succeeds when:

- every expected pre-search outcome has one bounded reason code;
- an accepted submission, worker attempt completion, ranking completion, and terminal search can be
  correlated through opaque internal UUIDs without a bearer token;
- logs from replaced `web` and worker containers remain queryable for up to 14 days, subject to an
  explicit 1 GiB host-journal safety cap;
- the previous day's aggregate report is generated automatically and is sufficient to reproduce
  the incident funnel without raw access-log identity;
- Nginx redacts the selfie submission route as well as bearer-result routes;
- schema, redaction, retention, rotation, aggregation, and deployment checks fail closed; and
- no application behavior, ranking threshold, result membership, or biometric retention changes.

## Scope

### Included

- Versioned single-line JSON events from Django and the worker.
- Exact event names, allowed fields, bounded enumerations, and redaction tests.
- A `journald` Docker logging driver for `web`, worker, and Nginx in the public Compose deployment.
- Host journal persistence with a maximum retention of 14 days and maximum disk use of 1 GiB.
- A host-side daily aggregation command and timer for the previous Moscow calendar day.
- A daily aggregate JSON event written back to the journal.
- Nginx request, referrer, user-agent, and client-address redaction for both the exact selfie
  submission route and every bearer-result route.
- Deployment preflight and post-deployment checks for logging driver, journal persistence, timer,
  retention configuration, and recent structured-event readability.
- Focused application, worker, aggregation, Nginx, and deployment-contract tests.

### Excluded

- Yandex Cloud Logging, a new log group, Unified Agent changes, or any other paid/cloud logging
  service.
- PostgreSQL audit or metrics tables.
- A dashboard, alert delivery, paging, email, Slack, or a public/admin analytics UI.
- Distributed tracing, request-wide correlation IDs, OpenTelemetry, Prometheus, Grafana, or Sentry.
- Persisting selfies, crops, query embeddings, rejected candidate vectors, EXIF, filenames, object
  keys, signed URLs, bearer tokens, IP addresses, referrers, or full user-agent strings.
- Using logs to identify a person, infer attendance, tune the cosine threshold, or evaluate false
  positives.
- Changes to upload formats, selfie guidance, face detection, ranking, model versions, thresholds,
  search states, cleanup, result authorization, or customer-facing behavior.
- Backfilling structured events for historical requests.
- Retention beyond 14 days or forwarding daily summaries outside the VM.

## Selected Design

### Structured event envelope

Every owned event is exactly one UTF-8 JSON object on one stdout line. The common envelope is:

```json
{
  "schema_version": 1,
  "event": "selfie_submission_finished",
  "occurred_at": "2026-08-04T07:15:30.123Z",
  "service": "web",
  "environment": "staging"
}
```

`schema_version`, `event`, `occurred_at`, `service`, and `environment` are required. Event-specific
fields are added at the top level. JSON keys are stable snake-case ASCII names. Values are JSON
strings, integers, booleans, or null; nested arbitrary dictionaries and exception payloads are not
allowed. One shared Django helper and one matching worker helper enforce the envelope and reject
unknown fields in tests.

The application clock supplies UTC `occurred_at`; journald supplies independent receipt time. The
daily report selects by event `occurred_at` and records late events separately rather than silently
moving them into another customer day.

### Event contracts

#### `selfie_submission_finished`

Emitted exactly once when the submission endpoint reaches an owned outcome.

| Field | Contract |
| --- | --- |
| `event_id` | Published event database ID |
| `outcome` | `accepted`, `rejected`, or `storage_unavailable` |
| `reason_code` | Empty for accepted; otherwise one allowed rejection code |
| `search_id` | Search UUID for accepted only; null otherwise |
| `actual_format` | `jpeg`, `png`, `heic`, `heif`, or `unknown` |
| `declared_type` | `jpeg`, `png`, `heic`, `heif`, `octet_stream`, `missing`, or `other` |
| `source_size_bucket` | `empty`, `le_1mib`, `le_5mib`, `le_10mib`, `le_20mib`, or `gt_20mib` |
| `duration_ms` | Non-negative bounded endpoint duration |

Allowed rejection reasons are `missing_or_empty`, `unsupported_format`, `corrupt_image`,
`source_too_large`, `normalized_too_large`, and `pixel_limit_exceeded`.
`storage_unavailable` is the only reason paired with the same-named outcome. An unexpected defect
remains an ordinary sanitized application error and does not accept an unbounded reason or raw
exception into this event.

`accepted` is emitted only after the temporary object and transactional search/job creation have
succeeded and immediately before the existing redirect. A failed request before that point cannot
claim acceptance. Compensating cleanup after a database failure remains application error evidence,
not a fabricated accepted or customer-rejected event.

#### `selfie_worker_attempt_finished`

Emitted once when one claimed selfie-query attempt reaches the worker's success or bounded failure
callback path. It replaces the selfie-query use of free-form `worker_lifecycle` terminal lines while
other processor types remain unchanged.

| Field | Contract |
| --- | --- |
| `event_id`, `search_id`, `job_id`, `attempt_id` | Opaque internal correlation identities |
| `outcome` | `succeeded` or `failed` |
| `reason_code` | Empty on success; existing bounded selfie-query worker error code on failure |
| `retryable` | Boolean matching the worker/API contract |
| `download_ms`, `compute_ms`, `total_ms` | Non-negative bounded durations or null when unavailable |

The event never contains `photo_id`, a temporary object identity, media URL, vector, model output,
input metadata, callback body, or raw exception.

#### `selfie_ranking_finished`

Emitted by Django after it has loaded and ranked the compatible cohort but before cleanup publishes
the terminal result.

| Field | Contract |
| --- | --- |
| `event_id`, `search_id`, `attempt_id` | Opaque internal correlation identities |
| `outcome` | `succeeded` or `incompatible` |
| `eligible_photo_count`, `eligible_face_count` | Non-negative cohort counts |
| `matched_photo_count` | Non-negative saved-result membership count |
| `load_ms`, `rank_ms` | Non-negative bounded phase durations or null |
| `configuration_hash` | Existing hash-only search configuration identity |

No query vector, candidate vector, matched photo identity, face identity, per-result distance, or
nearest rejected distance is logged. The event does not attempt to classify whether a zero match is
a true negative or false negative.

#### `selfie_search_terminal`

Emitted after temporary-selfie cleanup is confirmed and the durable public terminal state is
visible.

| Field | Contract |
| --- | --- |
| `event_id`, `search_id` | Opaque internal correlation identities |
| `status` | `ready`, `no_face`, `multiple_faces`, `quality_rejected`, `search_unavailable`, or `failed` |
| `matched_photo_count` | Non-negative count; positive and zero-ready remain distinguishable |
| `attempt_count` | Number of durable attempts owned by the search |
| `elapsed_ms` | Creation-to-terminal duration |
| `failure_code` | Existing bounded durable code or empty |
| `cleanup_confirmed` | Always true; any false value is a contract failure and must not be emitted |

Idempotent duplicate callbacks must not generate a second logical terminal event. Recovery that
performs the first successful cleanup and terminal publication owns the event.

### Severity

- Expected customer-correctable rejection and successful lifecycle events use `INFO`.
- `storage_unavailable`, retryable worker failure, lease expiry, aggregation discrepancy, malformed
  JSON input to the aggregator, and retention misconfiguration use `WARNING`.
- Unexpected application defects retain the existing sanitized `ERROR` path outside these bounded
  domain events.

Severity does not alter the JSON schema or authorize additional fields.

## Privacy and Edge Logging

Selfie-search observability is event diagnostics, not visitor analytics. Nginx must recognize both:

```text
/events/<event-slug>/selfie-search/
/events/<event-slug>/selfie-search/<bearer-token>/...
```

For both route families its access line must replace client address, request URI, referrer, and
user-agent with fixed placeholders. It retains only timestamp, method, route label, HTTP status,
response bytes, and request duration. Error logging for bearer paths remains suppressed/redacted as
required by the existing privacy contract. Static-asset and ordinary event-page access logging is
unchanged by this specification.

The application and worker denylist is absolute. No structured event may include:

- raw or hashed bearer token;
- selfie bytes, pixels, crop, vector, EXIF, filename, or storage identity;
- signed or unsigned URL;
- raw or hashed IP address;
- referrer, user-agent, device model, operating-system version, or social tracking parameter;
- photo or face identity in ranking results; or
- raw exception class, message, traceback, HTTP body, or third-party response.

Tests use sentinel secrets in every prohibited category and assert they are absent from captured
JSON and ordinary error output.

## Durable Local Log Transport

The public Compose deployment uses Docker's `journald` logging driver for `web`, every worker
replica, and Nginx. Stable Compose project/service tags allow the operator and aggregator to select
services after container replacement. `docker logs` remains usable for the current container, while
`journalctl` is authoritative across replacements.

The host journal is persistent under `/var/log/journal`. A managed journald drop-in sets:

- `Storage=persistent`;
- `MaxRetentionSec=14day`; and
- `SystemMaxUse=1G`.

Fourteen days is the time ceiling, not a disk guarantee: the 1 GiB safety cap may evict older data
earlier under abnormal volume. Deployment reports actual journal disk use and the oldest available
selfie event. It fails activation when persistence, the configured ceiling/cap, or service tags are
absent. It does not fail merely because a new environment has not yet accumulated 14 days.

Changing host journald configuration is an operational mutation performed only through the normal
reviewed deployment workflow. It does not change cloud resources, IAM, networking, availability, or
pricing. The deployment keeps a previous managed drop-in and restores it if validation or journald
restart fails.

## Daily Aggregate Report

A root-owned host timer runs at 00:10 Europe/Moscow and aggregates the immediately preceding Moscow
calendar day. A randomized delay is not used because the single VM has no fleet-level thundering
herd risk. The timer invokes a repository-owned script that reads only schema-version-1 JSON events
for the tagged public services from journald. It never reads Nginx identity fields or application
secrets.

The output is one `selfie_search_daily_summary` JSON event containing:

- `report_date`, `window_start`, and `window_end`;
- submission totals by outcome, rejection reason, actual format, declared type, and size bucket;
- accepted-submission count;
- terminal totals by status, including separate `ready_zero` and `ready_positive` counts;
- worker attempt success/failure/retryable counts and bounded reason counts;
- p50 and p95 for submission, download, compute, worker-total, cohort-load, ranking, and
  search-lifetime duration where samples exist;
- minimum and maximum eligible photo/face cohort counts;
- `accepted_without_terminal`, `terminal_without_accepted`, duplicate logical event, malformed
  event, unknown schema/event, and late-event counts; and
- `complete` boolean, false whenever an integrity discrepancy prevents a trustworthy funnel.

The summary contains no individual search, job, attempt, event-photo, client, or request identity.
Counts smaller than five are still allowed because the report stays in restricted host logs and
contains bounded technical outcomes rather than visitor attributes. The report retains the same
14-day bound as its journal.

Aggregation is deterministic and idempotent. Rerunning a date computes the same content and marks
the new journal event with `recomputed=true`; consumers select the latest generated summary for a
date. A day with no events emits a complete zero-valued report. A parser error or unknown schema is
reported and makes the summary incomplete rather than silently dropping input.

## Failure Semantics

- A structured logging failure must not reject a customer request, change a search transition, or
  prevent selfie cleanup. The ordinary sanitized error path records that observability failed.
- An invalid event payload is never emitted partially. Tests and typed constructors prevent owned
  call sites from supplying unknown or unbounded values.
- Journald unavailability leaves the application running through Docker's logging behavior, but
  deployment validation fails and operators receive a sanitized diagnostic.
- Daily aggregation failure does not mutate product data. The timer remains failed and queryable;
  the next run may recompute the missing date explicitly.
- Journal retention below 14 days because the 1 GiB cap is reached is a warning and makes affected
  historical coverage explicit. The design does not automatically increase disk use.
- Missing accepted/terminal counterparts do not fabricate events from assumptions. The report
  exposes the discrepancy and sets `complete=false`.
- Existing durable PostgreSQL records remain the authority for product state. Logs are operational
  evidence and may be incomplete.

## Deployment and Operation

The normal deployment owns the Compose logging configuration, managed journald drop-in, aggregation
script, and systemd service/timer. Deployment must:

1. validate the candidate files and expected immutable values before host mutation;
2. install or reconcile the journald drop-in and aggregator units with root ownership and
   non-secret permissions;
3. restart journald only when the validated managed configuration changed;
4. deploy the normal application containers with stable journald tags;
5. prove the services use `journald`, emit and retrieve a bounded non-secret probe event, and keep
   health checks green;
6. run the aggregator against a bounded fixture window without touching product state; and
7. restore the prior managed logging files and service state if reconciliation fails.

No cloud resource, log group, service account, secret, firewall rule, or paid capability is created.
The feature follows the same staging-first and promotion workflow as the application deployment.

## Acceptance Criteria

1. Each of the four domain events serializes as one schema-version-1 JSON line with exactly its
   allowed fields and bounded values.
2. Expected form validation and temporary-storage outcomes emit one correctly classified
   `selfie_submission_finished` event; accepted submission includes only the opaque search ID.
3. Successful, bounded-failure, retry, duplicate-callback, cleanup-recovery, and terminal paths
   emit the correct worker/ranking/terminal events without duplicate logical outcomes.
4. Captured logs contain none of the prohibited selfie, token, URL, storage, client, result-member,
   vector, or exception sentinels.
5. The exact submission URL and every bearer-result URL use the redacted Nginx log shape, including
   on 4xx, 5xx, upstream failure, and request-body buffering warnings; ordinary event routes retain
   their existing access behavior.
6. Replacing `web` and worker containers preserves their earlier structured events in journald;
   service tags still distinguish web, worker, and Nginx.
7. Deployment verifies persistent journal storage, `MaxRetentionSec=14day`, `SystemMaxUse=1G`,
   timer activation, structured probe readability, and application health, with a tested rollback
   for managed host files.
8. A deterministic fixture containing accepted, rejected, zero-match, positive-match, retried,
   malformed, duplicate, late, and missing-counterpart events produces the exact expected daily
   summary and completeness flag.
9. A zero-traffic day emits a complete zero-valued summary; an aggregation failure does not modify
   application or database state and can be recomputed explicitly.
10. Existing selfie-search state, ranking, cleanup, bearer authorization, gallery behavior, and
    worker no-credential tests remain unchanged and passing.

## Rejected Alternatives

### Keep Docker stdout only

This preserves the current blind spot: normal container replacement deletes the evidence needed to
analyze the previous day.

### Persist operational events in PostgreSQL

A new audit table would turn diagnostics into additional durable product-adjacent data, require
migrations and cleanup, and still not naturally capture pre-search rejection. PostgreSQL remains
the product-state authority, not the log sink.

### Use Yandex Cloud Logging now

Central logging would improve availability across VM loss, but introduces a cloud resource,
credentials, pricing, retention configuration, and rollout surface beyond the requested critical
path. The versioned JSON schema allows later forwarding without changing event producers.

### Retain or hash IP and full user-agent

The incident needed format/reason evidence, not durable visitor identity. Hashing remains linkable
and requires salt management. Bounded actual-format and declared-type fields provide the necessary
diagnosis with less privacy risk.

### Log nearest rejected cosine distance

An unlabelled distance cannot distinguish a true miss from another person's face and therefore
cannot safely select a new threshold. It adds sensitive per-search evidence without resolving the
quality question. Threshold evaluation belongs in a consented labelled benchmark.

### Add dashboards and alerts in the same increment

The daily summary and 14-day queryable events establish a reliable evidence layer first. External
delivery, visualization, and alert thresholds can be added only after actual event volume and
operator response needs are measured.
