# Clone Staging Database Locally Implementation Plan

- Date: 2026-07-22
- Status: Complete
- Owner: project maintainer
- Related specification: none
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented), [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0002](../adr/0002-postgresql-system-of-record.md), [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md)
- ADR impact: Conforms to ADR 0002 and ADR 0003; no new ADR is required. This adds a reversible
  developer clone workflow around the existing PostgreSQL/Compose topology and does not select a
  service backup product or recovery objective.

## Goal

Provide one command that creates a timestamped logical backup of staging PostgreSQL, replaces the current checkout's local database with that backup, verifies the restored baseline, and leaves it ready for developing new Django migrations.

## Scope

### In scope

- A developer command `make db-clone-staging` backed by a testable shell script.
- `pg_dump` in PostgreSQL custom format, streamed over SSH without exposing PostgreSQL publicly.
- Explicit, guarded replacement of only the current Compose project's local database.
- Restore verification, dump metadata/checksum recording, ignored local backup artifacts, and failure cleanup.
- Compatibility checks for PostgreSQL major version and Django migration state.

### Out of scope

- Scheduled production/staging backups, retention policy, off-host disaster recovery, RPO/RTO, media-object backup, or production restore.
- Copying Object Storage binaries or secrets.
- Automatically running a newly authored migration against staging.
- Deleting the whole Compose project or named volume.

## Acceptance criteria

- `make db-clone-staging` prompts before replacing local data (or requires `CONFIRM_REPLACE_LOCAL_DB=yes` in non-interactive use), creates `var/backups/staging/<UTC timestamp>.dump`, and restores it into the local database.
- The dump is produced by the staging PostgreSQL 16 container using `pg_dump --format=custom --no-owner --no-acl` and travels over SSH stdout; no staging password or dump content is logged.
- The command targets only the database resolved from the current checkout's `docker compose` configuration, terminates its active local connections, drops/recreates that database, and never runs `docker compose down --volumes`.
- A failed dump leaves the existing local database untouched. A failed restore exits non-zero, keeps the dump for diagnosis/retry, and clearly reports that the local database is incomplete.
- After restore, table data and `django_migrations` match the dump; `manage.py makemigrations --check --dry-run` can be run against the restored database before a developer authors a migration.
- Before confirmation, SSH, or database work, the command rejects every effective Docker endpoint
  except a local Unix socket or loopback `tcp://` endpoint, considering both Docker context and
  environment overrides.
- One atomic lock per resolved Compose project/database serializes the workflow before remote dump or
  retained-dump replacement. A stale lock is never removed automatically.
- A running local `web` service is stopped before the safety snapshot and destructive SQL, is never
  started by the clone workflow, and remains stopped for an explicit normal restart after successful
  validation.
- Any failure after local `DROP DATABASE` begins and before the staging archive restore completes
  attempts recovery from the exact retained local safety dump and still exits non-zero. Django
  validation failures retain the restored staging database and both dumps for diagnosis.

## Implementation

### Task 1: Define the clone command and destructive-action guards

**Files:** create `Makefile`; create `scripts/clone-staging-db.sh`; modify `.gitignore`; test `tests/deployment/test_clone_staging_database.py`.

**Interfaces:** `make db-clone-staging` calls the script; inputs are `STAGING_SSH_TARGET`, optional `BACKUP_DIR` (default `var/backups/staging`), and optional `CONFIRM_REPLACE_LOCAL_DB=yes`; local database coordinates come from `docker compose config`/the local `db` service, never from staging credentials.

- [x] Add failing subprocess tests for missing `STAGING_SSH_TARGET`, declined confirmation, dirty/absent local Compose database service, and non-interactive execution without exact confirmation. Assert no SSH dump or destructive local SQL occurs in these cases.
- [x] Add `var/backups/` to `.gitignore` and a small `Makefile` target that uses `sh scripts/clone-staging-db.sh` without embedding secrets or hostnames.
- [x] Implement strict shell mode, dependency checks (`ssh`, `docker`, Compose), current-project resolution through `docker compose config`, TTY confirmation showing the exact local Compose project/database, and traps for temporary files. Reject `DB_HOST` values other than the Compose-local `db` service to prevent accidental restoration into staging or another remote host.
- [x] Run `pytest -q tests/deployment/test_clone_staging_database.py -k guard`; expected GREEN is every guard case passed and no destructive recorder call before confirmation.

