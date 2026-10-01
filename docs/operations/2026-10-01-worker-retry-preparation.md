# Worker activation retry preparation

- Date: 2026-10-01.
- Status: repository preparation; live execution requires separate approval of the final PR/head and command ledger. No VM creation, live rules or cutover performed by this preparation.
- Implements [unified activation plan Task 2](../plans/2026-10-01-unified-worker-activation.md#task-2-complete-the-one-window-operational-package).
- Specifications: [folder isolation](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md), [cap one](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md).
- ADR impact: conforms to ADRs [0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [0048](../adr/0048-reuse-managed-prometheus-for-worker-alerts.md). No new architecture decision.
- Exact proposed actions: [retry command ledger](2026-10-01-worker-retry-command-ledger.md); [nonsecret configuration template](2026-10-01-worker-retry-config.template.json).

## What changed after the first attempt

PR249 fixed provider JSON-default reconciliation through the real release path, restored-domain public checks during abort, and safe bootstrap phase/category diagnostics. It did not establish the original bootstrap failure cause. Both original VMs failed after about 304 seconds in cloud-init final; a Docker timeout remains only a hypothesis. Do not increase timeouts, change shape, rotate the existing accepted GHCR token or rebuild the OS image based on that timing alone.

The first failed launch was cleaned up: both groups/VMs/disks gone, temporary IAM/network changes reverted, collector privilege removed. Its inactive root helper/source and root-only recovery archives are intentionally retained. An overlooked `rolled-back-local` journal blocked PR249's ordinary Deploy; it was then recoverably archived with explicit approval and Deploy36841285732 attempt2 succeeded. Retry cleanup now explicitly accounts for this journal.

This PR prepares `worker_alerts_enabled=true` in Git again. It does not apply rules. Both profiles remain validated independently; live check/apply requires fresh real inputs. Existing channels, routes, thresholds and native scaling are unchanged. Do not infer alert delivery from repository configuration or offline fixtures.

## Fresh baseline, not permanent guarantees

Read-only checks on 2026-10-01 around 10:18–10:21 UTC:

| Surface | Observed |
| --- | --- |
| Main and canonical release | `5245f81c0fffaf77c366666e8d18f12f8daba7dc`, PR250; Deploy36847930241 success |
| Worker image | `ghcr.io/peter-nikitin/photo-prjct-worker@sha256:0a1f2c9d6807e9a96e4489fa6c31a70b7e7071659c4908f66bb0995948474dc8`; OCI revision matches main |
| Placement | Local; two bulk processes and one selfie process |
| Worker folder | `b1gvs3prcd72lnivvdea`: zero groups, VMs and disks |
| Canonical | `epdr5g3p24tdns9890nr`, folder `b1g2qttgfhb4gdunvlge`, private `10.129.0.34`, only original SG `enpclrep8uilre076c6q` |
| Cloud quotas | SSD100/256GiB, vCPU10/32, RAM20/128GiB, instances2/12, groups0/10 |
| OS image | `fd8kaqq1av9l38kt7kun`, READY, worker folder |
| Queues | Bulk/selfie claimable0, processing0 and current leases0 at 10:18:42 UTC |
| Telemetry | Cloud/host/runtime fresh0, one expected/missing node per pool from retained diagnostic state; remote write unverified. Not evidence of a live worker. |
| Private listener / collector | No8443 listener, metrics timer inactive, sudo include absent |
| Base manager grants | Worker-folder compute.editor; canonical-folder vpc.user. New scoped grants still required. |

The historical unassigned job/attempt counts in the state report are not automatically claimable work and are not permission to re-enroll or backfill them. Refresh all queues/leases/identities, effective IAM (including inherited access), network rules, quotas and actual inventories immediately before mutations. Do not create a benchmark project.

PR250 added the bib-editing audit schema and UI. The retry baseline includes it; do not roll back to `a6eead0` or a first-attempt package. This preparation branch is based on PR250. The final activation candidate will be this PR's actual approved merge SHA and corresponding immutable image, not the historical baseline in this table. If unrelated main changes land, reconcile the diff and deployed baseline before binding again. Monitoring check/apply requires its revision to equal the current main dispatch SHA; do not silently substitute a new revision during the window.

## Worker/state/artifact release safeguards

- [x] **Live-state inventory.** Dated baseline above; fresh pre-mutation reports and exact active identities are required by the ledger. Existing results/artifacts are preserved.
- [x] **Compatibility matrix.** Same final web/worker release SHA and OCI digest; original local package retained during staged and real-work acceptance. PR250 schema stays in place. No old-reader/model or gate changes.
- [x] **Reviewed data-state migration or reset semantics.** Existing drain/lease recovery only. No data purge, enrollment, model migration, pgvector reversal or historical backfill.
- [x] **End-to-end contract sizing.** Existing accepted 32x512 face / 512-value selfie result under16KiB is unchanged; no protocol/body/serializer changes in this preparation.
- [x] **Previous-snapshot upgrade rehearsal.** Existing release stage/abort and nonempty durable-state regression coverage is unchanged. Selected operational suites remain required; live bootstrap and replacement are separate acceptance gates.
- [x] **Staged activation and rollback order.** Receiver with local serving, paused remote warmup, provider/monitoring acceptance, explicit activate, real-work/idle-wakeup checks, complete; same-candidate abort remains available until complete.
- [x] **Supported bounded operational commands.** Linked ledger uses canonical Deploy phases and receipt-bound provider operations. Four hours total, one serial replacement, at most three worker disks. Missing evidence stops rather than expands scope.

## Execution order after approval

1. Merge only the reviewed head with green CI; verify ordinary local Deploy and the final immutable web/worker pins. No paid resources yet. Revalidate current main and all baseline state.
2. Under the canonical deployment lock, archive only the three exact residual old manifests/observation files. Preserve previous archive/helper; verify root ownership and reviewed hashes before restoring the narrow metrics sudo include. All new receipts are exclusive and separate from the failed attempt.
3. Apply only the ledger-listed IAM/network deltas. Preserve original SSH/image-origin reachability. Replace the broad canonical SG before enabling8443; never attach broad and restricted SGs together.
4. Render/inspect the final create manifest; install with deploy0600 and verify checksum. Set the exact activation variables and run `receiver`. Require local serving and private authenticated TLS before creating groups.
5. Create bulk1 and selfie1 once, each2vCPU/8GiB/32GiBSSD. Record uncertain submissions before calls and reconcile operation IDs, not blind retries. Bind actual IDs/baselines and run `stage`. Remote claims remain paused.
6. If either bootstrap fails, capture only safe phase/category and operation/member/disk evidence before cleanup. Do not proceed to customer cutover or claim the original cause fixed. Follow bounded abort/cleanup; do not silently alter the candidate or timeout.
7. Require real fresh queue/cloud/native publisher and node diagnostics, then exact-main Monitoring check/apply, backup and finite evaluator drill. Require actual firing/resolved receipt in the existing notification channel. Rehearse one paused bulk replacement with exact old-disk absence and at most three disks.
8. Run explicit `activate` only after all previous gates. Remote workers may process every eligible production job, not just a test event. Verify one legitimate small upload and matching selfie, then bulk idle deletion and a second upload/wakeup; keep selfie1 warm.
9. Run `complete` only with processing, provider lifecycle, telemetry and notification evidence. Otherwise same-candidate `abort`, owned-resource cleanup and read-back; preserve compatible local service and recovery evidence. Backfill remains a separate next task.

## What the maintainer does / what the operator does

The maintainer approves the exact reviewed PR/head and the entire bounded ledger once, then supplies the two small legitimate uploads and matching selfie when requested and confirms real firing/resolved notifications. Earlier willingness remains context, not evidence that those tests happened.

The operator executes the command ledger, records generated IDs/digests/operations, proves each gate and performs scoped rollback if needed. No extra per-step confirmations within the approved unchanged envelope. A different release, shape, cost envelope, IAM authority or failed recovery returns for a new decision. This preparation request itself is not that execution approval.

## Cost and quota

Bulk remains0..1 preemptible, selfie1..1 regular; initial warm1+1. Initial SSD total164GiB; one serial replacement196GiB against current256GiB. At most six additional vCPU,24GiB RAM and three workers are allocated during replacement, within the observed quotas; other concurrent allocations invalidate this arithmetic.

Official Russia rates refreshed2026-10-01: regular Ice Lake CPU1.24RUB/h, RAM0.33RUB/GiB/h; preemptible CPU0.34 and RAM0.083; SSD0.0199RUB/GiB/h, VAT included. Selfie costs4202.46RUB/730h; continuously present bulk1445.98; both5648.45. Four-hour conservative compute+disk allowance53.98RUB at two regular plus one preemptible shape is not an all-inclusive billing cap. Bulk zero saves storage only after disk deletion. Existing canonical VM/NAT/image/secret costs remain; monitoring ingestion/evaluation and traffic add usage-dependent charges whose total is unknown.

Source: [official Compute prices](https://raw.githubusercontent.com/yandex-cloud/docs/master/md-docs/compute/pricing.md). No paid operation has occurred in this preparation.

## Verification and release boundary

Use the project's selector and exact-package evidence for the Git profile change; validate both rule profiles, SDK schemas and drill fixtures offline. Review the full command ledger with the implementation. No new product runtime code is intended. Architecture reconciliation: conforms to ADR0042/0046/0048; implemented remote topology remains unaccepted. Merge, normal Deploy, paid preparation, staged readiness, customer cutover and backfill are distinct states, never inferred from one another.
