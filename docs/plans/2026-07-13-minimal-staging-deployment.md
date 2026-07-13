# Minimal Staging Deployment Implementation Plan

- Date: 2026-07-13
- Status: Approved
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented)
- Related ADRs: [0003](../adr/0003-docker-compose-yandex-cloud.md), [0005](../adr/0005-promote-images-through-staging.md), [0007](../adr/0007-nginx-certbot-https-edge.md), [0009](../adr/0009-separate-staging-http-edge.md)

## Goal

Make a staging deployment succeed only when the requested immutable web image is running and its HTTP Nginx edge returns a healthy response.

## Scope

### In scope

- Split the staging HTTP edge from the future production HTTPS edge.
- Replace duplicate inline deployment shells with one versioned POSIX shell script.
- Make image identity and edge health the release gate and print diagnostics on every failure.
- Update deployment documentation, architecture summary, and repository contract tests.

### Out of scope

- Repairing public DNS, issuing a staging certificate, changing Yandex Cloud resources, or provisioning production.
- Changing database schema, application behavior, or media storage.

## Acceptance criteria

- The rendered staging stack contains PostgreSQL, Django, and Nginx only; Nginx publishes port 80 and Django publishes no host port.
- The staging workflow has no HTTPS toggle, Certbot bootstrap, certificate volume, or TLS probe.
- A deployment writes `deployed-image` only after the running web container image equals `APP_IMAGE` and Nginx serves `/health/` with HTTP 200.
- A non-zero Compose startup exit code is logged and does not bypass image and edge verification;
  stale services from the replaced topology are removed.
- A failed rollout prints Compose status and service logs before exiting non-zero.
- Production retains a separate HTTPS/Certbot overlay and promotion still requires the staging image marker.

## Implementation

### Task 1: Record the deployment boundary

**Files:** `docs/adr/0009-separate-staging-http-edge.md`, `docs/adr/README.md`, `docs/architecture.md`, `docs/plans/2026-07-13-minimal-staging-deployment.md`.

- [ ] Supersede the temporary runtime-toggle decision with explicit environment overlays.
- [ ] Describe the implemented staging topology and the deferred production HTTPS validation.

### Task 2: Create testable Compose overlays

**Files:** `docker-compose.prod.yml`, `docker-compose.staging.yml`, `docker-compose.production.yml`, `deploy/nginx/staging.conf`, `tests/test_repository_foundation.py`.

- [ ] Add failing repository-contract tests for the minimal staging overlay.
- [ ] Create the shared application Compose file and the HTTP-only staging overlay.
- [ ] Keep the existing HTTPS Nginx/Certbot configuration in the production overlay.
- [ ] Run the focused tests and rendered Compose validation.

### Task 3: Centralize deployment verification

**Files:** `deploy/apply-deployment.sh`, `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`, `tests/test_repository_foundation.py`.

- [ ] Add failing tests that require one versioned deployment script and image identity checks.
- [ ] Create a POSIX script that writes the remote environment, pulls images, applies the selected overlays, captures Compose status, waits for the edge, verifies the web image, and emits diagnostics on failure.
- [ ] Reduce each workflow's remote command to invoking that script with an explicit environment.
- [ ] Run shell syntax and focused contract tests.

### Task 4: Document operation and verify regression gates

**Files:** `.env.example`, `README.md`, `docs/architecture.md`, all changed files.

- [ ] Remove the obsolete staging HTTPS toggle from operator instructions.
- [ ] State that staging health is a VM-local HTTP edge probe while DNS remains unroutable.
- [ ] Run all repository quality checks, Compose rendering, Nginx syntax checks, and a local production-like staging stack health probe.

## Verification

Run `ruff format --check .`, `ruff check .`, `mypy`, `pytest --cov --cov-report=term-missing`, `python src/backend/manage.py check`, `python src/backend/manage.py makemigrations --check --dry-run`, `docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml config`, and `sh -n deploy/apply-deployment.sh`. Locally, start the staging overlays with a test image and verify `curl --fail http://localhost/health/` and the inspected web image.

## Operational impact and rollout

Merging deploys the HTTP-only staging overlay. The VM retains the existing PostgreSQL volume. The staging GitHub Environment no longer needs `ENABLE_HTTPS` or `LETSENCRYPT_EMAIL`; leaving either value configured is harmless because the workflow does not read it. The public DNS record remains an external blocker to public reachability and must be repaired before production HTTPS work.

## Rollback

Revert the merge and rerun the staging workflow. This restores the prior Compose files and workflow while retaining the database and any existing certificate volumes. No migration or data transformation is involved.

## Open questions

None.
