# PostgreSQL observability implementation

- Date: 2026-10-04
- Status: Approved scope; repository implementation in progress
- Owner: project maintainer
- Related specification: [PostgreSQL observability](../superpowers/specs/2026-10-04-postgresql-observability-design.md)
- Related architecture: [Current deployment](../architecture.md#current-architecture--implemented), [Search](../architecture.md#search)
- Related ADRs: [0002](../adr/0002-postgresql-system-of-record.md), [0018](../adr/0018-use-managed-yandex-monitoring.md), [0052](../adr/0052-notify-only-on-actionable-service-degradation.md), [0053](../adr/0053-reconcile-observability-independently-on-main.md)
- ADR impact: Conforms to the listed ADRs. PostgreSQL internals extend the first monitoring increment without altering its managed store or private collection boundary.

## Goal and scope

Implement the [specification](../superpowers/specs/2026-10-04-postgresql-observability-design.md#outcome-and-boundary) in the existing canonical deployment and Git-owned observability package. No worker contract, durable processing row or generated product artifact changes, so the worker/state/artifact release safeguard gate does not apply.

## Acceptance criteria

The [specification acceptance checks](../superpowers/specs/2026-10-04-postgresql-observability-design.md#acceptance-and-failure-semantics) apply. Repository completion additionally requires reproducible exporter configuration, bounded/private SQL observation, a distinct PostgreSQL dashboard group, diagnostic versus actionable routing tests, and a reviewed exact-revision rollout path. Live collection and notification proof are separate gates.

## Implementation

Use `$execute-implementation-plan` for the task and review loops.

### Task 1: Private PostgreSQL producers

**Files:** `docker-compose.deployment.yml`, `src/backend/config/metrics.py`, `src/backend/config/views.py`, focused backend tests, deployment/monitoring contract tests, and exact deployment secret/preflight helpers as required.

- **Specification:** Selected observation design; acceptance and failure semantics.
- **Depends on:** None.
- **Produces:** Fresh `findme_db_usable` and bounded PostgreSQL exporter metrics, with a dedicated least-privilege monitoring role and no public listener.

- [ ] First add failing tests for success/failure/missing SQL checks, privacy/cardinality, exporter configuration and monitoring role preparation.
- [ ] Implement with a pinned exporter version/digest, private listener, fixed collector set, and bounded credential handling. Ensure exporter failure never blocks application startup or a deployment rollback.
- [ ] Run focused tests; record exact commands, exits and results.

### Task 2: Agent, graphs and rules

**Files:** `deploy/monitoring/prometheus/render_agent.py`, `environment.json`, `dashboard.json`, `rules.yml`, `rule-tests.json`, `control.py` as required, and `tests/monitoring/**`.

- **Specification:** Dashboard group; alert policy.
- **Depends on:** Task 1 metric names/types/labels and endpoints.
- **Produces:** One PostgreSQL dashboard group and Git-owned rules that separate actionable failure from telemetry loss.

- [ ] First add failing source/agent, chart and rule behavior tests, including absent telemetry, sustained outage, corroborated saturation/locks, recovery and no duplicate disk page.
- [ ] Add exact verified metric selectors, scrape route, chart queries and rules; retain existing rules and routing ownership.
- [ ] Run focused tests, offline render/validation and rule fixtures; record exact commands, exits and results.

### Task 3: Release wiring and operator documentation

**Files:** `deploy/observability/reconcile.py`, release classifier/workflow paths if required, `deploy/monitoring/prometheus/README.md`, `docs/architecture.md`, `docs/engineering-jobs.md`, runbook and tests.

- **Specification:** Acceptance and failure semantics; existing ADR 0053 auto-reconciliation.
- **Depends on:** Tasks 1 and 2.
- **Produces:** Exact-main autonomous reconciliation after merge, a source/agent/Monium validation gate, operational rollback and explicit cost/activation boundary.

- [ ] First add failing release-classification and safe rollback tests where current contracts do not cover the new paths.
- [ ] Extend only the required existing release surface; describe secret provisioning, series-count/cost gate, monitored activation and rollback.
- [ ] Verify documentation against actual code, reconcile ADR/architecture impact, and run targeted checks.

## Final verification

Run `scripts/select_test_suites.py select` and `fingerprint` on the final package. Run the exact focused tests named by each task, `.venv/bin/pre-commit run --files` for changed Python, `make static` after integration, `make check` once, and every selector-required expensive suite for the final fingerprint. After push, CI repeats verification. Do not claim live collection, evaluator health or delivered alerts from repository tests.

## Operational impact and rollout

The package adds a database monitoring identity, a private exporter process, one more agent scrape, and new remote-write series. Prepare the monitoring credential and estimate series/cost before merging a main revision that auto-reconciles these inputs. Obtain a fresh explicit approval before database/VM changes or billable ingestion. Stage the producer and verify source/agent/Monium freshness before applying alert rules; then prove one controlled firing and recovery in email and Telegram. The database container and product traffic must remain available throughout monitoring installation.

## Rollback

Restore the saved agent configuration and preceding Git-owned dashboard/rules revision, then remove or disable the exporter route. Preserve database roles until the old package no longer references them; role/secret cleanup is a separate reviewed operation. No product rows or media are changed.

## Open questions

None for the repository package. Live role/secret provisioning, measured series count, pricing and activation require fresh operational approval before execution.

### Task 3 implementation amendment

ADR 0018 requires exporter startup to belong to observability reconciliation, using an opt-in
Compose profile and exporter-only rollback. Product Deploy remains eligible if host monitoring
fails. The cloud barrier additionally waits for same-SHA Deploy when Django metrics change.
Role and credential preparation remain separately approved operator operations.
