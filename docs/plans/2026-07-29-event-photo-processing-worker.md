# Event Photo Processing Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan. Steps use checkbox (`- [ ]`)
> syntax for tracking. Project `AGENTS.md` overrides generic per-task commit guidance: subagents
> leave all changes unstaged, and the root controller creates one final implementation commit only
> after independent review and final verification.

- Date: 2026-07-29
- Status: Approved
- Owner: project maintainer
- Related specification:
  [Event photo processing worker design](../superpowers/specs/2026-07-29-event-photo-processing-worker-design.md)
- Related architecture:
  [accepted constraints](../architecture.md#accepted-constraints),
  [photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing), and
  [evolution stage 4](../architecture.md#evolution-stages)
- Related ADRs:
  [ADR 0001](../adr/0001-django-modular-monolith.md),
  [ADR 0002](../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0014](../adr/0014-keep-stage-2-ingestion-request-driven.md), and
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md)
- ADR impact: Conforms to accepted ADR 0017

## Goal

Implement the approved specification's
[working first-stage pipeline](../superpowers/specs/2026-07-29-event-photo-processing-worker-design.md#goal):
confirmed private JPEGs enter an explicit PostgreSQL-backed processing state, a separately runnable
worker claims them through Django, extracts capture time, returns a typed result, and produces an
immutable minimal event-run report.

## Scope

Implement the specification without scope changes. In particular:

- implement only `capture_metadata`;
- do not implement preview generation, face processing, vector search, monitoring, VM mutation, or
  real-environment activation;
- add no Redis, RabbitMQ, Celery, worker ORM dependency, permanent worker S3 credential, or public
  worker endpoint; and
- default to one worker job at a time.

## Global Constraints

- Django and PostgreSQL own eligibility, exact current state, claims, leases, retries, accepted
  results, and immutable reports.
- The worker receives only its private API URL/token and claim-time exact-object presigned GET URL.
- Never persist or log signed URLs, query strings, worker tokens, storage credentials, original
  bytes, or unbounded exception details.
- Contract version is `1`; processor type is `capture_metadata`; initial processor version is `1`.
- A JPEG without supported capture time succeeds with `capture_time = null` and warning
  `capture_time_missing`.
- Worker concurrency is exactly `1` in this increment.
- Real-environment worker services remain disabled and unchanged.
- All behavior changes follow test-first red/green cycles, and subagents perform no Git index,
  history, branch, tag, remote, or push operations.

## Acceptance criteria

The implementation must satisfy all twelve
[specification acceptance criteria](../superpowers/specs/2026-07-29-event-photo-processing-worker-design.md#acceptance-criteria).
Additionally:

- migration application and reversal are covered for existing photos;
- API tests prove a missing or incorrect worker token receives no job or media authorization;
- Docker Compose configuration proves the local worker service receives no `DB_*`,
  `PRIVATE_MEDIA_S3_*`, `MEDIA_S3_*`, or `SECRET_KEY` setting; and
- the repository retains at least 80% configured branch coverage.

## Implementation

### Task 1: Processing persistence and photo enrollment

**Files:**

- Create `src/backend/processing/__init__.py`.
- Create `src/backend/processing/apps.py`.
- Create `src/backend/processing/models.py`.
- Create `src/backend/processing/services/enrollment.py`.
- Create `src/backend/processing/migrations/__init__.py`.
- Create `src/backend/processing/migrations/0001_initial.py`.
- Create `src/backend/processing/tests/__init__.py`.
- Create `src/backend/processing/tests/test_models.py`.
- Create `src/backend/processing/tests/test_enrollment.py`.
- Modify `src/backend/config/settings.py`.
- Modify `src/backend/ingestion/services/confirmation.py`.
- Modify `src/backend/ingestion/tests/test_confirmation.py`.
- Modify `pyproject.toml` only if migration exclusion needs the new app.

- **Specification:** Exact Photo State; Jobs, Attempts, and Idempotency; Immutable Event-Scoped
  Runs and Reports; Processing Flow steps 1–3.
- **Depends on:** Accepted ADR 0017.
- **Produces:** Django `processing` app and schema for `EventProcessingRun`,
  `PhotoProcessingState`, `ProcessingJob`, and `ProcessingAttempt`; idempotent
  `request_capture_metadata(photo)` enrollment service.

- [ ] Add failing model tests for status vocabularies, database constraints, one current
  `capture_metadata` state per photo, one exact job per run/photo/version/configuration, immutable
  terminal attempt evidence, and event ownership.
- [ ] Add failing enrollment tests proving: a photo begins with explicit `not_requested`; confirmed
  private JPEG enrollment creates/reuses the event's compatible `collecting` run and one queued
  job; repeated enrollment is a no-op; legacy/ineligible photos remain `not_requested`; and the
  upload confirmation transaction enrolls the newly created photo.
- [ ] Run the new targeted tests and confirm they fail because the processing app/schema/service do
  not exist.
- [ ] Implement focused models with UUID job/run/attempt identities, indexed claim fields,
  database status/check/unique constraints, bounded JSON configuration/result fields, transition
  timestamps, and protective photo/event relationships.
- [ ] Add a data migration that creates `not_requested` `capture_metadata` state for existing
  photos without queuing legacy rows. Ensure reversal removes only processing-app rows.
- [ ] Implement idempotent enrollment and call it from successful private upload confirmation
  inside the same database transaction that creates `Photo`.
- [ ] Run:
  `.venv/bin/pytest -q src/backend/processing/tests/test_models.py src/backend/processing/tests/test_enrollment.py src/backend/ingestion/tests/test_confirmation.py`.
  Expected: all selected tests pass.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` with the normal
  test database environment. Expected: no migration changes.

### Task 2: Transactional job state machine and event-run reports

**Files:**

- Create `src/backend/processing/contracts.py`.
- Create `src/backend/processing/services/jobs.py`.
- Create `src/backend/processing/services/reports.py`.
- Create `src/backend/processing/tests/test_jobs.py`.
- Create `src/backend/processing/tests/test_reports.py`.
- Modify `src/backend/processing/models.py` and `0001_initial.py` only when Task 1 tests expose a
  missing approved field or constraint before migration history is published.

- **Specification:** Processor Contract; Processing Flow steps 4–11; Exact Photo State; Jobs,
  Attempts, and Idempotency; Retry and Failure Semantics; Immutable Event-Scoped Runs and Reports.
- **Depends on:** Task 1 model and enrollment interfaces.
- **Produces:** Typed contract values and transactional `claim_job`, `heartbeat_attempt`,
  `refresh_download`, `complete_attempt`, `fail_attempt`, and `recover_expired_attempts` services;
  immutable run-closing report builder.

- [ ] Add failing tests for atomic claim and run sealing, compatible processor selection, empty
  queue response, current lease ownership, heartbeat renewal, refresh eligibility, retryable and
  permanent failure transitions, bounded attempts/backoff, expired lease recovery, idempotent
  identical completion, conflicting duplicate rejection, and stale completion isolation.
- [ ] Add failing report tests for exact sealed cohort, later-photo separation, terminal-only run
  closure, agreed event-level counts and min/median/max duration, bounded per-photo rows, and
  immutability after closure.
- [ ] Run the targeted tests and confirm expected failures from missing services.
- [ ] Implement database-locking services so S3 calls and worker computation never occur within a
  claim transaction; use explicit transition functions rather than status inference.
- [ ] Store a canonical hash of each terminal completion payload to distinguish identical retries
  from conflicting duplicates without retaining request headers or signed URLs.
- [ ] Build and persist the bounded report JSON only when every sealed member is terminal. Treat
  missing EXIF as successful output, not failure.
- [ ] Run:
  `.venv/bin/pytest -q src/backend/processing/tests/test_jobs.py src/backend/processing/tests/test_reports.py`.
  Expected: all selected tests pass.

### Task 3: Private worker API and exact-object download grants

**Files:**

- Create `src/backend/processing/auth.py`.
- Create `src/backend/processing/storage.py`.
- Create `src/backend/processing/views.py`.
- Create `src/backend/processing/urls.py`.
- Create `src/backend/processing/tests/test_auth.py`.
- Create `src/backend/processing/tests/test_storage.py`.
- Create `src/backend/processing/tests/test_views.py`.
- Modify `src/backend/config/settings.py`.
- Modify `src/backend/config/urls.py`.
- Modify `src/backend/ingestion/tests/fakes.py` only to support presigned GET test behavior shared
  with processing storage tests.

- **Specification:** Trust and Access Boundary; Processor Contract; Processing Flow; Retry and
  Failure Semantics; Observability Boundary.
- **Depends on:** Task 2 services.
- **Produces:** `/internal/photo-processing/v1/claim`, attempt heartbeat, download refresh,
  completion, and failure endpoints; constant-time bearer-token authentication; presigned exact
  final-object GET adapter.

- [ ] Add failing auth/view tests proving missing, malformed, and incorrect tokens return sanitized
  401 responses; unsupported contract/version returns no claim; accepted requests cannot select
  arbitrary photo, event, object key, or database field.
- [ ] Add failing storage tests proving claim-time exact-key presigning, configured short TTL,
  content-disposition/content-type constraints where supported, mapped S3 failures, and no URL
  persistence or logging.
- [ ] Add failing endpoint tests for claim, empty poll delay, heartbeat, refresh only during the
  current lease, success, retryable failure, permanent failure, idempotent replay, conflicting
  replay, expired lease, stale completion, schema/size limits, stable public codes, and method
  restrictions.
- [ ] Run the targeted tests and confirm expected failures from missing API/auth/storage behavior.
- [ ] Implement a setting-backed worker token with `secrets.compare_digest`; fail closed when the
  feature or token is unset. Use JSON-only, CSRF-independent machine endpoints restricted to the
  worker token and exact methods.
- [ ] Implement presigned GET creation in a processing-owned adapter that accepts only the
  application-selected final key and returns the URL transiently. Never serialize the URL into a
  model, error, or report.
- [ ] Implement strict request/result schema parsing and map validated calls to Task 2 services.
- [ ] Run:
  `.venv/bin/pytest -q src/backend/processing/tests/test_auth.py src/backend/processing/tests/test_storage.py src/backend/processing/tests/test_views.py`.
  Expected: all selected tests pass.

### Task 4: Standalone metadata worker

**Files:**

- Create `src/worker/requirements.txt`.
- Create `src/worker/photo_worker/__init__.py`.
- Create `src/worker/photo_worker/contracts.py`.
- Create `src/worker/photo_worker/client.py`.
- Create `src/worker/photo_worker/metadata.py`.
- Create `src/worker/photo_worker/runner.py`.
- Create `src/worker/photo_worker/__main__.py`.
- Create `src/worker/tests/__init__.py`.
- Create `src/worker/tests/test_contracts.py`.
- Create `src/worker/tests/test_client.py`.
- Create `src/worker/tests/test_metadata.py`.
- Create `src/worker/tests/test_runner.py`.

- **Specification:** First processor; Concurrency and Resource Bounds; Trust and Access Boundary;
  Observability Boundary.
- **Depends on:** Task 3 JSON contract.
- **Produces:** Django-independent `python -m photo_worker` process using Python standard-library
  HTTP plus Pillow, with concurrency one and deterministic EXIF output.

- [ ] Add failing contract tests for version/type validation, bounded fields, and secret/URL
  redaction.
- [ ] Add failing metadata tests using generated JPEG fixtures for EXIF precedence, explicit offset,
  missing capture time, malformed metadata, unsupported/decode failure, orientation-independent
  extraction, and configured input-size bounds.
- [ ] Add failing client/runner tests for bearer authentication, empty-poll delay, bounded jittered
  backoff, streamed bounded download, heartbeat/refresh, success/failure submission, transient
  retry classification, temporary-file cleanup, one-job-at-a-time behavior, and sanitized logs.
- [ ] Run worker tests and confirm expected failures because the package does not exist.
- [ ] Implement the smallest worker package with no import of Django, boto3, psycopg, project
  settings, or backend application modules.
- [ ] Use a per-attempt temporary file or bounded stream, close Pillow images promptly, and delete
  temporary bytes on every exit path.
- [ ] Run:
  `PYTHONPATH=src/worker .venv/bin/pytest -q src/worker/tests`.
  Expected: all worker tests pass.
- [ ] Run:
  `PYTHONPATH=src/worker .venv/bin/mypy src/worker/photo_worker`.
  Expected: no type errors.

### Task 5: Local container wiring and end-to-end evidence

**Files:**

- Create `Dockerfile.worker`.
- Modify `docker-compose.yml`.
- Modify `.env.example`.
- Create `tests/processing/test_worker_container_contract.py`.
- Create `tests/processing/test_pipeline_e2e.py`.
- Create `tests/processing/__init__.py`.
- Modify `docs/architecture.md`.
- Modify `docs/engineering-jobs.md` if and only if repository-executable evidence satisfies an
  existing processing capability row; do not mark real-VM activation implemented.

- **Specification:** Outcome; Deployment Boundary and VM Capacity Gate; Compatibility and
  Evolution; Acceptance Criteria.
- **Depends on:** Tasks 1–4.
- **Produces:** opt-in local worker Compose profile, separate minimal worker image, real-JPEG
  end-to-end proof, and implemented-architecture reconciliation.

- [ ] Add failing repository/container tests proving the worker image contains only worker code and
  dependencies, uses `python -m photo_worker`, defaults to concurrency one, is opt-in locally, and
  receives no database, Django secret, or permanent S3 settings.
- [ ] Add a failing end-to-end test that uses a real JPEG, fake exact-object URL, Django test client,
  and one worker iteration to prove queue → claim → download → EXIF extraction → completion →
  explicit state → immutable event report.
- [ ] Run both tests and confirm expected failures from missing container wiring/integration.
- [ ] Add `Dockerfile.worker`, pinned worker requirements derived from already approved production
  dependencies, and a local-only Compose `worker` profile. Do not modify staging, production,
  deployment workflows, VM resources, or enable flags in real environments.
- [ ] Implement the smallest test seam needed to run exactly one worker iteration without sleeping
  or starting a daemon.
- [ ] Update `docs/architecture.md` implemented facts only after executable evidence exists.
- [ ] Run:
  `.venv/bin/pytest -q tests/processing src/worker/tests src/backend/processing/tests`.
  Expected: all first-stage pipeline tests pass.
- [ ] Run `docker compose config`; expected: valid configuration and worker isolated from forbidden
  settings.
- [ ] Build the worker image and run its help/startup validation without contacting a real
  environment. Expected: image starts the worker package and fails closed or idles cleanly when
  processing is disabled.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADR 0017, and
  `docs/architecture.md`.
- [ ] Confirm preview generation, ML, vector search, monitoring, capacity selection, and real-VM
  activation remain excluded.
- [ ] Confirm the implementation does not alter ADR 0014 request-driven upload confirmation beyond
  transactionally enrolling the confirmed photo after successful promotion.
- [ ] Record conformance to ADR 0017 in the final implementation report.
- [ ] Stop for a maintainer decision rather than weakening an accepted ADR.

## Verification

Run the repository's complete documented CI-equivalent checks from the project virtual
environment:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest --cov --cov-report=term-missing
SECRET_KEY=check \
DEBUG=False \
ALLOWED_HOSTS=localhost,127.0.0.1 \
DB_NAME=app \
DB_USER=app \
DB_PASSWORD=app \
DB_HOST=localhost \
DB_PORT=5432 \
.venv/bin/python src/backend/manage.py check
SECRET_KEY=check \
DEBUG=False \
ALLOWED_HOSTS=localhost,127.0.0.1 \
DB_NAME=app \
DB_USER=app \
DB_PASSWORD=app \
DB_HOST=localhost \
DB_PORT=5432 \
.venv/bin/python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
sh tests/visual/run-in-container.sh test
docker compose config
docker build -f Dockerfile.worker -t photo-prjct-worker:test .
```

Expected: every command exits zero; Python coverage remains at or above 80%; migration drift is
empty; JavaScript and all visual cases pass; Compose configuration contains an opt-in worker with
no forbidden credentials; and the worker image builds.

## Operational impact and rollout

The change adds PostgreSQL tables and disabled-by-default worker settings. Apply database migrations
before any local worker is started. The local worker requires an explicit enable flag and token;
default web startup and upload confirmation remain functional when processing is disabled.

Do not add the worker to staging/production Compose overlays or deployment workflows. Before any
later real-environment activation, execute the VM capacity measurements in the specification and
approve explicit CPU/RAM, container limits, concurrency, and private-network transport.

## Rollback

Disable photo-processing job creation and stop the local worker before reverting application code.
Preserve processing tables and immutable attempts/reports while investigating; do not drop the
migration in an environment that has recorded evidence. A clean local environment may reverse the
processing migration after the worker is stopped and no later migration depends on it.

Object Storage originals are never modified or deleted by this increment. Rollback requires no S3
mutation.

## Open questions

None.
