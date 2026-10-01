# FindMe Photo deployment monitoring alert manifest

Create the baseline alerts below after the dashboard and email notification channel
exist. Create the resources in folder `__YANDEX_CLOUD_FOLDER_ID__` and pass that folder ID
as Monitoring request context, not as a metric label. Every selector is restricted to
`service=custom` and the canonical probe selector uses `check="canonical-health"`.

Notification channel: the one deployment operator **email** channel created during activation.
Enable notifications for `Alarm`, `No data`, and `OK` (recovery) on that channel.
No alert performs automated remediation. The alert resource names below are the exact names to use.

## Public service unavailable

- Resource name: `findme-photo-deployment-public-service-unavailable`
- Selector: `findme_probe_success{service="custom", check="canonical-health"}`.
- Aggregation: maximum over the 10-minute window; Alarm when the maximum is below `0.5`.
  With a five-minute writer, a preceding successful point must leave the window before
  sustained failures fire. A single failure immediately after a success stays OK.
- Evaluation window: 10 minutes. Monitoring evaluates the alert once per minute.
- No data: set both **No selector metrics** and **No points in evaluation window** to `No data`.
  The channel must notify on `No data` as well as `Alarm`. These are distinct alert states:
  `Alarm` means an observed failed check; `No data` means the probe has stopped reporting.
- Notification channel: deployment operator email.
- Firing annotation: `{{#isAlarm}}FindMe public HTTPS checks have failed throughout the 10-minute window.{{/isAlarm}}{{#isNoData}}FindMe public HTTPS probe observations are missing; site availability is unconfirmed.{{/isNoData}}`
- Recovery notification: `FindMe deployment public health probe has recovered.`

## TLS certificate expiring

- Resource name: `findme-photo-deployment-tls-certificate-expiring`
- Selector: `findme_probe_tls_days_remaining{service="custom", check="canonical-health"}`.
- Aggregation: minimum remaining certificate lifetime.
- Evaluation window: 5 minutes; below 14 days.
- No data: do not infer certificate expiry; the public-service-unavailable rule covers missing probe observations.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment TLS certificate has fewer than 14 days remaining.`
- Recovery notification: `FindMe deployment TLS certificate lifetime is back above 14 days.`

## VM telemetry missing

- Resource name: `findme-photo-deployment-vm-telemetry-missing`
- Selector: `ua.backlog{service="custom", scope="health"}` with corroborating `sys.system.UpTime{service="custom"}`.
- Aggregation: latest agent health and host telemetry point.
- Evaluation window: 5 minutes of missing agent or host telemetry.
- No data: actionable. It is an agent/host-observation failure only; do not relabel it as public outage.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment VM telemetry has been missing for five minutes; public health may still be up.`
- Recovery notification: `FindMe deployment VM telemetry has resumed.`

## Disk space critical

- Resource name: `findme-photo-deployment-disk-space-critical`
- Selector: `sys.filesystem.FreeB{service="custom", mountpoint="/"}` and `sys.filesystem.SizeB{service="custom", mountpoint="/"}` for the system filesystem only.
- Aggregation: available bytes divided by total bytes, and available bytes.
- Evaluation window: 10 minutes; below 10% or 5 GiB.
- No data: do not fire this resource-pressure alert; missing telemetry is handled by the telemetry alert.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment system filesystem is below 10% free or 5 GiB available.`
- Recovery notification: `FindMe deployment system filesystem capacity has recovered.`

## Memory pressure

- Resource name: `findme-photo-deployment-memory-pressure`
- Selector: `sys.memory.MemAvailable{service="custom"}` and `sys.memory.MemTotal{service="custom"}`.
- Aggregation: available memory divided by total memory.
- Evaluation window: 15 minutes; below 10%.
- No data: do not fire this resource-pressure alert; missing telemetry is handled by the telemetry alert.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment available memory has been below 10% for 15 minutes.`
- Recovery notification: `FindMe deployment available memory has recovered above 10%.`

## CPU pressure

