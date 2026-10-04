# Actionable observability and independent reconciliation

- Date: 2026-10-04
- Status: Approved by the project maintainer in conversation
- Related architecture: [Observability and release topology](../../architecture.md)
- Related ADRs: [0052](../../adr/0052-notify-only-on-actionable-service-degradation.md), [0053](../../adr/0053-reconcile-observability-independently-on-main.md), [0018](../../adr/0018-use-managed-yandex-monitoring.md), [0048](../../adr/0048-reuse-managed-prometheus-for-worker-alerts.md)
- ADR impact: Conforms to ADR 0052 and ADR 0053

## Outcome

An operator notification means a measured, sustained loss of customer or processing service, or imminent actionable loss of capacity. Git is the operational source of truth for existing observability resources: a merge to `main` reconciles affected monitoring components without deploying unrelated application components or waiting for a routine manual action. One dashboard presents each system part as a distinct collapsible section with service impact before diagnosis.

## Alert policy

The public probe reports observed failed HTTPS checks as service impact. Missing observations, collector freshness, native publisher freshness, VM diagnostics, and other telemetry gaps remain explicit diagnostic states, with no operator notification by themselves. Customer-serving Django 5xx are evaluated independently from internal worker-control requests. Worker impact requires fresh queue and capacity evidence plus sustained overdue work. A full worker pool alone is diagnostic. Commerce backlog, image-delivery failures, and critically low root disks remain actionable when measured and sustained. Host CPU/memory, TLS expiry, auth rejections, isolated imgproxy errors, and other early warnings remain visible without paging until paired with demonstrated service impact. Correlated conditions share an incident group; diagnostic rules cannot fall through to the operator route. Both channels receive firing and recovery for the same actionable rules.

For every rule, missing source data evaluates as unknown rather than healthy zero. Diagnostics require persistence before entering firing state. Exact duplicate worker diagnostic alerts are removed. The rule package includes fixtures for sustained degradation, short source gaps, recovery, and internal versus customer routes. Candidate rules are compared with the five-day audit before activation. Native UI alerts are inventoried separately; duplicates are retired only after replacement delivery is proved.

## Dashboard contract

The existing dashboard stays a single Git-owned Monium dashboard. It contains eight collapsible groups, with these subjects and ordering:

| Group | Service-impact view first | Diagnostic view afterward |
| --- | --- | --- |
| Public site | HTTPS success, request duration | TLS lifetime |
| Django/API | customer request rate and 5xx, latency | internal-control errors |
| Canonical VM | root-disk headroom, application availability | CPU, memory, swap, load, uptime, filesystem, disk/network I/O, agent backlog |
| Commerce | ready-work age | worker liveness and observation freshness |
| Photo processing/workers | oldest claimable work, ready work and throughput | pool capacity, utilization, operation duration histogram, source freshness and node diagnostics |
| CDN | 5xx and origin pressure | edge request volume and cache HIT/MISS |
| Image origin + VM | origin 5xx and 429 shares, root-disk headroom | response volume, CPU, memory, load, agent backlog |
| imgproxy | processing errors and p95 | request volume, latency distribution and duration histogram |

Related series with the same unit may share a chart, such as total and 5xx rates or p50 and p95. Distinct units retain separate axes or charts. The existing worker and imgproxy duration histograms remain visible. Titles identify the system, measure, and unit; duplicate-looking charts have a distinct operational question. Freshness and missing-data views never display absence as a healthy zero. New graphs use already collected metrics. PostgreSQL internals and Commerce queue count are explicitly out of scope until collected and verified; empty speculative graphs are not added.

## Reconciliation contract

An observability-only merge to `main` runs the affected cloud and host reconciliation at that exact SHA, independently of Django, photo-worker, and documentation releases. A combined merge may run multiple independent paths at the same SHA. Pull requests validate offline and cannot mutate production. Main reconciliation serializes writes; validates fresh required inputs; saves recoverable backups; applies the owned resource; reads back supported state; and records SHA and outcome. Missing source data, unsupported API behavior, or a failed read-back fails visibly. Existing manual actions remain recovery tools only. The existing main-only OIDC identity and least-privilege scope are retained. The GitHub `monitoring` environment must have no routine reviewer gate while retaining main-only deployment policy.

The cloud package owns rules, Alertmanager routing and dashboard. Host packages cover the existing canonical agent/exporter, public probe, Commerce monitor, image-origin agent/proxy metrics, and privileged selfie observability. Host updates use the existing bounded identity and backup checks. A monitoring-only commit must not build or deploy Django or photo-worker images. A docs-only commit must not contact cloud or runtime. This scope creates no new billable resource or notification recipient.

## Acceptance

1. Offline validation accepts the grouped dashboard and every nested query; live read-back matches the source after normalization of server defaults.
2. Alert fixtures and the five-day audit show no notifications caused solely by short observation gaps, internal-control 5xx, or busy pools without overdue work, while measured public, customer, processing, disk and image-delivery degradation still notify.
3. A monitoring-only `main` merge automatically reconciles affected resources at the merge SHA and runs no Django or worker build/deploy; docs-only and unrelated changes do not reconcile monitoring.
4. A failed preflight or apply is visible as a failed observability run, with a retained backup and a documented prior-revision recovery path.
5. Live acceptance requires fresh samples, matching Git-owned rules/dashboard, and separately confirmed email and Telegram firing and recovery. UI alert deletion follows only after this evidence.
