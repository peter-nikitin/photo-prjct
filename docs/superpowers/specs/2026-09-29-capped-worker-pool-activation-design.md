# Autoscaled worker pools with an initial ceiling of one

- Status: Approved in conversation on 2026-09-29; user explicitly replaced the earlier fixed-capacity proposal with queue-driven scaling capped at one.
- ADR impact: Conforms to [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md) and [0043](../../adr/0043-observe-isolated-workers-with-git-managed-alerts.md). This is a reversible capacity setting, not a new topology or diagnostic authority.
- Base specification: [autoscaled worker pools](2026-09-23-autoscaled-photo-worker-pools-design.md). Unchanged transport, processing, image, privacy, recovery and rollout contracts remain binding.

## Selected policy

Keep queue-driven Instance Groups, private verified TLS and canonical deployment authority.
Bulk scales 0..1; selfie stays 1..1. The reviewed provisioning configuration requires
`pool_max_size`, an integer exactly 1 or 2, bound into the checksum. No implicit default,
fixed policy or legacy configuration fallback. The approved first activation uses 1;
2 retains the previously accepted policy for a separately approved later change.
Both groups initially warm one instance. Bulk can subsequently return to zero with the
existing workload metric, idle retirement, group observation and lease recovery contracts.
Queue backlog cannot grant a second instance under the steady ceiling of one.

Keep approved 2 vCPU100%, 8 GiB RAM, 32 GiB network-ssd and 2 CPU/5 GiB container budgets.
The [disk diagnosis](../../operations/2026-09-29-worker-disk-sizing.md) identifies smaller
candidates but does not approve or implement them. Bulk is preemptible; selfie regular.
One bulk worker replaces the present two local bulk processes, reducing bulk concurrency;
longer bulk queue time is an accepted initial-stage trade-off. No database move/downsize,
pgvector/model/gate/backfill change, broker, shared filesystem or Kubernetes is in scope.

## Serial release and rollback

During a compatible remote release, temporarily raise only the current pool's maxSize to 2
and its required survivor/candidate floor through the existing staged warm/promote/drain
protocol. The other pool retains its reviewed ceiling. Restore the current pool's reviewed
maxSize and floor (bulk 0, selfie 1) before expanding the next pool. At most three worker
boot disks may exist at any time during a ceiling-one release, including stopped/transitional
members. Persist identified boot-disk IDs in the release journal before retiring their VMs.
Do not treat member disappearance as disk deletion: a complete fresh provider disk listing
must prove obsolete disks absent. Retained disks, incomplete/changing inventory, unexplained
worker disks, excess allocations and uncertain mutation receipts block the next expansion.
No ad-hoc cloud deletion or blind retries. Rollback obeys the same ordering and bounds,
preserving a compatible serving survivor; initial-local rollback remains supported.
Normal image releases and their rollback require the same reviewed ceiling in previous and
candidate manifests. Changing ceiling is a separately approved capacity/marker transition,
not an image release; this package rejects that mixed input rather than inventing a migration.

This is VM-level scaling, not creation of a disk per processing job. A VM processes many jobs.
Do not restart PostgreSQL/public edge to replace workers. Automatic provider recovery and
disk lifecycle must be rehearsed on actual cloud resources before customer cutover; repository
fixtures cannot prove the provider's physical allocation bound.

## Quota and activation authority

The last inspected canonical allocation was 100 GiB SSD within a 200 GiB quota. Two workers
add 64 GiB (164 total); a single replacement adds another 32 GiB (196 total). Only 4 GiB
remain, so builder and replacement must not overlap. Verify builder disk removal and fresh
quota/inventory before charged activation. Unrelated retained SSDs invalidate this arithmetic.
Preserve idle bulk-zero savings; NAT/image/secret costs remain distinct. Do not use the prior
continuous fixed 1+1 estimate as the minimum cost of this autoscaled stage.

Code approval does not authorize VM/network/IAM/secret/image creation or production cutover.
The [activation package](../../operations/2026-09-28-worker-pool-activation-approval.md) must
retain exact-ID, cost, image recipe, access, private endpoint and live-observability blockers.

## Acceptance

1. Provisioning/inspect/status prove WORKLOAD rules, bulk 0..1 and selfie 1..1, unchanged shapes/security, and strict checksum-bound ceilings; 2 preserves prior behavior.
2. Sole warm selfie cannot retire; bulk-zero and wakeup preserve claims/leases and stale-source fail-closed behavior. No backend capacity-mode schema or disabled retirement timer.
3. Forward/rollback fixture exercises two pools serially, restores the reviewed policy before the next expansion, and proves disk absence independently of VM membership. Retained/stopped disks and unknown inventory block expansion.
4. Existing real synthetic processing, private TLS/auth, immutable build, callback/result and recovery fixture passes. Cloud policy/disk lifecycle, real production results and fresh metric/alert delivery remain separate live gates.
5. No fixed-mode support survives in current tooling; future ceiling 2 is a separately approved checksum/config change, never automatic on quota approval.
