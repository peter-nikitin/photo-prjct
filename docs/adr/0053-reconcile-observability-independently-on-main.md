# 0053: Reconcile observability independently on main

- Status: Accepted
- Date: 2026-10-04
- Deciders: project maintainer; explicitly required automatic reconciliation after merge to main
- Supersedes: none
- Superseded by: none

## Context

Git owns a Managed Prometheus dashboard, rule file, Alertmanager routing and host-side collection
packages, but the cloud package currently needs a manual `Monitoring` workflow dispatch after
merge. Its GitHub environment also requires a human reviewer. Meanwhile the canonical Deploy
classifier treats every `deploy/monitoring/**` change as a Django image input, so a monitoring-only
merge can redeploy Django without reconciling Monitoring. Some host collection and privileged
observability changes also have manual activation steps. This defeats the intended separation of
documentation, Django, worker and observability releases.

## Decision drivers

- Make the merged Git revision the operational source of truth without a manual command after merge.
- Deploy only the components whose effective inputs changed.
- Preserve exact-revision identity, least-privilege OIDC, serialized writes, preflight, read-back
  and recoverable backups.
- Fail visibly on missing sources or unsupported cloud behavior instead of silently accepting drift.

## Considered options

1. Keep manual, protected `workflow_dispatch` application after merge.
2. Let the canonical Django Deploy job also apply monitoring changes.
3. Give observability its own automatic reconciliation on `main`, separate from Django and worker
   image publication.

## Decision

Select option 3. A merge to `main` that changes Git-owned observability inputs automatically
validates and reconciles the affected observability components from that exact merge revision.
The steady-state path must not wait for a manual workflow dispatch, environment approval, SSH
command or UI edit. Monitoring-only changes do not build or deploy Django or photo-worker images;
documentation-only changes do not contact runtime or cloud resources. Changes shared with an
application release may run both independent paths, each using the same reviewed commit.

The observability path owns the Managed Prometheus rule file, Alertmanager routing, dashboard,
and existing host-side metric, probe, agent and privileged selfie-observability packages. It
reconciles only resources already assigned to this project; new billable resources, IAM scope or
notification recipients require review before
merge under their own operational decisions. The existing `monitoring` OIDC identity and main-only
workflow restriction remain, but its required human environment review must be replaced with
automatic branch/workflow controls before the automatic path can operate. This is a one-time
foundation migration, not a per-release approval step.

Reconciliation remains serialized and idempotent. It validates the package and required fresh
samples, backs up owned state, applies supported API changes, reads back dashboard and rules,
waits for fresh successful evaluator snapshots, and records the exact source SHA and outcome.
Routing has no confirmed GET contract: a successful PUT and notification drill are distinct from
read-back proof, and recovery reapplies a known Git revision. Host updates retain bounded
identity/config checks, transactional backup and rollback. A failed preflight or apply makes the
merge's observability deployment fail visibly; it must not fall back to manual completion or
claim that Git and cloud match. It does not roll back an unrelated healthy Django or worker release.

Legacy UI-created alerts are inventoried and retired only after replacement delivery is proved;
they are not silently deleted by the Git reconciler. The existing manual actions may remain as
explicit recovery tools, but normal merges never depend on them.

## Consequences

### Positive

- Merged monitoring rules, routing, dashboard and collectors converge without an operator command.
- Monitoring-only merges stop causing unrelated Django or worker releases.
- An exact commit and recorded read-back connect Git review with deployed state.

### Negative

- A bad reviewed monitoring change can reach the live evaluator automatically.
- The protected environment and privileged host paths need a one-time secure automation change.
- Cloud or metric-source outages can fail reconciliation; an application release may still succeed
  while observability reports a separate failed deployment.

### Follow-up

- Implement component classification and automatic `main` reconciliation with tests for
  monitoring-only, host-only, documentation-only, Django-only, worker-only and combined changes.
- Replace the current required human environment review while preserving exact-workflow OIDC
  restrictions, and prove backup, failure reporting and recovery before removing manual activation.
- Migrate or retire UI-owned duplicates separately after controlled delivery acceptance.

## Validation and rollback

For an observability-only merge, prove no Django/worker image build or VM application deploy occurs,
and prove the exact merged rules/dashboard match cloud read-back without manual intervention.
Exercise missing-source and failed-update paths: they must produce a failed observability run with
retained backup and no false success. Verify host collection changes independently, including
public probe and image origin, and confirm no ordinary release needs a reviewer click. Recovery
reapplies a reviewed prior observability revision and its saved host/cloud state; it never changes
product data or queue authority.

## References

- [ADR 0051: Independent worker images](0051-release-photo-worker-images-independently.md)
- [ADR 0048: Managed Prometheus worker alerts](0048-reuse-managed-prometheus-for-worker-alerts.md)
- [Monitoring package](../../deploy/monitoring/prometheus/README.md)
- [Monitoring workflow](../../.github/workflows/monitoring.yml)
- [Canonical release classifier](../../deploy/classify-release.py)
