# Unified worker activation

- Date: 2026-10-01
- Status: Repository implementation approved in conversation on 2026-10-01. Live execution still requires the complete operational package and explicit approval.
- Owner: project maintainer
- Related specification: [separate-folder activation](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md), [cap-one policy](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md)
- Related architecture: [accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [0048](../adr/0048-reuse-managed-prometheus-for-worker-alerts.md)
- ADR impact: Conforms to those decisions. Staging is a reversible release implementation detail; no topology, processing contract or authorization expansion is proposed.

## Goal

Deliver the specifications' live acceptance and local-to-remote transition through one reviewed operational package and one explicit execution approval, rather than requesting approval for each intermediate network/resource action. Historical-event backfill is the next separate task, not part of this activation.

## Scope

Preserve bulk0..1/selfie1..1, initial1+1, standard-v3/2vCPU100%/8GiB/32GiBSSD, preemptible bulk and regular selfie. One serial replacement may temporarily add one VM/disk, never two simultaneous expansions. No database downsize/move, new model, pgvector gate change, backfill, data reset or removal of legacy implementations.

Reuse the existing worker folder, network, clean OS image and worker-only secret. The accepted existing GHCR credential is reused without a renewed scope-inspection gate. No extra builder, NAT, secret, shared disk or registry is needed.

## Evidence and newly discovered blocker

The preceding live read-only inventory found no worker groups, VMs or disks; local workers still serve. The worker folder is b1gvs3prcd72lnivvdea; canonical folder b1g2qttgfhb4gdunvlge; cloud b1gmcsmr51o5kvp86l55. Canonical VM epdr5g3p24tdns9890nr has private10.129.0.34 and public111.88.151.64, with only the broad SG enpclrep8uilre076c6q. No8443 listener existed. Image fd8kaqq1av9l38kt7kun is READY. Worker secret e6qimj7tffu1d2ur06o4 pins version e6qbc24hltjhvtq1evqa. Operational creation/secret/network receipts are in the existing ignored worker-base-image task directory; they contain no secret values.

The prepared provisioning inspect rejected `canonical 8443 is reachable outside exact worker SG`. This is a firewall-policy finding, not a claim that a listener is currently running. Replace the broad SG; adding a restrictive SG alongside it is insufficient.

On this turn origin/main advanced to 6f0d918af44ed7ecaec793c2c7c0803b861663ea (PR244). Deploy36813766614 completed successfully; runtime SHA/digest must still be reread before activation. The previous6c0f1691 provisioning checksum is historical and must not be applied against newer web. OS image reuse is independent of the application OCI revision; rebuild/pin the application image through normal Deploy, not the OS image.

**Blocking release gap:** `deploy/apply-deployment.sh` currently projects the fleet token/coordinator and private API only for remote placement, then calls `fleet_phase rollout`. `deploy/worker-pools/release.py` warms/transitions both pools and immediately calls `cutover(host)`, which pauses/drains local workers, stops them and enables remote claims. There is no supported persistent stop between remote readiness and customer cutover. A preflight-only command cannot provide a running private receiver. Thus ordinary remote Deploy cannot be used to rehearse alerts before switching customers. Do not work around this by editing the production .env, directly replacing containers, calling private Python internals or killing a workflow at a convenient line.

The Git-owned monitoring environment also keeps `worker_alerts_enabled=false`. A successful apply of that revision does not enable worker rules. Prepare its reviewed enabled revision without changing existing channels/rules; activation still uses the exact-SHA Monitoring workflow.

## Acceptance criteria

The 2026-10-01 read-only refresh confirmed canonical revision `6f0d918af44ed7ecaec793c2c7c0803b861663ea`, zero claimable jobs and zero current leases in both pools at 04:26 UTC. Worker disks remain absent. Cloud SSD usage is 100/256 GiB, so initial workers require 164 GiB total and a single serial replacement requires 196 GiB. These snapshots must be refreshed before the paid window; they do not establish ongoing queue isolation.

The manager currently has `compute.editor` on the worker folder and `vpc.user` on the canonical folder, but lacks the canonical `monitoring.viewer` role required for native custom-metric scaling. Task1 must fix the exact-binding validator; task2 must include that narrow grant and its rollback. No live IAM change has been made by this preparation.

The linked specifications remain authoritative. Additional sequencing checks:

1. A durable, explicitly selected staging phase exposes the private authenticated API and worker observations while local processing remains the serving path and remote claims remain paused.
2. Staging success is distinguishable from a committed remote deployment. Resume, abort, ordinary deployments and rollback cannot accidentally open remote claims or discard an unfinished receipt.
3. One executable approval package covers the exact network/IAM diffs, two group creates, metrics/rules, bounded live rehearsals, cutover and scoped rollback/cleanup. Outputs from creates may parameterize later commands only through validated receipts.
4. Existing queues/results/artifacts survive. Unknown source health, missing notification proof, invalid image identity or incomplete disk inventory stops cutover.

## Worker/state/artifact release safeguards

- [ ] **Live-state inventory.** Before the paid window, refresh `report_worker_pool_state --json`, `report_worker_pool_telemetry --json`, enabled identities, coordinator state, authoritative leases, accepted evidence and feature gates through the canonical read-only wrapper. Capture counts only. Previous inventory establishes local placement, not a current drained queue. Owner: activation operator.
- [x] **Compatibility matrix.** Same reviewed candidate web and worker SHA/digest; old local processing remains available during staging. Preserve accepted processor versions and durable schemas. No old single-folder input or older web rollback over uncertain remote attempts. Normal later releases retain the existing compatible-build protocol.
- [x] **Reviewed data-state migration or reset semantics.** Compatible drain and lease recovery only. No backfill, purge, manual row rewrite, model migration or pgvector reversal. Existing accepted artifacts and results remain authoritative.
- [x] **End-to-end contract sizing.** Retain the accepted real TLS fixture contract:32x512 face result and512-value selfie result within16KiB. Re-run existing acceptance fixtures for the final modified release package; do not infer cloud behavior from them.
- [x] **Previous-snapshot upgrade rehearsal.** No new processing schema is proposed. Retain the existing non-empty attempt/result fixture and add pending-stage/resume/abort coverage, preserving successful/failed/retryable/current/expired attempts and artifacts. Any implementation introducing a schema requires reopening this slot.
- [ ] **Staged activation and rollback order.** Implement and verify the missing supported stage boundary before marking the operational package ready. Rehearse stage interruption, explicit activation and local restoration. Owner: release implementation task1.
- [ ] **Supported bounded operational commands.** Task1 must expose the staging/acceptance/activation/abort commands through the canonical pipeline. Existing rollout is not a substitute. Exact IAM role matrix and live alert-drill commands must be resolved in task2 before execution approval. No invented commands in the approval request.

## Implementation

Use `$execute-implementation-plan` for approved repository implementation. This draft is not approval to execute cloud operations.

Implementation clarification: the canonical `receiver` action precedes group creation and pins the candidate while keeping local processing. Only after successful create receipts can `stage` bind exact group IDs and warm paused remote workers. `activate` remains the same-candidate, health-gated Deploy action after acceptance. An unbound receiver cannot blindly abort after an uncertain external group create; reconcile ownership while retaining the compatible, local-serving runtime. This ordering resolves the initial private-receiver/group-ID dependency without a second release pipeline.

The implementation worktree was fast-forwarded to `f0f715799eb7a96305cd87355640702c3956c4c7` (PR245, commerce-only). Refresh the deployed revision again before generating final application pins.

Review clarification: first activation retains the original local package/environment and
recovery gate after `activate`, while remote claims serve a bounded real-work acceptance
window. Only the same-candidate `complete` Deploy action repeats health/live checks, commits
the fleet-success marker and removes that recovery snapshot. Same-candidate `abort` remains
available until completion, including after actual remote attempts and interrupted local
rollback. Ordinary Deploy remains fenced during this window. This closes the accepted
post-cutover rollback requirement; it adds no new release pipeline or cloud authority.

### Task 1: Close the first-activation staging gap

**Files:** `deploy/apply-deployment.sh`, `deploy/worker-pools/release.py`, `deploy/run-remote.sh`, `.github/workflows/deploy.yml`, existing deployment projection/validation files selected by actual inputs, `tests/deployment/test_worker_pool_release.py`, corresponding apply-deployment/workflow tests, `docs/runbooks/worker-pools.md`.

- Specification: private API, one-release authority, acceptance-before-cutover and local restoration.
- Depends on: None; no paid resources needed for implementation.
- Produces: supported explicit first-activation stage and activation boundary, checksum-bound durable receipt, fail-closed resume/abort, exact operator commands. Keep the existing remote-release path, not a second deployment pipeline or arbitrary host script.
- Add failing tests proving staging keeps local claims/containers, remote claims remain paused, private receiver/collector get the right configuration, no remote success marker is committed, and a later unrelated Deploy cannot erase or bypass the pending stage.
- Implement the smallest cohesive change; use the existing journal/lock and coordinator operations. Reject candidate, folder, group, secret pin or checksum drift on resume. Keep a single candidate web/worker revision.
- Cover failed readiness, interrupted stage, stage abort, explicit final cutover, failed drain, and rollback after remote attempts exist. Preserve the existing900-second bounded drain semantics.
- Establish a bounded canary acceptance procedure using actual supported controls. A production worker can claim any eligible job: do not claim an event-isolated canary merely because one test event was created. Select only a supported cohort/queue precondition and disclose any real-work exposure. Do not add new routing infrastructure speculatively.
- Run `make test TESTS="-m operational tests/deployment/test_worker_pool_release.py tests/deployment/test_worker_pool_acceptance.py tests/deployment/test_worker_pool_provisioning.py"`, plus selector-required affected workflow/projection tests. Normalize changed Python, independent review and root final evidence under project rules. Create/attach the PR without another PR-creation question.

### Task 2: Complete the one-window operational package

**Files:** one dated operational approval document, nonsecret ignored provisioning config/manifest, Git-owned `deploy/monitoring/prometheus/environment.json` activation diff where required; never secrets in Git.

- Specification: ownership, exact access, private TLS and Prometheus lifecycle.
- Depends on: task1 reviewed commands and final candidate release, current main and actual OCI pins.
- Produces: one executable command ledger with target, before/after, stop condition, read-back, cost and rollback for every mutation. Supersedes the earlier two-operation network-only approval request; do not execute that request separately.
- Resolve actual canonical observer/release API calls, including preflight reads of folder/cloud IAM, exact service-account/secret metadata, VPC, groups and disks. Verify provider role definitions and existing grants; avoid assuming `compute.viewer` alone covers preflight. Restrict mutation to worker resources. Runtime SA remains payloadViewer only on its worker secret; no new canonical Compute-editor grant.
- Include the existing reviewed canonical-SG creation and image-origin SSH source rule, exact NIC replacement, listener disable-before-broad-SG-restore, and prior rule preservation.
- Include two checksum-bound expected-absent group creations, durable receipts and no blind resubmission. Resolve group IDs from successful operations; derive the subsequent existing-group baseline/checksum mechanically from read-back.
- Include canonical observer/native publisher installation, preserved private Prometheus route, telemetry opt-in and Git-reviewed worker-alert profile application through Monitoring. No UI edits or new workspace/channel. Preflight fresh existing sources before combined rule apply.
- Include exact bounded firing/missing-series/retained-stale/recovery drills and delivery receipts for both pools, without falsifying production observations or changing thresholds invisibly. Existing generic email delivery is not this proof.
- Read actual cloud quotas and all allocated disks, including stopped instances. Require room for two32GiB disks and one serial32GiB replacement. Do not infer quota from old256GiB records.
- Include safe failure cleanup: fence/drain before deleting only newly created worker groups and their proven-owned disks; disable their deletion protection through an approved exact command if required. Preserve prior receipts and all existing infrastructure. An uncertain in-flight operation is reconciled, not blindly deleted/retried.
- Once all commands are concrete, request one explicit execution approval covering the entire bounded window and successful steady-state retention. No per-substep reapproval inside that unchanged envelope. Material drift, new spend/authority, changed image or failed recovery stops the window.

### Task 3: Execute and accept the fleet under that single approval

**Files:** append-only operational receipts and final live report; no product code changes.

- Depends on: tasks1/2 and explicit execution approval.
- Preserve current processing; snapshot public health, SSH/origin dependency and queue state.
- Apply scoped access/network preparation and canonical staged receiver in the reviewed order. Validate no public8443 exposure and public web/SSH continuity. Creating a SG is not network acceptance.
- Create initial bulk1/selfie1. Require providerRUNNING, exact OS image, secret version, OCI digest/revision, private certificate/auth, actual registration and fresh observations; no image-pull permission re-audit.
- Enable the reviewed Git worker alert profile after fresh inputs exist; prove ingestion/evaluator/notification and bounded processing/provider lifecycle rehearsals. At most one temporary replacement, at most three worker disks, obsolete disk absence before the next pool.
- Complete the supported activation step: pause/drain local claims, stop only local photo workers, open remote claims, verify real accepted processing/selfie results, correct pool floors/ceilings and public health. Retain original local recovery inputs throughout this real-work window. Run the pinned `complete` action to commit fleet success only after the whole release verifies; otherwise the pinned `abort` remains supported.
- Observe idle bulk0 and deleted boot disk, then real recoverable-demand wakeup; keep warm selfie1. Confirm no unfinished release or temporary surge remains. Failure invokes the approved bounded rollback, not spending/shape increases.

### Final task: Architecture and ADR reconciliation

Record actual created resources, serving placement, accepted gates, remaining limitations and costs in the operational receipt; update implemented architecture/runbook facts after proof. Preserve ADR0042/0046/0048. Stop for a durable design decision rather than silently changing those boundaries.

## Cost envelope for the final approval

Official Compute prices refreshed2026-10-01: regular Ice Lake vCPU1.24RUB/h, RAM0.33RUB/GiB/h; preemptible0.34 and0.083; networkSSD0.0199RUB/GiB/h. VAT included. Calculated incremental VM+disk cost:

| Capacity | RUB/hour | RUB/730hours |
| --- | ---: | ---: |
| Selfie2CPU/8GiB/32GiBSSD | 5.7568 | 4202.46 |
| Bulk same shape, continuously present | 1.9808 | 1445.98 |
| Both continuously present | 7.7376 | 5648.45 |

Bulk zero saves both compute and disk only after actual disk deletion. Existing NAT, OS image and Lockbox are already retained; do not charge them as new creations again or imply they disappear at idle. Canonical VM cost remains unchanged. Traffic and additional native/Prometheus observation charges are usage-dependent and currently unknown, explicitly part of the final approval rather than a zero-cost claim.

Proposed acceptance window: at most4hours, at most3 simultaneous workers, with only one serial replacement. Conservative compute+disk allowance53.98RUB for4hours at two regular plus one preemptible shape; this is not a total billing cap and excludes the unknown usage above. Stopped disks continue billing. At window expiry, no implicit extension: complete approved safe rollback/cleanup or report why resources must remain fenced and billable. Successful steady-state retention must be explicitly included in the one-window approval.

Source: [official Compute price table](https://raw.githubusercontent.com/yandex-cloud/docs/master/md-docs/compute/pricing.md). Formula:730*(2*CPU_rate+8*RAM_rate+32*SSD_rate). Values rounded only after summation.

## Verification

Use `$select-verification-suites` for the final repository package. Root `make check` and every selected expensive layer require exact-fingerprint GREEN evidence; CI repeats rather than replaces it. Read-only provisioning inspect/status and the live acceptance checklist complement local tests but do not prove customer acceptance alone.

## Operational impact and rollout

One final operational approval, not permission inferred from approval of this draft. Public Nginx/web may reconcile during staged canonical Deploy; promise bounded recovery, not zero downtime. PostgreSQL, import/commerce configuration, pgvector and historical data are not changed by worker relocation. Existing deployment behavior must be disclosed in the exact command package.

## Rollback

Before customer cutover: abort the stage through the supported canonical path, retain local service and reconcile/fence any remote registrations. After cutover: fence remote claims, wait for authoritative attempts to drain/recover, restore compatible local placement, verify public and processing health. Keep compatible web if remote ownership is uncertain. Disable private listener before restoring broad old SG. Restore only changed grants/rules and the prior reviewed Git monitoring revision. Remove only receipt-owned new groups/disks after serving/ownership checks; no database restore or broad Docker/cloud cleanup.

## Open questions / blocked artifacts

No new product or capacity choice is requested. The release implementer must supply the missing supported stage/activate/abort boundary and canary procedure. The activation operator must supply the exact IAM matrix, fresh quotas, final candidate pins and bounded alert drill commands. Until these discoverable/implementation gaps are resolved, do not label the unified package execution-ready or request permission to run guessed commands.

After successful fleet acceptance, prepare the separate new-model historical-event backfill. Vectors go only to FaceEmbeddingVector; preserve required detections/attempts/provenance and close old-reader rollback deliberately before removing legacy implementations.
