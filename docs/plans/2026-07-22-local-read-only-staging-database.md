# Local Read-only Staging Database Implementation Plan

- Date: 2026-07-22
- Status: Draft
- Owner: project maintainer
- Related specification: none
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented), [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0002](../adr/0002-postgresql-system-of-record.md), [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md)
- ADR impact: Conforms to ADR 0002 and ADR 0003. The access path is a reversible operational detail: PostgreSQL remains the system of record and the staging database remains in the existing Compose deployment on the Yandex Cloud VM. No new ADR is required.

## Goal

Allow a developer to run the local Django application against current staging data for work that does not require data or schema changes, while making database writes impossible at the PostgreSQL permission boundary.

## Scope

### In scope

- A dedicated `staging_local_reader` PostgreSQL login with read-only privileges and read-only transaction defaults.
- A PostgreSQL port published only on the staging VM loopback interface and reached through SSH local forwarding.
- An ignored local environment file and a Compose override that start Django without migrations, bootstrap commands, or `collectstatic` against staging.
- One documented command to open the tunnel and one documented command to start the read-only development mode.
- Automated repository and shell-contract tests for the security boundaries.

### Out of scope

- Direct public exposure of PostgreSQL or an inbound cloud firewall rule for port 5432/55432.
- Any write access to staging from a developer machine, including Django sessions, Admin mutations, management commands, migrations, and test execution.
- Object Storage credential copying or write access. The mode may use separately issued read-only media credentials if rendering private staging media is later required.
- Production database access.

## Acceptance criteria

- `ssh -N -L 55432:127.0.0.1:55432 "$STAGING_SSH_TARGET"` is the only network path from the developer machine to staging PostgreSQL; the VM has no non-loopback database listener.
- `docker compose -f docker-compose.staging-db.yml up --build web` starts Django with `.env.staging-db`, defines no local `db` service, and does not execute `migrate`, `bootstrap_photographer_group`, or `collectstatic`.
- The application can execute `SELECT` through `staging_local_reader`.
- `INSERT`, `UPDATE`, `DELETE`, DDL, `SELECT ... FOR UPDATE`, sequence changes, and temporary-object creation fail even if application code attempts them.
- Missing `.env.staging-db`, a missing tunnel, or invalid credentials fail closed without falling back to the local database.
- Normal `docker compose up --build -d` remains unchanged and continues to use the local `db` service and startup initialization.

## Implementation

### Task 1: Provision a least-privilege staging database login

**Files:** create `deploy/reconcile-staging-readonly-db-role.sh`; modify `.github/workflows/deploy.yml`; modify `.env.example`; test `tests/deployment/test_staging_readonly_database.py`.

**Interfaces:**

- Consumes `STAGING_READONLY_DB_PASSWORD` from the GitHub `staging` Environment and existing `DB_NAME`/admin connection inside the staging `db` container.
- Produces login `staging_local_reader`, default transaction read-only behavior, and repeatable grants on the configured database and `public` schema.

- [ ] Add a failing subprocess test that replaces `docker` with a recorder and proves the script refuses an empty password, accepts only `DEPLOYMENT_TARGET=staging`, never prints the password, and executes SQL through `docker compose --project-name photo-prjct-staging -f docker-compose.prod.yml exec -T db psql`.
- [ ] Add an integration-style SQL contract test against PostgreSQL 16. It must create a representative table and sequence, run the reconciliation twice, prove `SELECT` succeeds, and prove writes, DDL, `nextval`, `SELECT FOR UPDATE`, and temporary tables fail for `staging_local_reader`.
- [ ] Run `pytest -q tests/deployment/test_staging_readonly_database.py`; expected RED is a missing script.
- [ ] Implement the idempotent script. Use `ALTER ROLE staging_local_reader LOGIN PASSWORD :'reader_password'`, `ALTER ROLE ... SET default_transaction_read_only = on`, `ALTER ROLE ... SET statement_timeout = '30s'`, revoke database/schema creation, grant `CONNECT`, `USAGE`, `SELECT` on all current tables and sequences, and set matching `ALTER DEFAULT PRIVILEGES` for objects subsequently created by the staging application owner. Revoke database `TEMP` from `PUBLIC`, grant it explicitly back to the application owner, and confirm no other existing login loses required access. Pass the password through a mode-0600 temporary file or psql variable input, install cleanup traps, and emit only sanitized status markers.
- [ ] Add `STAGING_READONLY_DB_PASSWORD` to the staging deploy step without writing it into the canonical application `.env`; call reconciliation after `db` is healthy and before exposing the loopback listener. Update `.env.example` with comments that the value is deployment-only and must not be placed in the application environment.
- [ ] Run the targeted test; expected GREEN is all cases passed and no secret appears in captured stdout/stderr.

### Task 2: Add the loopback-only VM endpoint

**Files:** create `docker-compose.staging-db-access.yml`; modify `.github/workflows/deploy.yml`; modify `tests/test_repository_foundation.py`.

**Interfaces:** the staging deployment composes `docker-compose.prod.yml`, `docker-compose.https.yml`, and `docker-compose.staging-db-access.yml`; the overlay adds `127.0.0.1:55432:5432` to `db` and no other service or network.

- [ ] Add failing YAML-contract assertions that the deploy copies and applies the overlay, the only added port is exactly `127.0.0.1:55432:5432`, and neither `0.0.0.0` nor bare host port 55432 occurs.
- [ ] Run `pytest -q tests/test_repository_foundation.py -k 'staging and database'`; expected RED identifies the missing overlay.
- [ ] Add the overlay and include it only for staging, leaving production and the temporary HTTP recovery overlay unchanged.
- [ ] Extend deployment verification to inspect the bound address with `docker compose port db 5432` and fail unless it is loopback port 55432.
- [ ] Run the targeted repository test and `docker compose -f docker-compose.prod.yml -f docker-compose.staging-db-access.yml config`; expected GREEN is passing tests and rendered loopback-only publication.

