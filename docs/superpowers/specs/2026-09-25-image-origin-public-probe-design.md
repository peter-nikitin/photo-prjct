# Image-origin hosted public health probe

- Status: Accepted from maintainer direction on 2026-09-25
- Governing decision: [ADR 0039](../../adr/0039-run-public-probe-on-image-origin-vm.md)
- Extends the [minimal monitoring design](2026-07-30-minimal-service-monitoring-design.md) and replaces only its GitHub Actions probe placement and related validation. All VM/application metrics, dashboard, email, and incident-classification boundaries remain in force.

## Goal and boundary

Observe the canonical public HTTPS path every five minutes from the existing `findme-gallery-image-origin` VM (`epdf6696opq3ock91pih`). Two consecutive failed or missing observations should produce an operator email within roughly 10–15 minutes when Yandex Monitoring and the probe VM are functioning. Only the maintainer's Yandex Cloud account receives notifications. A common `ru-central1-b` zone or Yandex Cloud outage is an accepted blind spot.

The VM also serves gallery images. Probe installation and normal execution must neither restart its Nginx/imgproxy containers nor alter their network exposure or secrets. The probe opens no listener and does not execute on the observed application VM.

## Runtime contract

A host `systemd` timer runs one bounded Python process every five minutes. The process uses the existing public-health check logic: trusted HTTPS, the exact `{"status":"ok"}` response, elapsed time, and trusted certificate lifetime. The HTTPS destination is the public `https://findme-photo.ru/health/` name, not a private address. A completed failed check writes `findme_probe_success=0`; successful checks write `1`. A failure to execute or write results in missing data and must be distinguishable from an observed HTTP failure. Logs contain only safe status/reason codes, no tokens, URLs with queries, body content, or customer data.

The process obtains a short-lived IAM token from the VM metadata service for the existing attached service account (`ajef0cammpfg75gn7ntd`), which already has `monitoring.editor` on folder `b1g2qttgfhb4gdunvlge`. It writes the existing three low-cardinality metrics to `service=custom` with `check=canonical-health`. It stores no API key or IAM token. The metadata endpoint is reachable only from the VM; the process validates token response shape and never logs the token.

A dedicated installation entrypoint packages the probe, unit, and timer independently of the image-origin Compose release. It validates files and target IDs, installs atomically, starts only the probe timer, and retains a previous package for rollback. The GitHub Actions deployment uses the existing SSH/OIDC projection and reviewed host keys, but does not execute image-origin `apply.sh` or project application deployment.

The existing GitHub scheduled workflow remains active until two fresh VM-generated canonical points are confirmed; its schedule is then removed to avoid duplicate writers. Validation checks use `check=validation-health` and cannot affect canonical alerts.

## Alert and operator contract

The dashboard uses the existing three probe metrics. Seven baseline alerts from `deploy/monitoring/alerts.md` are created after the email channel exists. Selectors use the folder as a Monitoring resource/query parameter, not a metric label. Public failure requires two consecutive failed five-minute observations; missing observation is labelled as telemetry loss, not a confirmed failed response. The operator's own Yandex Cloud account (`ajeo8gv7nlo4tgb9u9r4`, the existing cloud owner) is the sole email recipient. Its inherited role already covers Monitoring viewing; no new IAM binding is planned. Recovery notifications are enabled.

The runbook records the VM and timer, dashboard URL, alert and channel IDs, actual point freshness, test firing/recovery and email delivery, and rollback. Commerce worker placeholders remain inactive.

## Acceptance

1. Before activation, local tests cover the probe's metadata authentication, safe output, failure observation, unit/timer contract, and independent installer/rollback boundaries.
2. The image-origin VM has a healthy five-minute timer and two fresh consecutive canonical success/TLS points. Image-origin services and their metrics remain healthy without container restart during installation.
3. A controlled validation check produces one failure and one recovery observation under `check=validation-health`. A temporary alert scoped to that label verifies firing and recovery email, then is removed. An agent/probe absence test distinguishes missing telemetry from application failure without stopping the public application.
4. The dashboard displays fresh VM, Django, and public-probe graphs; all seven baseline alerts and one email channel exist with recorded IDs. Only the maintainer's account is a recipient.
5. The GitHub schedule is disabled only after VM probe verification. Rollback disables the VM timer and can restore the prior package without changing image-origin containers.

## Cost and authorization

No new VM or service account is created. At three metrics every five minutes, the probe writes about 25,920 values per 30 days (approximately ₽0.01 at the current first-tier Monitoring write rate, before billing rounding); the existing agent's metrics are separate. Live installation, IAM or channel changes, and billable metric activation require a fresh review of exact commands, current state, impact, validation, and rollback before execution.
