# Minimal staging monitoring runbook

Use this runbook for first response only. Monitoring does not restart containers or the VM, deploy
an image, run migrations, or perform rollback automatically.

## Resources and credentials

- Dashboard resource name: `findme-photo-staging-overview`.
- Alert resource names: `findme-photo-staging-public-service-unavailable`,
  `findme-photo-staging-tls-certificate-expiring`,
  `findme-photo-staging-vm-telemetry-missing`,
  `findme-photo-staging-disk-space-critical`, `findme-photo-staging-memory-pressure`,
  `findme-photo-staging-cpu-pressure`, and
  `findme-photo-staging-application-5xx-degradation`.
- GitHub Actions keeps `YANDEX_MONITORING_API_KEY` as an environment secret and
  `YANDEX_CLOUD_FOLDER_ID` as configuration. Do not print either value, put it in a command line,
  or copy it into tickets.

## Activation evidence

**Not activated.** Task 6 records the dashboard URL, alert IDs, notification-channel identity,
Unified Agent version, fresh `sys`, `ua`, and `app` datapoints, and controlled firing/recovery
evidence here. Until then, this file is a reviewed configuration contract, not evidence that an
email, dashboard, agent, or scheduled probe is live.

### 2026-07-30 activation attempt and rollback evidence

- Manual GitHub Actions agent-configuration run
  [`30564435043`](https://github.com/peter-nikitin/photo-prjct/actions/runs/30564435043) reached
  the agent step. The normal build and deploy paths were skipped.
- The agent step failed before installation because the staging SSH user did not have passwordless
  `sudo`. Temporary OS Login roles were granted solely to recover access, then removed after
  certificate authentication also failed.
- The approved rollback was completed and verified: the staging VM remained `RUNNING` with no
  attached service account; the dedicated monitoring service account, its `monitoring.editor`
  binding, its API key, and the GitHub staging monitoring secret and folder variable were absent.
- No Unified Agent, dashboard, alert, notification channel, or scheduled probe was activated.
  The application, PostgreSQL data, media, deployment state, and volumes were untouched.

## First response

1. Open the external GitHub Actions health-check result and dashboard
   `findme-photo-staging-overview`. Classify the alert before taking any recovery action:
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
   `systemctl is-active unified-agent` on the VM and inspect its status/logs. If public curl and
   the probe are healthy, treat an agent-only failure as telemetry loss rather than service outage.
5. For application 5xx degradation or public failure, follow the existing Compose diagnostics on
   the VM (for example, `docker compose ps` and `docker compose logs --tail 100 web nginx`). Use
   the repository deployment/rollback procedure if it applies; monitoring adds no recovery command.
6. After a controlled recovery, confirm fresh dashboard datapoints and that the relevant alert
   returns to normal. Confirm the corresponding recovery email arrives once.

## Controlled validation after activation

Use the manual workflow's **controlled failing target** with its `environment=validation` label to
prove one probe failure and one recovery email without touching the staging alert selector. Separately
stop or isolate only Unified Agent long enough to prove missing telemetry while a successful public
probe remains evidence that the service is up; restore the agent immediately. Do not fill disks,
consume all CPU or memory, expire the real certificate, stop the application, or expose `/metrics/`
publicly.

## Disable and rollback

1. Disable the scheduled public-health workflow, then disable alerts and the email notification
   channel. Keep the dashboard until incident evidence is exported.
2. Stop and disable Unified Agent; restore its prior configuration if one existed; remove only the
   monitoring package and monitoring configuration installed by this work.
3. If metrics instrumentation must be removed, use the existing immutable-image and Compose/Nginx
   rollback procedure. Never remove application or data volumes. In particular, do not run
   `docker compose down --volumes`.
4. After metric writers have stopped, remove the dedicated probe API key, detach the dedicated
   service account, and remove only its `monitoring.editor` binding. Delete alert/dashboard
   resources only after required incident evidence is retained.
