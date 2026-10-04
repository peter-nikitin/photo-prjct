# Actionable Observability Implementation Plan

- Date: 2026-10-04
- Status: Approved for implementation by the project maintainer in conversation
- Owner: project maintainer
- Related specification: [Actionable observability design](../superpowers/specs/2026-10-04-actionable-observability-design.md)
- Related architecture: [Architecture](../architecture.md)
- Related ADRs: [0052](../adr/0052-notify-only-on-actionable-service-degradation.md), [0053](../adr/0053-reconcile-observability-independently-on-main.md)
- ADR impact: Conforms to accepted ADR 0052 and ADR 0053

## Goal

Deliver the approved [outcome](../superpowers/specs/2026-10-04-actionable-observability-design.md#outcome) in one reviewed observability package.

## Scope

Exactly the approved specification. This plan does not change worker scaling, product data, or notification recipients.

## Acceptance criteria

The specification's [acceptance checks](../superpowers/specs/2026-10-04-actionable-observability-design.md#acceptance) apply. Code completion, merge, deployment, and notification delivery are reported separately.

## Implementation

Use `$execute-implementation-plan` for independent implementation and review.

### Task 1: Actionable alert policy

**Files:** `deploy/monitoring/prometheus/rules.yml`, `alertmanager.yml`, `control.py`, `rule-tests.json`, `worker-rule-tests.json`, relevant `tests/monitoring/`.

- **Specification:** Alert policy.
- **Depends on:** None.
- **Produces:** Rendered rules and routing with explicit actionable label and diagnostic fallback.
- [ ] Add failing tests for route selection, internal HTTP 5xx exclusion, stale-source persistence and worker impact correlation.
- [ ] Implement the minimal expressions, labels and routing; validate with `control.py validate` and focused monitoring tests.
- [ ] Compare candidate firing names and intervals against the five-day audit without equating evaluator episodes to email delivery.

### Task 2: Dashboard groups and chart set

**Files:** `deploy/monitoring/prometheus/dashboard.json`, `control.py`, `dashboard-query-tests.json`, `tests/monitoring/test_prometheus_control.py`, `tests/monitoring/test_prometheus_sdk.py`.

- **Specification:** Dashboard contract.
- **Depends on:** Task 1 expressions where charts reuse them.
- **Produces:** Eight groups with nested validation, service-impact ordering and distinct measures.
- [ ] Add failing nested-widget and query-contract tests.
- [ ] Group and refine existing charts, add verified collected metrics, preserve histograms.
- [ ] Run offline SDK/protobuf, query and rule validation.

### Task 3: Automatic independent reconciliation

**Files:** `.github/workflows/monitoring.yml`, `.github/workflows/deploy.yml`, `.github/workflows/deploy-image-origin.yml`, `.github/workflows/deploy-public-probe.yml`, `deploy/classify-release.py`, host package scripts and `tests/deployment/` as needed.

- **Specification:** Reconciliation contract.
- **Depends on:** Tasks 1–2 for final package.
- **Produces:** Main-only automatic component reconciliation at an exact SHA with isolated application release selection.
- [ ] Add failing path-classification and workflow-contract tests for monitoring-only, host-only, docs-only, Django-only, worker-only, and combined changes.
- [ ] Implement automatic main reconciliation using existing OIDC and host paths; keep manual recovery entrypoints.
- [ ] Verify failure visibility, backup and identity checks; do not weaken branch/workflow restrictions.

### Task 4: Architecture and operational reconciliation

**Files:** `docs/architecture.md`, `deploy/monitoring/prometheus/README.md`, related host runbooks.

- [ ] Compare delivered behavior with the specification and ADRs; update only implemented architecture facts.
- [ ] Record deployment ordering, one-time GitHub environment gate change, read-back limits and prior-revision recovery.

## Verification

Run focused tests as each behavior changes, then `$select-verification-suites` on the final package and `make check`. Run exact changed Python files through `.venv/bin/pre-commit run --files ...`, `make static` after integration, and the offline Monitoring workflow validator. Each selected expensive suite must have GREEN evidence for the final package fingerprint.

## Operational impact and rollout

Review and merge the PR after CI. Change the GitHub `monitoring` environment once so main-only automation has no reviewer gate; verify OIDC restrictions. Main then runs normal reconciliation. Compare fresh points, owned rule/dashboard read-back, and real email/Telegram firing and recovery. Retire native UI duplicates only after replacement proof. No automatic deletion is included.

## Rollback

Cloud recovery reapplies the last known reviewed Git revision and retained backups; host rollback uses the existing package backups and identity checks. A failed observability run does not roll back unrelated Django or worker deployment. Restore the previous routing/rules if the controlled service failure is missed.

## Open questions

None for implementation. Live acceptance depends on one-time GitHub environment policy change and delivery proof.
