# Remove One-Time Staging Reset Implementation Plan

- Date: 2026-07-12
- Status: Draft
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md), [ADR 0005](../adr/0005-promote-images-through-staging.md)

## Goal

Remove the completed one-time destructive staging migration so every future deployment preserves
the staging PostgreSQL volume and contains only steady-state release steps.

## Scope

### In scope

- Remove the `.staging-reset-v1` conditional and both destructive `docker compose down --volumes`
  commands from the staging workflow.
- Replace migration-specific test assertions with a contract that destructive reset commands and
  the reset marker are absent from steady-state deployment.
- Replace the README migration note with the durable rule that normal deployments preserve the
  `photo-prjct-staging_pgdata` volume.
- Validate one post-cleanup staging deployment and confirm both services become healthy without
  volume removal or recreation.

### Out of scope

- Deleting `/opt/photo-prjct/.staging-reset-v1` from the VM; after the workflow block is removed the
  marker is inert and avoiding an SSH cleanup keeps this change non-mutating outside deployment.
- Changing the staging VM, disk, VPC, IP, IAM, GitHub Environment, secrets, Compose project name,
  production workflow, database schema, or Yandex Cloud billing.
- Pruning Docker images or other host-wide resources.

## Acceptance criteria

- `.github/workflows/deploy.yml` contains no `.staging-reset-v1`, legacy `photo-prjct` cleanup, or
  `down --volumes` command.
- The workflow still pulls the immutable SHA image, runs `photo-prjct-staging` with `up -d --wait`,
  and records `deployed-image` only after health succeeds.
- Repository tests fail if a destructive reset command or migration marker is reintroduced.
- README describes steady-state volume preservation without instructions for reactivating the old
  migration.
- CI passes, and the first deployment of this cleanup shows no `Volume ... Removing`, `Removed`, or
  `Creating` event while PostgreSQL and web finish `Healthy`.

## Implementation

### Task 1: Lock the steady-state safety contract

**Files:** `tests/test_repository_foundation.py`.

- [ ] Change `test_deployment_workflows_separate_staging_and_production` to assert that
  `.staging-reset-v1`, `--project-name photo-prjct down`, and `down --volumes` are absent.
- [ ] Keep the positive assertions for the `main` trigger, staging environment, concurrency group,
  production approval environment, and production concurrency group.
- [ ] Run
  `.venv/bin/pytest tests/test_repository_foundation.py::test_deployment_workflows_separate_staging_and_production -q`
  and confirm it fails because the reset block is still present.

### Task 2: Remove the completed migration path

**Files:** `.github/workflows/deploy.yml`, `README.md`.

- [ ] Delete only the conditional block beginning with
  `if [ ! -f /opt/photo-prjct/.staging-reset-v1 ]; then` and ending with its matching `fi`.
- [ ] Preserve `.env` creation, optional GHCR login, immutable image pull, `up -d --wait`, deployed
  image recording, and failure diagnostics unchanged.
- [ ] Replace the README reset-marker paragraph with a statement that normal deployments reuse the
  `photo-prjct-staging` Compose project and preserve `photo-prjct-staging_pgdata`.
- [ ] Run the targeted repository test and confirm it passes.

### Task 3: Verify code and configuration

**Files:** all files changed by Tasks 1 and 2.

- [ ] Run `.venv/bin/ruff format --check .` and expect all files formatted.
- [ ] Run `.venv/bin/ruff check .` and expect no lint errors.
- [ ] Run `.venv/bin/mypy` and expect no type errors.
- [ ] Run `.venv/bin/pytest --cov --cov-report=term-missing` against PostgreSQL and expect all tests
  to pass with coverage at or above 80%.
- [ ] Run `.venv/bin/python src/backend/manage.py check` and expect no issues.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` and expect
  `No changes detected`.
- [ ] Run
  `APP_IMAGE=ghcr.io/peter-nikitin/photo-prjct:test docker compose -f docker-compose.prod.yml config -q`
  and expect exit code zero.
- [ ] Run `git diff --check` and expect no output.

### Task 4: Roll out and verify steady state

**Files:** None; GitHub Actions and deployment evidence only.

- [ ] Merge the reviewed cleanup PR into `main`.
- [ ] Observe the CI and `Deploy staging` runs for the merge SHA until both reach `success`.
- [ ] Inspect the deployment log and confirm there is no volume remove/create event, both
  `photo-prjct-staging-db-1` and `photo-prjct-staging-web-1` report `Healthy`, and the remote script
  exits successfully.
- [ ] If deployment fails, retain the existing staging volume, collect `docker compose ps` and web
  logs through the workflow diagnostics, and stop before any destructive recovery.

## Verification

Use the exact commands in Tasks 1–4. Successful completion requires green local checks, green GitHub
CI, a green staging deployment for its merge SHA, no destructive volume event in that deployment
log, and healthy database and web containers.

## Operational impact and rollout

This is a safety cleanup: future staging deployments can no longer erase PostgreSQL through the
completed migration path. The first rollout uses the existing `photo-prjct-staging` containers and
volume created successfully by run `29203980543`. No downtime beyond normal Compose replacement is
expected. The inert marker file may remain on the VM.

## Rollback

Revert the cleanup commit only if repository validation reveals an unexpected dependency on the
marker. Do not restore the destructive `down --volumes` behavior to recover an application failure;
use image rollback or targeted container diagnostics while preserving the database volume.

## Open questions

- None.
