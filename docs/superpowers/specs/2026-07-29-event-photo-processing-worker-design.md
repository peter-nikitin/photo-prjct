# Event Photo Processing Worker Design

## Status

Approved section by section in conversation on 2026-07-29. Written specification awaiting final
user review.

- Related architecture: [`docs/architecture.md`](../../architecture.md), proposed Media,
  Recognition, and Operations modules; photo ingestion and indexing flow; evolution stage 4; and
  the open Stage 3 background-processing decision
- Related ADRs:
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md), and
  [ADR 0014](../../adr/0014-keep-stage-2-ingestion-request-driven.md)
- ADR impact: Requires new ADR — the first Stage 3 worker boundary, Django polling API,
  PostgreSQL-backed job dispatch, lease/retry semantics, and per-object temporary download access
  are durable architecture choices not governed by an accepted ADR
- Implementation plan: not written; planning starts only after final specification approval and
  ADR resolution is included in the planning input

## Goal

Deliver the first working background photo-processing pipeline. After confirmed photos become
eligible, a separately runnable worker claims work from Django, downloads one photo through
temporary per-object authorization, performs a small deterministic computation, returns a
structured result, and lets Django persist the exact photo state and an immutable processing
history.

The first processor extracts capture time from JPEG metadata. It proves the distributed processing
contract without integrating a face detector, embedding model, vector search, or production
recognition logic. The same worker boundary must allow the current isolated face-processing code to
be integrated later without granting the worker permanent Object Storage credentials or database
access.

## Outcome

The completed increment provides observable evidence that:

- every eligible photo has an explicit processing state;
- a worker process can run independently from the Django web process;
- the worker can claim a job, obtain temporary access to one original, process it, and return a
  result;
- retries, worker disappearance, duplicate requests, and stale results do not corrupt current
  state;
- every completed attempt remains available as immutable evidence;
- a small immutable report shows what happened for one event and how long it took; and
- deployment to the current VM remains disabled until measured resource use supports a deliberate
  VM and concurrency choice.

## Scope

### Included

- A Django-owned, PostgreSQL-backed queue for photo-processing jobs.
- A private worker HTTP API for claiming work, refreshing the current download authorization,
  renewing a lease, and returning success or failure.
- A separately packaged and runnable worker container with no Django ORM or application database
  connection.
- Short-lived, read-only authorization for exactly one private original at a time.
- A versioned processing contract and a first `capture_metadata` processor.
- JPEG decoding sufficient to inspect EXIF capture-time fields without applying ML.
- Explicit per-photo processing state, immutable attempts, and immutable event-scoped runs.
- Bounded retries, stale-attempt handling, idempotent completion, and stable error codes.
- Minimal event-run reporting: counts, durations, configuration, and per-photo outcomes.
- Local container verification against real representative JPEGs.
- A deployment gate requiring resource measurements before worker activation on a real VM.

### Excluded

- Face detection, face crops, embeddings, clustering, vector indexing, and selfie search.
- Importing the isolated YuNet/SFace experiment into production code.
- Redis, RabbitMQ, Celery, or another external broker.
- A monitoring stack, dashboards, alerting, trend analysis, or automated anomaly detection.
- Claims about recognition accuracy or event quality based only on technical pipeline metrics.
- Automatic VM resizing, cloud infrastructure changes, or worker activation on staging or
  production.
- Operator UI for retrying jobs or browsing reports; Django Admin access may be added only if it
  does not widen the processing contract.
- Arbitrary image formats, metadata correction, derivative generation, or rewriting originals.
- Deletion or retention policy for future biometric results. Those require face-governance design
  before ML integration.

## Selected Architecture

Django is the trusted processing control plane. It owns product eligibility, job creation,
PostgreSQL state transitions, S3 authorization, result validation, and the selection of the current
accepted result. PostgreSQL is the durable queue and system of record.

