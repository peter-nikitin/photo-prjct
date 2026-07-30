# Minimal Service Monitoring Design

## Status

Approved in conversation on 2026-07-30.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current deployment
  topology, accepted Docker Compose and Yandex Cloud constraints, proposed Operations module, and
  the open observability-stack decision
- Related ADRs:
  [ADR 0003](../../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0007](../../adr/0007-nginx-certbot-https-edge.md),
  [ADR 0011](../../adr/0011-use-minimal-shared-https-rollout.md), and
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md)
- Governing ADR:
  [ADR 0018](../../adr/0018-use-managed-yandex-monitoring.md)
- ADR impact: Conforms to ADR 0018
- Implementation plan:
  [Draft minimal service monitoring plan](../../plans/2026-07-30-minimal-service-monitoring.md)

## Goal

Give one operator a small, actionable view of whether the current public FindMe Photo environment
is reachable, whether its VM has enough basic resources, and whether the Django application is
degrading. A persistent problem must produce an email without requiring the operator to inspect the
VM manually.

This increment monitors only the current critical path:

`public HTTPS -> Nginx -> Django on the current Yandex Cloud VM`

The design deliberately uses managed monitoring instead of running a metrics database and
dashboard stack on the same small VM that it observes.

## Outcome

The completed increment provides:

- an external view of public HTTPS availability, TLS validity, and response latency;
- one Yandex Monitoring dashboard with basic VM and Django HTTP graphs;
- email alerts for sustained public unavailability, missing VM telemetry, imminent resource
  exhaustion, sustained CPU pressure, and a material HTTP 5xx rate;
- recovery notifications for every actionable alert;
- a short operator runbook that distinguishes a public-service failure from a VM, application, or
  metrics-agent problem; and
- monitoring that cannot block application startup, request handling, deployment, or rollback.

## Scope

### Included

- The one currently active public environment at `https://findme-photo.ru/`.
- A managed external HTTPS check of `https://findme-photo.ru/health/`.
- Yandex Monitoring as the store, dashboard, and alert evaluator for VM and application metrics.
- One Yandex Unified Agent on the current VM.
- Standard Linux host metrics available through Unified Agent without a privileged monitoring
  container or a new self-hosted monitoring service.
- A private Prometheus-format endpoint for low-cardinality Django HTTP metrics.
- One operator email notification channel.
- Initial alert thresholds, alert-condition validation, recovery validation, and a minimal runbook.

### Excluded

- Self-hosted Prometheus, Grafana, Alertmanager, Loki, Elasticsearch, or another monitoring
  database.
- Centralized logs, log search, tracing, profiling, exception aggregation, and real-user
  monitoring.
- Business metrics, per-event or per-photo metrics, search quality, commerce, payments, and
  download-entitlement monitoring.
- PostgreSQL query, lock, replication, and table-level metrics.
- Worker, processing queue, ML runtime, and face-search monitoring while those paths are disabled in
  real environments.
- SLOs, error budgets, on-call rotations, escalations, SMS, Telegram, and automated remediation.
- Container-level metrics when collecting them requires exposing the Docker socket, running a
  privileged container, or adding another exporter.
- Production-environment monitoring before a separate production environment exists.

The worker and its queue return to monitoring scope when a worker is activated in a real
environment. PostgreSQL internals, business paths, and centralized logs require evidence that the
current signals cannot diagnose a realistic production problem.

## Selected Design

### Monitoring layers

The system has two independent observation layers:

1. A scheduled GitHub Actions workflow runs outside Yandex Cloud, makes periodic HTTPS requests to
   the canonical health endpoint, measures success, total response time, and certificate expiry,
   and writes those three custom metrics to Yandex Monitoring.
2. Yandex Unified Agent runs on the VM, collects standard Linux metrics, scrapes a private
   Prometheus endpoint exposed by Django, and sends both streams to Yandex Monitoring. Yandex
   Monitoring stores the time series, renders the dashboard, evaluates alerts once per minute, and
   sends email through its notification channel.

