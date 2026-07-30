# Minimal Service Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-07-30
- Status: Draft
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-07-30-minimal-service-monitoring-design.md`](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md)
- Related architecture:
  [`docs/architecture.md`](../architecture.md), current deployment topology, accepted constraints,
  proposed Operations module, and remaining open recovery decisions
- Related ADRs:
  [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0007](../adr/0007-nginx-certbot-https-edge.md),
  [ADR 0011](../adr/0011-use-minimal-shared-https-rollout.md),
  [ADR 0017](../adr/0017-use-django-polled-photo-processing-jobs.md), and
  [ADR 0018](../adr/0018-use-managed-yandex-monitoring.md)
- ADR impact: Conforms to accepted ADR 0018

## Goal

Deliver the approved [minimal monitoring outcome](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#goal)
for the current public staging environment: externally generated public-health metrics, basic VM
and Django HTTP graphs, actionable email alerts, and a short operator runbook.

## Scope

Implement the specification's [included scope and explicit exclusions](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#scope)
without change.

The external runner is now fixed as a scheduled GitHub Actions workflow. It writes three bounded
custom metrics to Yandex Monitoring every five minutes. This is the only task-specific
specialization of the specification.

## Global Constraints

- Keep PostgreSQL, Docker Compose, Nginx/Certbot, deployment rollback, and the disabled worker
  behavior unchanged.
- Do not add Prometheus, Grafana, Alertmanager, an exporter container, Docker socket access, or a
  public metrics route.
- Use one host-installed Yandex Unified Agent and its VM metadata identity.
- Metrics and probe delivery are best-effort observation paths and cannot fail application startup,
  request handling, deployment, or rollback.
- Application labels are limited to `environment`, normalized route, method, and status class.
- The current Gunicorn entrypoint has one worker. Increasing the worker count requires
  multiprocess-safe metric aggregation before rollout; do not silently scale workers with the
  process-local registry in this plan.
- The public probe uses only `environment` and a fixed check name as labels.
- Do not log API keys, IAM tokens, raw URLs with secrets, request identifiers, personal data,
  storage keys, or query strings.
- Cloud IAM changes, custom-metric activation, and retention/pricing effects require fresh explicit
  approval immediately before live mutation. Plan approval is not mutation approval.
- Do not activate production monitoring before a separate production environment exists.

## Acceptance Criteria

The implementation must satisfy all twelve
[specification acceptance criteria](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#acceptance-criteria).

Sequence-specific completion additionally requires:

- repository tests prove the public edge returns 404 for `/metrics/` while the loopback-only Nginx
  listener proxies that exact route;
- the GitHub probe always attempts to write an observation, including `probe_success = 0` for a
  completed failed check, and never prints its API key;
- live activation records the Unified Agent version, metrics actually available on VM
  `epdr5g3p24tdns9890nr`, dashboard URL, alert IDs, and controlled failure/recovery evidence in the
  runbook;
- the architecture Operations module remains `Proposed` until the live dashboard and notification
  checks are validated; and
- `EJ-009` advances only to the evidence-supported status at the end of delivery.

## Implementation

### Task 1: Add bounded Django HTTP metrics

**Files:**

- Create: `src/backend/config/metrics.py`
- Create: `src/backend/config/tests/__init__.py`
- Create: `src/backend/config/tests/test_metrics.py`
- Modify: `src/backend/config/settings.py`
- Modify: `src/backend/config/urls.py`
- Modify: `src/backend/config/views.py`
- Modify: `src/backend/requirements.txt`
- Modify: `.env.example`

- **Specification:** [Application metrics](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#application-metrics),
  [Health and metrics endpoints](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#health-and-metrics-endpoints),
  and [Security and privacy](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#security-and-privacy).
- **Depends on:** None.
- **Produces:** `HttpMetricsMiddleware`, a private `metrics` Django route, and Prometheus series
  named `findme_http_requests_total` and `findme_http_request_duration_seconds`. Task 2 exposes only
  this route to the host agent.

- [ ] Add `prometheus-client==0.25.0` to
  `src/backend/requirements.txt`; install it into the project `.venv` so local tests use the same
  dependency selected for the application image.
- [ ] Write failing middleware tests for a named 200 route, a named 5xx response, an unmatched path,
  and a dynamic event/photo route. Assert the counter labels are exactly `environment`, `route`,
  `method`, and `status_class`; normalized routes use `request.resolver_match.view_name`; unmatched
  requests use the fixed `unmatched` value; no raw path, slug, UUID, query string, or exception text
  appears in the exposition.
- [ ] Write a failing endpoint test that `GET /metrics/` returns Prometheus content and that the
  scrape itself does not create an unbounded or self-observing route series.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest src/backend/config/tests/test_metrics.py -q` and confirm the new module,
  middleware, and route are absent.