The worker is a separate container and process. It polls Django over a private HTTP API and may run
on the same VM initially, but its interface does not depend on shared disk, a shared process,
Docker-socket access, or colocated networking. It has no PostgreSQL connection string, Django
secret key, permanent Object Storage credential, bucket-wide permission, or access to application
administration endpoints.

For the first increment, a dedicated broker would duplicate durability already available through
Django and PostgreSQL while still leaving the media-access and result-persistence boundary to
solve. HTTP polling is therefore selected as the shortest route to a working pipeline. The worker
protocol is transport-focused and versioned so a later broker can trigger the same claim and result
contract without changing processor semantics.

### Rejected alternatives

#### Redis or RabbitMQ with Celery

This provides established delivery and retry machinery, but adds another deployed stateful
component, worker framework configuration, and operational recovery surface before throughput
requires them. It does not remove the need for narrow media access or Django-owned result
persistence. Reconsider it when measured polling load, scheduling needs, or worker scale exceed the
PostgreSQL-backed design.

#### Django streams every original to the worker

This keeps all direct Object Storage access away from the worker, but makes Gunicorn carry
large-file data transfer and requires worker-specific streaming, timeout, and retry behavior.
Short-lived per-object access is simpler for the first pipeline.

#### Separate media gateway

A gateway could isolate Object Storage access without loading Django, but creates another service
before that separation has operational value. It remains a future option if temporary direct
Object Storage access becomes unacceptable.

## Trust and Access Boundary

The worker has one environment-provided credential that authorizes only the private worker API. It
does not reuse a user session, staff credential, Django secret, deployment credential, or Object
Storage credential.

The worker credential permits only:

- claiming one compatible job;
- refreshing download authorization for the attempt currently leased to that worker;
- renewing that attempt's lease; and
- completing or failing that attempt.

Django creates a short-lived read-only URL for the exact original belonging to the claimed job.
The URL is created when work is claimed, not when the job is queued, so queue delay does not consume
its useful lifetime. Django may issue a replacement only while the same attempt has a valid lease.
The worker never receives permanent bucket credentials, list permission, write permission, another
photo's key, or authorization that outlives the bounded attempt workflow.

Temporary direct read access to one object is an intentional relaxation of complete S3 network
isolation. Permanent S3 credentials remain forbidden. Logs, database fields, attempts, reports,
and error messages must never persist the signed URL, its query string, the worker credential, or
other secrets. The worker must redact URLs before structured logging.

The worker API is not public product API. On a shared VM it is reachable only through the intended
private service path. Moving the worker to another host requires a separately approved network and
transport-security configuration; the bearer credential alone is not treated as sufficient
Internet exposure protection.

## Processor Contract

Every job declares:

- a stable `job_id` and unique `attempt_id`;
- `contract_version`;
- `processor_type`;
- `processor_version`;
- normalized processor configuration;
- source `photo_id` and `event_id`;
- an input fingerprint containing immutable object identity/version evidence, byte size, and
  content type;
- lease expiration and heartbeat timing;
- the short-lived download URL; and
- bounded input limits required before decoding.

The worker returns:

- the same job, attempt, contract, and processor identifiers;
- worker build/image version;
- start and finish timestamps;
- download and computation durations;
- a typed outcome;
- structured processor output;
- warning codes; and
- on failure, a stable error code, retryability classification, and sanitized detail.

Django rejects incompatible contract or processor versions before granting work. Result payloads
are size-bounded and schema-validated. The worker cannot ask Django to write arbitrary model fields;
Django maps an accepted typed result into application-owned fields.

### First processor: `capture_metadata`

The processor reads a bounded JPEG and inspects EXIF capture-time metadata. Its typed output
contains:

- nullable normalized `capture_time`;
- the source EXIF field when a value was selected;
- timezone state: `explicit`, `inferred_none`, or `not_applicable`;
- the unmodified source value only when it is safe, bounded, and useful for later diagnosis; and
- stable warning codes for missing, conflicting, or malformed metadata.

The processor applies one deterministic precedence rule to the supported EXIF date fields and
records the selected source. The exact supported fields and normalization rule are part of the
versioned processor configuration and cannot change without a new processor version.

