# Stage 2 Photographer Upload Design

- Date: 2026-07-13
- Status: Approved design
- Owner: project maintainer
- Related roadmap: [MVP product roadmap](../../plans/2026-07-11-mvp-product-roadmap.md#этап-2-доступ-фотографов-и-простая-загрузка)
- Related architecture: [Target MVP architecture](../../architecture.md#target-mvp-architecture--proposed)
- Related ADRs: [0001](../../adr/0001-django-modular-monolith.md), [0002](../../adr/0002-postgresql-system-of-record.md), [0006](../../adr/0006-yandex-object-storage-media.md)

## Outcome

An authorized photographer selects any event and uploads up to 10,000 files declared as
print-quality JPEGs from one browser page. The page maintains a bounded parallel queue and uploads
each file directly to a private incoming area in Yandex Object Storage. Django owns authorization,
batch and item state, minimal JPEG signature verification, promotion to an immutable final key, and
photo metadata without proxying the upload bytes through the application VM.

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
- Server-side promotion from a browser-writable incoming key to a final key never authorized to the
  browser.
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
         `-------------- JPEG bytes ------------> private incoming object
                                                        |
                                  Django verifies and promotes server-side
                                                        |
                                                        v
                                                private final object
```

Django remains the owner of users, permissions, events, upload state, photo records, and object-key
generation. Object Storage accepts only the authorized incoming write and never becomes the source
of product truth. Django verifies the incoming object and promotes it with an in-bucket server-side
copy to a new final key before deleting the incoming object. No background service or task broker is
introduced.

Incoming and final object keys are random and immutable. The incoming key necessarily appears in
its short-lived signed upload form, but a browser is never authorized to write the final key. A
confirmed photo is therefore unaffected if an old incoming grant is reused before it expires. No
key is rendered into public HTML or returned by read APIs, and key secrecy is not treated as an
authorization boundary. The private bucket remains the read-access boundary.

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
- confirm an item by checking its expected incoming object, validating its signature, promoting it
  to its final key, and checking the final object with S3 `HEAD`;
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

The adapter generates separate immutable incoming and final keys. It creates a presigned POST policy
valid for 10 minutes and bound to one exact incoming key, the private bucket, `image/jpeg`, and a
content length from 1 byte through 50 MB. It verifies incoming and final objects with `HEAD`, reads
only the required byte ranges for minimal signature validation, copies a valid incoming object to a
new final key with server credentials, and deletes incoming or rejected objects.

The browser receives the URL and fields required by the presigned POST but receives no storage
credentials and no final key. CORS allows `POST` from the configured application origins and only
the headers required by the POST policy. Browser access never includes object read, list, copy, or
delete. Server credentials are restricted to signing incoming writes plus `HEAD`, byte-range read,
copy, and delete within the private bucket prefixes used by ingestion.

These selected boundaries, including the 10-minute grant, incoming-to-final promotion, CORS, and
retention rules, must be recorded and accepted in an ADR before production implementation.

## Data model

### `UploadBatch`

- UUID primary key.
- Required event foreign key with protective deletion behavior.
- Required uploader foreign key with protective deletion behavior.
- Immutable expected item count from 1 through 10,000.
- Status: `created`, `uploading`, `completed`, `partial`, or `failed`.
- Created, last-activity, and completed timestamps.

Counts are derived from indexed item states rather than updated through contended counters while
four uploads complete concurrently.

### `UploadItem`

- UUID primary key and required batch foreign key.
- A browser-generated client item UUID unique within the batch.
- Original filename, declared content type, expected byte size, and separate immutable incoming and
  final keys generated during registration.
- Status: `pending`, `authorized`, `uploaded`, or `failed`.
- Stable public error code and sanitized error message.
- Authorization-expiry, last-activity, upload-attempt, and completion timestamps.
- Nullable one-to-one relationship to the resulting photo record.

The pair `(batch_id, client_item_id)` is the registration and retry idempotency key. Resubmitting a
lost registration request returns the existing item when its immutable metadata matches and rejects
the request when it differs. Retrying upload reuses the same database row and incoming key; it cannot
create a second final key or photo record.

### `Photo`

The preliminary model gains nullable uploader, unique nullable private-original key, nullable
original filename, nullable byte size, nullable content type, and nullable upload timestamp while
preserving existing primary keys and `event_id` relationships. All fields are nullable for legacy
rows; the ingestion service requires every new private-original field before creating a new row.
New photos use generated 32-character UUID hex identifiers compatible with the current primary-key
shape.

Legacy rows keep their non-empty `src` and null private-original fields. New private-original rows
store `src=""` and complete private-original fields. The model field becomes `blank=True` with an
empty-string default without changing its non-null database representation. A database check
constraint enforces either the legacy shape or the complete private-original shape, introduced with
PostgreSQL-safe `NOT VALID` and `VALIDATE` operations. Removing `src` or converting legacy objects
requires later inventory and a separate expand-and-contract cleanup after the new path is proven.

## State transitions

The server is authoritative. Every registration, retry, confirmation, finalization, and cleanup
transition locks the batch row and recalculates state from persisted items.

| Entity | From | Trigger | To |
| --- | --- | --- | --- |
| Batch | `created` | First item is authorized | `uploading` |
| Batch | `created` | Empty batch is inactive for 24 hours | `failed` |
| Batch | `uploading` | All items uploaded | `completed` |
| Batch | `uploading` | Items terminal, some uploaded and some failed | `partial` |
| Batch | `uploading` | Items terminal, all failed | `failed` |
| Batch | `partial` or `failed` | Failed item is manually retried while the page lives | `uploading` |
| Item | none | Idempotent registration | `pending` |
| Item | `pending`, `authorized`, or `failed` | Grant issue or refresh | `authorized` |
| Item | `authorized` | Verify, promote, and create photo succeed | `uploaded` |
| Item | `authorized` | Non-retryable verification or promotion failure | `failed` |
| Item | `pending` or `authorized` | No activity for 24 hours | `failed` |

`uploaded` is terminal. Confirmation of an already uploaded item returns the existing result.
Registration and authorization are rejected for `completed` batches. Concurrent finalization cannot
close a batch while a registration transaction holds its row lock. Finalization requires the
persisted item count to equal the immutable expected item count; a partially registered batch cannot
finalize while its page is active. After the 24-hour inactivity boundary, missing registrations count
as failures when cleanup derives `partial` or `failed`. A selection with zero files cannot create a
batch.

## Upload flow

1. An authorized user opens the uploader, selects an event, and selects files.
2. Client validation rejects an empty selection, more than 10,000 files, non-JPEG selections, and
   any file larger than 50 MB before batch creation.
3. The browser creates an `UploadBatch` with the immutable expected item count and receives only its
   public UUID and control endpoints.
4. The browser assigns a client item UUID to every selected file and registers metadata for up to
   100 items per control request. Repeating a request uses the same UUIDs.
5. Under a batch row lock, Django returns existing matching items, creates missing items, rejects
   conflicting UUID reuse, and prevents the persisted count from exceeding the expected count.
6. The browser requests presigned POST authorization for the next small queue window.
7. Up to four files upload directly to their incoming private-bucket keys.
8. After Object Storage reports success, the browser asks Django to confirm that item.
9. Django checks the incoming object size and metadata, reads the JPEG start and end signatures,
   and rejects an object that does not begin with `FF D8` and end with `FF D9`. This is container
   signature validation, not complete image decoding or proof that the file is safe to render.
10. Django copies the valid object to a new final key never authorized to the browser, verifies the
    final object with `HEAD`, creates photo metadata and marks the item `uploaded` in one database
    transaction, then deletes the incoming object best-effort.
11. Once all selected items are terminal, Django marks the batch `completed` when all succeeded,
    `partial` when success and failure coexist, or `failed` when every item failed.

The browser may send file metadata that is inaccurate or malicious. S3 verification, authorization
ownership, server-generated keys, signed constraints, minimal signature checks, and later complete
image decoding remain server-side trust boundaries. Stage 2 stores a private untrusted JPEG
container; Stage 3 must decode and validate it before creating any public derivative.

## Failure and lifecycle behavior

- A transient network or Object Storage failure is retried up to three times with increasing delay.
- An expired grant is refreshed without incrementing the file's data-failure count.
- A terminal item exposes a stable, localized error category and a manual retry action.
- A retry reuses the same `UploadItem` and incoming key. An uploaded item cannot be authorized or
  uploaded again.
- A failed `HEAD`, size mismatch, or metadata mismatch never creates `Photo`; the unexpected object
  is deleted best-effort and the failure is recorded.
- Confirmation locks the item before promotion. If a database failure follows the server-side copy,
  retry checks the same pre-generated final key and completes the same item instead of copying to a
  new key. A final object without a linked photo remains eligible for confirmation for 24 hours.
- Closing or reloading the page stops active requests. Confirmed photos remain valid and the batch
  remains `uploading`; no automatic browser recovery is promised.
- Successfully verified originals have no automatic deletion in this stage.
- A cleanup management command processes batches with no activity for at least 24 hours. Under
  `select_for_update(skip_locked)`, it rechecks `last_activity_at`, marks remaining `pending` and
  `authorized` items failed with `upload_expired`, deletes their incoming objects, deletes final
  objects that still have no linked photo, and derives the batch as `partial` or `failed`.
- Immediate and scheduled cleanup are idempotent: missing objects are success, linked final objects
  are never deleted, and an item whose activity changed after selection is skipped.
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
- The server generates separate incoming and final object keys; filenames never become key paths.
- Presigned POST grants expire after 10 minutes and bind the private bucket, exact incoming key,
  JPEG content type, and maximum byte size.
- The browser can overwrite only its untrusted incoming key while a grant is valid. It can never
  write, read, list, copy, or delete a final object.
- Private-bucket read and list access are never granted to the browser.
- Public templates, public endpoints, error bodies, and logs do not disclose object keys or signed
  URLs.
- File count, content type, and size constraints are enforced by the server as well as the browser.
- Minimal JPEG boundary signatures are checked before promotion. Complete decoding remains a Stage
  3 responsibility, and the object stays private even when later validation rejects it.

## Automated test strategy

### Django tests

- Permission matrix for anonymous, authenticated non-photographer, non-staff photographer,
  staff-plus-photographer, staff without the permission, and superuser.
- All-event selection for authorized uploaders, including draft events.
- Ownership checks that reject batch and item UUID substitution.
- Model constraints, every allowed and rejected state transition, idempotent registration and
  confirmation, partial and all-failed batches, and preservation of both legacy and new photo row
  shapes.
- Registration limits for file count, size, filename, and allowed content type.
- Lost-response registration retry, conflicting client item UUID reuse, and concurrent registration
  versus finalization under the 10,000-item server limit.
- S3 adapter tests for presigned POST constraints, refresh, `HEAD`, range signature reads, promotion,
  post-promotion incoming overwrite, mismatch, deletion, and sanitized failures.
- Cleanup tests for the 24-hour boundary, active-item race prevention, final objects without photos,
  linked-final preservation, and idempotent missing-object handling.
- Security assertions that read responses and public pages do not expose keys or signed URLs.

### Browser and visual tests

- The coordinator never exceeds four active transfers.
- It registers and signs bounded groups rather than loading 10,000 grants at once.
- Retry reuses the same item and refreshes an expired grant.
- Closing protection is active only while transfers remain.
- End-to-end mocked-storage scenarios cover full success and partial failure.
- Keyboard operation covers selection, start, cancel, and retry; progress and errors are announced
  through accessible status regions without announcing every byte update.
- Degraded-network tests cover slow transfers, grant expiry, transient failure, and a tab close while
  requests are active.
- Production visual snapshots cover empty selection, active progress, partial failure, completion,
  and the mobile layout.
- Promotion removes the obsolete reference-only upload route, fixture data, and snapshots and marks
  the production screen in the canonical inventory.

### Storage contract and staging verification

- A contract test against the selected S3-compatible test service verifies presigned POST policy
  constraints, CORS, upload, `HEAD`, range reads, server-side copy, rejection, and deletion.
- Staging verifies one realistic multi-file batch without exposing credentials or private reads.
- A bounded load test uses generated payloads and metadata to exercise 10,000 queue items without
  storing 500 GB in CI.

## Operational impact

- Provision a separate private bucket and least-privilege service credentials before enabling the
  feature. Pricing-affecting Yandex Cloud actions require explicit manual confirmation.
- Configure application origins in private-bucket CORS.
- Add environment variables for private bucket name and allowed origins. Signing lifetime, maximum
  files, maximum file bytes, registration group size, and browser concurrency have code defaults
  matching this specification and may be overridden through validated environment values.
- Install a versioned daily host schedule that runs the idempotent stale-upload cleanup management
  command inside the web container. Deployment must install or update that schedule repeatably.
- Monitor upload authorization failures, confirmation failures, cleanup failures, batch age, and
  private-bucket consumption.
- Do not increase Nginx request-body limits for media; control requests remain small.

## ADR gates

Implementation must not begin until maintainers record and accept the selected decisions below;
the ADRs document this design rather than defer implementation choices:

1. Django session authentication, additive `Photographer` permission, administrator role
   composition, all-event visibility, and batch ownership.
2. Direct browser upload to an incoming private prefix with a 10-minute constrained presigned POST,
   server-side promotion to an undisclosed final key, CORS, indefinite retention of confirmed
   originals, and 24-hour cleanup of unconfirmed objects.
3. Request-driven ingestion without a worker or broker in Stage 2, with asynchronous image
   processing deferred to later roadmap stages.

## Acceptance criteria

- An authorized photographer or administrator with upload permission selects any event and uploads
  a multi-file batch declared as JPEG from one browser page.
- The page sustains four concurrent direct-to-private-storage transfers without routing media bytes
  through Django.
- The server enforces 10,000 items per batch, 50 MB per item, idempotent registration, and minimal
  JPEG boundary signatures independently of browser validation.
- Successful items promote an incoming object to a final key and create exactly one photo record;
  transient failures are retryable without duplicating a record or final object.
- Partial failure preserves successful items and produces an observable `partial` batch.
- Closing the page stops unfinished work without corrupting completed items; automatic resume is not
  promised.
- After 24 hours without activity, cleanup fails remaining items without deleting linked final
  originals or racing an active confirmation.
- Anonymous, unauthorized, cross-user, and public-read access is denied.
- No public/read response exposes a private object key or a signed upload URL.
- Existing event and preliminary photo relationships remain valid through deployment.
