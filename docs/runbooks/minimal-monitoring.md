# Minimal deployment monitoring runbook

Use this runbook for first response only. Monitoring does not restart containers or the VM, deploy
an image, run migrations, or perform rollback automatically.

## Resources and credentials

- Dashboard resource name: `findme-photo-deployment-overview`.
- Alert resource names: `findme-photo-deployment-public-service-unavailable`,
  `findme-photo-deployment-tls-certificate-expiring`,
  `findme-photo-deployment-vm-telemetry-missing`,
  `findme-photo-deployment-disk-space-critical`, `findme-photo-deployment-memory-pressure`,
  `findme-photo-deployment-cpu-pressure`, and
  `findme-photo-deployment-application-5xx-degradation`.
- GitHub Actions keeps `YANDEX_MONITORING_API_KEY` as an environment secret and
  `YANDEX_CLOUD_FOLDER_ID` for manual validation checks. The image-origin VM probe uses its
  attached service account's short-lived metadata token. Do not print credentials or copy them
  into tickets.

## Activation evidence

**Partially activated.** The application VM agent sends host and Django HTTP metrics, and the
separate image-origin VM sends five-minute public HTTPS probe metrics. The dashboard, alerts, and
notification channel have not been activated; there is no firing/recovery or email-delivery
evidence. EJ-009 remains planned.

### 2026-09-25 public probe activation

- [PR #212](https://github.com/peter-nikitin/photo-prjct/pull/212) merged the independent host
  timer package. Manual [installation run 36105191624](https://github.com/peter-nikitin/photo-prjct/actions/runs/36105191624)
  installed repository SHA `633a3bbb4c4417655ba21727438c622060f2004a` on
  `findme-gallery-image-origin` (`epdf6696opq3ock91pih`) and reported
  `PUBLIC_PROBE_TIMER=active`. The installer did not run image-origin Compose.
- Monitoring API returned `findme_probe_success=1` and fresh TLS-lifetime points at
  `06:57:00Z` and `07:02:02Z`, roughly five minutes apart. Public `/health/` returned
  `{"status":"ok"}`; `img-origin.findme-photo.ru` passed TLS verification and returned its
  expected 404 on `/`. Image-origin host telemetry remained fresh.
- The VM probe and application VM both run in `ru-central1-b`, so a shared zone failure is not
  observed. The separate GitHub cron is retired by the follow-up cutover change; the manual
  validation workflow remains available under `check=validation-health`.
- To stop the first installation without access to the host SSH key, dispatch
  `gh workflow run deploy-public-probe.yml --ref main -f action=disable -f deployment_sha=633a3bbb4c4417655ba21727438c622060f2004a`.

### 2026-09-25 VM metric collection

- VM `dev-photo-prjct` (`epdr5g3p24tdns9890nr`) in folder `b1g2qttgfhb4gdunvlge` runs
  Unified Agent `26.09.10`. Its prior configuration is backed up at
  `/etc/yc/unified_agent/config.yml.pre-findme-20260925`.
- Applied `deploy/configure-monitoring-agent.sh --folder-id b1g2qttgfhb4gdunvlge` on the VM.
  The active configuration pulls `http://127.0.0.1:8080/metrics/` every 60 seconds into the
  `app` namespace. `check-config` passed and `unified_agent` was active after restart.
- Monitoring API returned fresh `sys.system.UpTime`, `ua.backlog`,
  `app.findme_http_requests_total`, and `app.findme_http_request_duration_seconds` series.
  The dashboard's request-rate derivative and p95 histogram queries returned fresh points for
  `route="health"`. Public `/health/` returned 200 and public `/metrics/` returned 404.
- This verifies metric ingestion and query compatibility, not an imported dashboard or a live
  alert. Restore the backup and restart `unified_agent` if the new scrape must be rolled back.

### 2026-07-30 activation attempt and rollback evidence

- Manual GitHub Actions agent-configuration run
  [`30564435043`](https://github.com/peter-nikitin/photo-prjct/actions/runs/30564435043) reached
  the agent step. The normal build and deploy paths were skipped.
- The agent step failed before installation because the deployment SSH user did not have passwordless
  `sudo`. Temporary OS Login roles were granted solely to recover access, then removed after
  certificate authentication also failed.
- The approved rollback was completed and verified: the deployment VM remained `RUNNING` with no
  attached service account; the dedicated monitoring service account, its `monitoring.editor`
  binding, its API key, and the GitHub deployment monitoring secret and folder variable were absent.
- No Unified Agent, dashboard, alert, notification channel, or scheduled probe was activated.
  The application, PostgreSQL data, media, deployment state, and volumes were untouched.

## First response

1. Open the image-origin VM probe timer result and dashboard
   `findme-photo-deployment-overview`. Classify the alert before taking any recovery action:
   public endpoint failure; VM/host telemetry loss; application 5xx degradation; resource pressure;
   or agent-only failure.
2. For a public endpoint failure, independently run
   `curl --fail --silent --show-error https://findme-photo.ru/health/`. A successful response while
   the alert says a probe point is missing means **missing external observation**, not a confirmed
   failed application response.
3. Check VM power and connectivity before application actions:
   `yc compute instance get epdr5g3p24tdns9890nr`. Then use the dashboard graphs to identify CPU,
   memory, filesystem, or network pressure. Do not stop the VM merely to test an alert.
4. For VM/host telemetry loss or agent-only failure, check
   `systemctl is-active unified_agent` and
   `/bin/unified_agent --config /etc/yc/unified_agent/config.yml check-config` on the VM, then
   inspect its status/logs. If public curl and the probe are healthy, treat an agent-only failure as
   telemetry loss rather than service outage.
5. For application 5xx degradation or public failure, follow the existing Compose diagnostics on
   the VM (for example, `docker compose ps` and `docker compose logs --tail 100 web nginx`). Use
   the repository deployment/rollback procedure if it applies; monitoring adds no recovery command.
6. After a controlled recovery, confirm fresh dashboard datapoints and that the relevant alert
   returns to normal. Confirm the corresponding recovery email arrives once.

## Controlled validation after activation

Use a controlled failing target with `check=validation-health` and a temporary alert scoped to
that check to prove one failure and one recovery email without touching the canonical selector.
Remove the temporary alert afterward. Separately stop or isolate only Unified Agent long enough to
prove missing telemetry while a successful public probe remains evidence that the service is up;
restore the agent immediately. Do not fill disks,
consume all CPU or memory, expire the real certificate, stop the application, or expose `/metrics/`
publicly.

## Disable and rollback

1. Disable the image-origin probe timer and any remaining GitHub schedule, then disable alerts and
   the email notification channel. Keep the dashboard until incident evidence is exported.
2. Stop and disable Unified Agent on the application VM; restore its prior configuration if one existed; remove only the
   monitoring package and monitoring configuration installed by this work.
3. If metrics instrumentation must be removed, use the existing immutable-image and Compose/Nginx
   rollback procedure. Never remove application or data volumes. In particular, do not run
   `docker compose down --volumes`.
4. After metric writers have stopped, remove the dedicated GitHub probe API key if one remains.
   Keep the image-origin VM's attached service account and its existing `monitoring.editor`
   binding because image-origin telemetry also uses them. Delete alert/dashboard resources only
   after required incident evidence is retained.