- [ ] Implement `config.metrics` with a module-owned `CollectorRegistry`, one `Counter`, one
  `Histogram`, and `HttpMetricsMiddleware`. Start timing before `get_response`, observe after it
  returns, derive only the allowed bounded labels, and skip instrumentation for the metrics route.
  Do not use the default global registry, which would add unrelated process/runtime series outside
  the approved contract.
- [ ] Add `MONITORING_ENVIRONMENT` to settings with default `local`, document it in `.env.example`,
  insert the middleware after `SecurityMiddleware`, and add a `require_GET` metrics view using
  `generate_latest` plus the Prometheus content type.
- [ ] Run the targeted command again and expect all new metrics tests to pass.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest src/backend/picflow/tests/test_views.py src/backend/config/tests/test_metrics.py
  -q` and expect the existing health behavior and new instrumentation to pass together.

### Task 2: Expose metrics only on the VM loopback path

**Files:**

- Modify: `deploy/nginx/https.conf.template`
- Modify: `docker-compose.https.yml`
- Modify: `tests/deployment/validate-nginx.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/test_repository_foundation.py`

- **Specification:** [Health and metrics endpoints](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#health-and-metrics-endpoints)
  and acceptance criteria 6–7.
- **Depends on:** Task 1's exact `/metrics/` route.
- **Produces:** host-only `http://127.0.0.1:8080/metrics/` for Task 4's Unified Agent and an explicit
  public 404 contract.

- [ ] Extend the Nginx validation fixture with failing assertions that the canonical HTTPS server
  returns 404 for `/metrics/` and the private listener proxies only `/health/` and `/metrics/`.
- [ ] Extend repository-foundation tests with a failing assertion that the HTTPS overlay publishes
  container port `8080` only as `127.0.0.1:8080:8080`; ports 80 and 443 remain public.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest tests/test_repository_foundation.py
  tests/deployment/test_deployment_scripts.py -q` and run
  `sh tests/deployment/validate-nginx.sh`; confirm the new privacy assertions fail.
- [ ] Add an exact `location = /metrics/ { return 404; }` to the canonical HTTPS server before its
  catch-all route. Change the private server to listen on container port 8080, proxy exactly
  `/metrics/` and `/health/`, and retain `return 444` for every other path.
- [ ] Publish only `127.0.0.1:8080:8080` from the Nginx container. Do not publish a Django port or
  change the public 80/443 mappings.
- [ ] Run the targeted Python and Nginx commands again and expect all assertions to pass, including
  the existing canonical redirect, health, unknown-host, and private worker-route checks.
- [ ] Render
  `PUBLIC_DOMAIN=findme-photo.ru PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru
  APP_IMAGE=example.invalid/app:test docker compose -f docker-compose.prod.yml
  -f docker-compose.https.yml config` and confirm only Nginx has the new loopback bind.

### Task 3: Generate external public-health metrics in GitHub Actions

**Files:**

- Create: `scripts/monitor_public_health.py`
- Create: `tests/monitoring/__init__.py`
- Create: `tests/monitoring/test_public_health.py`
- Create: `.github/workflows/monitor-public-health.yml`
- Modify: `tests/test_repository_foundation.py`

- **Specification:** [Monitoring layers](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#monitoring-layers),
  [Alert contract](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#alert-contract),
  and [Validation](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#validation).
- **Depends on:** Existing public `/health/` response contract; independent of Tasks 1–2.
- **Produces:** custom Yandex Monitoring metrics `findme_probe_success`,
  `findme_probe_duration_seconds`, and `findme_probe_tls_days_remaining` with only
  `environment` and `check` labels.

- [ ] Write failing unit tests around injected HTTP, TLS, clock, and metric-writer boundaries. Cover
  expected JSON/HTTP 200, wrong status, wrong body, TLS validation failure, connection timeout,
  certificate lifetime calculation, write API failure, and redaction. A completed target failure
  must still produce `findme_probe_success = 0`; certificate lifetime is omitted when no trusted
  certificate was observed.
- [ ] Write a failing workflow-contract test that requires `schedule` at `*/5 * * * *`,
  `workflow_dispatch`, `contents: read`, no checkout credential persistence beyond the job, a fixed
  production URL for scheduled runs, and only `YANDEX_MONITORING_API_KEY` plus
  `YANDEX_CLOUD_FOLDER_ID` as monitoring credentials/configuration.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest tests/monitoring/test_public_health.py
  tests/test_repository_foundation.py -q` and confirm the script and workflow contracts are absent.
