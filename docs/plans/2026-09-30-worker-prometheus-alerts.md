# Worker Prometheus alert integration

- Date: 2026-09-30
- Status: Approved for implementation; live activation remains gated
- Owner: project maintainer
- Authority: maintainer confirmed that alerts use the existing Prometheus stack and requested completion.
- Related specification: [Monitoring as code](../superpowers/specs/2026-09-28-monitoring-as-code-design.md), [worker diagnostics](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md)
- Related architecture: [Architecture](../architecture.md)
- ADR impact: [ADR 0047](../adr/0047-reuse-managed-prometheus-for-worker-alerts.md) narrowly reconciles worker alert evaluation and routing prerequisites with the existing monitoring platform; preserve ADR 0042 native autoscaling, ADR 0043 bounded diagnostics and ADR 0046 folder isolation. No new provider, identity, routing destination or compute topology.

## Goal and scope

Finish the missing worker-specific integration in the existing Managed Prometheus workspace.
Keep native autoscaling publication unchanged. No paid provisioning, IAM/network changes, live
monitoring apply or worker cutover is included. Existing public/VM/commerce alerts remain unchanged.

## Acceptance criteria

- Read-only private canonical scrape exposes bulk/selfie queue, trusted actual capacity and source
  timestamps; scrape cannot authorize queue evidence for autoscaling or perform cloud writes.
- Missing/stale observations are unknown, never substituted with zero capacity or target size.
- Optional worker rules are disabled in committed production configuration until fleet activation.
- Enabled rules cover overdue work, sustained cap-one saturation, observation/publisher loss and
  missing host/runtime diagnostics for expected nodes. Idle bulk with zero actual members is healthy.
- Offline validation tests both disabled and enabled profiles. Live preflight and evaluator checks
  derive their expected rules from the rendered package rather than a fixed count.
- No new monitoring workspace, channel, exporter process, secret or database migration.

## Worker/state/artifact release safeguards

This release only reads existing state and changes telemetry; it changes no worker payload, job,
attempt, lease, artifact or pgvector contract. Live-state inventory, old/new worker compatibility,
data migration/reset, result sizing, snapshot upgrade and bounded mutation commands are not
applicable. Existing rows and accepted artifacts remain untouched. Deploy compatible backend
first, then enable the existing optional canonical diagnostic scrape. Enable worker rules only
after approved fleet provisioning and fresh per-pool/node observations; rollback disables worker
rules through the known Git revision without changing placement or queues.

## Implementation

### Task 1: Complete private metrics and conditional Prometheus rules

**Owner:** implementer; backend services/view/tests and `deploy/monitoring/prometheus/`,
`tests/monitoring/` only. Root owns documentation outside that deployment directory.
**Depends on:** existing private diagnostics endpoint and Managed Prometheus control package.
**Produces:** default-off worker profile, working metrics, executable rule fixtures and preflight.

Requirements and resolved decisions:

1. Reuse the existing queue snapshot calculation in `worker_pool_metrics.py`; factor a read-only
   pool observation helper if necessary. Preserve native metric names, labels, publishing and
   successful-publication-only `record_queue_observation` semantics. Prometheus pool gauges use
   `pool` only: one DB identity per bulk/selfie pool; no new Django zone setting. Node diagnostic
   labels stay unchanged. Extend `/worker-diagnostics/metrics/`, not public `/metrics/`.
2. Expose queue observation timestamp, actual cloud observation timestamp and native publisher
   success timestamp as numeric Unix source values, alongside claimable/oldest-age/workload and
   fresh actual running count. Do not infer fresh source state from PromQL `timestamp()` or a
   cached freshness flag. Reuse the 90-second source bound. Queue observation failure must leave
   diagnostic exposition available and signal unavailable queue data without exposing identifiers.
3. Add one explicit boolean worker-alert opt-in to environment configuration, default false; the
   supported profile is exactly bulk/selfie, max one each. No generic multi-fleet framework.
   Render worker rules only when enabled, into the existing owned rules file and receiver.
4. Cap saturation means fresh queue and cloud evidence, actual running >= 1, oldest claimable age
   >300 seconds, positive recent age growth sustained for 5 minutes (reset/disappearance breaks
   persistence). Use recent sample delta and explicit source freshness gates. Separate overdue
   warning and missing queue/cloud/native publisher alerts. Missing sender must be detected even
   when ingestion retains old series. Do not alert for absent node metrics when bulk has zero
   expected members. Existing host/runtime unavailable gauges can support expected-node missing
   diagnostics, gated by fresh pool evidence and sample availability.
5. Enabled live preflight requires fresh per-pool queue/cloud/publisher source values and finite
   actual capacity, plus expected-node diagnostics when actual membership exists. Idle bulk zero
   does not require node samples. Preserve existing monitoring preflight contracts. Evaluator
   verification must match actual rendered rule identities across all groups, reject missing,
   extra or duplicate rules, and verify fresh successful evaluation. Remove fixed count 11.
