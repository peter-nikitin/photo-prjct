# HTTPS Edge Implementation Plan

- Date: 2026-07-13
- Status: Approved
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented), [security boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [0003](../adr/0003-docker-compose-yandex-cloud.md), [0005](../adr/0005-promote-images-through-staging.md), [0007](../adr/0007-nginx-certbot-https-edge.md)

## Goal

Serve the staging public domain through an automatically renewed Let's Encrypt certificate and an
Nginx reverse proxy while keeping Django private to the Compose network.

## Scope

### In scope

- Nginx HTTP-to-HTTPS routing, ACME challenge delivery, proxy headers, and baseline security headers.
- Certbot first issuance, persistent certificate state, and periodic webroot renewal.
- GitHub deployment configuration and post-deploy HTTPS verification.
- Django proxy-security configuration, documentation, ADR, and configuration tests.

### Out of scope

- CDN, WAF, rate limits, production VM provisioning, static-media offload, or multi-instance load balancing.

## Acceptance criteria

- Only Nginx publishes host ports 80 and 443; `web` has no host port.
- HTTP redirects to HTTPS and ACME challenges stay reachable on HTTP.
- HTTPS health succeeds with a certificate for `PUBLIC_DOMAIN`.
- A missing certificate is issued once; later deploys preserve certificate volumes and renewals run without manual action.
- Django recognizes requests proxied as HTTPS and all existing test/check gates pass.

## Implementation

### Task 1: Record and test the public-edge contract

**Files:** `docs/adr/0007-nginx-certbot-https-edge.md`, `docs/adr/README.md`, `docs/architecture.md`, `src/backend/picflow/tests/test_repository_foundation.py`.

- [ ] Add failing repository-contract tests for Nginx, Certbot, hidden Django port, and public-domain configuration.
- [ ] Run the target tests and confirm they fail before the Compose/workflow implementation exists.
- [ ] Record the accepted decision and implemented topology.
- [ ] Run the target tests and confirm they pass.

### Task 2: Add the Compose HTTPS edge

**Files:** `docker-compose.prod.yml`, `deploy/nginx/default.conf`, `deploy/nginx/reload-nginx.sh`, `deploy/certbot/renew-certificates.sh`.

- [ ] Add Nginx, Certbot, shared ACME webroot, and persistent certificate volumes.
- [ ] Keep only Nginx on host ports 80/443; configure internal proxying to `web:8000`.
- [ ] Validate rendered Compose and Nginx configuration locally without contacting Let's Encrypt.

### Task 3: Make deployment bootstrap, renew, and verify HTTPS

**Files:** `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`, `.env.example`, `README.md`.

- [ ] Add non-secret public-domain configuration and secret certificate-notification email to both deployment paths.
- [ ] Bootstrap a missing certificate with standalone HTTP-01 before the Nginx service binds port 80.
- [ ] Start/reconcile the stack, then verify HTTPS health through the configured domain and capture edge diagnostics on failure.
- [ ] Document environment setup, rollout, renewal check, and rollback.

### Task 4: Run regression checks

**Files:** all changed files.

- [ ] Run formatting, lint, type checks, Django checks, migrations dry-run, tests with coverage, Compose config, and Nginx syntax validation.
- [ ] Review the diff for secrets and verify no certificate material is tracked.

## Verification

Run `ruff format --check .`, `ruff check .`, `mypy`, `pytest --cov --cov-report=term-missing`, `python src/backend/manage.py check`, `python src/backend/manage.py makemigrations --check --dry-run`, `docker compose -f docker-compose.prod.yml config`, and an Nginx `nginx -t` container check. On staging, verify `curl -I http://$PUBLIC_DOMAIN/` returns a redirect, `curl --fail https://$PUBLIC_DOMAIN/health/` returns JSON, and `certbot renew --dry-run` succeeds.

## Operational impact and rollout

Before merge, add `PUBLIC_DOMAIN=xn--80aimcrbmaa3bu9m.xn--p1ai` as a `staging` Environment variable and add `LETSENCRYPT_EMAIL` as a `staging` Environment secret. Confirm DNS still resolves to the staging VM and ports 80/443 are not occupied. Merge starts the first certificate issuance and briefly replaces the staging application. The deploy must fail if issuance or the HTTPS probe fails.

## Rollback

Revert the merge and rerun staging deployment to restore the preceding Compose/workflow. Do not delete Docker certificate/account volumes; they are required for renewal history and recovery. If initial issuance fails, inspect Nginx/Certbot logs and DNS/port reachability before retrying to avoid rate limits.

## Open questions

None.