### Task 2: Stream and validate a staging logical backup

**Files:** modify `scripts/clone-staging-db.sh`; test `tests/deployment/test_clone_staging_database.py`.

**Interfaces:** remote command uses the fixed Compose project `photo-prjct-staging` and `/opt/photo-prjct/docker-compose.prod.yml`; it emits only the custom dump on stdout. Local outputs are `<timestamp>.dump`, `<timestamp>.dump.sha256`, and `<timestamp>.metadata` with timestamp, staging host label, PostgreSQL major, database name, and dump table-of-contents summary—never credentials.

- [x] Add failing tests proving the remote command executes `docker compose ... exec -T db pg_dump --format=custom --no-owner --no-acl`, quotes the configured database name safely, writes first to a same-directory temporary file, and never places a password in arguments/output.
- [x] Add failure tests for interrupted SSH, zero-byte/truncated dumps, `pg_restore --list` failure, insufficient disk space, and staging/local PostgreSQL major mismatch. Assert the prior local database has not been touched.
- [x] Implement a remote PostgreSQL major query before dump; require major 16 locally and remotely. Stream into the temporary file, validate nonzero size and `pg_restore --list`, then atomically rename it and write checksum/metadata with mode 0600.
- [x] Run `pytest -q tests/deployment/test_clone_staging_database.py -k dump`; expected GREEN is all dump and pre-restore failure cases passed.

### Task 3: Replace and restore only the local database

**Files:** modify `scripts/clone-staging-db.sh`; test `tests/deployment/test_clone_staging_database.py`.

**Interfaces:** local SQL runs only via `docker compose exec -T db`; `postgres` is the maintenance database; the target name/user come from the rendered local Compose configuration; restore uses `pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl` against the newly recreated target.

- [x] Add failing recorder tests that the script starts only `db`, waits for `pg_isready`, snapshots the existing local database to a temporary safety dump, terminates target connections, drops/recreates exactly the resolved target, and restores the staging dump. Assert there is no `down`, `--volumes`, volume removal, wildcard database name, or remote host target.
- [x] Add a real PostgreSQL 16 integration test with distinct staging/local marker rows. Expected result is staging markers present, old local markers absent, and owner/ACL normalized to the local application user.
- [x] Implement the local pre-restore safety dump in the same backup directory. If restore fails, automatically recreate the target from the safety dump; report whether recovery succeeded, retain both dumps, and exit non-zero. Never claim success after recovery from a failed staging restore.
- [x] Run `pytest -q tests/deployment/test_clone_staging_database.py -k restore`; expected GREEN includes success, restore failure with successful local recovery, and double-failure diagnostics.

### Task 4: Verify migration readiness and document the workflow

**Files:** modify `scripts/clone-staging-db.sh`; modify `README.md`; modify `docs/engineering-jobs.md`; test `tests/deployment/test_clone_staging_database.py`.

**Interfaces:** after restore, the script runs read-only validation in a one-off `web` container with its entrypoint overridden: `python manage.py showmigrations --plan` and `python manage.py makemigrations --check --dry-run`. It does not run `migrate` or start the normal mutating web entrypoint.

- [x] Add failing tests proving validation overrides the Docker image entrypoint and never invokes `migrate`, `bootstrap_photographer_group`, or normal `docker compose up web`.
- [x] Implement restored-database checks: database connectivity, nonempty/valid `django_migrations`, `showmigrations --plan`, and `makemigrations --check --dry-run`. If code is behind staging schema, fail with a message to update the branch; if model changes already exist, retain the restored database but report that the checkout is not a clean migration baseline.
- [x] Document prerequisites, the one-command flow, backup location, confirmation automation, retry from an existing dump, and the distinction between this local destructive operation and the read-only direct-staging mode.
- [x] Update EJ-010 only as partial local restore evidence; keep it `Candidate` because this workflow does not establish scheduled backups, retention, RPO/RTO, media recovery, or a staging disaster-recovery drill.
- [x] Run the complete targeted test; expected GREEN is all cases passed.

