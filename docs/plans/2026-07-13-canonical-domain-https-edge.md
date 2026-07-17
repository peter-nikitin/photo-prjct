# Canonical Domain HTTPS Edge Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` (if subagents
> are available) or `superpowers:executing-plans` to implement this plan. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Activate trusted HTTPS for `findme-photo.ru` on the current single staging VM through a
small shared Nginx/Certbot edge while retaining the HTTP edge as a manual recovery fallback.

**Architecture:** Combine `docker-compose.prod.yml` with an environment-neutral HTTPS overlay. The
deployment issues a missing certificate once, applies one immutable image, validates local health
and public behavior with `curl`, writes `deployed-image` only after success, and restores the prior
image inside the same process on failure. GitHub workflows do not manage a separate release state.

**Tech Stack:** Docker Compose, Nginx 1.27 Alpine, Certbot 2.11, POSIX shell, Django/Gunicorn,
GitHub Actions, RU-CENTER DNS, Yandex Cloud VM.

---

- Date: 2026-07-13
- Status: HTTPS activation complete; browser, internal-state, and renewal follow-ups remain; manual
  HTTP recovery fallback retained
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented),
  [deployment domain assignment](../architecture.md#deployment-domain-assignment--accepted)
- Related ADRs: [0003](../adr/0003-docker-compose-yandex-cloud.md),
  [0005](../adr/0005-promote-images-through-staging.md),
  [0007](../adr/0007-nginx-certbot-https-edge.md),
  [0011](../adr/0011-use-minimal-shared-https-rollout.md)
- Design spec:
  [Canonical domain HTTPS edge design](../superpowers/specs/2026-07-13-canonical-domain-https-edge-design.md)

## Scope

### In scope

- One shared HTTPS overlay for current staging and future production.
- Canonical apex routing and optional `www` redirect.
- One-shot certificate issuance when certificate state is absent and periodic renewal afterward.
- Exact redirect and trusted HTTPS health smoke checks using `curl`.
- In-process restoration of the prior application image after a failed apply.
- Separate preparation and activation releases, with the HTTP edge retained only for manual
  recovery.
- Focused deployment tests and stable operational documentation.

### Out of scope

- Automated DNS release gating or automatic certificate changes when hostnames change.
- A workflow-managed release state or a separate recovery workflow step.
- Yandex Cloud mutations, production provisioning, or VM pricing changes.
- CDN, WAF, load balancer, managed certificates, IPv6, or multiple public endpoints.
- Django domain behavior, database schema, media storage, or product features.
- `staging.findme-photo.ru` before separate production infrastructure exists.

## Acceptance criteria

- Preparation merges without changing the live staging HTTP topology or requesting a certificate.
- Only Nginx publishes ports 80/443 on the HTTPS topology; Django and PostgreSQL remain private.
- HTTP and optional alias HTTPS requests return 308 to canonical HTTPS while preserving path and
  query; unknown hosts never reach Django.
- A missing certificate is issued once for the configured canonical name and optional alias; an
  existing certificate is reused without automated modification.
- Canonical public `/health/` returns 200 through normal TLS trust validation.
- `deployed-image` changes only after image identity, local health, redirects, and HTTPS health pass.
- Any failed apply attempts to restore the prior `APP_IMAGE` without deleting data or certificate
  volumes and exits non-zero.
- `certbot renew --dry-run` remains an explicit operational follow-up before renewal readiness is
  considered validated; the manual HTTP recovery fallback remains available independently.
- No secret or certificate private-key material is tracked or printed.

## File responsibility map

| Path | Responsibility |
| --- | --- |
| `docker-compose.prod.yml` | Shared PostgreSQL and private Django services. |
| `docker-compose.https.yml` | Shared Nginx, Certbot, public ports, and certificate volumes. |
| `docker-compose.staging.yml` | Manual HTTP recovery fallback; excluded from normal staging deployment. |
| `deploy/nginx/https.conf.template` | Canonical, alias, ACME, health, and unknown-host routing. |
| `deploy/nginx/reload-nginx.sh` | Validate inputs, render atomically, start Nginx, and reload certificates. |
| `deploy/certbot/reconcile-certificate.sh` | Reuse an existing certificate or issue it once when absent. |
| `deploy/certbot/renew-certificates.sh` | Periodic webroot renewal checks. |
| `deploy/verify-public-edge.sh` | Exact redirect and trusted HTTPS health smoke checks. |
| `deploy/apply-deployment.sh` | Apply one image, recover in process on failure, and record success. |
| `.github/workflows/deploy.yml` | Automatic staging deployment through the shared HTTPS overlay. |
| `.github/workflows/promote-production.yml` | Future production deployment through the shared HTTPS edge. |
| `tests/test_repository_foundation.py` | Repository, workflow, and deployment-source contracts. |
| `tests/deployment/test_deployment_scripts.py` | Behavioral certificate, smoke, recovery, and marker tests. |
| `tests/deployment/validate-nginx.sh` | Alias/no-alias render and pinned-image `nginx -t`. |

## Chunk 1: Preparation release

This release is implemented on the preparation branch. It must be reviewed and deployed while the
staging workflow still selects the HTTP overlay.

### Task 1: Share and validate the HTTPS edge

**Files:**

- Create: `docker-compose.https.yml`
- Delete: `docker-compose.production.yml`
- Create: `deploy/nginx/https.conf.template`
- Delete: `deploy/nginx/https.conf`
- Modify: `deploy/nginx/reload-nginx.sh`
- Modify: `.github/workflows/promote-production.yml`
- Create: `tests/deployment/validate-nginx.sh`
- Modify: `tests/test_repository_foundation.py`

- [x] Move pinned Nginx/Certbot services and persistent certificate volumes into the shared overlay.
- [x] Keep only Nginx on host ports 80/443 and keep `web` and `db` private.
- [x] Render explicit default, canonical, optional alias, ACME, and private health server blocks.
- [x] Reject invalid or duplicate hostnames and validate a candidate configuration before replacing
  the working rendered file.
- [x] Keep staging on `docker-compose.staging.yml`; switch only future production configuration to
  `docker-compose.https.yml` during preparation.
- [x] Validate both alias and no-alias Nginx configurations with temporary certificates created in
  the pinned container toolchain.

Verification:

```bash
APP_IMAGE=ghcr.io/example/photo-prjct:test SECRET_KEY=test DEBUG=False \
  ALLOWED_HOSTS=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app \
  PUBLIC_DOMAIN=findme-photo.ru PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
  LETSENCRYPT_EMAIL=ops@example.com \
  docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
sh tests/deployment/validate-nginx.sh
```

Expected: both commands exit 0; only Nginx publishes ports and both Nginx variants pass syntax
validation.

### Task 2: Implement the minimal deployment contract

**Files:**

- Create: `deploy/certbot/reconcile-certificate.sh`
- Create: `deploy/verify-public-edge.sh`
- Modify: `deploy/apply-deployment.sh`
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/promote-production.yml`
- Modify: `.env.example`
- Modify: `tests/test_repository_foundation.py`
- Create: `tests/deployment/test_deployment_scripts.py`

- [x] Add `PUBLIC_DOMAIN` and optional `PUBLIC_DOMAIN_ALIAS` configuration; keep
  `LETSENCRYPT_EMAIL` on HTTPS deployment paths only.
- [x] Use pinned Certbot to check for the stable certificate file. If absent, perform one
  non-interactive standalone issuance for the canonical name and optional alias; otherwise exit
  without an issuance request.
- [x] Verify canonical HTTP redirect, optional alias HTTP/HTTPS redirects, and canonical trusted
  HTTPS health with `curl`, including exact 308 destination path and query.
- [x] Remember the prior `APP_IMAGE` from the protected remote `.env` and atomically write the
  requested environment. On registry, issuance, pull, Compose, local-health, or public-smoke
  failure, restore that image and reconcile the selected overlay before exiting non-zero.
- [x] Atomically update `deployed-image` only after image identity, local edge health, and the HTTPS
  smoke checks succeed. Preparation staging records success after its existing HTTP local health.
- [x] Cover missing/existing certificate behavior, alias/no-alias smoke checks, failed-apply
  recovery, successful marker update, and the absence of volume deletion in behavioral tests.

Verification:

```bash
sh -n deploy/*.sh deploy/certbot/*.sh deploy/nginx/*.sh
env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  .venv/bin/pytest tests/test_repository_foundation.py \
    tests/deployment/test_deployment_scripts.py -q
```

Expected: shell parsing succeeds and all focused tests pass.

### Task 3: Align preparation documentation and inventory

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-13-canonical-domain-https-edge-design.md`
- Modify: `docs/plans/2026-07-13-canonical-domain-https-edge.md`
- Modify: `.agents/skills/manage-yandex-cloud/references/inventory.md`
- Modify: `docs/architecture.md` only if its implemented/accepted summary is stale

- [x] Document environment variables, the separate activation boundary, and the minimal recovery
  contract.
- [x] Record only observed public DNS facts and retain the unresolved Yandex Cloud resource-ID
  warning.
- [x] Document the intentional hostname-change tradeoff: the operator must explicitly remove and
  reissue the certificate when the alias or canonical name changes.
- [x] Remove preparation instructions that imply live DNS/TLS activation already happened.

Preparation regression commands:

```bash
git diff --check
sh -n deploy/*.sh deploy/certbot/*.sh deploy/nginx/*.sh
env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  .venv/bin/pytest tests/test_repository_foundation.py \
    tests/deployment/test_deployment_scripts.py -q
sh tests/deployment/validate-nginx.sh
```

Historical preparation expectation: every command exited 0, and `.github/workflows/deploy.yml`
still copied `docker-compose.staging.yml` without certificate bootstrap before activation.

## Chunk 2: HTTPS activation release

Activation was delivered in a separate PR after preparation deployed successfully. The manual HTTP
recovery fallback is retained rather than handled by a cleanup release.

### Task 4: Configure and verify staging inputs

**External configuration:** GitHub Environment `staging`; RU-CENTER DNS.

- [x] Set Environment variable `PUBLIC_DOMAIN=findme-photo.ru`.
- [x] Set optional Environment variable `PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru`.
- [x] Set Environment secret `LETSENCRYPT_EMAIL` to the maintainer-selected operational email;
  confirm only that the secret exists.
- [x] From a public network, manually confirm apex and alias resolve to `111.88.151.64`, no obsolete
  address is returned, and ports 80/443 reach the intended VM. DNS evidence is an activation
  prerequisite, not a recurring deploy gate.
- [x] Confirm the preparation deployment is healthy:

  ```bash
  curl --fail-with-body --silent --show-error http://findme-photo.ru/health/
  ```

  Historical pre-activation expectation: HTTP 200 and the Django health response.

If the configured alias differs from the name on an existing environment certificate, do not merge
activation until a maintenance window is approved. During that window, stop Nginx, back up the
environment-specific `letsencrypt` volume, delete the stable `photo-prjct` certificate with the
pinned Certbot image, and rerun deployment once. A failed issuance must restore the volume backup
before the old edge is restarted. Normal deployments never perform this destructive operation.

### Task 5: Switch staging to the shared HTTPS overlay

Use `@superpowers:test-driven-development`.

**Files:**

- Modify: `.github/workflows/deploy.yml`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/test_repository_foundation.py`

- [x] First change the repository contract to require staging to copy/use
  `docker-compose.https.yml`, pass `LETSENCRYPT_EMAIL`, and stop selecting the HTTP overlay.
- [x] Run the focused repository test and confirm it fails for the expected staging-overlay reason.
- [x] Select the shared HTTPS overlay for staging. Keep the production path behavior unchanged.
- [x] Copy the shared overlay and pass the three activation inputs through the staging workflow.
- [x] Preserve in-process recovery and the rule that `deployed-image` changes only after the public
  smoke succeeds.
- [x] Run:

  ```bash
  git diff --check
  sh -n deploy/*.sh deploy/certbot/*.sh deploy/nginx/*.sh
  env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
    DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
    .venv/bin/pytest tests/test_repository_foundation.py \
      tests/deployment/test_deployment_scripts.py -q
  APP_IMAGE=ghcr.io/example/photo-prjct:test SECRET_KEY=test DEBUG=False \
    ALLOWED_HOSTS=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app \
    PUBLIC_DOMAIN=findme-photo.ru PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
    LETSENCRYPT_EMAIL=ops@example.com \
    docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
  sh tests/deployment/validate-nginx.sh
  ```

  Expected: every command exits 0; staging uses the shared overlay and only Nginx publishes 80/443.

- [x] Commit activation as `feat: activate HTTPS for canonical domain`.

### Task 6: Observe activation to a terminal result

**Runtime target:** Compose project `photo-prjct-staging` on the current preemptible VM.

- [x] Merge activation [PR #32](https://github.com/peter-nikitin/photo-prjct/pull/32) only after
  Chunk 1 and Task 4 are satisfied. No `yc` mutation was required.
- [x] Monitor build and remote apply until GitHub Actions reaches a terminal state. Staging deploy
  run [29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740)
  completed successfully.
- [x] Confirm `http://findme-photo.ru/` returns 308 to `https://findme-photo.ru/`.
- [x] Confirm `https://findme-photo.ru/` returns 200 with `ssl_verify_result` 0.
- [x] Confirm `http://www.findme-photo.ru/` returns 308 to `https://findme-photo.ru/`.
- [x] Confirm `https://www.findme-photo.ru/` returns 308 to `https://findme-photo.ru/`.

- [ ] Open the canonical URL in a normal browser and confirm certificate trust for the apex and
  alias. This is an operator observation, not an automated certificate-set comparison.
- [ ] Confirm `deployed-image` equals the running web image, Nginx owns 80/443, Django owns no host
  port, and PostgreSQL/data volumes remain present.
- [ ] Run the renewal simulation on the VM:

  ```bash
  docker compose --project-name photo-prjct-staging \
    --env-file /opt/photo-prjct/.env \
    -f /opt/photo-prjct/docker-compose.prod.yml \
    -f /opt/photo-prjct/docker-compose.https.yml \
    run --rm --entrypoint certbot certbot renew --dry-run \
    --webroot --webroot-path /var/www/certbot
  ```

  Expected: simulated renewal succeeds without stopping Nginx or changing the application image.

- [ ] If activation fails, preserve logs and existing volumes. Verify whether in-process image/edge
  recovery succeeded before deciding on a manual retry; do not repeat certificate issuance without
  identifying DNS, reachability, or rate-limit cause.

## Chunk 3: Manual HTTP recovery fallback retained

Status: complete. `docker-compose.staging.yml` and `deploy/nginx/staging.conf` are intentionally
retained for manual recovery and are excluded from normal staging deployment. The executable
contract
[`tests/test_repository_foundation.py::test_http_edge_fallback_remains_available_for_manual_recovery`](../../tests/test_repository_foundation.py)
protects the fallback while the normal deploy workflow is required to select the shared HTTPS
overlay. No deletion cleanup is planned.

## Operational impact and order

1. Preparation merged and deployed while the public runtime still used HTTP.
2. The three staging inputs were configured and DNS/reachability were verified.
3. Activation PR #32 merged, and staging deploy run
   [29556330740](https://github.com/peter-nikitin/photo-prjct/actions/runs/29556330740)
   completed successfully.
4. Public apex and `www` HTTP/HTTPS behavior was checked, including trusted TLS on the canonical
   apex.
5. Remaining operator follow-up: observe the apex and alias in a normal browser.
6. Remaining operator follow-up: inspect remote marker, container, port, and volume state.
7. Remaining operator follow-up: run the Certbot renewal dry-run.

No database migration, data reset, VM restart, public-IP change, security-group change, or pricing
mutation is part of this plan. Any newly discovered need for Yandex Cloud mutation must stop and
follow `@manage-yandex-cloud`, including explicit manual confirmation for pricing, availability,
access, or destructive impact.

## Recovery summary

- **Preparation:** revert the preparation merge; staging never left HTTP.
- **Failed apply:** the deploy process restores the prior `APP_IMAGE`, reconciles the same selected
  overlay, leaves `deployed-image` unchanged, and exits non-zero.
- **Failed initial certificate issuance:** inspect DNS, public port reachability, and Certbot output
  before retrying; retain the certificate volume.
- **Configuration rejection:** revert the activation commit and redeploy the last known image. Never
  use `down --volumes`.
- **Hostname change:** schedule explicit certificate backup/removal/reissue; it is not part of normal
  deployment.

## Open questions

None. No implementation choice remains. Browser trust observation, remote marker/container-state
inspection, and the Certbot renewal dry-run remain operational evidence follow-ups rather than open
design questions.