- [ ] Implement the probe with Python standard library only. Require explicit target, folder,
  environment, check name, and API key inputs; validate trusted TLS and the exact health JSON;
  measure monotonic elapsed time; calculate certificate days remaining; POST bounded DGAUGE values
  to `https://monitoring.api.cloud.yandex.net/monitoring/v2/data/write` with
  `service=custom`; never print the authorization header.
- [ ] Make the script write the observation before returning non-zero for a failed target. If the
  metrics write itself fails, return non-zero with a sanitized error so GitHub run history exposes
  loss of observation.
- [ ] Add the scheduled workflow on the default branch. Scheduled runs use only
  `https://findme-photo.ru/health/`, `environment=staging`, and `check=canonical-health`.
  `workflow_dispatch` accepts a controlled target and `environment=validation`; validation metrics
  cannot match production alert selectors.
- [ ] Run the targeted tests and expect them to pass.
- [ ] Run the script against a local deterministic HTTPS/HTTP test fixture without a real Yandex API
  key and verify the captured request contains only the three agreed metric names and bounded
  labels.

### Task 4: Configure one unprivileged Unified Agent

**Files:**

- Create: `deploy/monitoring/unified-agent.yml.template`
- Create: `deploy/configure-monitoring-agent.sh`
- Create: `tests/deployment/test_monitoring_agent.py`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_repository_foundation.py`

- **Specification:** [VM metrics](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#vm-metrics),
  [Failure semantics](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#failure-semantics),
  and [Security and privacy](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#security-and-privacy).
- **Depends on:** Task 2's host-loopback metrics route. Live execution also depends on the Task 6
  service-account approval gate.
- **Produces:** a versioned, testable host-agent configuration that sends `sys`, `ua`, and `app`
  namespaces to one Yandex Monitoring folder.

- [ ] Write failing tests for a template containing one `linux_metrics` input, one filtered
  `agent_metrics` health input, one `metrics_pull` input for
  `http://127.0.0.1:8080/metrics/`, a disk-backed bounded storage, and one `yc_metrics` output using
  VM metadata IAM. Assert the file contains no static credential and no Docker socket/container
  configuration.
- [ ] Write failing shell-contract tests for `deploy/configure-monitoring-agent.sh`: require root,
  explicit folder ID, supported `x86_64` Ubuntu, the official Yandex deb installation path, config
  rendering into `/etc/yandex/unified_agent/config.yml`, `check-config` before replacement,
  systemd enable/restart, and cleanup on failure. The script must print the installed version but no
  token.