A valid supported JPEG with no capture time completes successfully with `capture_time = null` and
a `capture_time_missing` warning. Missing metadata is a domain result, not an infrastructure
failure. An unsupported file, a file exceeding declared limits, a fingerprint mismatch, or an
undecodable JPEG produces a stable permanent failure.

## Processing Flow

1. Photo creation explicitly creates its `not_requested` processor-state row.
2. When Django marks a confirmed photo eligible according to product-owned rules, it enrolls that
   photo into the event's current `collecting` run and creates its job in one transactional
   operation. If no compatible collecting run exists, Django creates one.
3. A reconciliation operation can enroll eligible unassigned photos and create missing jobs after
   interrupted ingestion or deployment. It is idempotent and does not inspect derived fields to
   infer prior processing.
4. The worker polls the claim endpoint for a compatible processor contract.
5. Before returning the first job from a collecting run, Django atomically seals its exact cohort.
   Photos becoming eligible afterward enter a later collecting run.
6. Django atomically locks one claimable job, creates an in-progress attempt, assigns a bounded
   lease, changes current state to `processing`, and returns temporary download authorization.
7. The worker downloads the exact object, verifies the declared limits and available fingerprint
   evidence, performs the processor computation, and releases decoded image data before claiming
   another job.
8. The worker may heartbeat while processing or refresh the download URL while the attempt remains
   current and leased.
9. The worker submits one typed success or failure result.
10. Django records the terminal attempt payload, validates that the attempt is still current, and
   either advances the photo state or records the response as stale.
11. When every run member reaches a terminal state, Django closes the event run and creates its
   immutable report.

Polling with no available work returns an explicit empty response and a server-suggested minimum
delay. The worker uses bounded backoff and jitter so an idle worker cannot create a tight request
loop.

## Exact Photo State

Every eligible photo has one explicit `PhotoProcessingState` for each processor type selected for
the current pipeline generation. Absence of a face, embedding, EXIF field, attempt, or derived value
is never interpreted as processing status.

The state vocabulary is:

- `not_requested`: the photo exists but this processor has not been requested;
- `queued`: durable work is ready to be claimed;
- `processing`: a current attempt owns a valid lease;
- `retry_wait`: a retryable failure occurred and the next eligible time is explicit;
- `succeeded`: a typed result was accepted;
- `failed`: no automatic retry remains or the failure is permanent; and
- `cancelled`: processing was explicitly stopped.

State rows record the current event run, current job, current attempt when applicable, accepted
attempt when successful, next retry time when applicable, and transition timestamps.

State changes occur only through explicit transactional transitions. A lease timeout does not make
the read model silently reinterpret `processing` as another state. A recovery operation records the
expired attempt and transitions the state to `queued`, `retry_wait`, or `failed`.

## Jobs, Attempts, and Idempotency

A processing job represents one requested processor version and configuration for one photo in one
event run. Its idempotency identity prevents duplicate active jobs for that exact combination.

Every claim creates a distinct `ProcessingAttempt`. An attempt records:

- photo, event, run, job, and attempt identifiers;
- processor contract, version, and normalized configuration;
- input fingerprint;
- worker build identity;
- creation, claim, heartbeat, lease, and terminal timestamps;
- sanitized request-independent result or error payload;
- download, compute, and total durations; and
- whether the response became the accepted current result.

An in-progress attempt may receive only lease/heartbeat fields. Once terminal, its evidence fields
are immutable. A new attempt never overwrites an earlier attempt. A new processor version creates a
new job and history instead of rewriting the previous version's result.

Completion is idempotent by `attempt_id`: repeating an identical completion returns the recorded
outcome. A conflicting second payload is rejected and audited. A response arriving after its lease
was recovered is retained as a stale terminal attempt when safe to do so but cannot alter the
current job, photo state, accepted result, or event-run report.

## Retry and Failure Semantics

