# Worker telemetry collection implementation plan

- Date: 2026-09-28
- Status: Phase-one repository implementation prepared under the acceptance gates below.
  Paid activation, cloud delivery and diagnostic alerts remain separately gated.
- Owner: project maintainer
- Related specification: [Worker telemetry](../superpowers/specs/2026-09-28-worker-pool-telemetry-design.md#approved-delivery-milestones)
- Related architecture: [Accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [0043](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md),
  [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md),
  [0017](../adr/0017-use-django-polled-photo-processing-jobs.md),
  [0028](../adr/0028-operate-one-canonical-deployment.md)
- ADR impact: Conforms to accepted ADR 0043. Phased repository delivery does not waive
  its alert configuration verification or live activation requirements.

## Goal

Deliver phase one of the [approved telemetry specification](../superpowers/specs/2026-09-28-worker-pool-telemetry-design.md#intent-and-scope):
explain individual worker resource/runtime failures and missing observations through the canonical
backend without changing processing or scaling authority.

## Scope

Implement the specification's observation, transport and metric contracts, and prepare managed
delivery using the existing canonical agent. Do not implement or apply alert control APIs;
do not create paid resources, change IAM/network, enable remote placement or deploy production.
Workspace/channel lifecycle and routing read-back remain a separate blocked milestone documented
in the [API review](../research/2026-09-28-worker-diagnostic-alert-api.md).

### Integration baseline refreshed on 2026-09-28

The implementation branch includes main `269d628` (PRs #217–#223). Reuse the merged
[monitoring-as-code package](../../deploy/monitoring/prometheus/README.md), rather than creating
another agent renderer, Remote Write channel, installer or alert control client. Its reviewed
environment supplies existing target identities; workers still receive no monitoring credentials.
The [activation record](../operations/2026-09-28-monitoring-activation.md) now records baseline
ingestion, initial rule/dashboard activation and firing email receipt; recovery email acceptance
remains pending. These observations do not establish worker diagnostic delivery. The unknown Alertmanager GET
contract remains unknown; the merged package does not waive ADR 0043's verification gate.
Reconcile the [dashboard/routing ownership finding](../future-work/2026-09-28-worker-monitoring-ownership.md)
before future diagnostic activation; do not independently apply the old native dashboard template
or create a foreign diagnostic rule file in the dedicated workspace. Phase one does not apply
dashboards or rules. Refresh selected-suite fingerprints after this main integration; pre-integration
GREEN evidence is not evidence for the updated package.

## Acceptance criteria

Use the specification's [phase-one acceptance boundary](../superpowers/specs/2026-09-28-worker-pool-telemetry-design.md#release-and-acceptance-boundaries).
The final package must prove actual loopback scrape and private TLS submission locally, safe
missing/crashed-container observation, bounded/fenced diagnostic state, exact histogram/outcome
semantics, privacy rejection and unchanged durable job/lease/queue behavior. A disabled sender
configuration is not cloud delivery or alert proof.

## Worker/state/artifact release safeguards

- **Live-state inventory.** The 2026-09-28 stage-one rollout record at
  `.worktrees/worker-pool-isolation/.superpowers/sdd/2026-09-27-autoscaled-worker-isolation/local-rollout.md`
  records deployed `eddc9a1`, local placement, coordinator disabled, no pool rows, enabled pgvector,
  unchanged nine feature states and post-deploy counts: 202899 vectors, 295827 jobs, 298365 attempts,
  59699 selfie results. This is a dated baseline, not a current-state promise. Task tests inventory
  all relevant status/lease classes; any later production deployment must refresh the read-only
  inventory before mutation. No Object Storage prefix is added or deleted by this plan.
- **Compatibility matrix.** Old backend + old worker is unchanged. New backend + old worker accepts
  the existing processing protocol with diagnostic data unavailable. New backend + new worker
  enables optional telemetry only when explicitly configured. New worker + old backend keeps
  processing with telemetry disabled; an unavailable diagnostic endpoint must never break polling,
  renewal or callbacks. No processing-row version or processor identity changes.
- **Reviewed data-state migration or reset semantics.** Add one coordination-owned latest-snapshot
  model keyed by pool and physical instance, independent of member registration. Create an additive
  migration after current leaf 0012; finalize numbering against refreshed main. All existing jobs,
  attempts, members, vectors, published artifacts and feature rows remain untouched. No reset,
  purge, backfill or reconciliation of product data.
- **End-to-end contract sizing.** Preserve existing result-size regression tests. Independently
  construct the largest allowed telemetry envelope (all enabled kinds/outcomes and ten duration
  series per histogram set), require it to fit 16 KiB through encoding, client, private proxy,
  pre-parse Django validation and snapshot persistence. Test exact boundary and one-byte excess;
  never relax processing-result limits to accommodate diagnostics.
- **Previous-snapshot upgrade rehearsal.** Use the existing private pre-deploy dump in
  `/private/tmp/findme-worker-rollout.yx5wXB/postgresql.dump` only in an isolated local database,
  never print or upload records. Apply the additive migration and compare pre/post row counts,
  status/lease classifications, feature values and vectors; exercise old worker callbacks with
  unavailable telemetry. If the private snapshot is unavailable, stop and obtain a replacement,
  rather than claiming the rehearsal from synthetic fixtures. It is not full disaster recovery.
- **Staged activation and rollback order.** This package is repository-only. Future approved rollout:
  compatible receiver/schema first, one explicitly selected worker's probe second, verify trusted
  snapshot/freshness and unchanged processing, then canonical delivery on separately approved
  resources. Stop on unexpected state changes, leaked identities, missing queue publication or
  unsafe probe commands. Disable probes/sender first on rollback, return to compatible images;
  retain additive diagnostic schema and all product/vector state.
- **Supported bounded operational commands.** Retain `report_worker_pool_state --json` and
  the existing collector's native publishing commands. Add one read-only diagnostic report with
  pool/instance/freshness and bounded status only. It must not dump metric payloads or product data.
  No requeue, backfill, data purge, resource mutation or alert apply command is introduced.

## Implementation

Execute the reviewed plan through `$execute-implementation-plan` after maintainer approval.

### Task 1: Worker runtime aggregates and private loopback scrape

**Files:** `src/worker/photo_worker/telemetry.py` (new), `src/worker/photo_worker/runner.py`,
`src/worker/photo_worker/__main__.py`, `src/worker/requirements.txt`, `deploy/worker-pools/compose.yml`,
`src/worker/tests/test_telemetry.py` (new), `src/worker/tests/test_runner.py`.

- **Specification:** Metric contract; Observation and transport contract.
- **Depends on:** None.
- **Produces:** In-memory, fixed-kind/outcome Prometheus exposition and internally fenced process
  identity, reachable only through the worker host's loopback port mapping.
- Add failing tests for busy reset, exactly one execution observation including delivery, fixed
  histogram buckets, successful callback versus execution/transport failure and lease loss.
- Use `prometheus-client` with a dedicated registry and no default process/platform collectors;
  no arbitrary-label API. Keep exporter failure outside processing control flow.
- Prove no endpoint start for unconfigured/local workers and no default bind on public host IP.
- Verify `make test TESTS="src/worker/tests/test_telemetry.py src/worker/tests/test_runner.py"`.

### Task 2: Canonical diagnostic receipt, fencing and exposition

**Files:** `src/backend/processing/services/worker_pool_telemetry.py` (new),
`src/backend/processing/models.py`, next additive processing migration,
`src/backend/processing/views.py`, `src/backend/processing/urls.py`,
`src/backend/config/urls.py`, canonical metrics view/module,
`deploy/nginx/https.conf.template`, `deploy/nginx/private-worker.conf.template`,
`src/backend/processing/tests/test_worker_pool_telemetry.py` (new),
`tests/processing/test_worker_api_contract.py`, `tests/deployment/test_worker_pool_transport.py`.

- **Specification:** Observation and transport contract; Metric contract; Release boundaries.
- **Depends on:** Task 1 exposition/envelope contract.
- **Produces:** Private authenticated telemetry submission and loopback-only canonical Prometheus
  exposition, never included in public or native autoscaling metrics.
- Add RED tests for untrusted membership/build, public/local submission, oversize bodies before
  parsing, unknown/non-finite fields, duplicates, delayed sequence, clock skew, old boot/process
  replay, staged pre-registration host observation and complete membership removal.
- Store one latest diagnostic row per pool/instance, with source and receipt clocks separate.
  Reuse trusted cloud predicates, but never register a member or mark it ready from telemetry.
  Unknown or stale capacity must not be presented as authoritative membership or healthy zero.
- Export fresh values and explicit missing/freshness signals using exact allowed dimensions;
  preserve counter reset boundaries without boot/process labels.
- Prove unchanged job, attempt, lease, readiness, release and queue metrics during failures.
- Verify `make test TESTS="src/backend/processing/tests/test_worker_pool_telemetry.py tests/processing/test_worker_api_contract.py tests/deployment/test_worker_pool_transport.py"`.

### Task 3: Host-owned probe and worker bootstrap packaging

**Files:** `deploy/worker-pools/telemetry.py` (new), probe service/timer (new),
`deploy/worker-pools/bootstrap.py`, worker host dependency packaging,
`tests/deployment/test_worker_pool_telemetry.py` (new), `tests/deployment/test_worker_pool_provisioning.py`.

- **Specification:** Selected approach; Observation and transport contract; Metric contract.
- **Depends on:** Tasks 1–2.
- **Produces:** Independent 30-second host probe, bounded local scrape and TLS submission.
- Add RED fixtures for missing/crashed container, OOM/restart evidence, root filesystem pressure,
  Docker timeout/event coverage gaps, runtime scrape failure and receiver outage.
- Use `psutil` for Linux resources; fixed named-container Docker state/stats/events requests with
  field selection, timeouts and bounded output. Never full inspect, environment, journals,
  arbitrary container names or control commands. Bound event lookback and report coverage gaps.
- Keep only the latest pending snapshot. Probe epoch/sequence and boot/process fencing must survive
  retries without treating delayed data as fresh. No cloud writer credentials or Docker socket mount.
- Verify `make test TESTS="tests/deployment/test_worker_pool_telemetry.py tests/deployment/test_worker_pool_provisioning.py"`.

### Task 4: Canonical delivery preparation, status and local end-to-end rehearsal

**Files:** `deploy/monitoring/prometheus/render_agent.py`,
`deploy/monitoring/prometheus/README.md`, `tests/monitoring/test_prometheus_host.py`,
`deploy/monitoring/prometheus/install.py` only if required for the existing installation contract,
`src/backend/processing/management/commands/report_worker_pool_telemetry.py` (new),
`tests/deployment/test_worker_telemetry_delivery.py` (new), `docs/runbooks/worker-pools.md`.

- **Specification:** Approved delivery milestones; privacy and failure semantics.
- **Depends on:** Tasks 1–3.
- **Produces:** An opt-in diagnostic scrape in the merged canonical agent renderer, using its
  existing `findme_prometheus_remote_write` channel, and read-only status. Default rendering must
  remain identical for both existing roles. Re-rendering must retain the opt-in only when explicitly
  selected, without duplicating routes or deleting unrelated/native routes. No parallel sender,
  workspace discovery, control API client or credentials in Git. Use reviewed monitoring inputs,
  never guessed identities. Retain the merged minimum-version and installation checks; do not
  upgrade or restart production.
- Add RED tests for opt-in configuration, preserved native routes, label allowlist, missing
  credentials/workspace, rejected unapproved endpoint and bounded sender behavior. Run the merged
  monitoring renderer/host regression tests too; diagnostic label filtering is route-local and must
  not strip labels from the existing Linux, HTTP, Commerce or public scrape routes.
- Run a local TLS receiver/container scrape rehearsal with synthetic identities and fixture cloud
  membership; prove source reset/freshness and processing renewal during ingestion failure.
- Record emitted scalar count, maximum snapshot bytes and cardinality/churn cost inputs. Report
  actual cloud ingestion, alerts and notifications as deferred, not GREEN.
- Verify `make test TESTS="tests/deployment/test_worker_telemetry_delivery.py"` and the selected
  operational/migration suites; document exact local TLS rehearsal command in the task report.

### Final task: Architecture and ADR reconciliation

Update `docs/architecture.md` and `docs/engineering-jobs.md` only for delivered repository capability.
Preserve ADR 0043, existing active/native controls, alert-stage blocker and separately approved live
activation. Record conformance and deferred evidence in the PR; never claim a provisioned topology.

## Verification

Use `$select-verification-suites` for executable suite selection and final-package fingerprint;
do not infer expensive suite requirements from this document. Run exact focused commands above,
the selected layers, changed-file pre-commit checks, `make static` after integration and root
`make check` before handoff. All must exit zero with evidence matching the final package.
Record the isolated previous-snapshot rehearsal and synthetic local TLS integration separately
from CI and future live proof. No production failure injection or sizing benchmark is required.

## Operational impact and rollout

Repository implementation adds an optional diagnostic schema/endpoint, runtime exporter and probe
package. Defaults leave current local topology and native publishing unchanged. A future canonical
Deploy must preserve deployed pgvector and gates, apply compatible receiver before probes and
follow the safeguarded order above. Cloud activation is not authorized by plan approval.
The [cost estimate](../research/2026-09-28-worker-pool-activation-cost.md) and SSD-quota blocker belong
to later exact-resource approval, not implementation actions.

## Rollback

Disable diagnostic probe and sender configuration, then restore a processing-compatible canonical
release. Retain additive schema and current product/vector state; do not reverse migrations over
new rows or restore a pre-pgvector application image. Alert routing is untouched in phase one.

## Open questions

None for phase-one design. Phase two and paid activation remain explicitly blocked/deferred;
their API contracts, credentials, resources, costs and quota approval are not assumed here.