GitHub Actions is the selected external probe runner because the project already depends on it for
CI/CD. The workflow runs from the default branch every five minutes, and Yandex Monitoring retains
the probe history and sends failure and recovery email. Replacing the runner later does not change
the public-probe metric names, dashboard, alerts, application instrumentation, or VM collection.

### Data flow

```text
Scheduled GitHub Actions
        |
        |---- HTTPS ----> findme-photo.ru/health/
        `---- metrics ------------------------------\
                              |
                              `-> Nginx -> Django

VM Linux metrics ----\                                  |
                      > Yandex Unified Agent ------------> Yandex Monitoring -> dashboard
Django /metrics -----/                                                     |
                                                                           `-> operator email
```

Application service does not read from Yandex Monitoring and does not depend on successful metric
delivery. The agent buffers or drops telemetry according to its bounded configuration without
creating backpressure on Django.

### Health and metrics endpoints

The existing public `/health/` endpoint remains database-independent and inexpensive. It proves
that public DNS, trusted TLS, the HTTPS edge, and the Django process can return the expected
response. It does not become a dependency fan-out or claim that PostgreSQL, Object Storage, worker,
or future product flows are ready.

The Prometheus endpoint is separate from `/health/`. It is reachable only from the host or the
private path used by Unified Agent and is not routed through the public HTTPS edge. A missing or
failed scrape affects telemetry only.

## Metric Contract

### VM metrics

The dashboard shows the following when the standard Unified Agent Linux input provides them:

- CPU utilization and load average;
- total, used, and available RAM;
- swap total and use;
- filesystem total, used, available bytes, and available inodes for the system filesystem;
- disk read/write throughput and I/O pressure;
- network receive/transmit throughput;
- host uptime or boot time; and
- Unified Agent heartbeat/health.

A metric that is unavailable from the standard unprivileged Linux input may be omitted. Its absence
must be recorded during implementation rather than introducing a privileged container, Docker
socket access, or another exporter.

### Application metrics

Django exports only:

- HTTP request count;
- HTTP response count by status class, including a directly queryable 5xx count; and
- request-duration histogram sufficient to graph p50 and p95 latency.

Allowed labels are a fixed environment name, normalized Django route name, HTTP method, and status
class. Raw URL paths, query strings, client IPs, email addresses, session or user identifiers,
event/photo/job identifiers, object-storage keys, tokens, exception text, and other unbounded or
personal values are forbidden.

Unknown and unmatched paths collapse into one bounded route label. Metrics libraries and
instrumentation must not create a label from arbitrary request data.

### Dashboard

One overview dashboard contains:

- external availability, certificate time remaining, and public response latency from the GitHub
  probe metrics;
- VM CPU/load, memory/swap, filesystem capacity/inodes, disk I/O, network I/O, and uptime;
- Django request rate, 5xx rate/count, and p50/p95 latency; and
- current alert states or links to them.

## Alert Contract

Initial thresholds are operational defaults, not capacity claims or SLOs:

| Alert | Initial condition | Source |
| --- | --- | --- |
| Public service unavailable | Two failed or missing five-minute probe datapoints; expected detection within 10–15 minutes | Yandex Monitoring |
| TLS certificate expiring | Less than 14 days of trusted certificate validity remain | Yandex Monitoring |
| VM telemetry missing | No expected host/agent datapoints for 5 minutes | Yandex Monitoring |
| Disk space critical | Less than 10% or less than 5 GiB available for 10 minutes | Yandex Monitoring |
| Memory pressure | Less than 10% memory available for 15 minutes | Yandex Monitoring |
| CPU pressure | More than 90% CPU utilization for 15 minutes | Yandex Monitoring |
| Application 5xx degradation | More than 20% 5xx responses with at least 5 total requests in 5 minutes | Yandex Monitoring |

The public check must require two failed or missing scheduled datapoints rather than alerting on
one failed run. GitHub Actions schedule delay is a known limitation: the initial detection target
is 10–15 minutes, not a five-minute availability guarantee.
Yandex Monitoring rules must treat `No data` explicitly: missing VM metrics triggers only the
telemetry alert and must not be relabelled as a confirmed public outage.

Every rule sends one email when it enters the actionable state and one when it returns to normal.
Repeated evaluation while the state is unchanged must not create repeated email. Network, disk I/O,
swap, and latency remain graph-only until observed baseline data supports an actionable threshold.

Threshold changes after real usage are reversible configuration changes. Any change must retain the
metric meaning, minimum-traffic guard for 5xx, sustained evaluation window, and failure/recovery
notification contract.

## Failure Semantics

- If Django fails while the VM and agent remain alive, the external check reports public failure
  and internal request metrics stop or show 5xx.
- If the VM stops or loses connectivity, the external check reports public failure and Yandex
  Monitoring reports missing VM telemetry.
- If Unified Agent fails while the public service remains healthy, only the missing-telemetry alert
  fires. Monitoring must not claim a public outage.
- If GitHub Actions does not produce fresh probe metrics, the public-check rule treats two missing
  five-minute datapoints as actionable because the operator cannot otherwise distinguish scheduler
  loss from an unobserved outage. The email identifies the condition as missing external
  observation rather than a confirmed application response.
- If Yandex Monitoring is unavailable, the application continues serving traffic. The independent
  external check remains capable of detecting public failure.
- Monitoring never restarts a container or VM, changes traffic, runs migrations, deploys an image,
  or invokes rollback automatically.

## Security and Privacy

- Unified Agent receives only the IAM permissions required to write metrics to the intended Yandex
  Cloud folder. It receives no database, Django, Object Storage, GHCR, or deployment credentials.
- The operator receives only the roles needed to view the dashboard and alerts and to receive the
  configured email.
- The application metrics endpoint is private and has no public Nginx route.
- No monitoring component receives the Docker socket or privileged-container access in this
  increment.
- Agent credentials and the GitHub secret used only to write custom probe metrics are never
  committed to Git, exposed in a dashboard, or written to application logs.
- Metrics and labels follow the bounded allowlist in this specification. They contain no secrets,
  signed URLs, biometric data, media metadata, or user data.

## Operator Runbook Contract

The runbook remains short and covers only first response:

1. Open the external-check result and the Yandex Monitoring dashboard.
2. Classify the signal as public endpoint failure, VM/host telemetry loss, application 5xx
   degradation, resource pressure, or agent-only failure.
3. Check the current public health response, VM power/connectivity state, relevant dashboard
   graphs, and existing deployment/container diagnostics.
4. Use the repository's existing deployment and rollback procedures when they apply; monitoring
   introduces no new recovery command.
5. Confirm that the alert returns to normal and the recovery email arrives.

The runbook must not contain reusable secrets or prescribe destructive recovery.

## Validation

Configuration must be testable without exhausting or stopping the real VM:

- repository tests validate that the metrics route is not present on the public Nginx edge and that
  application labels stay within the allowlist;
- instrumentation tests issue representative named, unknown, successful, and 5xx requests and
  verify counters and latency observations without raw-path or identifier labels;
- monitoring configuration is syntax-checked or API-validated before activation;
- alert queries are evaluated with synthetic metric series for normal, firing, `No data`, and
  recovery states;
- the probe workflow supports a manual controlled target override that writes test-labelled metrics
  so failure and recovery email can be proved without changing the production alert series;
- a controlled agent stop or blocked scrape proves the telemetry `No data` alert without claiming
  public outage; and
- the live dashboard is inspected after activation to confirm fresh VM and application datapoints.

Tests must not fill the disk, consume all memory or CPU, stop the production service, expire the
real certificate, or expose a public metrics endpoint.

## Acceptance Criteria

1. A scheduled GitHub Actions runner outside Yandex Cloud validates trusted HTTPS and the expected
   successful response from `https://findme-photo.ru/health/` every five minutes and writes bounded
   success, duration, and certificate-lifetime metrics to Yandex Monitoring.