Retryable failures include bounded network interruption, temporary Django or Object Storage
unavailability, HTTP 5xx responses, and download authorization expiring during an otherwise valid
lease. Unsupported input, violated size/type limits, fingerprint mismatch, invalid result schema,
and deterministic decode failure are permanent for the current processor version.

Automatic retries use configured bounded exponential backoff with jitter and a configured maximum
attempt count. The current state exposes the next retry time and terminal failure rather than
hiding them in logs. Expired leases consume and close an attempt. Exhausting retries changes the
state to `failed`.

An operator-triggered retry creates a new event run and job or an explicitly linked retry job. It
does not reopen, delete, or mutate a closed event run or terminal attempt.

## Immutable Event-Scoped Runs and Reports

The reporting unit is:

`event × processor type × processor version × normalized configuration × exact photo cohort`

An `EventProcessingRun` belongs to exactly one event. While its state is `collecting`, eligible
photos may be enrolled transactionally. The first successful claim changes the run to `sealed`,
freezes its manifest of photo IDs and input fingerprints, and prevents further enrollment. Photos
becoming eligible afterward enter a later collecting run and never change a sealed or closed run.

While processing is active, current counts may be presented as a mutable projection. When every
member reaches a terminal state, the run closes and its report becomes immutable. Django stores the
report as a bounded JSON snapshot linked to the normalized run, membership, job, and attempt rows;
it is not a mutable file on worker-local disk. The first report contains only:

- event and run identifiers;
- processor contract, version, normalized configuration, and worker build versions observed;
- exact cohort size;
- counts of `succeeded`, `failed`, and `cancelled`;
- count of successful photos with and without capture time;
- total attempt and retry counts;
- run start, finish, and total duration;
- minimum, median, and maximum accepted per-photo total duration; and
- one bounded row per photo with final status, accepted attempt, capture-time presence, attempt
  count, duration, warnings, and stable error code.

Counts always include their denominator. Reports do not include signed URLs, credentials, original
image bytes, EXIF blobs, embeddings, or unbounded exception text.

The first increment includes no charts, alerts, percentile suite, trend engine, cross-event
dashboard, or automatic degradation verdict. Event ownership and normalized counts preserve the
minimum evidence needed to see what processed and how long it took, and allow later analytics to
compare events without changing historical reports.

Technical success rates are not recognition-accuracy metrics. Future face-processing reports may
add versioned measures such as zero-face, detected-face, quality-rejected, and embedding-success
rates. Accuracy or quality claims require labelled evaluation data and a separately approved
contract.

## Concurrency and Resource Bounds

The first worker defaults to one concurrent job. Concurrency, polling interval, lease duration,
heartbeat interval, maximum attempts, maximum response size, maximum input bytes, and image decode
limits are explicit configuration recorded in the event run.

The worker processes one image in bounded memory, does not retain decoded pixel arrays across jobs,
and never persists originals to a durable shared directory. Any temporary file is scoped to one
attempt and removed after success or failure. A process restart may abandon an attempt; lease
recovery supplies durability.

The Django claim transaction is short and does not include S3 access or image transfer. Network
downloads and computation happen outside database locks.

## Deployment Boundary and VM Capacity Gate

The worker ships as a separately runnable container or Compose service, disabled by default in
real environments. Sharing the current VM for the first activation is allowed, but the present
smallest VM configuration is not assumed sufficient.

Before staging or production activation, a representative event cohort must be processed with
worker concurrency `1` while measuring:

- worker peak and steady memory;
- total VM memory headroom alongside Django, PostgreSQL, Nginx, and deployment services;
- worker CPU utilization and effect on web-request latency;
- download throughput and photos processed per minute;
- event-run wall-clock duration;
- temporary disk use and free space; and
- failure and retry counts under the measured limits.

The evidence must support an explicit choice of VM CPU/RAM, worker concurrency, and container
resource limits. If the existing VM lacks safe headroom, activation requires resizing or moving the
worker rather than relying on swap pressure or unbounded contention.

