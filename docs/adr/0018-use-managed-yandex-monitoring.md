# 0018: Use managed Yandex Monitoring with independent public probes

- Status: Proposed
- Date: 2026-07-30
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

The current public FindMe Photo environment runs Django, PostgreSQL, Nginx, and Certbot through
Docker Compose on one small preemptible Yandex Cloud VM. Deployment verifies public HTTPS health,
but no independent recurring check or host and application dashboard detects degradation after a
successful deployment.

The first monitoring increment needs public availability, basic VM resource graphs, basic Django
HTTP graphs, and email alerts. Running a metrics database and dashboard on the observed VM would
consume its limited resources and disappear during the same VM failure that monitoring must report.
A durable choice is required for metric storage, collection, alert evaluation, IAM, and the
boundary between cloud-internal telemetry and independent public observation.

## Decision drivers

- Detect full public-path or VM failure independently from the observed VM.
- Provide basic host and application graphs without operating a monitoring database.
- Reuse the current Yandex Cloud boundary and standard unprivileged VM collection where practical.
- Keep monitoring failure from blocking application startup, requests, deployment, or rollback.
- Avoid privileged containers, public metrics endpoints, and Docker socket exposure.
- Keep the first operational surface and recurring cost proportionate to one small VM.

## Considered options

1. Use Yandex Monitoring and Unified Agent for host and application metrics, plus a managed public
   checker outside Yandex Cloud.
2. Use Grafana Cloud for Linux, application, and synthetic monitoring in one external platform.
3. Run Prometheus, Grafana, exporters, and alerting on the current VM.
4. Use only an external HTTPS checker.

## Decision

Use Yandex Monitoring as the managed store, dashboard, and alert evaluator for metrics from the
current Yandex Cloud VM and Django application. Run one Yandex Unified Agent on the VM to collect
standard unprivileged Linux metrics and scrape a private Prometheus-format application endpoint.

Keep application metrics low-cardinality and operational. The initial contract contains HTTP
request count, response status class including 5xx, and request duration grouped only by bounded
environment, normalized route, method, and status-class labels. The metrics endpoint remains
private and is not routed through the public HTTPS edge.

Use a managed checker whose probes run outside Yandex Cloud to validate the canonical public HTTPS
health endpoint, normal TLS trust, certificate lifetime, and response latency. The exact checker
provider remains replaceable if it preserves independent probes, history, sustained-failure and
certificate alerts, and failure/recovery email.

Monitoring is observation-only. It does not restart services, mutate the VM, deploy an image, run
migrations, change traffic, or invoke rollback. Application and deployment paths do not depend on
successful metric collection or delivery.

Do not add a self-hosted monitoring database, privileged monitoring container, Docker socket
access, centralized logging, tracing, business metrics, PostgreSQL internals, or disabled-worker
metrics in the first increment. A standard host metric unavailable through simple unprivileged
Unified Agent collection is omitted and recorded rather than recovered through more invasive
collection.

## Consequences

### Positive

- Host and application time series survive loss of the observed VM.
- Public availability is checked independently from Yandex Cloud and can expose a complete
  VM, edge, DNS, or cloud-path outage.
- The current VM runs one collection agent instead of a metrics storage and dashboard stack.
- Yandex Monitoring supplies existing IAM, dashboards, alert evaluation, and email channels.
- The private, bounded application metric contract limits data exposure and cardinality cost.
- The external checker can be replaced without changing Django instrumentation or VM collection.

### Negative

- Operators use Yandex Monitoring plus a linked external-check history rather than one fully unified
  interface.
- The system depends on Yandex Monitoring availability, IAM, quotas, retention, and pricing for
  internal telemetry.
- Unified Agent installation and credentials add host configuration outside the application image.
- A separate managed checker adds another account, notification path, and operational dependency.
- Missing internal telemetry cannot by itself distinguish VM loss from agent or metric-delivery
  failure; the independent public check supplies the necessary second signal.

### Follow-up

- Implement and validate the minimal monitoring specification before changing the Operations module
  from proposed to implemented.
- Record which standard VM metrics are actually available on the current VM without privileged
  collection.
- Tune thresholds only after retained graphs provide a representative baseline.
- Add worker and queue signals when a worker is activated in a real environment.
- Reconsider one cross-platform monitoring service when multiple clouds or environments make the
  split operator view materially costly.

## Validation and rollback

Validate the decision by observing fresh standard VM and bounded Django HTTP metrics in Yandex
Monitoring, viewing the agreed dashboard, and proving synthetic normal, firing, missing-data, and
recovery alert transitions. Prove that a checker outside Yandex Cloud detects a controlled HTTPS
failure and certificate lifetime and sends one failure plus one recovery email. Confirm that
stopping Unified Agent does not affect public health and produces only the missing-telemetry
signal.

Rollback removes or disables Unified Agent, private application instrumentation, Yandex Monitoring
resources, and the external check without changing product state or the existing public health and
deployment contracts. Reconsider this decision if Unified Agent cannot provide the required basic
signals without privileged access, Yandex Monitoring cannot express the agreed dashboard or
alerts, recurring cost becomes disproportionate, or the split external/internal view delays
incident response.

## References

- [Current architecture](../architecture.md#current-architecture--implemented)
- [Operations module](../architecture.md#target-mvp-architecture--proposed)
- [Architecture open decisions](../architecture.md#open-decisions)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0007](0007-nginx-certbot-https-edge.md)
- [ADR 0011](0011-use-minimal-shared-https-rollout.md)
- [Minimal service monitoring design](../superpowers/specs/2026-07-30-minimal-service-monitoring-design.md)
