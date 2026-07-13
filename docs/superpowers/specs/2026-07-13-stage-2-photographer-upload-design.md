# Stage 2 Photographer Upload Design

- Date: 2026-07-13
- Status: Approved design
- Owner: project maintainer
- Related roadmap: [MVP product roadmap](../../plans/2026-07-11-mvp-product-roadmap.md#этап-2-доступ-фотографов-и-простая-загрузка)
- Related architecture: [Target MVP architecture](../../architecture.md#target-mvp-architecture--proposed)
- Related ADRs: [0001](../../adr/0001-django-modular-monolith.md), [0002](../../adr/0002-postgresql-system-of-record.md), [0006](../../adr/0006-yandex-object-storage-media.md)

## Outcome

An authorized photographer selects any event and uploads up to 10,000 print-quality JPEG files from
one browser page. The page maintains a bounded parallel queue and uploads each original directly to
private Yandex Object Storage. Django owns authorization, batch and item state, verification, and
photo metadata without proxying the media bytes through the application VM.

## Current-state audit

The audit was performed against `origin/main` at commit `71574cd`.

### Already implemented

- [x] Stage 1 event catalog, including free/paid access type and publication state.
- [x] Django authentication, sessions, and authentication middleware.
- [x] Django Admin for events and users.
- [x] ADR 0006 selects Yandex Object Storage, a separate private bucket for originals, immutable
      object keys, and PostgreSQL metadata.
- [x] S3 dependencies and environment-selected storage exist for public event covers.
- [x] A test-only upload screen exists in the visual reference gallery. It is not a production
      route or working uploader.

### Not implemented

- [ ] Photographer group and upload permission.
- [ ] Photographer login/logout and production upload routes.
- [ ] Private bucket configuration, signing, CORS, and least-privilege access.
- [ ] Upload batch and item state.
- [ ] Direct browser-to-S3 uploads and bounded client-side concurrency.
- [ ] Private-original metadata on the target photo model.
- [ ] Retry, partial-batch, S3 verification, and cleanup behavior.
- [ ] Permission, security, storage-contract, browser-flow, and visual tests.

## Scope

### In scope

- Django session authentication and an additive photographer permission.
- A production upload page available to authorized photographers and administrators with the same
  permission.
- Selection of any event, regardless of its publication state.
- Selection of up to 10,000 JPEG files, each no larger than 50 MB.
- A page-lifetime upload queue with four concurrent transfers by default.
- Short-lived upload authorization issued in bounded groups.
- Direct upload to a private Yandex Object Storage bucket.
- Durable batch and per-file state, partial success, automatic retry, and manual retry while the
  page remains open.
- Verification of uploaded objects before creating authoritative photo metadata.
- Safe compatibility with preliminary `Photo` rows and their event relationships.

### Out of scope

- ZIP archives.
- A desktop or CLI uploader.
- Queue restoration after a tab closes, a page reloads, or the browser restarts.
- Background task infrastructure, Celery, a broker, or an ingestion worker.
- Image decoding, EXIF extraction, derivative generation, publication, galleries, and recognition.
- Photographer-to-event assignments.
- Public original access or downloads.
- Automatic deletion of successfully verified originals.
- Global duplicate detection across independent upload batches.

## Considered approaches

### Direct per-file browser upload to private Object Storage — selected

The browser transfers media directly to Object Storage using short-lived authorization issued by
Django. A single page controls concurrency and reports completion to Django. This avoids making the
application VM a bandwidth bottleneck and confines retry to one 10–50 MB file.

### Proxy every file through Django — rejected

This is conceptually simple but sends 50–500 GB per shoot through Nginx, Gunicorn, and the
application VM. It duplicates network and disk pressure, expands timeout and request-size concerns,
and makes the current single VM the ingestion bottleneck.

### Upload one ZIP archive and unpack it on the server — rejected for this stage

JPEG files gain little from ZIP compression. A 50–500 GB ZIP64 archive requires multipart resume,
large temporary disk or seekable range reads, a background worker, archive-bomb defenses, and
another copy into per-photo objects. Failure also operates at archive scale instead of file scale.

## Architecture

```text
Photographer browser ---- control requests ----> Django ----> PostgreSQL
         |                                         |
         |                                         `---- short-lived upload authorization
         |
         `-------------- JPEG bytes ------------> private Yandex Object Storage
                                                        ^
                                                        |
                                      Django verifies with S3 HEAD
```

Django remains the owner of users, permissions, events, upload state, photo records, and object-key
generation. Object Storage accepts only the authorized write and never becomes the source of
product truth. No background service or task broker is introduced.

Object keys are random and immutable. A key necessarily appears inside its short-lived signed
upload request, but it is not rendered into public HTML, returned by read APIs, or treated as an
authorization secret. The private bucket remains the read-access boundary.

## Roles and authorization

- `Photographer` is a Django group with a dedicated upload permission.
- Group membership is independent of `is_staff`; a staff administrator can also be a photographer.
- Active superusers receive the upload permission through Django's normal superuser semantics.
- A non-staff photographer cannot enter Django Admin.
- A staff user without the upload permission cannot use the photographer uploader.
- An authenticated user with the permission may select every event, including draft events.
- A photographer may read and mutate only their own upload batches and items.
- Superusers may inspect every batch through Django Admin.
- Anonymous requests redirect to the photographer login page; authenticated requests without the
  permission return HTTP 403.
- The MVP has no public registration, password reset, or photographer-to-event assignment flow.

The authentication and permission boundary requires an ADR before implementation changes rely on
it as accepted architecture.

## Components and interfaces

### Ingestion Django module

The `ingestion` module owns upload batches, upload items, control endpoints, S3 authorization,
verification, and state transitions. It depends on the existing event and photo domain but exposes
no media read endpoint.

Its application service interface covers:

- create a batch for an event and authorized user;
- register a bounded group of selected-file metadata;
- issue or refresh short-lived upload authorization for pending items;
- confirm an item by checking its expected private object with S3 `HEAD`;
- mark an item failed with a stable public error code;
- derive the batch state from its item states;
- delete an unverified or mismatched object best-effort.

### Browser upload coordinator

A production JavaScript module owns the in-memory queue. It:

- registers selected files in bounded groups of 100;
- requests authorization just in time rather than pre-signing all 10,000 files;
- runs four uploads concurrently by default;
- displays total and per-file progress;
- retries transient failures up to three times with backoff;
- obtains fresh authorization when a grant expires;
- reuses the existing item identity and object key for automatic or manual retry;
- warns before leaving while transfers are active;
- finalizes the batch when every in-memory item reaches a terminal state.

It does not persist `File` objects, queue state, or credentials in local storage or IndexedDB.

### Private storage adapter

The adapter generates immutable opaque keys, short-lived upload authorization, `HEAD` verification,
and deletion for rejected objects. AWS-compatible credentials remain server-side. The private bucket
requires CORS limited to the deployed application origins and only the methods and headers required
by the chosen signed-upload mechanism.

The signing mechanism, grant lifetime, CORS contract, private-key exposure boundary, and successful
original retention require an ADR before production implementation.

## Data model

### `UploadBatch`

- UUID primary key.
- Required event foreign key with protective deletion behavior.
- Required uploader foreign key with protective deletion behavior.
- Status: `created`, `uploading`, `completed`, or `partial`.
- Created and completed timestamps.

Counts are derived from indexed item states rather than updated through contended counters while
four uploads complete concurrently.

### `UploadItem`

- UUID primary key and required batch foreign key.
- Original filename, declared content type, expected byte size, and immutable object key.
- Status: `pending`, `uploading`, `uploaded`, or `failed`.
- Stable public error code and sanitized error message.
- Upload-attempt and completion timestamps.
- Nullable one-to-one relationship to the resulting photo record.

The item UUID is the idempotency key. Retrying an item reuses the same database row and object key;
it cannot create a second photo record.

### `Photo`

The preliminary model gains uploader, private original key, original filename, byte size, content
type, and upload timestamp while preserving existing primary keys and `event_id` relationships.
New photos use generated 32-character UUID hex identifiers compatible with the current primary-key
shape.

The legacy `src` field remains compatible during this stage. Its removal or final data conversion
requires later inventory and a separate expand-and-contract cleanup after the new path is proven.

## Upload flow

1. An authorized user opens the uploader and selects an event.
2. The browser creates an `UploadBatch` and receives only its public UUID and control endpoints.
3. The user selects files. Client validation rejects more than 10,000 files, non-JPEG selections,
   and any file larger than 50 MB before registration.
4. The browser registers metadata for up to 100 items per control request.
5. The browser requests upload authorization for the next small queue window.
6. Up to four files upload directly to the private bucket.
7. After Object Storage reports success, the browser asks Django to confirm that item.
8. Django verifies that the expected object exists and its size and required metadata match.
9. Django creates the photo metadata and marks the item `uploaded` in one database transaction.
10. Once all selected items are terminal, Django marks the batch `completed` when all succeeded or
    `partial` when at least one failed.

The browser may send file metadata that is inaccurate or malicious. S3 verification, authorization
ownership, server-generated keys, signed constraints, and later image decoding remain server-side
trust boundaries.

## Failure and lifecycle behavior

- A transient network or Object Storage failure is retried up to three times with increasing delay.
- An expired grant is refreshed without incrementing the file's data-failure count.
- A terminal item exposes a stable, localized error category and a manual retry action.
- A retry reuses the same `UploadItem` and object key. A completed item cannot be uploaded again.
- A failed `HEAD`, size mismatch, or metadata mismatch never creates `Photo`; the unexpected object
  is deleted best-effort and the failure is recorded.
- Database failure after object upload leaves the item confirmable with the same object key, so the
  confirmation request is idempotent.
- Closing or reloading the page stops active requests. Confirmed photos remain valid and the batch
  remains `uploading`; no automatic browser recovery is promised.
- Successfully verified originals have no automatic deletion in this stage.
- Failed and unverified objects are deleted immediately when possible and by an idempotent cleanup
  management command when immediate deletion fails.
- Error responses and logs never contain credentials, signed URLs, or private object keys.

## User interface

The production screen reuses the shared Django design system and promotes the upload concept from
the test-only visual gallery. It contains:

- event selection;
- a multi-file picker and drop target;
- total file count, uploaded count, failed count, bytes, and overall progress;
- a virtualized or bounded-render queue so 10,000 rows do not freeze the page;
- per-file progress and error state;
- manual retry for failed items;
- a clear message that closing the page stops unfinished uploads;
- completed and partial summary states.

The premature zone, photographer selector, recognition details, and processing claims in the
reference-only upload concept are not promoted. The working user is always the recorded uploader,
and recognition remains outside this stage.

## Security

- All Django control requests use session authentication, CSRF protection, and the upload
  permission.
- Every batch and item lookup is scoped to the current uploader unless the caller is a superuser.
- The server generates object keys; filenames never become key paths.
- Signed grants expire quickly and authorize one expected object and bounded upload.
- Private-bucket read and list access are never granted to the browser.
- Public templates, public endpoints, error bodies, and logs do not disclose object keys or signed
  URLs.
- Content-type and size constraints are applied at selection, authorization, and confirmation.
- The object remains private even when later content validation rejects it.

## Automated test strategy

### Django tests

- Permission matrix for anonymous, authenticated non-photographer, non-staff photographer,
  staff-plus-photographer, staff without the permission, and superuser.
- All-event selection for authorized uploaders, including draft events.
- Ownership checks that reject batch and item UUID substitution.
- Model constraints, state transitions, idempotent confirmation, partial batches, and preservation
  of preliminary photo relationships.
- Registration limits for file count, size, filename, and allowed content type.
- S3 adapter tests for signing, refresh, `HEAD`, mismatch, deletion, and sanitized failures.
- Security assertions that read responses and public pages do not expose keys or signed URLs.

### Browser and visual tests

- The coordinator never exceeds four active transfers.
- It registers and signs bounded groups rather than loading 10,000 grants at once.
- Retry reuses the same item and refreshes an expired grant.
- Closing protection is active only while transfers remain.
- End-to-end mocked-storage scenarios cover full success and partial failure.
- Production visual snapshots cover empty selection, active progress, partial failure, completion,
  and the mobile layout.
- Promotion removes the obsolete reference-only upload route, fixture data, and snapshots and marks
  the production screen in the canonical inventory.

### Storage contract and staging verification

- A contract test against the selected S3-compatible test service verifies upload authorization,
  CORS, successful upload, `HEAD`, rejection, and deletion.
- Staging verifies one realistic multi-file batch without exposing credentials or private reads.
- A bounded load test uses generated payloads and metadata to exercise 10,000 queue items without
  storing 500 GB in CI.

## Operational impact

- Provision a separate private bucket and least-privilege service credentials before enabling the
  feature. Pricing-affecting Yandex Cloud actions require explicit manual confirmation.
- Configure application origins in private-bucket CORS.
- Add environment variables for private bucket name, signing lifetime, maximum files, maximum file
  bytes, registration group size, and browser concurrency.
- Monitor upload authorization failures, confirmation failures, cleanup failures, batch age, and
  private-bucket consumption.
- Do not increase Nginx request-body limits for media; control requests remain small.

## ADR gates

Implementation must not begin until maintainers accept decisions covering:

1. Django session authentication, additive `Photographer` permission, administrator role
   composition, all-event visibility, and batch ownership.
2. Direct browser upload to private Object Storage, short-lived signed grants, key-exposure
   boundaries, CORS, successful-original retention, and cleanup of rejected objects.
3. Request-driven ingestion without a worker or broker in Stage 2, with asynchronous image
   processing deferred to later roadmap stages.

## Acceptance criteria

- An authorized photographer or administrator with upload permission selects any event and uploads
  a multi-file JPEG batch from one browser page.
- The page sustains four concurrent direct-to-private-storage transfers without routing media bytes
  through Django.
- Successful items create exactly one photo record; transient failures are retryable without
  duplicating a record or object key.
- Partial failure preserves successful items and produces an observable `partial` batch.
- Closing the page stops unfinished work without corrupting completed items; automatic resume is not
  promised.
- Anonymous, unauthorized, cross-user, and public-read access is denied.
- No public/read response exposes a private object key or a signed upload URL.
- Existing event and preliminary photo relationships remain valid through deployment.