- [ ] Add a failing workflow-contract assertion that monitoring-agent configuration is a separate
  manual `workflow_dispatch` step or job, never part of normal application reconciliation and never
  part of application rollback.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest tests/deployment/test_monitoring_agent.py
  tests/test_repository_foundation.py -q` and confirm the new contracts fail.
- [ ] Implement the template using the official `linux_metrics`, `agent_metrics`, `metrics_pull`,
  filesystem storage, and `yc_metrics` plugins. Collect basic CPU, memory, network, storage, I/O,
  kernel, and agent-health metrics at 60-second cadence; scrape Django at 60-second cadence; keep
  bounded disk buffering.
- [ ] Implement idempotent host configuration. Install the official Yandex Unified Agent deb only
  when absent; preserve the previous config; render the explicit folder ID; validate the candidate
  with `unified_agent ... check-config`; atomically promote it; restart and verify
  `systemctl is-active unified-agent`; restore the previous config when validation or restart
  fails.
- [ ] Add a manually dispatched staging-only workflow path that copies and runs the configuration
  script through the existing SSH boundary. It accepts no API key because the agent uses the VM
  metadata identity. Keep it absent from production promotion.
- [ ] Run the targeted tests and `sh -n deploy/configure-monitoring-agent.sh`; expect all to pass.
- [ ] Build or run an official Unified Agent image/package in an isolated validation environment and
  execute `check-config` against a rendered template before requesting live activation.

### Task 5: Define Yandex Monitoring resources and the operator runbook

**Files:**

- Create: `deploy/monitoring/dashboard.json`
- Create: `deploy/monitoring/alerts.md`
- Create: `docs/runbooks/minimal-monitoring.md`
- Create: `tests/deployment/test_monitoring_contract.py`
- Modify: `docs/engineering-jobs.md` only after evidence supports a status change

- **Specification:** [Dashboard](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#dashboard),
  [Alert contract](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#alert-contract),
  [Operator runbook](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#operator-runbook-contract),
  and acceptance criteria 8–12.
- **Depends on:** Metric names and labels from Tasks 1, 3, and 4.
- **Produces:** reviewable dashboard JSON, exact alert manifest, and the activation/diagnostic
  runbook.

- [ ] Write failing contract tests that require dashboard panels for external success/duration/TLS,
  CPU/load, memory/swap, filesystem bytes/inodes, disk I/O, network I/O, uptime/agent health,
  request rate, 5xx, and p50/p95 latency.
- [ ] Require exact alert-manifest entries for two failed/missing five-minute probe points, TLS
  below 14 days, five minutes of missing agent/host telemetry, disk below 10% or 5 GiB for 10
  minutes, memory available below 10% for 15 minutes, CPU above 90% for 15 minutes, and 5xx above
  20% with at least five requests in five minutes. Each entry states selector, aggregation,
  evaluation window, no-data policy, email channel, firing text, and recovery text.
- [ ] Require the runbook to contain the exact dashboard/alert resource names, credential names,
  safe first-response classification, controlled validation steps, a clearly marked activation
  evidence section that initially states `Not activated`, and rollback steps that never remove
  application/data volumes.
- [ ] Run
  `DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 SECRET_KEY=test
  .venv/bin/pytest tests/deployment/test_monitoring_contract.py -q` and confirm the artifacts are
  absent.
- [ ] Create one importable Yandex Monitoring dashboard JSON named
  `findme-photo-staging-overview`. Use only the metric names/labels produced by earlier tasks and
  include alert-status widgets or exact links after live alert creation.
- [ ] Create the alert manifest with the exact specification thresholds and explicit `No data`
  treatment. The public probe rule distinguishes `probe_success = 0` from missing external
  observation in its annotation even when both are actionable.
- [ ] Write the runbook for dashboard access, signal classification, public curl check, VM state,
  agent status, existing Compose diagnostics, recovery confirmation, and safe removal/disable
  order.
- [ ] Run the targeted test and expect it to pass.

### Task 6: Activate and validate staging monitoring

**Files:**

- Modify: `docs/runbooks/minimal-monitoring.md` with immutable live evidence
- Modify: `docs/architecture.md` only after the implemented status is observed
- Modify: `docs/engineering-jobs.md` with one evidence-backed EJ-009 transition
- Modify: `docs/plans/2026-07-30-minimal-service-monitoring.md` checkboxes and status

- **Specification:** [Validation](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md#validation)
  and all acceptance criteria.
- **Depends on:** Tasks 1–5 merged to the delivery branch and the explicit live-mutation approval
  below.
- **Produces:** live staging evidence, architecture reconciliation, and a final status for EJ-009.

- [ ] Run the complete repository verification suite from the [Verification](#verification) section
  and retain counts/output before any live change.
- [ ] Present the live mutation gate to the maintainer with profile `default`, cloud
  `b1gmcsmr51o5kvp86l55`, folder `b1g2qttgfhb4gdunvlge`, staging VM
  `epdr5g3p24tdns9890nr`, current attached service account (`none` as observed on 2026-07-30),
  proposed `sa-monitoring-staging`, exact IAM/API-key/VM-update commands, official current custom
  metric pricing, estimated monthly delta or `unknown`, availability/access impact, validation, and
  rollback. Stop until the user gives fresh explicit approval.
- [ ] After approval, create the dedicated service account, grant only `monitoring.editor` in the
  target folder, attach it to the staging VM, create one API key for the GitHub probe, save only the
  API key value as GitHub Environment secret `YANDEX_MONITORING_API_KEY`, and save the folder ID as
  variable `YANDEX_CLOUD_FOLDER_ID`. Never print or commit the key.
- [ ] Run the manual agent-configuration workflow and verify the installed version,
  `systemctl is-active unified-agent`, `http://127.0.0.1:8080/metrics/`, and fresh `sys`, `ua`, and
  `app` datapoints. Record any standard VM metric unavailable without privileged collection as an
  accepted omission.
- [ ] Import/create the dashboard, email notification channel, and seven alert definitions from
  Task 5. Record resource IDs and the dashboard URL in the runbook.