6. `control.py validate` must validate both profiles using pinned promtool and test sustained
   saturation, below-cap backlog, age reset, missing/stale/future sources, total sender outage and
   idle bulk zero. Preserve all existing rule tests and public/commerce behavior.
7. Follow RED/GREEN tests, `$select-verification-suites`, exact Python pre-commit normalization,
   self-review and final-package evidence. Do not modify Git state or dispatch subagents. Report
   exact commands/statuses and final fingerprint under the plan's `.superpowers/sdd/` directory.
   Coordinate with root before final suite runs so root documentation is frozen first.

### Final task: Documentation and review

Root reconciles outdated native worker-alert instructions with the current Prometheus path,
records repository delivery separately from activation, and requests independent review of the
whole working-tree package. No accepted ADR decision is silently rewritten.

### Approved continuation: Worker dashboard and configuration preservation

The maintainer approved queue graphs, processing throughput and duration distributions in
conversation on 2026-09-30, followed by monitoring preparation before creating worker VMs.
This is an extension of the existing read-only telemetry and Git-owned dashboard, not a new
monitoring architecture. Execute with `$execute-implementation-plan`.

1. Extend `deploy/monitoring/prometheus/dashboard.json` and its existing renderer/tests with
   per-pool claimable queue, oldest waiting age, workload and actual capacity. Add runtime
   operations/minute, duration bucket distributions and p50/p95 split by operation kind; selfie
   is not bulk photo throughput. Preserve existing panels and distinguish missing/stale data
   from valid zero. Aggregate histogram buckets before quantiles; never average node percentiles.
2. Show backend-accepted photo throughput separately from terminal runtime observations. Do not
   count failed attempts, callback retries or multiple processing stages as additional photos.
   Document the exact completion boundary in the chart, including the distinction between an
   accepted processing result and public gallery availability. Keep observation read-only and
   processing, lease, artifact and database schema contracts unchanged.
   The bounded initial definition is accepted clean `preview-small-v1` publication, labelled
   "Фото с принятым превью/мин", not completion of every stage. Increment a label-free backend
   counter only after the publication transaction commits; retries, failures, rollback and
   watermarked derivatives do not increment it. Use the existing private multiprocess `/metrics/`
   route and five-minute counter rate, with fresh-sample gating. No historical database scan or
   new durable state is required. This is operational telemetry, not an accounting ledger.
3. Fix canonical `deploy/configure-monitoring-agent.sh` native refresh so it preserves installed
   Prometheus resources and routes, including the optional worker scrape. Verify repeated refresh,
   validation before promotion and existing rollback. Do not edit `deploy/image-origin/**`.
4. Run behavioral regression tests, real SDK dashboard parsing and pinned promtool query fixtures,
   then independent review and final selector-required evidence. Root reconciles this plan,
   architecture and worker runbook with the delivered behavior before the final package freeze.

Deployment order: compatible backend and safe agent configuration first; enable the private
scrape and publish dashboard with fresh queue/backend evidence before worker creation. Missing
VM/runtime evidence remains explicitly unavailable. After separately approved provisioning,
prove current-node runtime graphs and alert delivery before customer cutover. No cloud mutation
is implied by this continuation; exact live commands retain the approval gates below.

## Verification

Focused backend telemetry/pool metric tests; monitoring unit/SDK tests and pinned promtool fixtures;
selector-required suites; root `make static` and `make check` on the final package. Record exact
commands and outcomes in the PR, not inferred cloud success.

## Operational rollout and rollback

Merge/deploy does not turn on rules or create workers. Existing local workers remain authoritative.
After separately approved remote resources: install optional canonical scrape, prove fresh raw
series and expressions, enable worker profile in Git, run exact-SHA monitoring reconciliation,
read back fresh successful evaluations and prove firing/recovery on the existing destination.
Routing restore uses a known Git revision, per the existing platform contract. If worker evidence
fails, keep local placement; revert worker profile only, preserving unrelated monitoring rules.

Unified Agent ownership is on the canonical VM, not worker VMs: worker host probes send to the
private backend; `deploy/monitoring/prometheus/render_agent.py` owns the optional worker scrape.
Repeated installation must preserve all required native and Prometheus routes, pass `check-config`
and service-active checks, then prove fresh cloud samples and source clocks after deployment.
Service health alone is not delivery evidence. Deploy the route-preserving
`deploy/configure-monitoring-agent.sh` revision before activation; older revisions replace the
canonical configuration wholesale. Live read-back and fresh delivery remain prerequisites,
coordinated with observability. Do not use the older replacement against an activated worker scrape.
Observability owns
`deploy/image-origin/**`; this worker package does not modify those files.

## Open questions

None for repository implementation. Resource creation and live activation require separate approval.
