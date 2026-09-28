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

## Worker pool observation missing

- Activate after the canonical host collector publishes every thirty seconds for each exact
  `pool` (`bulk`, `selfie`) and configured `zone_id`. Create one alert per pool.
- Resource names: `findme-photo-worker-bulk-observation-missing` and
  `findme-photo-worker-selfie-observation-missing`.
- Selector: `worker_pool_capacity_fresh{service="custom", pool="bulk", zone_id="<zone>"}`
  (use `selfie` for its corresponding alert). Aggregation: maximum below 0.5 over 90 seconds
  is `Alarm` for unavailable/stale cloud membership. Both no-data policies are `No data`;
  missing publication points over that window mean unconfirmed demand and capacity. Notify
  `Alarm`, `No data` and `OK` through the existing operator channel. Queue publication and F
  are in the same complete Monitoring write; `worker_pool_observed_timestamp` corroborates it.
- A collection, endpoint or Monitoring write failure produces no workload zero and leaves the
  coordinator's last successful queue timestamp unchanged. No data means demand is unconfirmed;
  it is not proof of an empty queue, dead worker or customer-facing outage.
- A cloud-read failure can
  coexist with fresh queue metrics; inspect the collector's nonzero exit and capacity freshness,
  not only the queue timestamp. Capacity unavailable is not zero VMs.

## Worker pool work overdue

- Resource names: `findme-photo-worker-bulk-work-overdue` and
  `findme-photo-worker-selfie-work-overdue`.
- Selector: `worker_pool_oldest_claimable_age_seconds{service="custom", pool="<pool>", zone_id="<zone>"}`.
- Maximum over five minutes, above 300 seconds. Notify on `Alarm` and `OK`; no data is handled
  by the observation rule. These initial operator thresholds are not measured capacity promises.
- Inspect claimable, active/recoverable lease and failed-job gauges before changing capacity.
  No worker alert changes group sizes, feature gates, jobs or releases automatically.

## Worker pool at-ceiling saturation

- Prepare one resource per exact pool/zone: `findme-photo-worker-bulk-at-ceiling-saturation`
  and `findme-photo-worker-selfie-at-ceiling-saturation`. Do not activate before fleet telemetry
  and the existing operator email channel are separately approved.
- Selector: named queries `R = worker_pool_running_instances{service="custom", pool="<pool>", zone_id="<zone>"}`,
  `F = worker_pool_capacity_fresh{service="custom", pool="<pool>", zone_id="<zone>"}` and
  `A = worker_pool_oldest_claimable_age_seconds{service="custom", pool="<pool>", zone_id="<zone>"}`.
  Select exactly one timeseries per query, with the same pool and zone; folder is request context.
- Test query `S = F * ramp(sign(replace_nan(R, -1) - 1.5)) * ramp(sign(A - 300)) * ramp(sign(derivative(A)))`.
  Aggregation: minimum of S over a five-minute evaluation window; `Alarm` above 0.5, no Warning,
  evaluation delay zero, evaluate once per minute. This requires two actual running VMs and
  claimable age above 300 seconds with positive change at every observed interval throughout
  the window. Queue progress/age reset, fewer running VMs or unavailable capacity breaks it.
- Capacity source is the coordinator's ordered complete trusted current cloud membership:
  RUNNING_ACTUAL/RUNNING_OUTDATED rows only, completion time within 90 seconds. Not requested
  target size, historical registered processes, pending slots or workload-derived capacity.
- No data: both no-selector and no-points policies `No data`; notify `Alarm`, `No data`, `OK`.
  Do not fill/interpolate with last values or zero. NaN in R uses -1 as **unknown predicate**, never an
  actual-capacity zero. Absent/stale capacity publishes F=0 and omits R; saturation is excluded,
  and the observation-missing rule reports this separately after 90 seconds. Missing queue
  observations are likewise handled by that rule, not interpreted as empty/healthy capacity.
  The first undefined derivative is not replaced with zero or -1 (which would permanently
  suppress a minimum-window alarm); require subsequent age deltas and separately verify the
  observation rule's 90-second gap/staleness behavior during activation.
- Firing annotation: `FindMe <pool> is at its approved VM ceiling while claimable age keeps rising; inspect worker-pool diagnostics before any capacity decision.`
- Recovery notification: `FindMe <pool> no longer satisfies the sustained saturation predicate; confirm queue progress and fresh capacity.`
- Response: use `docs/runbooks/worker-pools.md` diagnostics to distinguish warm/serving capacity,
  stalled attempts and container restarts. Preserve hard maximum two and selfie claim cap one;
  this alert never raises a limit, opens the second claim slot or restarts/mutates anything.
- A total publisher outage may leave positive historical S points inside this five-minute
  window even when the 90-second observation rule reports Alarm/NoData. That observation state
  takes precedence: historical saturation is UNCONFIRMED until fresh current capacity, queue
  and coordinator status are obtained. Do not label it current saturation or authority to raise
  resources. Exact native gap suppression/evaluation is not proven by repository preparation.
  Apply this qualification to the firing annotation above when observation is Alarm/NoData.

Named queries, derivative/ramp/sign/replace_nan and window aggregation follow the
[Monitoring query language](https://yandex.cloud/en/docs/monitoring/concepts/querying) and
[alert contract](https://yandex.cloud/en/docs/monitoring/concepts/alerting/alert).
Native evaluation, gap handling and notification/recovery proof remain approved activation gates.
