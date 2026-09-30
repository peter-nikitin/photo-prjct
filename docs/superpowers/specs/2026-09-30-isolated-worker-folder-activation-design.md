# Isolate worker-pool management in a separate cloud folder

- **Status:** Approved in conversation on 2026-09-30 after written-spec review. No cloud mutation or customer cutover is approved.
- **Date:** 2026-09-30.
- **Related architecture:** [current worker boundary](../../architecture.md#current-architecture--implemented), [accepted constraints](../../architecture.md#accepted-constraints), and [operations](../../architecture.md#target-mvp-architecture--proposed).
- **Related ADRs:** [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md), [0043](../../adr/0043-observe-isolated-workers-with-git-managed-alerts.md), [0028](../../adr/0028-operate-one-canonical-deployment.md).
- **ADR impact:** Conforms to [ADR 0046](../../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), recording the explicitly selected folder/IAM isolation boundary, and ADRs 0042/0043 for queue, private API, release, and diagnostic-alert behavior.
- **Related specifications:** [autoscaled pools](2026-09-23-autoscaled-photo-worker-pools-design.md) and [ceiling-one activation](2026-09-29-capped-worker-pool-activation-design.md). Their workload, capacity, image, processing, data, and rollback contracts remain authoritative except for the explicit folder separation here.

## Intent and evidence

The first paid worker-pool activation must not give the Instance Groups manager permission to manage the canonical VM and its PostgreSQL disk. Yandex Cloud requires the group's manager service account to have `compute.editor` on the folder containing the group. Putting the groups in the existing canonical folder would therefore extend that permission to customer-serving stateful resources. The maintainer selected a separate worker folder in the same cloud, with private communication over the existing VPC, rather than accepting that blast radius.

The read-only 2026-09-30 inventory found only the existing `default` folder (`b1g2qttgfhb4gdunvlge`) in cloud `b1gmcsmr51o5kvp86l55`. It contains the canonical VM `epdr5g3p24tdns9890nr`, its 100 GiB SSD, the image-origin VM, and VPC `enpevjgdgdavmrv9ahb8`. No worker group, worker image, NAT gateway, or route table exists. SSD quota is 256 GiB with 100 GiB allocated. This is dated preparation evidence, not a cutover snapshot.

## Selected boundary

Create one dedicated worker folder in the existing cloud. Both Instance Groups, their group-manager and worker-runtime identities, clean worker OS image, worker-only bootstrap secret, worker subnet, route table, NAT gateway, and worker security group belong to that folder. The canonical VM, PostgreSQL disk, application secret, public edge, authoritative PostgreSQL queue, and native Monitoring metric writer remain in the existing folder. Do not move any existing VM, disk, network, subnet, secret, or service account merely to establish this boundary.

Extend the existing VPC across the two folders with a **new** worker subnet in `ru-central1-b`; the intended CIDR is `10.131.0.0/24`, subject to a fresh overlap/routing check before creation. A supported multi-folder VPC operation may create the subnet in the VPC's folder and move only that new subnet to the worker folder. Worker VMs have no public IP or inbound application listener. The worker subnet alone uses its reviewed NAT route for GHCR, Lockbox, and signed Object Storage URLs. Canonical networking and public 22/80/443 behavior stay in place. The private API remains verified HTTPS to the canonical VM's existing private address/hostname on 8443; its ingress is limited to the exact worker security-group identity. Replace, rather than supplement, the canonical default SG's current `ANY 0.0.0.0/0` ingress, preserving the image-origin SSH dependency and a tested immediate network rollback.

The group-manager account has `compute.editor` **only on the worker folder** and the minimum usable access to the cross-folder VPC resources. No `compute.editor`, `compute.operator`, `editor`, or `admin` grant to that account on the canonical folder, cloud, or organization is acceptable. The worker-runtime account is distinct from the manager and has `lockbox.payloadViewer` only on the exact worker bootstrap secret; bootstrap also pins the reviewed secret version. It cannot read the canonical application secret, manage Compute, or write Monitoring. The canonical observer/release identity receives only the reviewed worker-folder read/mutation authority needed by actual lifecycle calls; it gains no new Compute authority over the canonical folder through this change. Exact effective grants, including inherited organization/cloud grants and cross-folder `vpc.user`, must be reviewed and read back before any worker VM boots. No long-lived cloud key is placed in the worker image or container.

The existing provisioning and acceptance contracts must represent **both** folders explicitly. Worker group/instance/disk/image/worker-secret ownership and lifecycle listings use the worker folder. Canonical VM/NIC and application-secret validation use the canonical folder. A folder mix-up, missing resource, unexpected inherited authority, broad SG union, or incomplete cross-folder read fails closed. The worker-group folder is checksum- and receipt-bound; no implicit fallback to the CLI profile folder or to the canonical folder is allowed. The canonical publisher observes exact group IDs in the worker folder but continues publishing authoritative demand/capacity metrics into the existing canonical Monitoring folder. A failed cross-folder observation publishes neither a fabricated zero nor stale capacity and blocks voluntary retirement. Release and rollback preserve the existing single-SHA image, durable journal, serial replacement, and disk-absence fences within the worker folder.

The provisioning and trusted observation documents retain `folder_id` for worker resources and require a distinct `canonical_folder_id` for the canonical VM, application secret, and native metric namespace. Both fields are explicit immutable release inputs. Instance Groups' WORKLOAD rule references `canonical_folder_id`; the periodic collector and release-time publication use that same value, while group and disk inventories use `folder_id`. Previous/candidate manifests must agree on both folders during normal image release and rollback. Older single-folder inputs reject; no active remote fleet requires their migration. Both folder resources must belong to the reviewed cloud, the shared VPC remains owned by the canonical folder, and worker resource ownership must be checked independently of network membership.

The approved ceiling remains bulk `0..1`, selfie `1..1`, with initial warm `1+1` and unchanged 2 vCPU / 8 GiB / 32 GiB SSD VM shapes. A separate folder does not raise the ceiling or alter the selfie claim cap. The dated SSD arithmetic remains 164 GiB for two worker disks plus the canonical disk, and 196 GiB for one serial replacement; builder overlap and retained disks require a fresh full inventory before each charged step. A dedicated folder is an IAM boundary, not a quota exemption or a promise of lower charges.

## Alert and activation boundary

Before any customer cutover at ceiling one, enable the Git-reviewed Managed Prometheus worker
profile under [ADR 0047](../../adr/0047-reuse-managed-prometheus-for-worker-alerts.md). It recognizes
one actual running VM, fresh queue/cloud/publisher source values, and sustained growth of oldest
claimable age. Missing/stale evidence remains unknown, never zero or healthy. Prove sustained
firing, absent and retained-stale observations, recovery and delivery on the approved operator
channel. No alert raises capacity or grants processing authority.

This clarification follows the maintainer's 2026-09-30 direction to reuse the delivered Prometheus
stack. It replaces this specification's former separate native cap-one alert prerequisite, not
native autoscaling. Reuse the existing workspace, receiver and Git-to-API reconciliation:
owned rule read-back, fresh evaluator verification and known-Git-revision routing rollback, with
server-side routing drift explicitly unverified. No UI setup, new workspace or native alert API
investigation is required. Prometheus receives its own private read-only observations; native
series are not assumed to appear automatically. Existing public/Commerce controls are unchanged.

## Acceptance and authority

1. Repository tests prove disjoint folder IDs, exact worker-folder ownership, canonical-folder validation, checksum/receipt binding, independent observation/write folders, least-privilege rejection, and unchanged cap-one/serial-release behavior. A same-folder or implicit-folder configuration rejects before mutation.
2. Actual cloud read-back proves the worker manager's `compute.editor` is confined to the new worker folder, including inherited bindings, while the canonical VM, disk and secret remain outside that scope. The groups can use their exact VPC resources without a broad canonical-folder grant.
3. Live tests prove private TLS/auth, public 8443 denial, worker-only secret access, no public worker IP, fresh native observations, warm selfie, bulk zero/wakeup, provider VM/disk lifecycle, representative bulk/selfie results, and local-placement rollback without data migration or DB restart.
4. The cap-one alert and notification lifecycle passes before customer cutover. Diagnostics, if separately activated, pass their own ADR 0043 ingestion/routing/read-back gates. Local fixtures, Deploy success, a VM `RUNNING` status, or a sent resource-create receipt are not substitutes.
5. Before every paid or access-changing command, present the exact target IDs, commands, current and proposed state, official current cost delta or explicit unknown, availability risk, validation and rollback; obtain fresh manual approval. This specification authorizes no folder, IAM, network, image, disk, VM, secret, alert, or cutover mutation.

The current canonical local bulk/selfie workers remain the serving and initial rollback path until all gates pass. PostgreSQL/pgvector, public web, import, commerce, feature gates, model generation, historical backfill, and canonical VM size do not change in this stage.