### Task 3: Add a mutation-free local Django mode

**Files:** create `docker-compose.staging-db.yml`; modify `.gitignore`; modify `.env.example`; test `tests/test_repository_foundation.py` and `tests/deployment/test_entrypoint.py`.

**Interfaces:** `.env.staging-db` contains `DB_HOST=host.docker.internal`, `DB_PORT=55432`, staging database name, `DB_USER=staging_local_reader`, and its local secret; the dedicated Compose file defines only `web`, reuses the normal build context/source mount/port, and replaces the image entrypoint with `python manage.py runserver 0.0.0.0:8000`.

- [ ] Add failing Compose-contract tests proving the staging-db file defines only `web`, uses only `.env.staging-db`, and overrides `entrypoint` rather than merely `command` (the image `ENTRYPOINT` currently performs mutations before its command).
- [ ] Add `.env.staging-db` to `.gitignore`; add a non-secret `.env.staging-db.example` rather than real credentials. Set `DEBUG=True`, local `ALLOWED_HOSTS`, the tunnel host/port, and `MEDIA_STORAGE_BACKEND=filesystem` by default.
- [ ] Add a standalone Compose file containing only `web`; include `extra_hosts: ["host.docker.internal:host-gateway"]` for Linux compatibility and replace the entrypoint with the development server command.
- [ ] Run `docker compose -f docker-compose.staging-db.yml config`; expected output has no `db` service, no startup entrypoint script for `web`, no `depends_on`, and no staging secret in tracked files.
- [ ] Run the targeted tests; expected GREEN is all Compose and entrypoint regression cases passed.

### Task 4: Document and verify the developer workflow

**Files:** modify `README.md`; create `deploy/check-staging-readonly-db.sh`; test `tests/deployment/test_staging_readonly_database.py`.

**Interfaces:** the check script consumes `.env.staging-db` and tunnel port 55432; it returns zero only after a read succeeds and a rolled-back write probe is rejected as read-only.

- [ ] Add failing tests for missing environment file, closed tunnel, successful `SELECT 1`, and rejection of a write probe. Assert diagnostics contain no password or full connection URI.
- [ ] Implement the check with the PostgreSQL 16 client container, `ON_ERROR_STOP=1`, a short connect timeout, and an explicit write attempt inside a transaction. Treat a successful write as a fatal safety failure.
- [ ] Document: copy `.env.staging-db.example` to ignored `.env.staging-db`; export `STAGING_SSH_TARGET`; open `ssh -N -L 55432:127.0.0.1:55432 "$STAGING_SSH_TARGET"`; run the safety check; then run `docker compose -f docker-compose.staging-db.yml up --build web`. State that login/session creation and other authenticated flows that persist state are intentionally unavailable in this mode.
- [ ] Document prominent prohibitions: do not run tests, `manage.py migrate`, mutating management commands, Admin edits, or background jobs in this mode; use the cloned local database workflow for migration work.
- [ ] Run `pytest -q tests/deployment/test_staging_readonly_database.py tests/test_repository_foundation.py tests/deployment/test_entrypoint.py`; expected GREEN is all selected tests passed.

### Final task: Architecture and ADR reconciliation

- [ ] Update `docs/architecture.md` implemented operations with the SSH-tunneled, PostgreSQL-enforced read-only development path and its no-public-port boundary.
- [ ] Update `docs/engineering-jobs.md` with a maintainer job and repository-test evidence only after verification; do not claim live staging validation until the role, tunnel, and denial probes have actually run there.
- [ ] Confirm the delivered behavior still conforms to ADR 0002 and ADR 0003 and does not expose a new public service. Record “conforms; no new ADR” in the pull request.

## Verification

```bash
pytest -q tests/deployment/test_staging_readonly_database.py tests/test_repository_foundation.py tests/deployment/test_entrypoint.py
docker compose -f docker-compose.staging-db.yml config
docker compose -f docker-compose.prod.yml -f docker-compose.staging-db-access.yml config
ruff check .
mypy
```

Expected: all tests and static checks pass; rendered staging access binds only `127.0.0.1:55432`; rendered local read-only mode bypasses the mutating image entrypoint and does not start or depend on local `db`.

Live staging acceptance, performed explicitly by an authorized operator after deployment:

```bash
ssh -N -L 55432:127.0.0.1:55432 "$STAGING_SSH_TARGET"
sh deploy/check-staging-readonly-db.sh
```

Expected: the tunnel stays open, a read succeeds, the deliberate write is rejected with a read-only transaction/permission error, and no database secret is printed.

## Operational impact and rollout

Add `STAGING_READONLY_DB_PASSWORD` as a rotated staging-only secret. Deploy the role and validate denial probes first; then activate the loopback Compose overlay; finally document developer access. Existing web traffic and the application database login are unchanged. Rotate the reader credential when access is revoked or suspected compromised. The approach requires SSH access to the VM and deliberately adds no Yandex Cloud firewall ingress.

## Rollback

Remove the staging access overlay and redeploy to remove the loopback listener. Run `ALTER ROLE staging_local_reader NOLOGIN;` immediately to revoke access; drop the role only after revoking its grants/default privileges if permanent removal is required. Delete local `.env.staging-db` copies and close SSH tunnels. These actions do not modify application data.

## Open questions

- None. The plan chooses PostgreSQL-enforced read-only access over a convention-only application flag and chooses SSH forwarding over public database exposure.