2. Two failed or missing scheduled probe datapoints send one operator email within the expected
   10–15 minute window, and restored success sends one recovery email.
3. Certificate validity below 14 days triggers the agreed operator email.
4. Yandex Monitoring receives fresh CPU/load, memory, filesystem, network, uptime, and agent-health
   metrics available from the standard unprivileged Unified Agent Linux input on the current VM.
5. Any agreed VM metric unavailable by that simple method is documented and omitted without adding
   Docker socket access, a privileged monitoring container, or a self-hosted exporter stack.
6. Django exposes private low-cardinality request count, 5xx, and duration metrics sufficient for
   request-rate, 5xx, p50, and p95 graphs.
7. The application metrics endpoint is not reachable through the public HTTPS edge, and no metric
   label contains a raw path, query string, entity identifier, personal data, secret, or storage
   key.
8. One Yandex Monitoring overview dashboard shows the agreed VM, Django, and GitHub-probe graphs.
9. Synthetic data proves the disk, memory, CPU, application-5xx, and telemetry-absence alert
   conditions plus their recovery transitions.
10. Stopping or isolating Unified Agent produces only the missing-telemetry alert while a successful
    external probe remains evidence that the public service is up.
11. Monitoring failure cannot block Django startup, public requests, deployment, or rollback, and
    no monitoring rule performs automated remediation.