- [ ] Enable the scheduled GitHub workflow only after its API key can write
  `environment=staging,check=canonical-health` metrics. Confirm two fresh successful points and TLS
  lifetime.
- [ ] Use the manual validation label and a controlled failing target to prove exactly one failure
  and one recovery email without changing the production selector. Separately stop or isolate the
  agent long enough to prove only the missing-telemetry alert, then restore it and confirm recovery;
  do not stop the application or exhaust VM resources.
- [ ] Rerun the complete repository verification suite.
- [ ] Reconcile architecture and ADRs: update the current architecture with the delivered monitoring
  flow while keeping backups, retention policy, RPO/RTO, recovery, logs, worker monitoring, and the
  broader Operations module boundaries accurately proposed/open. Record conformance to ADR 0018.
- [ ] Advance EJ-009 to `Delivered` only if the dashboard, scheduled probe, and email alerts are live;
  advance to `Validated` only if controlled firing and recovery evidence is retained. Update current
  state, detail, date, and exactly one append-only history row.
- [ ] Mark this plan `Complete` only after the evidence and reconciliation are committed.

## Verification

Run from the worktree with the project `.venv` and a reachable PostgreSQL 16 test database using the
same values as CI:

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  .venv/bin/pytest --cov --cov-report=term-missing
SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  .venv/bin/python src/backend/manage.py check
SECRET_KEY=ci-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
  DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
  .venv/bin/python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
APP_IMAGE=example.invalid/app:test PUBLIC_DOMAIN=findme-photo.ru \
  PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru \
  docker compose -f docker-compose.prod.yml -f docker-compose.https.yml config
sh -n deploy/configure-monitoring-agent.sh
sh tests/deployment/validate-nginx.sh
sh tests/visual/run-in-container.sh test
```

Expected outcomes:

- Ruff format/lint and mypy exit zero.
- The full PostgreSQL pytest suite exits zero with branch coverage at or above the repository's
  75% regression guard and reports the exact test count.
- Django system and migration-drift checks exit zero.
- JavaScript tests and all containerized visual tests pass with their exact counts.
- Compose renders only ports 80/443 publicly and metrics on host loopback.
- Monitoring shell and Nginx configuration checks exit zero.

Live validation commands are run only after Task 6 approval and are recorded with sanitized output:

```bash
curl --fail-with-body --silent --show-error https://findme-photo.ru/health/
curl --fail-with-body --silent --show-error http://127.0.0.1:8080/metrics/
systemctl is-active unified-agent
unified_agent --config /etc/yandex/unified_agent/config.yml check-config
```

Expected outcomes are health JSON, private Prometheus exposition, `active`, and a zero-exit validated
configuration. The loopback metrics curl runs on the VM and must fail from a public host.

## Operational Impact and Rollout

1. Deliver application instrumentation and the private Nginx/Compose route while monitoring remains
   unconfigured. Missing agent or Yandex resources cannot affect requests or deployment.
2. Obtain the Task 6 access/pricing approval.
3. Create the least-privilege service account and GitHub probe credential.
4. Configure Unified Agent and confirm internal metrics before creating alerts.
5. Create the dashboard and email channel, then alerts.
6. Enable the scheduled external probe and confirm successful observations.
7. Validate controlled alert and recovery transitions.
8. Publish the evidence-backed documentation/status reconciliation.

There is no database migration or product-data change. Custom metric writes and reads may incur
Yandex Monitoring charges. The first activation is staging-only.

## Rollback

Rollback is ordered to preserve observation while components are removed:

1. Disable the scheduled GitHub monitoring workflow.
2. Disable Yandex alerts and notification channel; retain the dashboard temporarily for diagnosis.
3. Stop and disable Unified Agent, restore its previous configuration when one existed, and remove
   only the monitoring package/configuration owned by this plan.
4. Redeploy the previous immutable application image and Compose/Nginx revision if application
   instrumentation or the loopback route must be removed. Do not run `docker compose down
   --volumes`.
5. After confirming no writers remain, delete the probe API key, detach the dedicated service
   account from the VM, remove only its `monitoring.editor` binding, and delete the dedicated
   service account if unused.
6. Delete monitoring alerts/dashboard only after any required incident evidence is exported.

Application state, PostgreSQL data, media, deployment markers, certificates, and existing
application rollback remain unchanged. Reverting metrics code alone is safe because no product
state depends on it.

## Open Questions

None. Live cloud mutation and potentially billable custom-metric activation remain explicit
execution approval gates, not unresolved design choices.
