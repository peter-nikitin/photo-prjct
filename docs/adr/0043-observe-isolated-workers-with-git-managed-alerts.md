# 0043: Observe isolated workers with Git-managed alerts

- Status: Proposed
- Date: 2026-09-28
- Deciders: project maintainer
- Supersedes: [ADR 0018](0018-use-managed-yandex-monitoring.md) only for isolated
  photo-worker host/container observation and the evaluator/application mechanism of
  their new diagnostic alerts, upon acceptance
- Superseded by: none

## Context

ADR 0042 isolates bulk and selfie workers but its authoritative queue and capacity metrics
do not explain individual resource pressure, runtime failure or missing observations.
ADR 0018 excludes invasive Docker monitoring and uses native Monitoring alert evaluation.
The approved telemetry specification selects bounded host-side container observation and
requires Git-reviewed alerts without manual cloud UI configuration.

The maintainer approved automated Managed Prometheus API application instead of Terraform
for rules and routing after checking the Yandex provider's published resource surface.
That approves the application mechanism, not acceptance of this previously unwritten ADR.
Workspace/channel lifecycle and configuration read-back must still be verified; undocumented
API methods must not be inferred. Existing native metrics are not assumed to be queryable
in a Prometheus workspace.

## Decision drivers

- Explain worker OOM/restart, resource pressure and telemetry loss without routine SSH.
- Keep privileged host access and cloud metric credentials out of worker containers.
- Review, reproduce and roll back alert configuration from Git without UI edits.
- Preserve processing authority, queue-driven autoscaling and active monitoring controls.
- Retain managed metric storage without operating a new monitoring stack.

## Considered options

1. Retain aggregate-only observations and manually configured native alerts.
2. Give each worker host Monitoring write authority and deploy a general container exporter.
3. Relay bounded host/runtime observations through the canonical backend, with diagnostic
   Managed Prometheus rules and routing applied from Git through the documented API.

## Decision

Propose option 3, within the approved specification's metric and transport contract.

A host-owned probe may read Linux resources and fixed, allowlisted state/resource/lifecycle
fields for exactly `findme-photo-worker` through bounded read-only Docker commands. It runs
independently of the worker container. This exception permits no general inspection service,
full inspect output, environment, journals, arbitrary containers or host-control commands.
No Docker socket is mounted in a worker or monitoring container.

The worker exports in-memory aggregates through host loopback. The host sends bounded snapshots
over the existing private authenticated TLS fleet interface; the canonical backend remains the
sole cloud metric writer for these observations. The worker and host probe receive no Monitoring
credentials. Snapshots are diagnostic, boot/process fenced and freshness-aware; they never change
jobs, attempts, leases, admission, readiness, retirement or release promotion.

Version new worker diagnostic alert rules, thresholds, windows and notification routing in Git.
Apply YAML/PromQL rules and Alertmanager configuration through documented Managed Prometheus APIs,
not UI edits or an imperative Terraform wrapper presented as managed resources. The workspace
must receive the required observations through the canonical collection boundary. Keep cloud
credentials outside tracked files. Establish configuration ownership, supported verification,
drift detection and reviewed-version rollback before live application; do not replace shared
Alertmanager configuration blindly or assume undocumented read-back operations exist.

Leave ADR 0042's authoritative native queue/capacity publishing and autoscaling evaluator intact.
Do not migrate HTTP, public-probe or Commerce alerting in this worker change. Existing active
alerts remain until replacement ingestion, firing, missing-data, notifications and recovery are
proven; retire replaced rules afterward without steady-state duplicate notifications.

This narrowly supersedes ADR 0018 for worker host observation and new diagnostic alerting only.
Its managed storage, private bounded telemetry, observation-only behavior and public monitoring
remain governing constraints. ADRs 0017, 0028 and 0042 remain authoritative. No self-hosted
Prometheus/Grafana/Alertmanager, new processor, pgvector change, backfill, VM downsizing,
automatic remediation, paid provisioning, IAM change or live activation is authorized.

## Consequences

### Positive

- Per-node resource and runtime failures become distinguishable from telemetry loss.
- Diagnostic alert configuration is reviewable and reproducible without cloud UI edits.
- Existing credential and processing/scaling authority boundaries remain intact.

### Negative

- Host Docker reads add a narrowly privileged operational package requiring review.
- Diagnostic delivery to a workspace adds ingestion, IAM, cost and naming validation work.
- The API application path needs explicit ownership and verification rather than Terraform state.
- OOM/events/counters remain best-effort observations, not an exact durable audit.

### Follow-up

- Obtain explicit acceptance of this ADR before writing the implementation plan.
- Resolve supported workspace/channel lifecycle and Alertmanager read-back/rollback without
  manual configuration; return unsupported requirements to the maintainer, not to UI setup.
- Coordinate current-main/migration integration after the neighboring pgvector delivery.

## Validation and rollback

Require private scrape/TLS ingestion, source fencing/freshness, missing/crashed-container fixtures,
bounded labels and privacy rejection, preserved job/lease/queue behavior, validated Git definitions,
and verified automated apply/rollback. Local tests do not prove cloud delivery or notifications.
Live activation separately requires approved resources/cost and fresh workspace samples plus
controlled firing, missing-data and recovery evidence.

Rollback disables diagnostic probes/delivery and restores the reviewed owned alert configuration
and compatible canonical release. Preserve product and vector state and existing active controls.
Reconsider if the bounded host probe cannot stay within its read-only contract or managed API
application cannot provide reproducible configuration and safe verification/rollback.

## References

- [Worker telemetry specification](../superpowers/specs/2026-09-28-worker-pool-telemetry-design.md)
- [Architecture accepted constraints](../architecture.md#accepted-constraints)
- [ADR 0017](0017-use-django-polled-photo-processing-jobs.md)
- [ADR 0018](0018-use-managed-yandex-monitoring.md)
- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [ADR 0042](0042-isolate-autoscaled-photo-worker-pools.md)
- [Managed Prometheus alerting API](https://yandex.cloud/en/docs/monium/operations/prometheus/alerting-rules)
- [Yandex Terraform provider](https://github.com/yandex-cloud/terraform-provider-yandex)
