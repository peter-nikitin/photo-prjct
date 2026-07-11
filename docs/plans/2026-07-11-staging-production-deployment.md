# Staging and Production Deployment Implementation Plan

- Date: 2026-07-11
- Status: Complete
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md), [ADR 0005](../adr/0005-promote-images-through-staging.md)

## Goal

Deploy every `main` merge to staging and provide an approval-gated promotion of the verified image
to a separate future production VM without provisioning or changing cloud resources.

## Scope

### In scope

- Immutable Compose image selection and application health check.
- Automatic staging and manual production GitHub Actions workflows.
- Project Yandex Cloud operations skill with pricing and destructive-operation gates.
- Architecture, deployment, sizing, and operator documentation.

### Out of scope

- Running `yc` mutations, creating production resources, configuring GitHub secrets/environments,
  DNS/TLS, monitoring products, or backup products.

## Acceptance criteria

- A push to `main` builds a SHA-tagged image, deploys it to the `staging` GitHub Environment, and
  records the successful image reference on the staging host.
- Manual production promotion verifies that reference on staging, pauses at the `production` GitHub
  Environment, and deploys the same reference without rebuilding.
- Compose refuses to deploy without `APP_IMAGE` and exposes an application health check.
- The project skill inventories safely and blocks any potential pricing mutation until an explicit
  command-specific confirmation.
- Repository and Django checks pass.

## Implementation

### Task 1: Record the deployment decision and repository contracts

**Files:** `docs/adr/0005-promote-images-through-staging.md`, `docs/adr/README.md`,
`docs/architecture.md`, `tests/test_repository_foundation.py`.

- [x] Add failing contracts for ADR, skill, workflow, and immutable Compose behavior.
- [x] Record the accepted environment and promotion decision.
- [x] Run targeted tests and confirm the contracts pass.

### Task 2: Add health and immutable Compose behavior

**Files:** `src/backend/config/views.py`, `src/backend/config/urls.py`,
`src/backend/picflow/tests/test_views.py`, `docker-compose.prod.yml`.

- [x] Add the failing health endpoint test.
- [x] Implement the database-independent endpoint and container health check.
- [x] Run targeted Django and repository tests.

### Task 3: Separate staging deployment and production promotion

**Files:** `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`, `README.md`.

- [x] Trigger staging on `main`, serialize it, deploy a SHA tag, and record the successful image.
- [x] Verify the recorded staging image before approval-gated production promotion.
- [x] Document required GitHub Environments and secrets.

### Task 4: Add the project Yandex Cloud skill

**Files:** `.agents/skills/manage-yandex-cloud/SKILL.md`,
`.agents/skills/manage-yandex-cloud/agents/openai.yaml`,
`.agents/skills/manage-yandex-cloud/references/inventory.md`.

- [x] Add safe discovery, classification, confirmation, execution, and verification rules.
- [x] Record only known non-secret cloud and folder identifiers; leave timed-out inventory unknown.
- [x] Validate metadata and pressure scenarios through repository tests.

### Task 5: Verify the complete change

**Files:** all changed files.

- [x] Run `ruff format --check .`, `ruff check .`, `mypy`, and the complete pytest suite.
- [x] Run Django system and migration drift checks with local PostgreSQL values matching the shared
  environment configuration model.
- [x] Run `git diff --check` and inspect the PR diff.

## Verification

Run the required checks from `AGENTS.md`. Expected: every command exits zero, pytest remains above
80% coverage, Django reports no issues, and no migrations are generated.

## Operational impact and rollout

Create GitHub Environments named `staging` and `production`. Put host, SSH, application, database,
and GHCR read credentials in each environment. Configure required reviewers for `production` before
enabling promotion. Merge enables automatic staging deployment; production remains inert until its
environment and VM exist.

## Rollback

Revert the workflow and Compose changes to restore manual single-target deployment. On a host,
restore the previous recorded `APP_IMAGE` and run Compose after verifying migration compatibility.
No Yandex Cloud resource rollback is needed because this plan performs no cloud mutations.

## Open questions

- None.
