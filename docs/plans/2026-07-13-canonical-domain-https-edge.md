# Canonical Domain HTTPS Edge Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` (if subagents
> are available) or `superpowers:executing-plans` to implement this plan. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Serve the current single active staging environment at `https://findme-photo.ru/` through
the shared Nginx and Certbot edge, with safe certificate activation, public release verification,
and rollback.

**Architecture:** Keep PostgreSQL and Django in `docker-compose.prod.yml` and move the public Nginx
and Certbot services into one environment-neutral HTTPS overlay. The current staging Compose project
uses that overlay after a preparation release; future staging and production reuse it with isolated
GitHub Environments, Compose project names, data, certificate volumes, and release markers.

**Tech Stack:** Docker Compose, Nginx 1.27 Alpine, Certbot 2.11, POSIX shell, Django/Gunicorn,
GitHub Actions, RU-CENTER DNS, Yandex Cloud VM.

---

- Date: 2026-07-13
- Status: Approved design; implementation not started
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented),
  [deployment domain assignment](../architecture.md#deployment-domain-assignment--accepted)
- Related ADRs: [0003](../adr/0003-docker-compose-yandex-cloud.md),
  [0005](../adr/0005-promote-images-through-staging.md),
  [0007](../adr/0007-nginx-certbot-https-edge.md),
  [0010](../adr/0010-share-https-edge-across-environments.md)
- Design spec:
  [Canonical domain HTTPS edge design](../superpowers/specs/2026-07-13-canonical-domain-https-edge-design.md)

## Scope

### In scope

- Replace the production-named edge overlay with a shared HTTPS overlay.
- Configure canonical `findme-photo.ru` routing and optional `www.findme-photo.ru` redirection.
- Reconcile the exact certificate SAN set and preserve environment-specific certificate state.
- Separate local candidate health from public release finalization.
- Restore the HTTP edge during first-activation failure and use HTTPS image rollback after cleanup.
- Update deployment documentation, repository contracts, and stable inventory facts.

### Out of scope

- Creating, starting, resizing, reserving, or otherwise mutating Yandex Cloud resources.
- Provisioning production, changing VM pricing, or declaring the preemptible VM production-ready.
- Adding CDN, WAF, load balancer, managed certificates, IPv6, or multiple public endpoints.
- Changing Django domain behavior, database schema, media storage, or product features.
- Creating `staging.findme-photo.ru` before separate production infrastructure exists.

## Acceptance criteria

- `findme-photo.ru` and `www.findme-photo.ru` each have exactly A `111.88.151.64` and no AAAA.
- Only Nginx publishes ports 80 and 443; Django and PostgreSQL remain private to Compose.
- HTTP requests and `www` HTTPS requests return 308 to the canonical HTTPS host while preserving
  path and query; unknown hosts never reach Django.
- The trusted certificate covers the exact configured canonical/alias set and survives deploys.
- `/health/` returns HTTP 200 over trusted public HTTPS.
- A release becomes `deployed-image` only after external DNS, redirect, health, and certificate
  checks pass.
- Certificate bootstrap failure restores HTTP without a marker; post-candidate activation failure
  restores the previous image and HTTP edge; post-cleanup failure restores the previous image while
  retaining HTTPS.
- `certbot renew --dry-run` succeeds before the HTTP fallback is removed.
- No secret or certificate private-key material is tracked or printed.

## File responsibility map

| Path | Responsibility |
| --- | --- |
| `docker-compose.prod.yml` | Shared PostgreSQL and private Django services. |
| `docker-compose.https.yml` | Shared Nginx, Certbot, public ports, and certificate volumes. |
| `docker-compose.staging.yml` | Temporary HTTP activation fallback; deleted only in cleanup. |
| `deploy/nginx/https.conf.template` | Explicit canonical, alias, ACME, and unknown-host routing. |
| `deploy/nginx/reload-nginx.sh` | Render the template, validate Nginx, serve, and reload certificates. |
| `deploy/certbot/renew-certificates.sh` | Periodic webroot renewal checks. |
| `deploy/certbot/reconcile-certificate.sh` | Compare desired names with existing SANs and issue exactly once when absent or mismatched. |
| `deploy/apply-deployment.sh` | Render private environment state, select the versioned overlay, apply the image, and create `candidate-image` after local health. |
| `deploy/finalize-deployment.sh` | Atomically promote the expected candidate marker to `deployed-image`. |
| `deploy/rollback-deployment.sh` | Restore the previous successful image on the requested HTTP/HTTPS edge mode. |
| `deploy/verify-public-edge.sh` | Verify exact public DNS, redirects, HTTPS health, and certificate SANs from the GitHub runner. |
| `.github/workflows/deploy.yml` | Prepare, activate, externally verify, finalize, and roll back staging. |
| `.github/workflows/promote-production.yml` | Reuse the shared HTTPS overlay and public release contract for future production. |
| `tests/test_repository_foundation.py` | Versioned deployment and documentation contracts. |
| `tests/deployment/test_deployment_scripts.py` | Behavioral marker, certificate, verification, and rollback tests with stubbed external commands. |
| `tests/deployment/validate-nginx.sh` | Render alias/no-alias templates with temporary certificates and run `nginx -t`. |

## Chunk 1: Preparation release

The preparation release adds and validates every new path but leaves staging on the current HTTP
overlay. Its merge must not request a certificate or interrupt the public edge.

### Task 1: Lock the shared-edge contract with failing tests

Use `@superpowers:test-driven-development`.

**Files:**

- Modify: `tests/test_repository_foundation.py`

- [ ] **Step 1: Replace production-only edge assertions with shared-edge assertions**

  Require `docker-compose.https.yml`, require only the production workflow to reference it during
  preparation, and require `docker-compose.production.yml` to be absent. Keep the staging workflow
  assertion exclusively on `docker-compose.staging.yml` during this chunk.

  ```python
  def test_public_environments_share_one_https_edge_overlay() -> None:
      shared = yaml.safe_load((ROOT / "docker-compose.https.yml").read_text())
      staging_workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
      production_workflow = (ROOT / ".github/workflows/promote-production.yml").read_text()

      assert shared["services"]["nginx"]["ports"] == ["80:80", "443:443"]
      assert "certbot" in shared["services"]
      assert "docker-compose.https.yml" in production_workflow
      assert "docker-compose.staging.yml" in staging_workflow
      assert not (ROOT / "docker-compose.production.yml").exists()
  ```

- [ ] **Step 2: Add contracts for focused deployment scripts**

  Assert that the reconciliation, public verification, finalization, and rollback scripts exist;
  workflows must call versioned scripts rather than embed certificate or marker logic.

- [ ] **Step 3: Add canonical-domain configuration contracts**

  Require `PUBLIC_DOMAIN`, `PUBLIC_DOMAIN_ALIAS`, and `EXPECTED_PUBLIC_IPV4` in `.env.example` and
  workflow environment wiring. Require `LETSENCRYPT_EMAIL` only on HTTPS paths.

- [ ] **Step 4: Run the focused tests and verify the red state**

  Run:

  ```bash
  env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
    DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
    .venv/bin/pytest tests/test_repository_foundation.py -q
  ```

  Expected: FAIL because the shared overlay and focused scripts do not exist and workflows still
  encode the old production-only edge.

- [ ] **Step 5: Commit the failing contracts**

  ```bash
  git add tests/test_repository_foundation.py
  git commit -m "test: define shared HTTPS edge contract"
  ```

### Task 2: Extract the shared HTTPS overlay and explicit Nginx routing

**Files:**

- Create: `docker-compose.https.yml`
- Delete: `docker-compose.production.yml`
- Create: `deploy/nginx/https.conf.template`
- Delete: `deploy/nginx/https.conf`
- Modify: `deploy/nginx/reload-nginx.sh`
- Modify: `.github/workflows/promote-production.yml`
- Create: `tests/deployment/validate-nginx.sh`

- [ ] **Step 1: Move Nginx, Certbot, and volumes into the shared overlay**

  Preserve the existing pinned images, port mappings, health dependencies, persistent
  `letsencrypt`/`certbot-webroot` volumes, and private `web:8000` upstream. Pass `PUBLIC_DOMAIN` and
  optional `PUBLIC_DOMAIN_ALIAS` into Nginx.

- [ ] **Step 2: Render explicit server blocks from environment**

  The template must provide:

  - an HTTP default server that returns `444` for unknown hosts;
  - HTTP ACME plus 308 canonical redirect for configured hosts;
  - an HTTPS default server that completes TLS and returns `444` without proxying;
  - an HTTPS alias server returning 308 to `https://$PUBLIC_DOMAIN$request_uri` when alias exists;
  - an HTTPS canonical server proxying to Django with the existing forwarded headers and security
    headers;
  - an un-published listener bound specifically to `127.0.0.1:8080` that proxies only `/health/` to
    Django for the Compose health check, sets `Host` to the rendered canonical `PUBLIC_DOMAIN`, and
    uses the same forwarded-header contract as the public HTTPS upstream.

  Template rendering must not leave an empty `server_name` directive when the alias is absent.

- [ ] **Step 3: Keep certificate reload behavior focused**

  Update `reload-nginx.sh` to render the template atomically, run `nginx -t`, start Nginx, and retain
  the six-hour reload loop. It must fail before replacing a working rendered file when template
  validation fails. Change the Compose health check to
  `http://127.0.0.1:8080/health/`; do not send an unknown Host to the public default server.

- [ ] **Step 4: Switch only the production workflow to the shared overlay**

  Copy `docker-compose.https.yml` to the remote deploy root and keep its existing production
  Environment, approval, and Compose project identity. Do not switch staging in this chunk.

- [ ] **Step 5: Render and validate Compose**

  Run:

  ```bash
  APP_IMAGE=ghcr.io/example/photo-prjct:test SECRET_KEY=test DEBUG=False \
    ALLOWED_HOSTS=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app \
    PUBLIC_DOMAIN=findme-photo.ru PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
    EXPECTED_PUBLIC_IPV4=111.88.151.64 LETSENCRYPT_EMAIL=ops@example.com \
    docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
  ```

  Expected: exit 0; only Nginx publishes 80/443 and `web` has no host port.

- [ ] **Step 6: Render both Nginx variants and validate syntax**

  Implement `tests/deployment/validate-nginx.sh` to create temporary self-signed test certificate
  files through `certbot/certbot:v2.11.0 --entrypoint openssl`, render the template once with
  `PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru` and once with an empty alias, mount each rendered
  configuration and certificate directory into the pinned Nginx image, and run `nginx -t`. Use
  `mktemp -d` plus a trap so no certificate material remains and no host OpenSSL dependency is added.

  Run:

  ```bash
  sh tests/deployment/validate-nginx.sh
  ```

  Expected: two successful `nginx -t` checks and exit 0; neither rendered file contains an empty
  `server_name` directive.

- [ ] **Step 7: Run the targeted tests**

  Run the Task 1 pytest command. Expected: shared-overlay assertions pass; focused-script assertions
  remain red until Task 3.

- [ ] **Step 8: Commit the shared overlay**

  ```bash
  git add docker-compose.https.yml docker-compose.production.yml deploy/nginx \
    .github/workflows/promote-production.yml tests/deployment/validate-nginx.sh
  git commit -m "refactor: share HTTPS edge across environments"
  ```

### Task 3: Isolate certificate and release-marker operations

Use `@superpowers:test-driven-development`.

**Files:**

- Create: `deploy/certbot/reconcile-certificate.sh`
- Create: `deploy/finalize-deployment.sh`
- Create: `deploy/rollback-deployment.sh`
- Create: `deploy/verify-public-edge.sh`
- Modify: `deploy/apply-deployment.sh`
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/promote-production.yml`
- Modify: `.env.example`
- Modify: `tests/test_repository_foundation.py`
- Create: `tests/deployment/test_deployment_scripts.py`

- [ ] **Step 1: Add public-edge configuration inputs**

  Add `PUBLIC_DOMAIN`, optional `PUBLIC_DOMAIN_ALIAS`, and `EXPECTED_PUBLIC_IPV4` to `.env.example`.
  Pass them through both workflows where applicable. Keep `LETSENCRYPT_EMAIL` restricted to the
  shared HTTPS production path during preparation; staging does not read it yet.

- [ ] **Step 2: Implement exact SAN reconciliation**

  `reconcile-certificate.sh` runs on the VM but depends only on Docker. It must build the desired set
  from non-empty `PUBLIC_DOMAIN` and `PUBLIC_DOMAIN_ALIAS`, inspect an existing `fullchain.pem`
  through `certbot/certbot:v2.11.0` with `--entrypoint openssl` and the environment-specific
  certificate volume mounted read-only, normalize/sort the `openssl x509 -ext subjectAltName` DNS
  set, and exit without issuance when it matches.

  Missing state performs exactly one `certbot/certbot:v2.11.0 certonly --standalone` container run
  with host networking, the certificate volume mounted read-write, stable `--cert-name photo-prjct`,
  `--non-interactive`, `--agree-tos`, `--email "$LETSENCRYPT_EMAIL"`, and exact desired `-d`
  arguments. A mismatched SAN set uses the same one-shot command plus `--force-renewal` so removed as
  well as added names reconcile to the exact desired set without a prompt. It must never install host
  packages, loop, or print private key material.

- [ ] **Step 3: Make apply produce a candidate, not a successful marker**

  Before changing an existing successful deployment, copy `deployed-image` to `previous-image`.
  After the requested image and local edge health pass, write `candidate-image` atomically. Do not
  update `deployed-image` in `apply-deployment.sh`.

  During preparation, staging still selects the HTTP overlay. Production selects the shared HTTPS
  overlay and invokes SAN reconciliation after stopping only Nginx. On bootstrap failure, restart
  the preceding edge inside `apply-deployment.sh` without requiring `candidate-image`.

- [ ] **Step 4: Implement atomic finalization**

  `finalize-deployment.sh <expected-image>` must require candidate equality, use atomic `mv` to
  replace `deployed-image`, and remove `previous-image` only after success. Mismatch exits non-zero
  without changing markers.

- [ ] **Step 5: Implement versioned rollback modes**

  `rollback-deployment.sh <expected-candidate> http|https` must require candidate equality and a
  `previous-image`, update only `APP_IMAGE` in the protected remote `.env`, reconcile the requested
  overlay, verify the restored image and edge health, restore `deployed-image`, and retain bounded
  failure diagnostics. It must never remove data or certificate volumes.

- [ ] **Step 6: Implement public verification**

  `verify-public-edge.sh` must:

  - require canonical domain and expected IPv4;
  - compare the sorted canonical A set with the one expected IPv4 and require an empty AAAA set;
  - conditionally repeat DNS, redirect, and SAN checks for a non-empty alias;
  - require HTTP 308 and an exact canonical `Location` preserving path/query;
  - require trusted HTTPS 200 from `https://$PUBLIC_DOMAIN/health/`;
  - compare the served certificate SAN set with the configured non-empty domain set.

- [ ] **Step 7: Preserve release-marker behavior during preparation**

  Update the HTTP staging workflow to invoke `finalize-deployment.sh` immediately after remote local
  health succeeds; it does not run HTTPS public verification yet. Update the future production path
  to run public verification before finalization. This keeps `deployed-image` current after every
  preparation merge while ensuring the shared HTTPS path already uses the final public gate.

- [ ] **Step 8: Add source contracts and behavioral script tests**

  Extend repository tests to require candidate/finalize semantics, conditional alias verification,
  one-shot SAN reconciliation, and rollback modes. Avoid tests that merely search for generic words;
  assert command paths, required variables, and absence of `down --volumes`.

  In `tests/deployment/test_deployment_scripts.py`, use temporary directories and fake executables
  prepended to `PATH` to prove at least:

  - matching certificate SANs do not invoke Certbot;
  - mismatched SANs invoke Certbot exactly once with the exact configured names;
  - finalize rejects a mismatched candidate without changing `deployed-image`;
  - finalize atomically promotes a matching candidate;
  - rollback refuses to run without matching candidate and `previous-image` markers;
  - successful HTTP rollback restores `previous-image`, preserves `deployed-image` and named
    volumes, selects the HTTP overlay, and validates restored HTTP edge/image health;
  - successful HTTPS rollback restores `previous-image`, preserves `deployed-image` and named
    volumes, selects the shared HTTPS overlay, and validates restored HTTPS edge/image health;
  - public verification skips every alias command when the alias is empty.

  Add stubbed positive and negative public-verification cases for exact A/no-AAAA comparison, 308
  `Location` preservation, HTTP 200 health, and exact certificate SAN comparison.

- [ ] **Step 9: Validate shell syntax and targeted tests**

  Run:

  ```bash
  sh -n deploy/apply-deployment.sh deploy/finalize-deployment.sh \
    deploy/rollback-deployment.sh deploy/verify-public-edge.sh \
    deploy/certbot/reconcile-certificate.sh deploy/certbot/renew-certificates.sh \
    deploy/nginx/reload-nginx.sh
  ```

  Expected: exit 0.

  Run:

  ```bash
  env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
    DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
    .venv/bin/pytest tests/test_repository_foundation.py \
      tests/deployment/test_deployment_scripts.py -q
  ```

  Expected: all repository-foundation and behavioral deployment tests pass.

- [ ] **Step 10: Commit focused deployment scripts**

  ```bash
  git add .env.example .github/workflows/deploy.yml \
    .github/workflows/promote-production.yml deploy tests
  git commit -m "feat: gate deployment on public HTTPS health"
  ```

### Task 4: Finish preparation documentation and regression checks

**Files:**

- Modify: `README.md`
- Modify: `docs/architecture.md` only if implementation paths differ from its accepted summary
- Modify: `.agents/skills/manage-yandex-cloud/references/inventory.md`

- [ ] **Step 1: Document environment values without secrets**

  Document `LETSENCRYPT_EMAIL` as a GitHub Environment secret and `PUBLIC_DOMAIN`, optional
  `PUBLIC_DOMAIN_ALIAS`, and `EXPECTED_PUBLIC_IPV4` as variables. The example values were added in
  Task 3 so repository contracts are green before this documentation-only task.

- [ ] **Step 2: Correct stale operational statements**

  Remove claims that public DNS is unroutable. State that preparation leaves staging on HTTP and
  that activation is a separate release. Record only verified DNS/IP facts in inventory; retain the
  unresolved Yandex Cloud resource-ID discovery warning.

- [ ] **Step 3: Run preparation verification**

  Run:

  ```bash
  git diff --check
  env SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
    DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
    .venv/bin/pytest tests/test_repository_foundation.py -q
  sh -n deploy/*.sh deploy/certbot/*.sh deploy/nginx/*.sh
  sh tests/deployment/validate-nginx.sh
  ```

  Run the shared Compose render from Task 2 and both deployment pytest files from Task 3. Expected:
  every command exits 0. Confirm the staging workflow still references the HTTP overlay, immediately
  finalizes local HTTP candidates, and contains no certificate bootstrap.

- [ ] **Step 4: Commit preparation documentation**

  ```bash
  git add README.md docs/architecture.md \
    .agents/skills/manage-yandex-cloud/references/inventory.md
  git commit -m "docs: prepare canonical domain HTTPS activation"
  ```

## Chunk 2: HTTPS activation release

Activation is a separate PR/merge after Chunk 1 is deployed successfully. Use
`@superpowers:verification-before-completion` before merging and do not combine cleanup.

### Task 5: Configure and verify staging deployment inputs

**External configuration:** GitHub Environment `staging`; RU-CENTER public DNS.

- [ ] **Step 1: Set non-secret staging variables**

  Configure exactly:

  ```text
  PUBLIC_DOMAIN=findme-photo.ru
  PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru
  EXPECTED_PUBLIC_IPV4=111.88.151.64
  ```

- [ ] **Step 2: Set the staging certificate secret**

  Add `LETSENCRYPT_EMAIL` with the maintainer-selected operational email. Never print its value or
  commit it. Confirm only that the secret name exists.

- [ ] **Step 3: Verify public DNS independently of local Fake-IP DNS**

  Run this DNS-over-HTTPS assertion from a network that can reach `dns.google`:

  ```bash
  python3 -c 'import json,urllib.request
  def answers(name, kind):
      url=f"https://dns.google/resolve?name={name}&type={kind}"
      data=json.load(urllib.request.urlopen(url, timeout=15))
      assert data["Status"] == 0, (name, kind, data)
      return sorted(item["data"] for item in data.get("Answer", []) if item["type"] == {"A":1,"AAAA":28,"NS":2}[kind])
  expected_ip=["111.88.151.64"]
  expected_ns=sorted(["ns3-l2.nic.ru.","ns4-cloud.nic.ru.","ns4-l2.nic.ru.","ns8-cloud.nic.ru.","ns8-l2.nic.ru."])
  for host in ("findme-photo.ru","www.findme-photo.ru"):
      assert answers(host,"A") == expected_ip
      assert answers(host,"AAAA") == []
  assert answers("findme-photo.ru","NS") == expected_ns
  print("DNS preflight passed")'
  ```

  Expected: `DNS preflight passed`, exit 0. This bypasses the local Fake-IP UDP resolver and proves
  the exact canonical/alias A, empty AAAA, and five-server RU-CENTER delegation sets.

- [ ] **Step 4: Confirm the preparation deployment is healthy**

  Confirm the latest staging workflow for the preparation commit succeeded and the current HTTP
  `/health/` endpoint is available before changing the edge.

  Run:

  ```bash
  curl --fail-with-body --silent --show-error http://findme-photo.ru/health/
  ```

  Expected: exit 0, HTTP 200, and the Django health response body.

### Task 6: Switch staging to the shared HTTPS edge

Use `@superpowers:test-driven-development`.

**Files:**

- Modify: `.github/workflows/deploy.yml`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/test_repository_foundation.py`

- [ ] **Step 1: Change the contract test to require HTTPS staging**

  Require the staging workflow to copy/use `docker-compose.https.yml`, pass all public-edge
  variables plus `LETSENCRYPT_EMAIL`, invoke public verification and finalization, and retain an
  activation rollback step using `http` mode. Require that staging no longer selects the HTTP overlay
  during normal apply.

- [ ] **Step 2: Run the focused test and confirm failure**

  Run the Task 1 pytest command. Expected: FAIL because staging still selects HTTP.

- [ ] **Step 3: Activate the shared overlay in staging apply**

  Make staging and production both select `docker-compose.https.yml`. Before missing/mismatched SAN
  reconciliation, stop only Nginx, then call the focused certificate script. If it fails during this
  first activation, restart `docker-compose.staging.yml` and exit without a candidate marker.

- [ ] **Step 4: Add external verification, finalize, and rollback workflow steps**

  After remote apply succeeds, run `deploy/verify-public-edge.sh` on the GitHub runner with
  `id: public_verify` and `continue-on-error: true`. The remote finalization step must use:

  ```yaml
  if: steps.public_verify.outcome == 'success'
  ```

  The remote activation rollback and explicit failure steps must each use:

  ```yaml
  if: always() && steps.public_verify.outcome == 'failure'
  ```

  Rollback invokes `rollback-deployment.sh <candidate> http` and verifies restored HTTP health. The
  explicit failure step runs after rollback and exits non-zero, so a recovered failed activation is
  still a failed workflow. Do not use bare `success()` or `failure()` for these branches.

- [ ] **Step 5: Run targeted static verification**

  Run:

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
    EXPECTED_PUBLIC_IPV4=111.88.151.64 \
    docker compose -f docker-compose.prod.yml -f docker-compose.staging.yml config --quiet

  APP_IMAGE=ghcr.io/example/photo-prjct:test SECRET_KEY=test DEBUG=False \
    ALLOWED_HOSTS=localhost DB_NAME=app DB_USER=app DB_PASSWORD=app \
    PUBLIC_DOMAIN=findme-photo.ru PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
    EXPECTED_PUBLIC_IPV4=111.88.151.64 LETSENCRYPT_EMAIL=ops@example.com \
    docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
  ```

  Expected: every command exits 0. The retained HTTP render contains `db`, `web`, and `nginx`, with
  only port 80 published and no Certbot. The active HTTPS render contains `db`, `web`, `nginx`, and
  `certbot`, with only Nginx publishing ports 80 and 443. Staging normal apply selects HTTPS while
  activation rollback retains the HTTP files.

- [ ] **Step 6: Commit activation**

  ```bash
  git add .github/workflows/deploy.yml deploy/apply-deployment.sh \
    tests/test_repository_foundation.py
  git commit -m "feat: activate HTTPS for canonical domain"
  ```

### Task 7: Observe activation to a terminal result

**Runtime target:** Existing staging Compose project `photo-prjct-staging` on the current
preemptible VM.

- [ ] **Step 1: Merge only after Chunk 1 and Task 5 gates are satisfied**

  The merge triggers the activation deployment. No `yc` mutation is authorized or required.

- [ ] **Step 2: Monitor the GitHub Actions run**

  Wait for build, remote apply, public verification, and finalization to reach a terminal state. Do
  not stop after certificate issuance alone.

- [ ] **Step 3: Verify successful external behavior**

  Expected:

  ```text
  http://findme-photo.ru/health/      -> 308 https://findme-photo.ru/health/
  https://findme-photo.ru/health/     -> 200
  http://www.findme-photo.ru/health/  -> 308 canonical HTTPS
  https://www.findme-photo.ru/health/ -> 308 canonical HTTPS
  ```

  Confirm the trusted certificate SANs are exactly `findme-photo.ru` and `www.findme-photo.ru`.

- [ ] **Step 4: Verify release state on the VM through workflow diagnostics**

  Confirm `candidate-image` was promoted to `deployed-image`, the running web image equals that
  marker, Nginx owns 80/443, Django owns no host port, and PostgreSQL/data volumes were preserved.

- [ ] **Step 5: Exercise certificate renewal safely**

  On the VM, run:

  ```bash
  docker compose --project-name photo-prjct-staging \
    --env-file /opt/photo-prjct/.env \
    -f /opt/photo-prjct/docker-compose.prod.yml \
    -f /opt/photo-prjct/docker-compose.https.yml \
    run --rm --entrypoint certbot certbot renew --dry-run \
    --webroot --webroot-path /var/www/certbot
  ```

  Expected: exit 0 and Certbot reports a successful simulated renewal without stopping Nginx or
  replacing the application.

- [ ] **Step 6: If activation fails, stop and preserve evidence**

  Confirm marker-independent bootstrap recovery or candidate-aware HTTP rollback completed. Do not
  retry certificate issuance until DNS, port, and rate-limit evidence identifies the cause.

## Chunk 3: Post-activation cleanup

Start this chunk only after Task 7 succeeds and renewal dry run passes. It is a separate PR/merge.

### Task 8: Remove the temporary HTTP fallback and switch rollback to HTTPS

Use `@superpowers:test-driven-development`.

**Files:**

- Delete: `docker-compose.staging.yml`
- Delete: `deploy/nginx/staging.conf`
- Modify: `deploy/apply-deployment.sh`
- Modify: `deploy/rollback-deployment.sh`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_repository_foundation.py`
- Modify: `tests/deployment/test_deployment_scripts.py`

- [ ] **Step 1: Change tests to require HTTPS-only public environments**

  Require the temporary HTTP overlay/configuration to be absent, remove activation rollback mode
  from the staging workflow, and require rollback to restore the previous image on the shared HTTPS
  edge. Update behavioral tests to remove the transition-only successful HTTP rollback case while
  retaining negative marker checks and the successful HTTPS restoration case.

- [ ] **Step 2: Run the focused tests and verify failure**

  Run the Task 1 pytest command. Expected: FAIL while HTTP fallback files and workflow references
  remain.

- [ ] **Step 3: Remove activation-only files and branches**

  Delete the HTTP overlay/configuration. Remove marker-independent HTTP recovery code that is only
  reachable during initial bootstrap. Keep certificate reconciliation for future domain/SAN changes;
  on later reconciliation failure, restart the preceding validated HTTPS edge and certificate.

- [ ] **Step 4: Make normal rollback HTTPS-only**

  Public verification failure restores `previous-image` with `docker-compose.https.yml`, checks
  local and public HTTPS health, and preserves the certificate volume and prior marker.

- [ ] **Step 5: Run targeted verification**

  Run:

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
    EXPECTED_PUBLIC_IPV4=111.88.151.64 LETSENCRYPT_EMAIL=ops@example.com \
    docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
  sh tests/deployment/validate-nginx.sh
  ```

  Expected: every command exits 0; the rendered topology contains exactly `db`, `web`, `nginx`, and
  `certbot`; only Nginx publishes ports 80 and 443; no tracked deployment path references the HTTP
  overlay; behavioral tests prove successful HTTPS rollback and reject invalid marker state.

- [ ] **Step 6: Commit cleanup**

  ```bash
  git add docker-compose.staging.yml deploy/nginx/staging.conf deploy \
    .github/workflows/deploy.yml tests/test_repository_foundation.py \
    tests/deployment/test_deployment_scripts.py
  git commit -m "refactor: remove staging HTTP fallback"
  ```

### Task 9: Reconcile documentation and final verification

**Files:**

- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `.agents/skills/manage-yandex-cloud/references/inventory.md`
- Modify: `docs/plans/2026-07-13-canonical-domain-https-edge.md` only to mark completed evidence

- [ ] **Step 1: Record implemented HTTPS state**

  State that the canonical domain is publicly routed through the shared HTTPS edge, the current VM
  remains staging/preemptible, and production is still unprovisioned. Record the successful workflow
  run, certificate names, renewal result, and `111.88.151.64` as the verified configured/observed
  public endpoint. Do not call it a statically allocated address unless cloud discovery independently
  proves the address resource and allocation state; do not guess cloud resource IDs.

- [ ] **Step 2: Run proportional regression checks**

  Deployment/runtime configuration changed, so run:

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
    EXPECTED_PUBLIC_IPV4=111.88.151.64 LETSENCRYPT_EMAIL=ops@example.com \
    docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config --quiet
  ```

  Run exact Nginx syntax validation:

  ```bash
  sh tests/deployment/validate-nginx.sh
  ```

  Expected: alias and no-alias variants both pass `nginx -t`. Run broader application checks only if
  implementation changes Django/runtime application settings beyond the existing proxy trust
  configuration.

- [ ] **Step 3: Re-run live public checks**

  Run the versioned script from outside the VM:

  ```bash
  PUBLIC_DOMAIN=findme-photo.ru \
    PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
    EXPECTED_PUBLIC_IPV4=111.88.151.64 \
    sh deploy/verify-public-edge.sh
  ```

  Expected: exit 0 with exact A/no-AAAA answers, canonical 308 behavior, trusted HTTPS health, and
  exact canonical/alias SANs.

- [ ] **Step 4: Commit final documentation**

  ```bash
  git add README.md docs/architecture.md \
    .agents/skills/manage-yandex-cloud/references/inventory.md \
    docs/plans/2026-07-13-canonical-domain-https-edge.md
  git commit -m "docs: record canonical HTTPS activation"
  ```

## Operational impact and deployment order

1. Merge and observe Chunk 1; public runtime remains HTTP.
2. Configure GitHub staging values and independently confirm public DNS.
3. Merge Chunk 2; expect a short edge interruption while Certbot uses port 80.
4. Verify public behavior and renewal; leave the HTTP fallback available until both succeed.
5. Merge Chunk 3; future rollbacks retain HTTPS and change only the application image.

No database migration, data reset, VM restart, public-IP change, security-group change, or pricing
mutation is part of this plan. Any newly discovered need for a Yandex Cloud mutation must stop and
follow `@manage-yandex-cloud`, including explicit manual confirmation for pricing, availability,
access, or destructive impact.

## Rollback summary

- **Preparation:** revert the preparation merge; staging never left HTTP.
- **Initial activation certificate bootstrap:** deployment script restarts the preceding HTTP edge
  without relying on candidate markers.
- **Activation after local health:** workflow restores `previous-image` plus the HTTP edge, verifies
  health, and leaves `deployed-image` unchanged.
- **After cleanup:** SAN reconciliation failure restarts the preceding validated HTTPS edge and
  certificate; later public verification failure restores `previous-image` on that shared HTTPS
  edge. Both paths preserve the validated certificate volume.
- **All phases:** never use `down --volumes`; preserve PostgreSQL, certificates, and prior successful
  release identity.

## Open questions

None. The maintainer must supply the operational `LETSENCRYPT_EMAIL` value before activation; this is
a secret input, not an unresolved implementation choice.