12. The operator runbook explains where to look, how to distinguish the monitored failure classes,
    the first safe checks, and how to confirm recovery.

## Alternatives Considered

### Grafana Cloud for all signals

Grafana Cloud can combine Linux integrations, Prometheus application metrics, synthetic HTTPS
checks, dashboards, and email alerts in one interface. It was not selected because the current
project already runs entirely in Yandex Cloud and does not yet need another broad observability
platform, its credentials, quotas, and collection contract. It remains a valid reconsideration if
the project needs one cross-cloud monitoring interface or Yandex Monitoring cannot support the
required application graphs.

### Self-hosted Prometheus and Grafana

This provides full control but adds containers, resource consumption, persistent metric storage,
updates, and backup work to the smallest current VM. It also fails with the VM it is meant to
observe. The option is rejected for this increment.

### External uptime check only

This is the smallest availability signal but cannot explain capacity pressure or application 5xx
degradation while `/health/` still returns successfully. It does not satisfy the approved need for
VM and application graphs.

### Yandex Monitoring without an external check

This minimizes providers but observes the environment from the same cloud and does not prove
public reachability independently. It is rejected because complete VM, edge, DNS, or cloud-path
failure is the most important outage to detect.

## Architecture and ADR Reconciliation

The design conforms to:

- ADR 0003 by keeping the current Docker Compose VM deployment and adding its required monitoring
  follow-up without adopting an orchestrator;
- ADR 0007 and ADR 0011 by monitoring the existing trusted public HTTPS health contract without
  changing edge topology, certificate ownership, or deployment rollback; and
- ADR 0017 by leaving the disabled worker and its accepted processing semantics unchanged until a
  real-environment worker activation brings worker monitoring back into scope.

Selecting Yandex Monitoring and Unified Agent as the durable home for host and application metrics
creates a new operational dependency, IAM boundary, telemetry flow, and retention/cost surface not
governed by an accepted ADR. The implementation plan must therefore begin with a new ADR approving
that choice. The external-check provider remains replaceable when it preserves this specification's
independence, probe, history, alert, and recovery contracts.

This specification does not update `docs/architecture.md` from proposed to implemented. That
current-state change belongs only after the monitoring capability is delivered and validated.