### Final task: Architecture and ADR reconciliation

- [x] Update `docs/architecture.md` implemented operations to state that staging logical dumps can be restored into an isolated local Compose database for migration development, without describing it as the service backup strategy.
- [x] Confirm conformance to ADR 0002 and ADR 0003: conforms; no new ADR is required.
- [x] Keep backup targets, retention, RPO/RTO, encryption-at-rest policy, and disaster recovery in the existing open-decision set; stop for a separate ADR/spec before expanding this developer workflow into production backup operations.

### Final review hardening

- [x] Enforce the local Docker-engine boundary for Docker Desktop Unix sockets and loopback TCP,
  with sanitized rejection of SSH, non-loopback TCP/HTTP(S), and unknown schemes.
- [x] Serialize clone processes with a portable atomic `mkdir` lock keyed by the resolved Compose
  project/database, retaining stale locks for deliberate operator inspection.
- [x] Quiesce a running normal `web` service before the local safety dump and destructive SQL, never
  start it in the workflow, and leave it stopped with an explicit restart instruction.
- [x] Recover the exact local safety dump when DROP, CREATE, or staging restore fails; keep the clone
  exit non-zero even after successful recovery.
- [x] Add separate opt-in PostgreSQL 16 integrations for marker/owner/ACL restoration and for the
  actual project web image, migrated source schema, `django_migrations`, `showmigrations --plan`,
  and `makemigrations --check --dry-run`.

## Verification

```bash
pytest -q tests/deployment/test_clone_staging_database.py tests/test_repository_foundation.py
RUN_POSTGRES_INTEGRATION=1 pytest -q tests/deployment/test_clone_staging_database.py -k restore_postgres_16_integration
RUN_DJANGO_CLONE_INTEGRATION=1 pytest -q tests/deployment/test_clone_staging_database.py -k real_django_postgres_16_integration
sh -n scripts/clone-staging-db.sh
ruff check .
mypy
```

Expected: all tests and checks pass; contract tests prove the local Docker boundary, serialized
local-only destructive scope, web quiescing, pre-destruction dump validation, safety
backup/recovery, secret-free output, and mutation-free Django verification. The opt-in integrations
prove PostgreSQL 16 marker/owner/ACL normalization and the actual project image's readiness checks
against a restored migrated schema without staging network contact.

One authorized end-to-end acceptance run:

```bash
STAGING_SSH_TARGET=<user>@<staging-host> make db-clone-staging
docker compose exec db psql -U app -d app -c 'select count(*) from django_migrations;'
docker compose run --rm --entrypoint python web manage.py makemigrations --check --dry-run
```

Expected: the command creates a timestamped validated dump, replaces only local `app`, reports successful validation, the migration table is readable, and Django reports `No changes detected` on an unchanged branch.

This authorized live-staging acceptance remains explicitly unperformed. The completed plan is
supported by repository tests, isolated PostgreSQL 16 restore evidence, and isolated real-Django
migration-readiness evidence only; it is not live-staging evidence.

## Operational impact and rollout

This is an on-demand developer workflow and does not change deployed application behavior. It requires existing staging SSH authorization and enough local disk for the staging dump plus a local safety dump. Logical dumps may contain personal data; keep `var/backups/` untracked with mode 0600, use encrypted developer disks, do not upload dumps to shared services, and define a short manual deletion policy (recommended: remove when the migration branch is complete). A separate decision is required for formal retention and automated service backups.

## Rollback

Revert the script, Makefile, documentation, and ignored-path changes. Existing local dumps remain ignored and must be deleted explicitly by their owner. If a clone attempt fails, the script restores the pre-operation local safety dump; if that recovery also fails, retain both dumps and recreate only the named local database before restoring the safety dump manually. Staging is read-only throughout and requires no data rollback.

## Open questions

- None. The plan uses logical custom-format dumps for migration portability, preserves a local safety dump before replacement, and deliberately leaves formal service backup policy for separate architecture work.
