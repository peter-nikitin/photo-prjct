# Monitoring as code implementation plan

- Date: 2026-09-28
- Status: Approved for repository implementation; live activation pending
- Owner: project maintainer
- Related specification: [Design](../superpowers/specs/2026-09-28-monitoring-as-code-design.md)
- Related architecture: [Operations](../architecture.md)
- Related ADRs: [0018](../adr/0018-use-managed-yandex-monitoring.md), [0039](../adr/0039-run-public-probe-on-image-origin-vm.md)
- ADR impact: Conforms to both accepted ADRs, as detailed in the design.

## Goal, scope and acceptance

Implement the linked design. Deliver a reviewable offline package and reproducible explicit
commands; record live activation as pending until the workspace and routing foundation is ready.
Use `$execute-implementation-plan` for implementation and independent review.
No durable processing rows, worker contracts or photo artifacts change; the worker/state/artifact
release safeguards do not apply.

## Task 1: Declarative control plane

**Files:** `deploy/monitoring/prometheus/` tooling, rules, routing, dashboard, environment config,
isolated requirements and README; `tests/monitoring/` focused tests;
`.github/workflows/monitoring.yml`; test-suite manifest only if required for new workflow classification.
**Specification:** Declarative management, Rules and missing data, Acceptance.
**Depends on:** None.
**Produces:** render/validate/check/apply interface consumed by activation.

- Write failing transport/validation tests, observe RED, then implement.
- Produce equivalent rules and promtool tests for realistic failure/recovery paths.
- Verify dashboard JSON against the generated official SDK schema.
- Validate without cloud credentials; check/apply fail closed when live inputs are missing.
- Supply manual main-only OIDC workflow and operator runbook with exact commands.

## Task 2: Prometheus ingestion and host installation

**Files:** `deploy/monitoring/prometheus/` host exporter, units, installer and additive UA templates;
`tests/monitoring/` host/exporter/rollback tests.
**Specification:** Data flow, Acceptance.
**Depends on:** Task 1 configuration rendering.
**Produces:** bounded fresh scrape contract for existing UA on each VM.

- Write failing tests for failed HTTPS, failed Commerce observation, private binding and rollback.
- Reuse existing Python probe/Commerce collectors; do not change native delivery during preparation.
- Implement a bounded explicit host installer and additive route rendering with metadata IAM.
- Require workspace preflight to validate actual Linux names/types before rules are enabled.

## Final task: Verification and reconciliation

- Run `.venv/bin/pre-commit run --files <changed Python files>` and focused `make test` selectors.
- Run promtool rule syntax and behavior tests with a pinned Prometheus tool version.
- Run selector/fingerprint against base `866a894adf6b5ac1bba5bda2a4920cf88b661bbf`;
  run selected suites, then independent review. Root runs `make check` on the final package.
- Reconcile design/ADRs and document repository-ready versus live-unverified state.
- Consolidate approved files into one commit and a draft PR; CI is repetition of local verification.

## Operational impact and rollout

Repository delivery has no live effect. After foundation inputs and separate operational approval:
snapshot current dashboard/config/units; enable additive ingestion; verify fresh series/types and
sample volume; apply routing then operational rules; prove a separate validation alert firing,
missing-data and recovery email; update dashboard; finally retire old native observation alerts and
timers. Preserve autoscaling control metrics. Record each live evidence item explicitly.

## Rollback

Keep native alerts active until acceptance. Restore backed-up agent configuration/host units and
dashboard snapshot, reapply the previous owned rules and routing Git revision. For first activation,
remove only the newly owned rules file if necessary. Never delete workspace or unrelated files.

## Open questions

No repository design blockers. The user supplied workspace `mon0c97qv2s5uju1ark8` and email-channel
name `findme-photo-operator-email`. Remaining activation inputs: protected GitHub environment/IAM
readiness, workspace Linux metric mapping and price confirmation.
