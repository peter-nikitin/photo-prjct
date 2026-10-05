# Worker-folder operational handoff

- Date: 2026-09-30 (Europe/Moscow).
- Status: repository preparation and read-only baseline; **no paid, IAM, network, secret, alert or cutover operation is approved here**.
- Governing design: [approved two-folder specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md), [ADR 0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md), [ADR 0042](../adr/0042-isolate-autoscaled-photo-worker-pools.md), [ADR 0043](../adr/0043-observe-isolated-workers-with-git-managed-alerts.md).
- Full dated resource/quota/tariff evidence: [2026-09-30 preflight](2026-09-30-worker-folder-activation-preflight.md). The [2026-09-28 proposal](2026-09-28-worker-pool-activation-approval.md) is historical and assumed one folder; its old 200 GiB quota and prices are not current authorization.
- Next review: [prerequisite command package](2026-09-30-worker-prerequisites-approval.md), refreshed after PR #231 deployment; includes IAM/network batches, rollback, prices and integration with the delivered Prometheus alert platform. Its monitoring reconciliation section distinguishes existing platform delivery from remaining worker-specific coverage.

## Targets known from read-only discovery

The 2026-09-30 snapshot found cloud `b1gmcsmr51o5kvp86l55`, canonical folder
`b1g2qttgfhb4gdunvlge`, VPC `enpevjgdgdavmrv9ahb8`, canonical VM
`epdr5g3p24tdns9890nr` at `10.129.0.34`, canonical subnet
`e2l18k26thgtq8vobbq7`, default SG `enpclrep8uilre076c6q`, canonical boot disk
`epdrviptgj09r7ac478d`, and image-origin
VM/SG `epdf6696opq3ock91pih` / `enphgh6s669dv1647ggv`. The canonical VM was still
`PHOTO_WORKER_PLACEMENT=local` at deployed SHA
`58889666cd68f3af801b7cd82be1d7d64ef0a633`.
Application-secret ID `e6q85jjl76r45maigtfb` comes from the separate
[2026-09-28 proposal](2026-09-28-worker-pool-activation-approval.md),
not the 2026-09-30 preflight; refresh its ownership and version before use.

The dedicated worker folder, worker subnet/route/NAT/SG, manager/runtime identities,
bootstrap secret/version, clean OS image, and both group IDs **did not exist in that
snapshot**. Treat every one as an unresolved target from a later reviewed creation
receipt. The intended new subnet CIDR `10.131.0.0/24` is subject to fresh VPC/VPN
overlap checks. The old image digest and app SHA are observations, not future release pins.

## Required authority and network package before a VM boots

| Principal/resource | Required exact boundary | Read-back failure |
| --- | --- | --- |
| Group manager account | Created in worker folder; direct `compute.editor` only on that folder (which includes `vpc.user` there), direct `vpc.user` on canonical folder for the shared VPC, no cloud binding | Any canonical `compute.editor`/`compute.operator`/`editor`/`admin`, cloud/organization Compute inheritance, or unexpected resource grant |
| Worker runtime account | Created in worker folder; `lockbox.payloadViewer` only on the exact worker bootstrap secret; pinned version contains only `PHOTO_PROCESSING_FLEET_TOKEN` and `IMAGE_PULL_AUTH` | Any cloud/folder ancestor grant, application-secret access, Compute or Monitoring write authority |
| Canonical observer/release and native writer | Existing operator-approved identity, read/mutation authority only for actual worker-folder lifecycle calls; native metric write remains canonical folder | Broad canonical Compute grant added for convenience or worker Monitoring writer |
| Shared network | VPC and existing canonical VM/NIC/disks/app secret remain canonical; new worker subnet/route/NAT/SG and image belong to worker folder | Wrong ownership, network mismatch, public worker IP/listener, or incomplete attached-SG union |

