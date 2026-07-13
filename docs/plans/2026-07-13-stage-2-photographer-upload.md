# Stage 2 Photographer Upload Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An authorized photographer or administrator uploads up to 10,000 JPEG originals from one live browser page directly to private Yandex Object Storage, while Django securely records, verifies, promotes, and tracks every file.

**Architecture:** Django remains the authorization and transactional owner. The browser uploads four files concurrently to short-lived incoming S3 keys using constrained presigned POST forms; Django binds verification and server-side promotion to one ETag before creating `Photo` metadata for an undisclosed final key. No ZIP, desktop client, worker, broker, or queue restoration is introduced.

**Tech Stack:** Python 3.12, Django 6, PostgreSQL 16, boto3/django-storages, Yandex Object Storage S3 API, server-rendered Django templates, browser JavaScript with `XMLHttpRequest`, Node's built-in test runner, Playwright, Docker Compose, GitHub Actions.

---

- Date: 2026-07-13
- Status: Ready for implementation
- Owner: project maintainer
- Related design: [Stage 2 Photographer Upload Design](../superpowers/specs/2026-07-13-stage-2-photographer-upload-design.md)
- Related roadmap: [Stage 2: Photographer access and simple upload](2026-07-11-mvp-product-roadmap.md#этап-2-доступ-фотографов-и-простая-загрузка)
- Related architecture: [Target MVP architecture](../architecture.md#target-mvp-architecture--proposed), [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- Related ADRs: [0001](../adr/0001-django-modular-monolith.md), [0002](../adr/0002-postgresql-system-of-record.md), [0006](../adr/0006-yandex-object-storage-media.md); Task 1 adds ADRs 0010–0012 before implementation depends on them.
- Required implementation skills: `@superpowers:test-driven-development`, `@django-expert:django-expert`, `@django-safe-migration:django-safe-migration`, `@update-visual-design`, `@manage-yandex-cloud`, and `@superpowers:verification-before-completion`.

## Current readiness audit

Audited against `origin/main` at commit `71574cd` before this plan was written.

### Already done

- [x] Stage 1 event catalog with free/paid type, publication state, Django Admin, and public pages.
- [x] Django authentication, sessions, authentication middleware, users, and Admin user management.
- [x] ADR 0006 selects separate public/private Yandex Object Storage access boundaries and immutable keys.
- [x] boto3, django-storages, environment-selected public S3 configuration, and event-cover storage.
- [x] PostgreSQL, Docker Compose, CI, staging deployment, HTTPS production edge design, and risk-based repository verification.
- [x] Shared production design system and a test-only upload visual reference.
- [x] Approved and independently reviewed Stage 2 upload design.

### Still required

- [ ] Accepted ADRs for photographer authorization, direct private upload, retention/cleanup, and request-driven Stage 2 ingestion.
- [ ] Private storage configuration and manually provisioned private bucket/IAM/CORS.
- [ ] `UploadBatch`, `UploadItem`, compatible `Photo` expansion, constraints, and migrations.
- [ ] Photographer group bootstrap, login/logout, ownership enforcement, and Admin inspection.
- [ ] Presigned POST, ETag-bound verification, incoming-to-final promotion, and cleanup.
- [ ] JSON control endpoints and the production browser queue.
- [ ] Permission, migration, domain, storage, API, JavaScript, visual, contract, and load tests.
- [ ] Repeatable deployment configuration and daily stale-upload cleanup schedule.

## Scope

### In scope

- Additive `Photographer` permission; staff administrators may also hold it and superusers inherit it.
- Every authorized uploader may select every event, including drafts.
- One page-lifetime queue, 1–10,000 JPEG items, at most 50 MiB each, four concurrent transfers.
- Idempotent registration in groups of at most 100 items.
- Ten-minute constrained presigned POST to a private incoming prefix.
- ETag-bound `HEAD`, range reads, server-side promotion, final verification, and exactly-one `Photo` creation.
- Partial/all-failed state, retry while the page lives, and 24-hour stale cleanup.
- Safe compatibility with preliminary `Photo` rows and `src`.
- Feature-flagged rollout and staging storage contract verification.

### Out of scope

- ZIP, desktop/CLI upload, restart/resume, multipart-per-photo upload, global cross-batch deduplication.
- Celery, broker, worker, EXIF, decoding, derivatives, publication, galleries, recognition, or public downloads.
- Photographer-to-event assignments, public registration, password reset, or a custom operator account system.
- Automatic deletion of confirmed originals.

## Acceptance criteria

- An anonymous user is redirected to photographer login; a signed-in user without permission gets 403.
- A photographer, staff-plus-photographer, or superuser can upload to any event; a photographer sees only their batches.
- Server-side constraints enforce expected count 1–10,000, item size 1–52,428,800 bytes, and JPEG metadata independently of the browser.
- The browser holds at most four active S3 transfers, registers at most 100 items per control call, and signs only a small queue window.
- A successful item is verified against one ETag, minimally checked for JPEG boundaries, copied to one final key, and linked to exactly one `Photo`.
- A retry reuses `(batch_id, client_item_id)`, incoming key, final key, and photo relationship.
- Browser closure stops incomplete transfers without damaging confirmed items; no restart/resume is promised.
- After 24 inactive hours cleanup fails unfinished items, removes unlinked objects, preserves linked finals, and cannot race active confirmation.
- Public/read responses, templates, and logs contain no private keys, credentials, or signed upload forms.
- Legacy `Photo` rows keep their IDs, event relationships, and `src`; new rows use empty `src` plus complete private-original metadata.
- Targeted, full Python, JavaScript, visual, migration, shell, Compose, and staging contract checks produce the expected successful outcomes.

## File and responsibility map

### Documentation and decisions

- Create `docs/adr/0010-use-django-photographer-permissions.md`: authentication and additive role decision.
- Create `docs/adr/0011-use-direct-private-object-storage-ingestion.md`: presigned POST, incoming/final keys, retention, cleanup, IAM, and CORS.
- Create `docs/adr/0012-keep-stage-2-ingestion-request-driven.md`: explicit no-worker boundary for Stage 2.
- Modify `docs/adr/README.md`, `docs/architecture.md`, and this plan as statuses move from planned to implemented.

### Django ingestion module

- Create `src/backend/ingestion/apps.py`, `models.py`, `admin.py`, `forms.py`, `urls.py`, `views.py`: app registration, persistence, Admin, validation, routes, HTML, and JSON control endpoints.
- Create `src/backend/ingestion/services/batches.py`: row-locked batch/item state transitions and idempotency.
- Create `src/backend/ingestion/services/confirmation.py`: ETag-bound verification, promotion, and `Photo` creation.
- Create `src/backend/ingestion/storage.py`: private S3 adapter and sanitized storage errors.
- Create `src/backend/ingestion/context_processors.py`: feature-aware photographer navigation.
- Create `src/backend/ingestion/management/commands/bootstrap_photographer_group.py`: idempotent group/permission setup.
- Create `src/backend/ingestion/management/commands/cleanup_stale_uploads.py`: race-safe 24-hour cleanup.
- Create `src/backend/ingestion/management/commands/verify_private_upload_storage.py`: staging contract probe with guaranteed cleanup.

### Models and migrations

- Modify `src/backend/picflow/models.py`: expand-compatible private-original fields and row-shape constraint state.
- Create `src/backend/picflow/migrations/0003_photo_private_original_expand.py`: nullable fields and state-compatible `src` change.
- Create `src/backend/picflow/migrations/0004_photo_private_original_constraints.py`: concurrent unique index plus `NOT VALID` FK/check constraints.
- Create `src/backend/picflow/migrations/0005_validate_photo_private_original_constraints.py`: non-atomic validation.
- Create `src/backend/ingestion/migrations/0001_initial.py`: new empty batch/item tables, indexes, and custom permission.

### Browser UI

- Create `src/backend/templates/registration/login.html` and `src/backend/templates/ingestion/upload.html`.
- Create `src/backend/static/ui/upload.css` and `src/backend/static/ui/upload-coordinator.js`.
- Modify `src/backend/templates/ui/base.html`, `src/backend/static/ui/icons.svg`, and `config` URL/settings modules.
- Remove the obsolete test-only upload reference and replace it with deterministic production-screen visual fixtures.

### Tests and operations

- Create `src/backend/ingestion/tests/` suites for models, permissions, batches, confirmation, storage, views, cleanup, commands, and migrations.
- Create `tests/js/upload-coordinator.test.js`; modify `package.json` and `package-lock.json` only if npm metadata changes.
- Modify `tests/visual/`, `tests/test_repository_foundation.py`, `pyproject.toml`, `.env.example`, Compose/deployment workflows, and deployment scripts.
- Create `deploy/run-upload-cleanup.sh` and `deploy/install-upload-cleanup-cron.sh`.

## Chunk 1: Decisions, authorization, and safe schema

### Task 1: Record the approved architecture decisions

**Files:**
- Create: `docs/adr/0010-use-django-photographer-permissions.md`
- Create: `docs/adr/0011-use-direct-private-object-storage-ingestion.md`
- Create: `docs/adr/0012-keep-stage-2-ingestion-request-driven.md`
- Modify: `docs/adr/README.md`
- Modify: `docs/architecture.md`
- Test: `tests/test_repository_foundation.py`

- [ ] **Step 1: Write the failing ADR-index contract test**

Extend `test_adr_index_lists_all_accepted_decisions` so it expects ADRs 0010–0012 with status `Accepted`, and add assertions that the architecture no longer lists authentication, private ingestion, or Stage 2 async execution as open choices.

- [ ] **Step 2: Run the contract test and confirm the expected failure**

Run: `pytest tests/test_repository_foundation.py::test_adr_index_lists_all_accepted_decisions -q`

Expected: FAIL because ADRs 0010–0012 and their index rows do not exist.

- [ ] **Step 3: Write the accepted ADRs from the approved design**

Use `@write-adr`. Record only durable decisions:

```text
0010: Django sessions + additive ingestion.upload_photos permission;
      staff and photographer are independent; superusers inherit permission;
      all-event visibility; own-batch ownership.

0011: private incoming prefix + 10-minute constrained presigned POST;
      browser never writes final keys; ETag-bound promotion;
      confirmed originals retained; unconfirmed objects stale after 24 hours;
      separate private bucket, least privilege, restricted CORS.

0012: request-driven control and confirmation in Stage 2;
      browser supplies bounded concurrency; no worker/broker;
      background image processing remains deferred.
```

Set status to `Accepted`: the project maintainer explicitly approved every design section and the committed design status is `Approved design`. Add all three records to the index in numeric order.

- [ ] **Step 4: Synchronize architecture status**

Update the accepted constraints, ingestion flow, open decisions, and implemented/proposed module table without claiming code exists. Authentication/storage execution become accepted; the ingestion module remains proposed until the rollout task is complete.

- [ ] **Step 5: Run documentation verification**

Run: `pytest tests/test_repository_foundation.py -q && git diff --check`

Expected: repository contract PASS and no whitespace errors.

- [ ] **Step 6: Commit the decision records**

```bash
git add docs/adr docs/architecture.md tests/test_repository_foundation.py
git commit -m "docs: accept photographer ingestion architecture"
```

### Task 2: Add validated ingestion settings

**Files:**
- Create: `src/backend/ingestion/__init__.py`
- Create: `src/backend/ingestion/apps.py`
- Create: `src/backend/ingestion/tests/test_settings.py`
- Modify: `src/backend/config/settings.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: Write failing settings tests**

Assert these defaults and validation rules:

```python
assert settings.PHOTO_UPLOAD_ENABLED is False
assert settings.PHOTO_UPLOAD_MAX_FILES == 10_000
assert settings.PHOTO_UPLOAD_MAX_FILE_BYTES == 50 * 1024 * 1024
assert settings.PHOTO_UPLOAD_REGISTRATION_CHUNK == 100
assert settings.PHOTO_UPLOAD_CONCURRENCY == 4
assert settings.PHOTO_UPLOAD_GRANT_TTL_SECONDS == 600
assert settings.PHOTO_UPLOAD_STALE_AFTER_SECONDS == 86_400
```

When `PHOTO_UPLOAD_ENABLED=True`, Django system checks must error unless private bucket, credentials,
endpoint, region, and allowed origins are non-empty. Validate files `1..10_000`, bytes
`1..52_428_800`, registration chunk `1..100`, concurrency `1..4`, grant TTL `60..600` seconds, and
stale age at least `86_400` seconds. Production/staging retain the approved defaults unless a lower
operational limit is intentional; configuration can never exceed the approved security caps.

- [ ] **Step 2: Run tests and confirm settings are absent**

Run: `pytest src/backend/ingestion/tests/test_settings.py -q`

Expected: FAIL because the ingestion app and settings do not exist.

- [ ] **Step 3: Implement the ingestion app and configuration**

Add `ingestion.apps.IngestionConfig` to `INSTALLED_APPS`. Parse all values from environment variables, keep uploads disabled by default, and register a tagged Django system check. Use separate private credentials:

```text
PRIVATE_MEDIA_S3_BUCKET
PRIVATE_MEDIA_S3_ACCESS_KEY_ID
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY
PRIVATE_MEDIA_ALLOWED_ORIGINS
PHOTO_UPLOAD_ENABLED
PHOTO_UPLOAD_MAX_FILES
PHOTO_UPLOAD_MAX_FILE_BYTES
PHOTO_UPLOAD_REGISTRATION_CHUNK
PHOTO_UPLOAD_CONCURRENCY
PHOTO_UPLOAD_GRANT_TTL_SECONDS
PHOTO_UPLOAD_STALE_AFTER_SECONDS
```

Reuse the existing endpoint and region defaults. Do not put private originals in Django's public/default storage alias.

Add `src/backend/ingestion` to `coverage.run.source` so the new security-sensitive module is included in the existing 80% quality gate.

- [ ] **Step 4: Run targeted verification**

Run: `pytest src/backend/ingestion/tests/test_settings.py -q && python src/backend/manage.py check`

Expected: PASS with uploads disabled under normal test configuration.

- [ ] **Step 5: Commit ingestion configuration**

```bash
git add .env.example pyproject.toml src/backend/config/settings.py src/backend/ingestion
git commit -m "feat: configure private photo ingestion"
```

### Task 3: Expand `Photo` safely and create ingestion persistence

**Files:**
- Modify: `src/backend/picflow/models.py`
- Modify: `src/backend/picflow/tests/test_models.py`
- Create: `src/backend/picflow/tests/test_photo_migrations.py`
- Create: `src/backend/picflow/migrations/0003_photo_private_original_expand.py`
- Create: `src/backend/picflow/migrations/0004_photo_private_original_constraints.py`
- Create: `src/backend/picflow/migrations/0005_validate_photo_private_original_constraints.py`
- Create: `src/backend/ingestion/models.py`
- Create: `src/backend/ingestion/migrations/0001_initial.py`
- Create: `src/backend/ingestion/tests/test_models.py`
- Create: `src/backend/ingestion/management/__init__.py`
- Create: `src/backend/ingestion/management/commands/__init__.py`
- Create: `src/backend/ingestion/management/commands/bootstrap_photographer_group.py`
- Create: `src/backend/ingestion/tests/test_bootstrap_group.py`
- Modify: `src/backend/entrypoint.sh`

- [ ] **Step 1: Write failing model-shape tests**

Cover legacy and new shapes:

```python
legacy = Photo(id="LEGACY", event=event, src="photos/legacy.jpg")
legacy.full_clean()  # private fields may remain null

uploaded = Photo(
    id=uuid4().hex,
    event=event,
    src="",
    uploaded_by=photographer,
    original_key=f"originals/{uuid4().hex}",
    original_filename="race.jpg",
    original_size=30 * 1024 * 1024,
    original_content_type="image/jpeg",
    uploaded_at=timezone.now(),
)
uploaded.full_clean()
```

Reject mixed/incomplete shapes, duplicate final keys, batch expected counts outside 1–10,000, item sizes outside 1–52,428,800, and duplicate `(batch_id, client_item_id)`.

- [ ] **Step 2: Run the focused tests and confirm missing fields/models**

Run: `pytest src/backend/picflow/tests/test_models.py src/backend/ingestion/tests/test_models.py -q`

Expected: FAIL because private fields and ingestion models do not exist.

- [ ] **Step 3: Add expand-compatible Django model state**

Use nullable fields for legacy compatibility. Keep `Photo.src` non-null in PostgreSQL but set `blank=True, default=""` in Django. Add the custom `upload_photos` permission to `UploadBatch.Meta`.

Implement this schema explicitly:

```text
UploadBatch:
  id UUIDField(primary_key=True, default=uuid4, editable=False)
  event FK(PROTECT), uploader FK(PROTECT)
  expected_item_count PositiveIntegerField, check 1..10_000
  status CharField(max_length=16, default="created"), database check for
    created|uploading|completed|partial|failed
  created_at DateTimeField(auto_now_add=True)
  last_activity_at DateTimeField(default=now)
  completed_at DateTimeField(null=True, blank=True)

UploadItem:
  id UUIDField(primary_key=True, default=uuid4, editable=False), batch FK(CASCADE)
  client_item_id UUIDField
  original_filename CharField(max_length=255)
  declared_content_type CharField(max_length=100), database check = "image/jpeg"
  expected_size BigIntegerField, check 1..52_428_800
  incoming_key and final_key CharField(max_length=255, unique=True)
  verified_source_etag CharField(max_length=128, null/blank)
  status CharField(max_length=16, default="pending"), database check for
    pending|authorized|uploaded|failed
  error_code CharField(max_length=64, blank, default="")
  sanitized_error_message CharField(max_length=255, blank, default="")
  authorization_expires_at DateTimeField(null=True, blank=True)
  last_activity_at DateTimeField(default=now)
  upload_attempts PositiveSmallIntegerField(default=0)
  completed_at DateTimeField(null=True, blank=True)
  photo OneToOne(PROTECT, nullable, blank, related_name="upload_item")

Photo additions:
  uploaded_by FK(PROTECT, null/blank)
  original_key CharField(max_length=255, unique=True, null/blank)
  original_filename CharField(max_length=255, null/blank)
  original_size BigIntegerField(null/blank)
  original_content_type CharField(max_length=100, null/blank)
  uploaded_at DateTimeField(null/blank)
```

The `(batch, client_item_id)` constraint is unique. `completed_at` is set only for a terminal state;
`authorization_expires_at` is cleared outside `authorized`; `verified_source_etag` stores the
quote-free `etag_value` after conditional signature reads and before copy. Keys and immutable
registration metadata are never
updated after insert; enforce this in the only mutation service and with tests, while uniqueness and
shape remain database-enforced. Counts remain derived from `UploadItem` states; do not add mutable
success/failure counters to the batch.

Indexes must support:

```text
UploadBatch(uploader, -created_at)
UploadBatch(status, last_activity_at)
UploadItem(batch, status)
UploadItem(batch, client_item_id) UNIQUE
Photo(uploaded_by)
Photo(original_key) UNIQUE (ordinary non-partial PostgreSQL uniqueness permits multiple nulls)
```

- [ ] **Step 4: Generate then rewrite migrations using the safe patterns**

Use `@django-safe-migration:django-safe-migration` and the repository's established two-second timeout.

`0003` adds nullable columns only. Add `uploaded_by` in Django state with `db_constraint=False, db_index=False`; no table scan, inline FK, or inline index is allowed.

`0004` uses `atomic = False` and `SeparateDatabaseAndState` to:

- create the uploader lookup index and unique final-key index concurrently;
- attach the non-partial final-key index as a table constraint with
  `ALTER TABLE picflow_photo ADD CONSTRAINT picflow_photo_original_key_uniq UNIQUE USING INDEX picflow_photo_original_key_uniq`;
- add `uploaded_by` FK as `NOT VALID` with a two-second local lock timeout;
- add the legacy-or-complete-private-row check as `NOT VALID` with the same fail-fast timeout;
- use state-only `AlterField`/`AddConstraint`/`AddIndex` operations to represent `unique=True`,
  `db_constraint=True`, the row-shape check, and the named uploader index without asking Django to
  create a second database object.

The state-only `AlterField(original_key, unique=True)` represents final-key uniqueness;
`AddConstraint` represents only `picflow_photo_legacy_or_private_chk` and must not describe another
unique constraint.

Run every lock-acquiring `ALTER TABLE` in its own explicit transaction containing
`SET LOCAL lock_timeout = '2s'`; reset is automatic at transaction end. Concurrent index builds run
outside transactions. This exact split prevents a fallback blocking index or constraint build.

Use stable names `picflow_photo_uploaded_by_idx`, `picflow_photo_original_key_uniq`, `picflow_photo_uploaded_by_fk`, and `picflow_photo_legacy_or_private_chk`. The row-shape check is:

```text
(src <> '' AND every private-original field IS NULL)
OR
(src = '' AND every private-original field IS NOT NULL)
```

`0005` uses `atomic = False` and validates the FK/check constraints in separate `RunSQL` operations with reversible/no-op reverse SQL. Do not put `lock_timeout` on the long `VALIDATE` scan.

- [ ] **Step 5: Add migration preservation tests**

Use `MigrationExecutor` to migrate from `picflow.0002_event_catalog` through `0005`. Seed a legacy event/photo before migration and assert afterward:

```python
assert migrated_photo.pk == "LEGACY"
assert migrated_photo.event_id == original_event_id
assert migrated_photo.src.name == "photos/legacy.jpg"
assert migrated_photo.original_key is None
```

Also create a new private row at the target state and prove the database rejects incomplete and duplicate shapes.

- [ ] **Step 6: Inspect actual PostgreSQL SQL**

Run:

```bash
python src/backend/manage.py sqlmigrate picflow 0003
python src/backend/manage.py sqlmigrate picflow 0004
python src/backend/manage.py sqlmigrate picflow 0005
python src/backend/manage.py sqlmigrate ingestion 0001
```

Expected: nullable expand columns; `CREATE INDEX CONCURRENTLY picflow_photo_uploaded_by_idx`;
`CREATE UNIQUE INDEX CONCURRENTLY picflow_photo_original_key_uniq` without a `WHERE` clause; exact
`ADD CONSTRAINT ... UNIQUE USING INDEX`; `NOT VALID`; separate `VALIDATE CONSTRAINT`; no destructive
`DROP COLUMN`; no non-concurrent index build on populated `picflow_photo`.

- [ ] **Step 7: Bootstrap the Photographer group after permission creation**

After `migrate` applies `ingestion.0001_initial` and Django's `post_migrate` handler creates the model
permission, implement an idempotent
`bootstrap_photographer_group` command:

```python
group, _ = Group.objects.get_or_create(name="Photographer")
permission = Permission.objects.get(
    content_type__app_label="ingestion",
    codename="upload_photos",
)
group.permissions.add(permission)
```

Test first-run creation, repeated execution, preservation of unrelated permissions, and a clear
error if the permission is unexpectedly absent. Add it to `entrypoint.sh` immediately after
`migrate --noinput` and before `collectstatic`, with an ordering contract test.

- [ ] **Step 8: Run schema and bootstrap verification**

Run: `pytest src/backend/picflow/tests/test_photo_migrations.py src/backend/picflow/tests/test_models.py src/backend/ingestion/tests/test_models.py src/backend/ingestion/tests/test_bootstrap_group.py -q && python src/backend/manage.py makemigrations --check --dry-run`

Expected: PASS and `No changes detected`.

- [ ] **Step 9: Commit the schema expansion and permission bootstrap**

```bash
git add src/backend/picflow src/backend/ingestion src/backend/entrypoint.sh
git commit -m "feat: add photographer upload persistence"
```

## Chunk 2: Storage contract and server behavior

### Task 4: Implement the private Object Storage adapter

**Files:**
- Create: `src/backend/ingestion/storage.py`
- Create: `src/backend/ingestion/tests/test_storage.py`
- Create: `src/backend/ingestion/tests/fakes.py`

- [ ] **Step 1: Define the adapter contract in failing tests**

Test these public methods and typed results:

```python
class PrivateUploadStorage:
    def create_presigned_post(self, *, incoming_key: str, max_bytes: int) -> UploadGrant: ...
    def inspect(self, *, key: str) -> ObjectIdentity: ...
    def read_range(self, *, key: str, etag_wire: str, start: int, end: int) -> bytes: ...
    def promote(self, *, incoming_key: str, final_key: str, etag_wire: str) -> ObjectIdentity: ...
    def delete(self, *, key: str) -> None: ...
```

`UploadGrant` contains URL, form fields, and expiry but is returned only by write-control endpoints.
`ObjectIdentity` contains `etag_wire` (the quoted value passed unchanged to S3 conditional headers),
`etag_value` (quotes removed only for equality/log-safe internal comparison), byte size, and normalized
lowercase content type. Never rebuild a conditional header from `etag_value`.

- [ ] **Step 2: Run the tests and confirm the adapter is absent**

Run: `pytest src/backend/ingestion/tests/test_storage.py -q`

Expected: FAIL importing `ingestion.storage`.

- [ ] **Step 3: Implement constrained presigned POST**

Accept only the immutable incoming key already generated by the registration service; the adapter does not choose or persist keys. The policy must bind exact key, `image/jpeg`, 1–configured-max bytes, bucket-default private visibility, and configured 600-second expiry. Return sanitized `StorageUnavailable`, `ObjectMissing`, `ObjectChanged`, and `ObjectMismatch` exceptions; never embed the AWS/Yandex response body in user-visible errors. Do not require an ACL form field when bucket ownership settings reject object ACLs.

- [ ] **Step 4: Implement ETag-bound inspection and promotion**

Call `head_object`, then `get_object` for bytes `0-1` and `size-2` through `size-1` with
`IfMatch=identity.etag_wire`. Promote via
`copy_object(..., CopySourceIfMatch=identity.etag_wire)` and verify that final `HEAD` has the same
`etag_value`, byte size, and normalized content type as the captured incoming identity. Treat 404 as
missing and conditional 409/412 as changed. Delete incoming best-effort only after the final identity
is confirmed. The opt-in real-storage contract in Task 11 is the release gate for Yandex preserving
this identity across conditional copy.

- [ ] **Step 5: Test the overwrite race**

The fake S3 client must mutate the incoming ETag between `HEAD`, range read, and copy. Assert confirmation fails and no final object/photo is accepted. Also prove overwriting incoming after promotion cannot change the final object.

- [ ] **Step 6: Run targeted verification**

Run: `pytest src/backend/ingestion/tests/test_storage.py -q && ruff check src/backend/ingestion/storage.py`

Expected: PASS.

- [ ] **Step 7: Commit the adapter**

```bash
git add src/backend/ingestion/storage.py src/backend/ingestion/tests
git commit -m "feat: add private upload storage contract"
```

### Task 5: Implement idempotent batch and item transitions

**Files:**
- Create: `src/backend/ingestion/services/__init__.py`
- Create: `src/backend/ingestion/services/batches.py`
- Create: `src/backend/ingestion/tests/test_batch_services.py`

- [ ] **Step 1: Write failing service tests**

Cover `create_batch`, `register_items`, `authorize_item`, `report_item_failed`, `derive_batch_status`, and manual retry. Required cases:

- expected count 1–10,000 enforced by Django and database;
- registration chunks at most 100;
- lost-response replay returns existing rows;
- conflicting client UUID metadata returns 409-equivalent domain error;
- row lock prevents concurrent registration from exceeding expected count;
- finalization rejects missing registrations;
- uploaded is terminal;
- partial/failed returns to uploading only when a failed item is retried;
- all-success, mixed, and all-failed derive completed, partial, and failed.
- initial data authorization increments `upload_attempts` from 0 to 1;
- automatic data retry increments it up to four total transfers (initial plus three retries);
- grant refresh replaces expiry/form data without incrementing it;
- manual retry of a terminal failed item clears the public error and resets attempts before issuing
  a new first data attempt.

- [ ] **Step 2: Run tests and confirm service functions are missing**

Run: `pytest src/backend/ingestion/tests/test_batch_services.py -q`

Expected: FAIL importing `ingestion.services.batches`.

- [ ] **Step 3: Implement row-locked transitions**

Every mutation starts with `UploadBatch.objects.select_for_update().get(...)`, scopes ownership before lookup, validates current state, changes only affected items, updates `last_activity_at`, and derives status from persisted counts. Keep database work inside `transaction.atomic()` and return immutable result dataclasses rather than model/queryset objects to views.

At first registration, generate and persist keys exactly once from server-owned UUIDs:

```text
incoming/<batch_uuid>/<item_uuid>
originals/<item_uuid.hex>
Photo.id = item_uuid.hex after successful confirmation
```

Retries and lost-response replays reuse these values; filenames never contribute to a key.

`authorize_item` accepts an internal reason enum `data_attempt|grant_refresh`. It invokes
`PrivateUploadStorage.create_presigned_post`, stores the new expiry, and returns a write-only grant.
Only `pending`, `authorized`, or a `failed` item explicitly moved back to uploading may be authorized;
`uploaded` is terminal. A data attempt is rejected once the current retry cycle reaches four, while
an expiry refresh for an active attempt does not change the counter.

Map failures consistently: network/timeouts, HTTP 408/429/5xx, sanitized storage-unavailable errors,
and `ObjectChanged`/conditional 409 or 412 remain retryable with a fresh data attempt; expired/403
grants request `grant_refresh`. Stable size, content type, JPEG signature, ownership, and state
mismatches are terminal with public codes. Browser
exhaustion reports `transfer_retries_exhausted`; it never overwrites `uploaded`.

The coordinator-facing stable set is `storage_unavailable`, `object_missing`, `object_changed`,
`size_mismatch`, `content_type_mismatch`, `invalid_jpeg`, `promotion_conflict`,
`transfer_retries_exhausted`, `transfer_cancelled`, and `upload_expired`; unknown internal exceptions
map to `storage_unavailable` or `internal_error` without diagnostic text.

- [ ] **Step 4: Prove concurrency and idempotency**

Use `TransactionTestCase` with separate database connections for racing registration/finalization. Expected result: at most `expected_item_count` rows and one stable row per client UUID.

- [ ] **Step 5: Run targeted verification**

Run: `pytest src/backend/ingestion/tests/test_batch_services.py -q`

Expected: PASS including concurrency cases on PostgreSQL.

- [ ] **Step 6: Commit domain transitions**

```bash
git add src/backend/ingestion/services src/backend/ingestion/tests/test_batch_services.py
git commit -m "feat: add upload batch state transitions"
```

### Task 6: Verify, promote, and persist one photo idempotently

**Files:**
- Create: `src/backend/ingestion/services/confirmation.py`
- Create: `src/backend/ingestion/tests/test_confirmation.py`
- Modify: `src/backend/ingestion/services/batches.py`

- [ ] **Step 1: Write failing confirmation tests**

Test success, repeated confirmation, first/last JPEG signature mismatch, ETag change, size/type
mismatch, copy failure, final `HEAD` mismatch, database failure after copy, incoming delete failure,
and cross-user access. Assert every successful path produces one `Photo`, one item link, and one final
key. Retryable storage failures leave the item authorized with a sanitized retryable code;
an `ObjectChanged` precondition failure returns to a retryable attempt; non-retryable
object/verification mismatches mark it failed; uploaded remains terminal. Inject a crash after the
source checkpoint, copy, final `HEAD`, photo commit, and before incoming deletion, then prove each
retry converges to one item/photo/final object.

- [ ] **Step 2: Run the tests and confirm confirmation is absent**

Run: `pytest src/backend/ingestion/tests/test_confirmation.py -q`

Expected: FAIL importing `confirm_upload_item`.

- [ ] **Step 3: Implement confirmation in the required order**

```text
transaction 1: lock item and owner-scoped batch
return existing photo when status=uploaded
when verified_source_etag exists, inspect final first and select recovery only on exact identity
when final is absent, continue with source verification and allow a fresh checkpoint
HEAD incoming and capture ETag
conditional first/last range reads
require FF D8 ... FF D9
persist quote-free verified_source_etag and commit checkpoint
transaction 2: re-lock item/batch and revalidate owner, authorized state, and checkpoint
unless recovering an existing final, conditionally copy to the pre-generated final key
HEAD final and require matching etag_value, size, and normalized content type
create Photo(src="", complete private metadata)
link item, mark uploaded, derive batch state
commit database transaction
delete incoming best-effort
```

If final exists after a prior database failure, inspect the same pre-generated key and complete the
same item only when its ETag equals persisted `verified_source_etag` and its size/type equal immutable item
metadata. The browser cannot create this undisclosed unique final key. If no source checkpoint
exists, do not adopt the final: record a sanitized conflict for operator cleanup. Never generate a
replacement key during retry. `Photo` creation, item linking/uploaded state, and batch derivation
remain one database transaction after the source checkpoint. If no final exists on retry, a fresh
verified source may replace the checkpoint before another conditional copy; if a final exists, the
checkpoint is immutable and is the only admissible recovery identity.

- [ ] **Step 4: Run confirmation and regression tests**

Run: `pytest src/backend/ingestion/tests/test_confirmation.py src/backend/ingestion/tests/test_batch_services.py src/backend/picflow/tests/test_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit confirmation behavior**

```bash
git add src/backend/ingestion/services src/backend/ingestion/tests src/backend/picflow/tests
git commit -m "feat: confirm private photo uploads"
```

### Task 7: Add authenticated control endpoints and Admin inspection

**Files:**
- Create: `src/backend/ingestion/forms.py`
- Create: `src/backend/ingestion/views.py`
- Create: `src/backend/ingestion/urls.py`
- Create: `src/backend/ingestion/admin.py`
- Create: `src/backend/ingestion/tests/test_permissions.py`
- Create: `src/backend/ingestion/tests/test_views.py`
- Modify: `src/backend/config/urls.py`
- Modify: `src/backend/config/settings.py`

- [ ] **Step 1: Write the failing permission matrix**

For upload page and every control endpoint, cover anonymous, authenticated without permission, photographer, staff without permission, staff-plus-photographer, and superuser. Verify own-batch scoping and UUID substitution defenses.

- [ ] **Step 2: Write failing endpoint contract tests**

Define these named routes and JSON shapes:

```text
GET  /photographer/login/                         photographer_login
POST /photographer/logout/                        photographer_logout
GET  /photographer/uploads/                       upload_page
POST /photographer/uploads/batches/               upload_batch_create
POST /photographer/uploads/<batch>/items/          upload_items_register
POST /photographer/uploads/<batch>/items/<item>/authorize/ upload_item_authorize
POST /photographer/uploads/<batch>/items/<item>/confirm/   upload_item_confirm
POST /photographer/uploads/<batch>/items/<item>/failed/    upload_item_failed
POST /photographer/uploads/<batch>/finalize/       upload_batch_finalize
```

CSRF is mandatory. Return stable error codes with 400 validation, 403 authorization, 404 ownership-hiding lookup, 409 state/idempotency conflict, and 503 sanitized storage failure.

Use these exact JSON contracts (UUIDs are strings; timestamps are ISO 8601):

```text
POST batches request:
  {"event_id": 123, "expected_item_count": 42}
response 201:
  {"batch": {"id": "<uuid>", "status": "created", "expected_item_count": 42}}

POST items request (1..100 entries):
  {"items": [{"client_item_id": "<uuid>", "filename": "a.jpg",
              "content_type": "image/jpeg", "size": 12345}]}
response 200/201:
  {"items": [{"id": "<uuid>", "client_item_id": "<uuid>", "status": "pending"}]}

POST authorize request:
  {"reason": "data_attempt" | "grant_refresh"}
response 200 (the only response allowed to contain a signed form):
  {"item": {"id": "<uuid>", "status": "authorized", "attempt": 1},
   "grant": {"url": "https://...", "fields": {...}, "expires_at": "..."}}

POST confirm request: {}
response 200: {"item": {"id": "<uuid>", "status": "uploaded", "photo_id": "<hex>"}}

POST failed request:
  {"code": "transfer_retries_exhausted" | "transfer_cancelled"}
response 200: {"item": {"id": "<uuid>", "status": "failed", "error_code": "..."}}

POST finalize request: {}
response 200:
  {"batch": {"id": "<uuid>", "status": "completed|partial|failed",
             "expected": 42, "uploaded": 40, "failed": 2}}

all errors:
  {"error": {"code": "stable_snake_case", "message": "sanitized text",
             "fields": {"field": ["sanitized text"]}}}
```

Registration replay returns 200; newly created registration returns 201 only when the complete
request was new. Batch/item read payloads and logs never contain grants or either object key.

- [ ] **Step 3: Run tests and confirm routes return 404**

Run: `pytest src/backend/ingestion/tests/test_permissions.py src/backend/ingestion/tests/test_views.py -q`

Expected: FAIL because routes/views do not exist.

- [ ] **Step 4: Implement thin forms and views**

Use Django Forms/custom validators for JSON data, built-in `LoginView`/`LogoutView`, `login_required`, and permission checks. Views call application services and serialize only public IDs, state, progress metadata, and the write-only signed form. Never serialize incoming/final keys in item/read responses.

When `PHOTO_UPLOAD_ENABLED=False`, upload/control routes return 404 while login/logout continue to work.

- [ ] **Step 5: Add read-only Admin inspection**

Register batches/items with filters for status/event/uploader/date. Prevent add/change/delete in Admin; operational state changes remain service-controlled. Superusers can inspect signed-URL-free metadata and sanitized failures.

- [ ] **Step 6: Run endpoint verification**

Run: `pytest src/backend/ingestion/tests/test_permissions.py src/backend/ingestion/tests/test_views.py -q`

Expected: PASS.

- [ ] **Step 7: Commit server endpoints**

```bash
git add src/backend/config src/backend/ingestion
git commit -m "feat: expose photographer upload controls"
```

## Chunk 3: Browser experience, operations, and rollout

### Task 8: Promote the upload screen into production

**Files:**
- Create: `src/backend/templates/registration/login.html`
- Create: `src/backend/templates/ingestion/upload.html`
- Create: `src/backend/static/ui/upload.css`
- Modify: `src/backend/templates/ui/base.html`
- Modify: `src/backend/static/ui/icons.svg`
- Create: `src/backend/ingestion/context_processors.py`
- Modify: `src/backend/config/settings.py`
- Modify: `.agents/skills/update-visual-design/references/screen-inventory.md`
- Modify: `tests/visual/views.py`
- Modify: `tests/visual/urls.py`
- Modify: `tests/visual/visual.spec.js`
- Delete: `tests/visual/templates/design_reference/upload.html`
- Delete: `tests/visual/visual.spec.js-snapshots/desktop-reference-upload.png`
- Delete: `tests/visual/visual.spec.js-snapshots/mobile-reference-upload.png`
- Test: `src/backend/ingestion/tests/test_templates.py`

- [ ] **Step 1: Use the visual-design workflow and write failing template tests**

Use `@update-visual-design`. Assert the production page has an event selector, multiple JPEG input, drop target, progress/status regions, queue container, retry controls, close-warning copy, CSRF data, and no zone/photographer selector, recognition claim, private key, or unfinished links.

- [ ] **Step 2: Run template tests and confirm the production template is absent**

Run: `pytest src/backend/ingestion/tests/test_templates.py -q`

Expected: FAIL because `ingestion/upload.html` does not exist.

- [ ] **Step 3: Implement accessible production templates and CSS**

Reuse `ui/base.html` and existing design tokens. Add photographer navigation only when uploads are enabled and the user has permission. Keep keyboard-visible focus, `<progress>` plus textual totals, `aria-live="polite"` summary updates, and per-file errors associated with retry buttons. Render only a bounded queue window so 10,000 selected files do not create 10,000 live rows.

- [ ] **Step 4: Promote and simplify the visual reference**

Replace the design-reference upload route with deterministic rendering of the production template for empty, active, partial, complete, and mobile states. Remove `UPLOAD_QUEUE` recognition/zone fixtures and obsolete snapshots. Update the screen inventory status to `production` and record `/photographer/uploads/`.

- [ ] **Step 5: Update and inspect snapshots**

Run: `npm run test:visual:update`

Expected: create `desktop-upload-empty.png`, `desktop-upload-active.png`,
`desktop-upload-partial.png`, `desktop-upload-complete.png`, `mobile-upload-empty.png`,
`mobile-upload-active.png`, `mobile-upload-partial.png`, and `mobile-upload-complete.png`; remove both
`*-reference-upload.png` snapshots. Inspect every changed image, then run `npm run test:visual` and
expect PASS with no overflow or resource failures.

- [ ] **Step 6: Commit the production screen**

```bash
git add src/backend/templates src/backend/static src/backend/ingestion tests/visual .agents/skills/update-visual-design/references/screen-inventory.md
git commit -m "feat: add photographer upload screen"
```

### Task 9: Implement the page-lifetime upload coordinator

**Files:**
- Create: `src/backend/static/ui/upload-coordinator.js`
- Create: `tests/js/upload-coordinator.test.js`
- Modify: `src/backend/templates/ingestion/upload.html`
- Modify: `package.json`
- Modify: `package-lock.json` only if npm changes it
- Modify: `tests/visual/visual.spec.js`

- [ ] **Step 1: Write failing JavaScript tests with Node's built-in runner**

Keep the coordinator dependency-free and exportable for Node tests. Cover:

- selection validation and stable `crypto.randomUUID()` item IDs;
- registration groups of 100;
- at most four active `XMLHttpRequest` S3 POSTs;
- progress aggregation without one DOM row per file;
- one initial transfer plus at most three automatic retries;
- grant refresh without consuming a data retry;
- failure reporting and same-item manual retry;
- finalize only after every selected item is registered and terminal;
- `beforeunload` only while work is active.

- [ ] **Step 2: Add the test command and verify red**

Add `"test:js": "node --test tests/js/*.test.js"`.

Run: `npm run test:js`

Expected: FAIL because the coordinator module is absent.

- [ ] **Step 3: Implement the coordinator**

Use `fetch` for small CSRF-protected Django control requests and `XMLHttpRequest` for S3 upload
progress. The retry limit means one immediate initial transfer plus at most three retries after
1, 3, and 7 seconds (four total transfers). Retry XHR network errors/timeouts and HTTP 408, 429, or
5xx. A 403 performs
one grant refresh without consuming an attempt; a repeated 403 or any other 4xx is terminal.
User cancellation is terminal for the current cycle and is not automatically retried; manual retry
resets the cycle. Maintain only page-memory `File` references. Sign just in time for the next queue
window. Never store signed forms, keys, or files in `localStorage`/IndexedDB. Surface stable localized
errors and allow cancel/manual retry without opening extra tabs.

- [ ] **Step 4: Add Playwright behavior tests**

Route/stub Django control responses and Object Storage POSTs in the containerized Playwright environment. Test success, partial failure, slow upload, grant expiry, four-transfer cap, keyboard retry, accessible progress announcements, and closing while active.

- [ ] **Step 5: Run JavaScript and visual verification**

Run: `npm run test:js && npm run test:visual`

Expected: all Node and Playwright tests PASS.

- [ ] **Step 6: Commit the coordinator**

```bash
git add package.json package-lock.json src/backend/static/ui/upload-coordinator.js src/backend/templates/ingestion/upload.html tests/js tests/visual
git commit -m "feat: upload photos from one browser queue"
```

### Task 10: Add stale cleanup and repeatable deployment configuration

**Files:**
- Create: `src/backend/ingestion/management/commands/cleanup_stale_uploads.py`
- Create: `src/backend/ingestion/tests/test_cleanup.py`
- Create: `deploy/run-upload-cleanup.sh`
- Create: `deploy/install-upload-cleanup-cron.sh`
- Modify: `deploy/apply-deployment.sh`
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/promote-production.yml`
- Modify: `tests/test_repository_foundation.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing cleanup tests**

Freeze time around the 24-hour threshold. Cover created/pending/authorized items, partial registration, unlinked final objects after database failure, linked-final preservation, missing-object idempotency, `skip_locked`, activity changes after candidate selection, and batch derivation to partial/failed.

- [ ] **Step 2: Run tests and confirm the command is absent**

Run: `pytest src/backend/ingestion/tests/test_cleanup.py -q`

Expected: FAIL because `cleanup_stale_uploads` does not exist.

- [ ] **Step 3: Implement bounded cleanup**

Process a configurable batch limit ordered by oldest activity. Recheck each batch inside `transaction.atomic()` with `select_for_update(skip_locked=True)`. Delete only incoming objects and final keys with no linked `Photo`; update state only after rechecking the 24-hour cutoff. Return non-zero only for command-level failure, while recording per-object failures for retry on the next run.

- [ ] **Step 4: Write failing deployment-contract tests**

Assert both deployment workflows pass these environment-scoped values through the SSH action and
list every name in `envs`:

```text
vars.PHOTO_UPLOAD_ENABLED -> PHOTO_UPLOAD_ENABLED (default "False")
vars.PRIVATE_MEDIA_S3_BUCKET -> PRIVATE_MEDIA_S3_BUCKET
secrets.PRIVATE_MEDIA_S3_ACCESS_KEY_ID -> PRIVATE_MEDIA_S3_ACCESS_KEY_ID
secrets.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY -> PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY
vars.PRIVATE_MEDIA_ALLOWED_ORIGINS -> PRIVATE_MEDIA_ALLOWED_ORIGINS
```

Assert `apply-deployment.sh` writes those exact names to the mode-0600 remote `.env` without values
entering the repository or logs. It writes plain `staging|production` to
`$DEPLOY_ROOT/deployment-target` and the exact `COMPOSE_PROJECT_NAME` to
`$DEPLOY_ROOT/compose-project-name` only after a successful health check. Assert both new shell
scripts pass `sh -n`, cron installation preserves unrelated entries, and exactly one block delimited
by `# BEGIN photo-prjct-upload-cleanup` / `# END photo-prjct-upload-cleanup` exists.

- [ ] **Step 5: Implement idempotent daily scheduling**

`run-upload-cleanup.sh` reads and validates both recorded files, selects the matching Compose overlay,
uses `flock -n "$DEPLOY_ROOT/upload-cleanup.lock"` to prevent overlap, and runs:

```bash
docker compose ... exec -T web python manage.py cleanup_stale_uploads
```

`install-upload-cleanup-cron.sh` replaces only its own marker block with `17 3 * * *` in the host's
local timezone and appends stdout/stderr to `$DEPLOY_ROOT/upload-cleanup.log`; installation prints
the detected timezone from `date +%Z`. `run-upload-cleanup.sh` returns the management command's exit
status; an already-held lock exits 0 with one log line. `apply-deployment.sh` installs the schedule
only when `PHOTO_UPLOAD_ENABLED=True`; enabled deployment fails early when `crontab`, `flock`, or any
private configuration is missing. Disabled deployment removes only the marked block so stale code is
not scheduled after rollback.

- [ ] **Step 6: Run cleanup and deployment verification**

Run:

```bash
pytest src/backend/ingestion/tests/test_cleanup.py tests/test_repository_foundation.py -q
sh -n deploy/run-upload-cleanup.sh
sh -n deploy/install-upload-cleanup-cron.sh
sh -n deploy/apply-deployment.sh
docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml config >/dev/null
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit cleanup and deployment support**

```bash
git add .env.example .github/workflows deploy src/backend/ingestion/management src/backend/ingestion/tests/test_cleanup.py tests/test_repository_foundation.py
git commit -m "feat: clean stale photographer uploads"
```

### Task 11: Add storage contract, bounded load proof, and rollout evidence

**Files:**
- Create: `src/backend/ingestion/management/commands/verify_private_upload_storage.py`
- Create: `src/backend/ingestion/tests/test_storage_contract_command.py`
- Create: `src/backend/ingestion/tests/test_upload_scale.py`
- Modify: `src/backend/ingestion/tests/test_templates.py`
- Modify: `tests/js/upload-coordinator.test.js`
- Modify: `tests/visual/visual.spec.js`
- Modify: `docs/architecture.md`
- Modify: `docs/plans/2026-07-13-stage-2-photographer-upload.md`
- Modify: `README.md`

- [ ] **Step 1: Write the failing contract-command tests**

The command must create a tiny valid JPEG fixture, generate and exercise the configured upload grant,
capture ETag, issue conditional first/last reads, promote conditionally, verify the final object,
verify private anonymous read denial, and delete every contract key in `finally`. With a deliberately
small contract-only maximum, it must prove real-service rejection of a wrong key, wrong content type,
and oversized POST. Send an `OPTIONS` request with the configured application `Origin` and required
preflight headers, then require the exact allowed origin/method/headers in the response. It must not
print credentials, signed fields, or keys.

- [ ] **Step 2: Run tests and confirm the command is absent**

Run: `pytest src/backend/ingestion/tests/test_storage_contract_command.py -q`

Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement the opt-in real-storage contract command**

Require `PHOTO_UPLOAD_ENABLED=True` and explicit `--confirm-real-storage`; otherwise exit without
mutation. Require `--origin` to equal one configured allowed origin. Use a unique contract prefix and
guaranteed cleanup. Exercise POST through an HTTP client rather than calling `put_object`, including
negative policy cases and CORS preflight. Treat policy, CORS, conditional-read, or conditional-copy
incompatibility as a release blocker, matching the approved spec review recommendation.

- [ ] **Step 4: Add bounded 10,000-item proof**

Create metadata-only items and fake 1 KiB transfers; do not store real 500 GB. The Python scale test
asserts 100 registration calls of 100 items, persisted count 10,000, bounded batch-summary query
count, and a bounded template queue window. The JavaScript test asserts no more than four active
XHRs and no more than the configured visible queue rows across 10,000 descriptors; the visual test
checks the bounded summary state rather than rendering 10,000 DOM rows.

Run: `pytest src/backend/ingestion/tests/test_upload_scale.py src/backend/ingestion/tests/test_templates.py -q && npm run test:js && npm run test:visual`

Expected: PASS under the existing CI job timeout with the functional bounds above. Print elapsed time
and query count as diagnostic evidence, but do not use a machine-dependent millisecond assertion.
Record the first CI run's values in this plan's rollout evidence; the maintainer reviews them before
staging enablement.

- [ ] **Step 5: Run the full repository verification before rollout**

With environment values matching `.env.example` and PostgreSQL available, run:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov --cov-report=term-missing
python src/backend/manage.py check
python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
npm run test:visual
git diff --check
```

Expected: every command exits 0 and coverage remains at least the configured 80%.

- [ ] **Step 6: Commit implementation and pass PR/CI before any rollout**

```bash
git add README.md src/backend/ingestion tests/js tests/visual
git commit -m "test: verify private upload readiness"
```

Open/review the implementation PR, require the full CI suite to pass, merge it, and select the
resulting immutable `ghcr.io/<repository>:<merge_sha>` image. The following provisioning and staging
steps operate only on that reviewed revision; they are not performed from an uncommitted worktree.

- [ ] **Step 7: Provision private cloud resources only after explicit confirmation**

Use `@manage-yandex-cloud`. Present the exact bucket, IAM, CORS, lifecycle, quota, and estimated cost changes and obtain explicit manual confirmation immediately before any pricing-affecting `yc` action. Plan approval is not execution approval.

Provision/verify:

- separate private bucket with public read/list/write denied;
- least-privilege credentials for incoming writes and server-side inspect/copy/delete;
- CORS limited to exact staging/production application origins and presigned POST requirements;
- sufficient storage quota for at least one expected 50–500 GB shoot plus retained originals;
- no lifecycle rule capable of deleting confirmed `originals/` keys.

- [ ] **Step 8: Deploy disabled, migrate, then enable staging**

Deployment order:

1. Deploy code and migrations with `PHOTO_UPLOAD_ENABLED=False`.
2. Verify old application and legacy photos remain healthy.
3. Configure staging private secrets/variables and bucket CORS.
4. Require the staging environment's `PRIVATE_MEDIA_ALLOWED_ORIGINS` to contain exactly its one
   scheme-qualified public origin, then run inside the web container:
   `sh -lc 'python manage.py verify_private_upload_storage --confirm-real-storage --origin "$PRIVATE_MEDIA_ALLOWED_ORIGINS"'`.
5. Enable the feature and deploy the same immutable image/config revision.
6. Upload a representative multi-file JPEG batch; verify four concurrent direct S3 requests,
   private reads denied, exactly-one photos, and partial retry.
7. Create a dedicated failed-test batch/object, backdate only its `last_activity_at` past 24 hours,
   run `cleanup_stale_uploads`, and verify its unlinked objects are removed while a linked final from
   the successful batch remains.
8. Keep production disabled until contract, scale, cleanup, and upload evidence is reviewed and
   production infrastructure exists.

- [ ] **Step 9: Update status documentation after evidence exists**

Mark ingestion implemented in `docs/architecture.md`, update this plan to `Implemented`, check completed tasks, and document actual contract/load/staging evidence. Do not mark the plan or architecture implemented based on code merge alone.

- [ ] **Step 10: Commit final evidence documentation**

Create a fresh worktree and dedicated non-main evidence branch from the deployed merge commit. Make
the documentation updates there, run the documentation checks, push it, and open a second PR; do not
commit rollout evidence from the main checkout or directly to `main`.

```bash
git add README.md docs/architecture.md docs/plans/2026-07-13-stage-2-photographer-upload.md src/backend/ingestion
git commit -m "docs: record photographer upload rollout"
```

## Verification summary

Targeted commands appear after every behavior change. Before pull-request readiness, the complete required set is:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov --cov-report=term-missing
python src/backend/manage.py check
python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
npm run test:visual
git diff --check
```

Migration review additionally requires `sqlmigrate` for every new migration and explicit inspection for concurrent index creation, `NOT VALID`, separate `VALIDATE`, two-second fail-fast locks, and absence of destructive operations.

The real Yandex Object Storage contract command is opt-in and runs only after explicit cloud-resource confirmation and staging configuration.

## Operational impact and rollout

- Runtime adds a private bucket, private credentials, browser-to-S3 CORS, control endpoints, and daily cleanup cron; it does not add a broker or worker.
- Database rollout is expand-only for existing `Photo` rows. New ingestion tables are empty at creation. Validation follows non-blocking PostgreSQL patterns.
- Deploy disabled first. Feature enablement is configuration-only after migrations, group bootstrap, private storage contract, CORS, and staging evidence succeed.
- Observe batch/item status counts, grant failures, S3 conditional failures, cleanup failures, stale age, and bucket consumption. Logs use public IDs only.
- Confirm object-storage quota before realistic shoots: the approved maximum is 500 GB per batch and confirmed originals have no automatic deletion.
- Production activation remains blocked on the repository's existing production-infrastructure/readiness gates.

## Rollback

1. Set `PHOTO_UPLOAD_ENABLED=False` and redeploy; upload/control routes return 404 and navigation disappears while confirmed metadata and originals remain intact.
2. Do not roll database schema back while any new private `Photo` rows exist. The expand columns and new ingestion tables are backward-compatible with the previous application and may remain dormant.
3. Revert application code only after disabling the feature. Keep private bucket objects and PostgreSQL rows for investigation; never bulk-delete confirmed originals during rollback.
4. Remove only the marked cleanup cron block if cleanup code is rolled back. Preserve unrelated crontab entries.
5. Abort/delete an incoming object only when it has no linked final `Photo`. Object deletion is irreversible and requires targeted evidence.
6. If conditional promotion fails in staging, leave the feature disabled, remove only contract-test objects, and revise ADR/design before choosing another storage flow.

## Open questions

- None.
