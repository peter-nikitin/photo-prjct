# Isolated worker telemetry through the canonical backend

- Date: 2026-09-28
- Status: Written specification approved in conversation on 2026-09-28; implementation and
  the required host-probe ADR remain pending. Publication in the current PR is authorized;
  paid provisioning and live activation are not authorized.
- Owner: FindMe Photo.
- Related architecture: [Accepted constraints](../../architecture.md#accepted-constraints),
  [Operations](../../architecture.md#target-mvp-architecture--proposed),
  [open decisions](../../architecture.md#open-decisions).
- Related ADRs: [0018](../../adr/0018-use-managed-yandex-monitoring.md),
  [0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [0028](../../adr/0028-operate-one-canonical-deployment.md),
  [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md),
  proposed [0043](../../adr/0043-observe-isolated-workers-with-git-managed-alerts.md).
- Related specification: [Autoscaled pools](2026-09-23-autoscaled-photo-worker-pools-design.md).
- ADR impact: Requires a new ADR narrowly superseding ADR 0018's prohibition on Docker
  inspection for monitoring: a fixed-command, host-owned read-only worker probe only.
  Git-managed alerting and any Managed Prometheus ingestion/evaluator transition require
  reconciliation with ADR 0018 before implementation; this specification does not accept
  that transition or claim Terraform resource support is already established.
  Conforms to ADR 0042's private transport, credential and scaling boundaries. No accepted
  ADR is amended or implicitly accepted by approving this specification.

## Intent and scope

Operators must distinguish an overloaded worker, a cold or stale process, an OOM/restart,
an execution failure and missing telemetry without SSH being the normal observation path.
Cover both isolated bulk and selfie workers, including staged workers and failed startup.

The existing isolation package publishes authoritative pool demand and trusted cloud capacity.
It does not export individual worker resources, runtime durations or readiness as Monitoring
series. This addition closes those gaps without changing processing, recognition, scaling
limits or the durable ownership protocol. It is not a benchmark or sizing workstream.

Exclude pgvector schema, reader, gate and release; historical backfill; legacy deletion;
database relocation or internal database metrics; import/commerce; main-VM downsizing;
logs/traces export; automatic remediation; paid provisioning and live activation.
The deployed pgvector baseline `866a894` and enabled reader gate (2026-09-28 inventory)
must be preserved; telemetry preparation neither reverts it nor races canonical Deploy.

## Selected approach and alternatives

Use the canonical backend as the sole Monitoring writer for this worker telemetry.

```text
worker runtime aggregates -- loopback scrape --\
                                               host probe -- private TLS --> backend
host Linux + named-container observations -----/                              |
existing coordinator readiness + cloud membership ----------------------------|
                                                                              v
                                                     canonical publisher --> Monitoring
```

The worker exposes a small Prometheus-format runtime endpoint only through a host-loopback
binding. A separate host-owned probe combines its allowlisted samples with host resources
and safe state of the exact `findme-photo-worker` container, then submits a bounded snapshot
through the existing private authenticated fleet API. The worker is unprivileged and receives
neither the Docker socket nor host-control, database, media or Monitoring credentials.
Use established metrics/resource libraries; do not build a generic exporter framework.

The host probe runs independently of the worker process so it can report a missing/crashed
container. It has narrowly defined host-side read access, not a privileged monitoring container.
Read only named-container ID/state, restart count, OOM/exit state, resource observations and
bounded lifecycle events. Never collect full inspect output, environment, arbitrary containers,
command lines, payloads or journals. The probe has no stop/restart/retirement operation.

Rejected alternatives:

- Giving each worker VM Monitoring write authority changes the existing narrow service-account
  contract and exposes folder metric-writing credentials to the worker host. Do not add it.
- Central queue-only monitoring cannot diagnose individual RAM pressure, OOM or process loss.
- A new cAdvisor/Prometheus/Grafana stack or Docker socket mounted into a container adds an
  unnecessary privileged service and is outside this increment.

## Observation and transport contract

Host collection targets a 30-second interval. Each attempt is bounded; collection, scrape and
submission failures cannot block the worker, attempt renewal, callbacks, warming or drain.
Keep at most the latest pending snapshot per source, not a growing retry queue. No product data
is cached by the probe. The worker endpoint serves in-memory aggregates without inspecting jobs.

The receiver uses the existing private TLS listener, certificate/hostname validation and distinct
fleet authentication. Public and local-worker routes cannot submit remote telemetry. No redirects,
plaintext fallback or new inbound worker-VM security-group rule is permitted. Telemetry JSON has
a separate 16 KiB limit, enforced before parsing, with an exact field/metric allowlist; unknown
fields, non-finite numbers, invalid identities and oversized bodies are rejected without logging
the body. It does not reuse or expand the processing-result payload contract.

An envelope identifies pool, physical instance, kernel boot, deployed build and monotonically
ordered snapshot sequence within a collector epoch. Runtime samples also identify their process
registration generation internally. These are operational protocol identities, not product IDs.
Validate membership/build against the configured trusted cloud observation; known boot or
generation mismatches cannot overwrite current data. A newly observed VM may report host data
before its worker registers, but this never creates or readies a coordinator member.

Persist only the latest validated diagnostic snapshot per current instance/source in separate
coordination-owned observation state. Duplicate snapshots are idempotent; delayed older snapshots
cannot refresh freshness, regress counters or replace a newer boot/process. Record server receipt
time and source sample time separately. Diagnostic state does not rewrite jobs, attempts, vectors,
grants, member heartbeat, admission, readiness or release state.

Observation freshness is 90 seconds. Delayed replay or clock skew cannot renew freshness merely
because the backend received a request. Boot/process changes establish a new counter baseline;
they must not produce negative rates or attribute old work to a replacement process. Old instance
observations are no longer exported once complete trusted membership shows they are gone.

Backend or Monitoring failure produces missing/stale diagnostic data, never job failure. Telemetry
is self-reported diagnostic evidence, not authority for claim admission, recovery, retirement,
release promotion or cloud mutations. Existing authoritative queue publishing remains independent:
a telemetry ingestion/publication error must not suppress demand or wake-up from bulk zero.

## Metric contract

Export these bounded signal families; keep source freshness visible beside every resource graph.

| Layer | Required signals | Authority and meaning |
| --- | --- | --- |
| Host | CPU utilization, available/total RAM, available/total root-filesystem space | Sampled Linux resource data, not worker-container usage |
| Container | Present/running, CPU and memory usage versus existing limits, restart count, OOM/exit observation | Exact named container; missing values are unknown, not zero |
| Runtime | Busy, execution outcomes, execution-duration histogram, runtime scrape freshness | Worker execution observations, not proof of accepted/published product results |
| Coordinator | Ready, serving, draining, heartbeat age/freshness | Derived from existing canonical admission/generation/readiness predicates |
| Delivery | Host and runtime observation freshness/age, collection failure or missing-source state | Separates resource/process failure from observation loss |

Host/node series have only `pool`, `instance_id` and `zone_id` dimensions. Runtime counters and
durations may additionally use the fixed enabled processor-kind enum and bounded outcome enum.
No raw build, boot, process, container, model, exception or user-provided values become labels.
Full release identities remain available through existing status diagnostics instead.

An execution runs from receipt of an admitted job until the runner leaves its handling path,
including download, inference and bounded result-delivery work. Measure elapsed time monotonically;
count one terminal runtime observation per handled attempt, not per callback retry. Distinguish
successful callback delivery from execution/transport failure; neither is called accepted search
success. Use fixed duration buckets: 1, 5, 15, 60, 300, 900, 1800 seconds and infinity.
Counters/histograms reset with the process; gaps and resets are explicit, not fabricated continuity.
OOM-killed processes may lose their final runtime sample; the host signal covers that failure.

Container restart counters reset on recreation. OOM/lifecycle event evidence is bounded and
best-effort, not an exact durable audit: a Docker event-buffer gap or probe outage must not imply
zero incidents. Document the observation window and reset/coverage limits. Do not add an event-log
database to make stronger guarantees. Confirm execution outcomes against existing durable job
status when diagnosing a customer failure.

No photo/person/event/job/attempt ID, vector, object key, signed URL, token, path, exception text,
customer data or biometric content appears in metrics. Allowlisted identities and fixed bucket
counts bound samples per snapshot; exporter and receiver reject arbitrary label expansion.
Ephemeral instance labels still create historical series as VMs churn: the activation estimate
must include that cardinality and retention, not just the four concurrent-VM ceiling.

## Dashboard, alerts and failure semantics

Extend the existing worker dashboard with per-node resource/container views, ready/serving and
heartbeat freshness, execution rate/duration/failures and telemetry freshness. Pool-level queue,
capacity and saturation graphs retain their authoritative sources and existing semantics.

Alert definitions, thresholds, evaluation windows, missing-data rules and notification routing
are versioned in Git and changed through reviewed pull requests. Creating or editing these
objects in the cloud UI is not the delivery mechanism; the UI may be used for observation only.
Application must be reproducible and automated, with verification of deployed configuration
and a rollback to a reviewed version. Credentials remain outside tracked definitions.

Terraform was the initial target application mechanism. After checking the published Yandex
provider resource surface, the maintainer explicitly approved Managed Prometheus API application
for rules and routing on 2026-09-28. Do not substitute an imperative Terraform provisioner for
managed-resource semantics. Supported workspace/channel lifecycle, configuration read-back,
ownership, drift detection and rollback remain implementation prerequisites; unsupported API
operations must not be guessed, and manual UI setup is not an acceptable fallback. Proposed
ADR 0043 records the architectural reconciliation and still requires explicit acceptance.

Managed Prometheus YAML/PromQL rules and Alertmanager configuration are the documented API path,
not proof that existing native Monitoring points are queryable in its workspace. If that path
is selected, worker observations must be delivered through the canonical collection boundary
into the target workspace, and actual metric names/labels must be verified there. Worker VMs
still receive no Monitoring credentials. Do not move authoritative autoscaling demand/capacity
series or change their evaluator as an incidental consequence of the diagnostic alerting work.

Prepare observation-only alerts for a required running member with stale host/runtime data;
missing/unready worker after the existing startup grace; sustained host/container memory or disk
pressure; observed OOM/restart failures; and repeated execution failures with actual work.
Duration is graph-only initially: no latency SLA or universal processor timeout is inferred.
An idle pool has no missing-VM alarm when its fresh trusted membership contains no expected VM.
Planned staged warming/draining and a known retirement are not unexpected service-loss alarms.
Do not hide resource/collection failures for a staged VM that is still expected to exist.

Missing telemetry takes precedence over interpreting historical resource values as current.
Loss of the whole VM cannot be reported by its probe: compare trusted cloud membership, required
pool floor, existing queue age and last observations. Selected managed alert evaluation, no-data alignment
and notification/recovery require later live proof; offline tests do not establish them.
Runbook responses inspect status and preserved ownership first; no automatic restart, lease expiry,
capacity increase, second selfie claim slot or model activation is authorized by an alert.

Preserve existing time-window semantics rather than replacing aggregation windows mechanically
with PromQL `for`. Missing samples, idle zero and evaluator errors are distinct states. Existing
active alerts remain in place until target ingestion, intended firing/missing-data behavior,
notification delivery and recovery are proven on explicitly approved resources; then retire the
replaced rules without leaving duplicate notifications as the steady state. This worker increment
does not authorize migration of unrelated HTTP, public-probe or Commerce alerts.

## Release and acceptance boundaries

This is one canonical web/worker release, not another deployment pipeline. Publish the compatible
private receiver before enabling probes. Old workers without runtime telemetry remain process-
compatible during bounded drain; mark telemetry unavailable, never deny their existing callbacks
because an optional diagnostic field is absent. Rollback disables diagnostic collection and restores
the compatible release without deleting processing/vector state or changing fleet authority.

Coordinate migration identity and current-main integration only after the neighboring pgvector
deployment completes. Any additive diagnostic schema belongs solely to coordination; never merge
an old application image over the newly deployed pgvector contract. Schema and upgrade details
require the implementation plan's release safeguards, not speculative migration numbering here.

Acceptance requires real loopback scrape and private TLS ingestion, current/duplicate/stale/boot-
reset observations, actual container restart/OOM fixtures, histogram/outcome correctness, privacy
allowlist and body rejection, cold/idle/draining members, missing telemetry and backend/Monitoring
failure while jobs and leases continue. Existing durable results and authoritative queue metrics
must be unchanged. No benchmark or production failure injection is part of repository acceptance.

Repository acceptance also requires validated versioned alert/routing definitions and automated
application verification/rollback for the approved mechanism. Git files or a successful Terraform
apply alone do not prove cloud ingestion, rule evaluation or notification delivery. Full live
acceptance requires fresh target-series queries and controlled firing, missing-data and recovery
evidence; no manual UI configuration is required to reproduce the delivered worker alerts.

Local/CI preparation is separate from paid/live activation. Before activation, approve the added
custom-metric cardinality/cost, resolve the host-probe ADR and verify actual fresh worker points,
source attribution and controlled alert/recovery on explicitly approved resources. No provisioning,
IAM/network mutation, production deploy, branch rewrite, pgvector change or backfill is authorized
by this document. Current implementation/architecture status remains unchanged until delivery.