The repository inspector verifies direct cloud, both folder and named-resource bindings and
ownership; the operator must separately review **effective** organization/cloud inheritance,
access policies and any other resource grants. The exact operation-to-role matrix and
current provider policy evidence must be reviewed before changes. Yandex documents the
manager's folder-level `compute.editor` requirement and that it includes `vpc.user`:
[Compute access](https://yandex.cloud/en/docs/compute/security/),
[Instance Groups access](https://yandex.cloud/en/docs/compute/concepts/instance-groups/access).
The [multi-folder VPC guide](https://yandex.cloud/en/docs/vpc/tutorials/multi-folder-vpc)
supports moving only a newly created subnet within one cloud or creating it in the target
folder. Do not move any existing canonical subnet, VM or disk.

Capture the canonical NIC's full current SG list, the default SG rules, image-origin SG
dependency, routes, and public 22/80/443 checks before any SG operation. A new restrictive
SG alongside `enpclrep8uilre076c6q` does not close private port 8443 because its broad
ingress remains in the allow union. Stage the image-origin SSH source-equivalent rule,
replace the canonical NIC SG list with a reviewed SG that allows 8443 only from the exact
worker SG, and prove private TLS and public 8443 denial immediately. If that proof fails,
disable the private 8443 listener **before** restoring the prior broad SG list; preserve
public web/SSH and the pre-change SG/route snapshot. These are proposed steps pending
exact rule diff and approval, not commands to run from this document.

Use a separately supplied GHCR read-only credential and separate fleet token in the new
worker Lockbox payload. Pin the exact secret version, keep both payload values and any
materialized JSON/receipts outside Git, terminal arguments and shared logs; never fetch
payload contents for an audit report. Preserve all existing app-secret keys and prior
version when preparing its separately approved fleet-token version. Build a clean worker
image from a reviewed recipe, prove installed Docker/Compose/telemetry versions and image
sanitization, then delete the temporary builder VM **and its disk** with full read-back
before a worker replacement. Do not clone the canonical disk.

## Reviewable command sequence and approval boundaries

1. Refresh cloud/folder, VM/NIC/SG, subnet/routes, quotas, folder bindings and active
   deployment read-only. Recheck actual costs and capacity; the 2026-09-30 cloud SSD
   snapshot was 256 GiB quota with 100 GiB allocated. Two workers add 64 GiB, one serial
   replacement reaches 196 GiB, and a simultaneous 32 GiB builder reaches 228 GiB.
   These are allocation calculations, not billing or provider lifecycle proof. Capture
   exact current ID and output for each check.
2. Prepare an explicit command-by-command proposal for worker folder, exact IAM grants,
   new subnet/route/NAT/SG, canonical SG replacement, worker-only secret and clean image.
   For each command show exact target, current/proposed state, charge/cost or explicit
   unknown, availability risk, read-back and rollback. User approves each paid or
   access-changing package immediately before execution. Creation outputs supply future IDs;
   do not substitute names or guessed IDs in a later command.
3. After approved prerequisites exist and their ownership/effective authority is read
   back, materialize the [complete nonsecret JSON shape](../runbooks/worker-pools.md#bounded-fleet-preparation-and-prerequisite-boundary)
   with real IDs, current SHA/digest, cap `1`, expected-absent groups and private
   credential files. The following repository commands are the supported bounded
   interface; angle-bracket values are **unresolved review inputs**, never runnable
   target IDs:

   ```sh
   .venv/bin/python deploy/worker-pools/provision.py --config <PRIVATE_NONSECRET_JSON>
   .venv/bin/python deploy/worker-pools/provision.py --config <PRIVATE_NONSECRET_JSON> --profile default --inspect
   .venv/bin/python deploy/worker-pools/provision.py --config <PRIVATE_NONSECRET_JSON> --profile default --status
   .venv/bin/python deploy/worker-pools/acceptance.py --pool-max-size 1
   ```

   The first command is local dry run. `--inspect` is read-only and checks direct scopes,
   ownership, topology and canonical SG allow union. Compare its checksum to the dry run;
   a changed worker **or** canonical folder changes that checksum. An older one-folder
   config fails validation. Preserve output without credentials. `--status` reads exact
   worker-folder group names/IDs and policies; absence remains absence.
4. Only after the checksum and exact group creation diff receive fresh approval, submit
   through `provision.py --apply <REVIEWED_SHA256> --receipt <FRESH_PRIVATE_PATH>` with the
   same config/profile. The durable receipt records checksum, both folders and each
   group state/operation ID before and after submission. A timeout is uncertain, not
   permission to create a new receipt or resubmit. Reconcile via read-only `--status`,
   exact group ID/name and provider operation. Prove actual bulk 0..1/selfie 1..1 policy;
   a submitted receipt is no capacity or delivery proof.
5. Canonical Deploy remains the release entry point. Its reviewed previous/candidate
   manifests must agree on both immutable folders; the release journal binds image,
   group, worker disk and pending operation. The periodic canonical collector reads
   worker groups/VMs/disks in `folder_id`, then publishes durable queue demand in
   `canonical_folder_id`; release-time publication does likewise. Actual fresh samples,
   private TLS/auth, warm selfie, bulk zero/wakeup, accepted bulk/selfie results, bounded
   serial forward/rollback and interrupted recovery require live evidence. At cap one,
   only one pool temporarily expands and at most three worker disks may exist. A fourth,
   retained, transitional, unexplained or wrong-folder disk blocks expansion.

Before any customer cutover, deliver a Git-owned **native cap-one** alert lifecycle:
reviewed predicate for one actual running VM with fresh growing queue age, supported
apply/read-back/rollback, native Alarm, both NoData paths, recovery and notification to
the approved operator channel for each actual pool/zone. The existing `R - 1.5` ceiling-two
rule does not satisfy this. A missing/stale source is unknown, including a total publisher
outage with retained historical positive samples. The separate diagnostic rules retain
ADR 0043's unresolved Managed Prometheus workspace/channel lifecycle, documented API
application and read-back/rollback gates. Neither alert path is established by a Markdown
manifest, local fixture or green Deploy.

At the end of the approved window, choose whether to retain an accepted fleet or restore
local placement. Initial rollback fences remote claims, drains/recovers current leases and
uses compatible canonical Deploy to restore local photo workers; do not restore old web
while remote drain is uncertain. Preserve PostgreSQL/pgvector, jobs, attempts, results,
artifacts and feature gates. Inspect receipt and provider operations before any cleanup;
delete only exact approved new worker resources after proving they no longer serve.