Local completion of the working pipeline does not authorize staging or production activation.
Cloud mutation, VM selection, rollout, and operational monitoring belong to later planning and
approval.

## Observability Boundary

Structured logs identify event, run, photo, job, and attempt through opaque identifiers and record
state transitions, durations, and stable codes. They exclude credentials, signed query strings,
EXIF blobs, image content, and unbounded exception text.

The exact database state and immutable run report are the first operational evidence. Metrics
exporters, dashboards, alerts, tracing, and long-term monitoring are deferred until the pipeline
works and the ML processor is integrated.

## Compatibility and Evolution

- Processor and worker API contracts are versioned independently from worker builds.
- Django may support more than one contract version during a bounded rollout.
- Changing EXIF precedence, normalization, result schema, or processor behavior requires a new
  processor version.
- A future broker may replace polling while preserving job, attempt, processor, and result
  semantics.
- A future ML runtime may implement the same worker-side processor interface without receiving
  database or permanent Object Storage credentials.
- A vector index remains derived state. PostgreSQL continues to own processing truth and accepted
  result metadata.
- Existing Stage 2 request-driven ingestion remains unchanged; no upload confirmation waits for
  this worker.

## Privacy and Security

The first processor stores only bounded metadata required by its result contract. It does not store
image bytes, crops, faces, or embeddings.

This worker foundation does not authorize biometric processing. Before production face detection
or embedding storage, a later specification must define collection basis, access control,
retention, deletion, suppression, incident handling, model licensing, and event-scoped search
behavior.

## Acceptance Criteria

1. Every eligible fixture photo exposes exactly one explicit current state for
   `capture_metadata`; no test or application path infers processing status from a nullable result.
2. The independently runnable worker has no database configuration or permanent Object Storage
   credential and communicates only through the private worker API plus a temporary exact-object
   download URL.
3. A real JPEG job can be created, claimed, downloaded, processed, completed, and reflected as
   `succeeded` with a typed capture-time result.
4. A valid JPEG without supported capture-time metadata succeeds with a null capture time and a
   stable warning.
5. Temporary download authorization is created at claim time, can be refreshed only for the
   current leased attempt, and is never persisted or logged.
6. Duplicate claim and completion requests, an expired lease, worker termination, temporary
   download failure, permanent decode failure, and a stale completion produce the specified
   explicit states without duplicate accepted results.
7. Terminal attempts remain immutable across retries and processor-version changes.
8. One closed event run has a sealed exact photo cohort and an immutable minimal report containing
   the agreed counts, durations, configuration, and bounded per-photo outcomes.
9. Adding photos after run closure creates a later event run and leaves the closed report
   unchanged.
10. The worker defaults to concurrency `1`, respects configured byte/decode/result bounds, releases
    per-image resources, and cannot hold a database transaction while downloading or processing.
11. The local container pipeline passes its contract, state-machine, idempotency, retry,
    authorization-redaction, immutable-report, and real-JPEG end-to-end checks.
12. Worker activation remains disabled in real environments until the VM capacity measurements
    listed in this specification have been reviewed and explicit VM, concurrency, and container
    limit choices have been made.

## Architecture Reconciliation

The design conforms to:

- ADR 0001 by keeping Django responsible for product and transactional rules while extracting only
  the specialized worker runtime behind an explicit interface;
- ADR 0002 by keeping PostgreSQL authoritative and treating later indexes as derived state;
- ADR 0006 by retaining private originals in Object Storage and PostgreSQL metadata ownership;
- ADR 0013 by leaving direct upload and immutable final-object ingestion boundaries unchanged; and
- ADR 0014 by leaving Stage 2 upload confirmation request-driven and introducing the worker only
  for the deferred Stage 3 processing work.

It also selects architecture that `docs/architecture.md` and ADR 0014 deliberately left open:
worker dispatch, polling transport, temporary download access, leases, retries, and processing
evidence. An accepted new ADR is therefore required before implementation relies on these choices.
The ADR must not imply approval of face processing, vector storage, real-VM capacity, or production
activation.
