# Monitoring as code

- Date: 2026-09-28
- Status: Approved design; implementation requested by the maintainer in this task
- Related architecture: [Operations](../../architecture.md)
- Related ADRs: [0018](../../adr/0018-use-managed-yandex-monitoring.md), [0039](../../adr/0039-run-public-probe-on-image-origin-vm.md)
- ADR impact: Conforms to ADR 0018 and ADR 0039. The managed Yandex metric store,
  evaluator, email delivery, private collection and probe placement remain unchanged.

## Outcome and scope

Store operational alert rules, notification routing, dashboard and agent configuration in Git.
Validate changes before applying an exact revision through supported Yandex APIs. Use Managed
Prometheus within the existing Yandex account/folder, without a self-hosted database.
The existing dashboard is `fbeketud0mdaupj43of6`; the existing email channel ID is
`fbefs2ubu6sq0k0jvlch`, with confirmed name `findme-photo-operator-email`.
The user supplied Prometheus workspace ID `mon0c97qv2s5uju1ark8`.
Workspace creation and channel provisioning remain one-time foundation operations because their
public create APIs have not been confirmed. Missing workspace/name must block activation clearly.

Preserve the baseline public, TLS, telemetry, disk, memory, CPU, HTTP and Commerce signals.
Native worker-pool publishing belongs to the parallel autoscaling work and must remain untouched:
its observations are a control dependency, not an observation-only migration target.

## Data flow

One existing Unified Agent per host delivers through the documented `metrics` Remote Write output
using VM metadata IAM. The canonical host scrapes private HTTP, Linux and Commerce observations;
the image-origin host scrapes public HTTPS observations every five minutes.
Small loopback-only host exporters reuse the existing probe and Commerce observation functions.
Each scrape obtains a fresh bounded observation; no cached healthy values are served after a
collection failure. Public failed HTTPS attempts export success=0; inability to observe Commerce
exports no Commerce samples and returns an error. No customer identifiers or secrets are exported.
The standard library exporter exposes only fixed metrics paths and no file browsing or writes.

During the bounded migration, native timers/agent routes continue to provide the existing alerts.
The additional Prometheus routes are additive and use a separate buffer. After live parity and
notification proof, remove the obsolete native observation routes/timers in the reviewed cutover.
There is no automatic fallback between transports. Application/worker state is unchanged.

## Declarative management

Use the official Yandex Python SDK for DashboardService Get/Update over gRPC. Its generated
multi-source chart schema supports Prometheus targets; the current Terraform dashboard schema
does not expose this contract. Keep the SDK in isolated operational tooling requirements.
Update the existing dashboard with its fresh etag and verify folder identity. Export a pre-update
snapshot; wait for completion and read back the managed fields. Preserve fields outside the owned
dashboard specification. Do not invent REST endpoints from protobuf annotations.

Use documented REST PUT/GET for one project-owned rules file and PUT for Alertmanager. Never
delete unrelated rules. GET Alertmanager is unconfirmed: report routing drift as unverified,
and restore routing by reapplying a known Git revision, without claiming a server-side backup.
Apply routing before rules; require fresh expected samples and valid finite expression results
before enabling operational alert rules. Snapshot calculation errors are failures, not health.
Separate read-only check, offline validation, rendering and explicit apply commands.

GitHub Actions validates on pull requests. Live apply is manual, main-only, serialized and bound
to an exact source revision. Use short-lived GitHub OIDC identity; do not add a long-lived key.
Configure the protected monitoring environment and required IAM separately before activation.

## Rules and missing data

Keep `max_over_time(success[10m]) < 0.5` for public availability and
`max_over_time(alive[5m]) < 0.5` for Commerce liveness. Keep maximum ready age over five minutes
above 300 seconds. Separate missing observations with `absent_over_time`, which does not prove
the observed service is unavailable. Resource-pressure rules must not fire on missing series.
Disk rules cover both 10% and 5 GiB; memory covers 10%; CPU covers 90%; HTTP covers 20% 5xx with
at least five requests in five minutes. Preserve the documented aggregation windows explicitly.
The isolated loopback-output inspection of canonical Unified Agent 26.09.10 confirms
`sys_system_UsefulTime` and `sys_system_IdleTime` are counters. Use their rates; the aggregate
series has no `cpu` label. Memory and filesystem metrics become `sys_memory_MemAvailable`,
`sys_memory_MemTotal`, `sys_filesystem_FreeB`, `sys_filesystem_SizeB` gauges; the root filesystem
has `mountpoint="/"`. Linux observations carry `instance="dev-photo-prjct"`.
These names and labels must also be checked in the actual workspace before rule apply;
an unsupported mapping blocks activation rather than treating no data as healthy. This local
agent-output inspection proves serialization, not cloud ingestion or rule evaluation.
Alert annotations use only Yandex-supported `$labels` and `$value` interpolation.

## Acceptance and operational boundary

- Offline configuration/schema validation and Prometheus rule tests cover sustained failure,
  recovery, missing data, counter reset and insufficient HTTP traffic.
- Fake transport tests prove correct API methods, no unrelated deletion, folder/etag guards,
  read-back failures and safe error output without credentials.
- Host exporters are loopback-only, collect freshly and cannot turn observation errors into zero.
- Reviewed bounded installation has backup/rollback and never restarts application containers.
- Before cutover, actual workspace names, timestamps, rule calculation and firing/recovery emails
  must be observed. Repository verification alone is not activation evidence.
- Cloud pricing, IAM changes and service restarts require the fresh explicit operational approval
  specified by the project cloud skill. This implementation request authorizes the reviewable
  repository package; no chargeable cloud action has been approved by it.

## Sources

- [Agent configuration](https://yandex.cloud/en/docs/monitoring/operations/prometheus/ingestion/prometheus-agent)
- [Rules and routing](https://yandex.cloud/en/docs/monitoring/operations/prometheus/alerting-rules)
- [Dashboard gRPC](https://yandex.cloud/en/docs/monitoring/operations/dashboard/api-examples)
- [Multi-source schema](https://github.com/yandex-cloud/cloudapi/blob/master/yandex/cloud/monitoring/v3/multi_source_chart_widget.proto)