- Resource name: `findme-photo-deployment-cpu-pressure`
- Selector: `sys.system.UsefulTime{service="custom", cpu="-"}` and `sys.system.IdleTime{service="custom", cpu="-"}`.
- Aggregation: `100 * UsefulTime / (IdleTime + UsefulTime)`.
- Evaluation window: 15 minutes; above 90%.
- No data: do not fire this resource-pressure alert; missing telemetry is handled by the telemetry alert.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment CPU utilization has been above 90% for 15 minutes.`
- Recovery notification: `FindMe deployment CPU utilization has recovered below 90%.`

## Application 5xx degradation

- Resource name: `findme-photo-deployment-application-5xx-degradation`
- Selector: `app.findme_http_requests_total{service="custom"}` and the same
  selector restricted to `status_class="5xx"`.
- Aggregation: five-minute 5xx response count divided by five-minute total request count, with at
  least five total requests.
- Evaluation window: 5 minutes; above 20% with at least 5 requests.
- No data: do not fire this application alert; missing telemetry is handled by the telemetry alert.
- Notification channel: deployment operator email.
- Firing notification: `FindMe deployment HTTP 5xx responses exceed 20% with at least five requests in five minutes.`
- Recovery notification: `FindMe deployment HTTP 5xx response rate has recovered.`

## Commerce worker unavailable

- Activate only after the independent host collector described in
  `docs/runbooks/commerce-monitoring.md` publishes fresh observations. It runs once per minute
  outside the Commerce worker. A valid observation publishes `1` for live or `0` for unavailable;
  a failed collection publishes neither liveness nor queue age.
- Resource name: `findme-photo-commerce-worker-unavailable`.
- Console selector: `"commerce_worker_alive"{folderId="__YANDEX_CLOUD_FOLDER_ID__", service="custom", check="canonical-commerce"}`.
- Aggregation: maximum; Alarm when below `0.5` throughout a five-minute window. Clear Warning.
- Evaluation window: 5 minutes; evaluation delay 0 seconds.
- No data: set both policies to `No data`. Missing observation is a collector/web/database
  observation failure, not a confirmed worker death or payment failure.
- Notification channel: deployment operator email.
- Notification states: `Alarm`, `No data`, and `OK`.
- Firing annotation: `{{#isAlarm}}FindMe Commerce worker liveness observations are zero throughout the five-minute window.{{/isAlarm}}{{#isNoData}}FindMe Commerce worker observations are missing; inspect the collector, web and database.{{/isNoData}}`
- Recovery notification: `FindMe Commerce worker liveness has recovered.`

## Commerce ready work overdue

- Activate only after the same independent collector publishes fresh
  `commerce_oldest_ready_age_seconds` observations. An observed empty ready queue publishes zero;
  a failed observation never publishes zero.
- Resource name: `findme-photo-commerce-ready-work-overdue`.
- Console selector: `"commerce_oldest_ready_age_seconds"{folderId="__YANDEX_CLOUD_FOLDER_ID__", service="custom", check="canonical-commerce"}`.
- Aggregation: maximum ready-work age.
- Evaluation window: 5 minutes; above 300 seconds.
- Clear Warning; evaluation delay 0 seconds. Set both no-data policies to `OK`;
  the worker-unavailable rule handles missing independent observation.
- Notification channel: deployment operator email.
- Notification states: `Alarm` and `OK`.
- Firing notification: `FindMe Commerce ready work is overdue; inspect Commerce Admin and worker health.`
- Recovery notification: `FindMe Commerce ready work is within the configured threshold.`

## Worker pool alerts

Worker rules are owned by the existing [Prometheus package](prometheus/README.md), following
[ADR 0048](../../docs/adr/0048-reuse-managed-prometheus-for-worker-alerts.md). Do not create a
second native worker-alert lifecycle or use the retired ceiling-two predicate.

The worker profile is enabled in Git for preparation, but has not been applied live and remote
workers have not been accepted. It covers
cap-one saturation, overdue work, queue/cloud/native-publisher observation loss and missing
expected-node diagnostics. Queue age must exceed 300 seconds and keep growing for five minutes
at fresh actual running capacity >=1 to qualify as cap-one saturation. Missing/stale observations
are unknown, not an empty queue or spare capacity. Bulk with fresh actual zero members is a
valid idle state. Rules never scale, restart, grant claims or mutate processing state.

Native demand/capacity publication remains authoritative for Instance Groups autoscaling.
The additional private Prometheus scrape only observes that state; native and Prometheus
delivery must be proved separately. Before customer cutover, prove controlled firing, missing
series, retained stale sources, recovery and notification delivery through the existing receiver.
See the [worker runbook](../../docs/runbooks/worker-pools.md) and
[live acceptance gate](../../docs/future-work/2026-09-29-cap-one-worker-saturation-alert.md).
