# Worker prerequisites: command and approval package

- Status: ready for review; commands below have **not** been executed.
- Baseline: `e66a09d2bf3573254e3ec8468033b37bd3470401` (PR #231), verified live on 2026-09-30.
- Design authority: [approved specification](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md), [ADR 0046](../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md).
- This package makes the next infrastructure changes reviewable. It does not authorize them or replace the [activation acceptance and rollback](2026-09-30-worker-folder-operational-handoff.md).

## Current evidence and recommended order

Read-only discovery used `yc` 1.21.0, profile `default`, explicit cloud/folder IDs, on
2026-09-30 after PR #231 deployed. The cloud still has only its canonical folder.
Both existing VMs are RUNNING, and no NAT gateway or route table was listed there.
The canonical host reports the baseline image and `PHOTO_WORKER_PLACEMENT=local`;
public 22/80/443 listeners exist and no 8443 listener exists.

| Existing resource | Verified ID/state |
| --- | --- |
| Organization / cloud | `bpf5t03kot36lj32f8il` / `b1gmcsmr51o5kvp86l55` |
| Canonical folder / VPC | `b1g2qttgfhb4gdunvlge` / `enpevjgdgdavmrv9ahb8` |
| Canonical VM / NIC | `epdr5g3p24tdns9890nr` / index `0`, private `10.129.0.34`, public `111.88.151.64` |
| Canonical subnet / SG | `e2l18k26thgtq8vobbq7` / only `enpclrep8uilre076c6q` |
| Canonical disk / runtime identity | `epdrviptgj09r7ac478d`, 100 GiB SSD / `aje62dg0p7tpn6tqu67d` |
| Image-origin VM / SG | `epdf6696opq3ock91pih` / `enphgh6s669dv1647ggv` |
| Application secret metadata only | `e6q85jjl76r45maigtfb`, canonical folder, active version `e6q72kupvflgru2iu8r2` |

Listed VPC CIDRs are `10.128.0.0/24`, `10.129.0.0/24`, `10.130.0.0/24`;
the other network uses `192.168.1.0/24`. Proposed `10.131.0.0/24` does not overlap
these. External/VPN routing is not established by that listing; check before applying.
Organization/cloud direct bindings contain only individual user accounts, with no
`allUsers`/`allAuthenticatedUsers` grant. New account IDs and effective policies still
require read-back after creation. Existing canonical identity has `monitoring.editor`
and `backup.user` on its folder; no Compute grant is added there.

The monitoring platform already exists: Managed Prometheus, Git-owned rules and
Alertmanager application are delivered by the observability work. The remaining
worker-specific prerequisite is to connect queue/capacity and diagnostics to that
platform and add cap-one rules. A new native-alert API investigation is not the next
step. Folder/accounts can be prepared separately; schedule paid NAT/image/VM resources
with the worker-monitoring integration and clean-image recipe, then prove alerts with
live worker data before customer cutover.

## Price and quota envelope

Public RUB rates including VAT were refreshed from the official 2026-09-30 catalogue:
regular Ice Lake CPU/RAM `1.24`/`0.33` per vCPU/GiB-hour, preemptible `0.34`/`0.083`,
SSD `0.0199` per GiB-hour, image `0.0051` per billed GiB-hour, NAT `0.39528` per hour.
Lockbox lists `0.0274` per version-hour and `3.79` per 10,000 get operations.
These are estimates without account discounts or traffic/diagnostic usage.

| Increment | 730-hour comparison |
| --- | ---: |
| Folder, accounts, IAM bindings, subnet, route table, SG definitions | No separately priced SKU identified; no VM/IP/NAT/secret is included |
| NAT, created in phase B | 288.55 RUB/month while retained |
| One worker secret version, later phase | 20.00 RUB/month plus reads |
| Regular selfie, 2 vCPU / 8 GiB / 32 GiB SSD | 4,202.46 RUB/month |
| Preemptible bulk, same shape, present continuously | 1,445.98 RUB/month |
| Selfie + NAT + one version, bulk zero | 4,511.02 RUB/month plus image/usage |
| Both workers + NAT + one version | 5,957.00 RUB/month plus image/usage |

Image storage adds `3.723 × billed GiB` RUB/month. A regular 2/8/32 builder costs
`5.7568 × running hours` RUB plus retained disk/image/traffic; its duration and recipe
are not yet approved. The canonical VM's bill does not decrease in this package.

Cloud quota read-back: SSD 100/256 GiB, cores 10/32, RAM 20/128 GiB, VMs 2/12,
groups 0/10, images 0/32. Two workers produce 164 GiB SSD, one serial replacement
196 GiB, and a simultaneous 32 GiB builder 228 GiB. Delete the exact temporary
builder and its disk before a replacement; preserve the accepted serial-release fence.
Bulk zero saves disk charges only after the VM **and** its boot disk are deleted.

Sources: [Compute pricing](https://yandex.cloud/ru/docs/compute/pricing),
[VPC pricing](https://yandex.cloud/ru/docs/vpc/pricing),
[Lockbox pricing](https://yandex.cloud/ru/docs/lockbox/pricing),
[dated Compute catalogue](https://yandex.cloud/api/priceList/getPriceList?installationCode=ru&currency=RUB&lang=ru&services%5B%5D=dn22pas77ftg9h3f2djj&withExpired=false&withThresholds=true&pageSize=1000&from=2026-09-30&to=2026-09-30),
[dated VPC catalogue](https://yandex.cloud/api/priceList/getPriceList?installationCode=ru&currency=RUB&lang=ru&services%5B%5D=dn21qssbrdtcaus362kp&withExpired=false&withThresholds=true&pageSize=1000&from=2026-09-30&to=2026-09-30).

## Phase A: folder and identities

Approval target: create `findme-photo-workers` in the existing cloud and two identities
in that new folder. No keys, network attachment, secret payload or VM is created.
After each creation, retain its response and read back the exact ID; do not rerun a
create after a timeout until listing and operation status resolve its outcome.

The uppercase variables below stand for IDs from those reviewed creation receipts.
They are not supplied values or executable defaults. Render each dependent command
with its actual IDs before approving/executing its batch.

```sh
yc resource-manager folder create --name findme-photo-workers --cloud-id b1gmcsmr51o5kvp86l55 --profile default --format json
yc iam service-account create --name findme-worker-manager --folder-id "$WORKER_FOLDER_ID" --profile default --format json
yc iam service-account create --name findme-worker-runtime --folder-id "$WORKER_FOLDER_ID" --profile default --format json
yc resource-manager folder add-access-binding "$WORKER_FOLDER_ID" --role compute.editor --subject "serviceAccount:$WORKER_MANAGER_ID" --profile default
yc resource-manager folder add-access-binding b1g2qttgfhb4gdunvlge --role vpc.user --subject "serviceAccount:$WORKER_MANAGER_ID" --profile default
```

The manager gets only `compute.editor` in the worker folder and `vpc.user` in the
canonical folder, matching the deployed inspector. The runtime account initially has
no grants. Its later permission is `lockbox.payloadViewer` on the exact worker-only
secret, never a folder grant. This batch changes authority but has no identified
recurring resource charge and does not attach anything to either running VM.

Validate with `yc resource-manager folder get`, `yc iam service-account get`, and
`yc resource-manager folder list-access-bindings`, all using the receipt IDs and
`--profile default --format json`; read organization/cloud inheritance again. Reject
unexpected broad/group/public access or a folder outside the reviewed cloud.

Rollback the two grants individually with the matching `remove-access-binding`
commands and exact same role/subject/scope. Keep empty accounts/folder for inspection;
deletion is unnecessary for stopping spend. Do not use `set-access-bindings`, which
would replace unrelated grants. Canonical observer/release permissions are a later
batch tied to actual API calls; creating the manager does not grant them automatically.

```sh
yc resource-manager folder remove-access-binding b1g2qttgfhb4gdunvlge --role vpc.user --subject "serviceAccount:$WORKER_MANAGER_ID" --profile default
yc resource-manager folder remove-access-binding "$WORKER_FOLDER_ID" --role compute.editor --subject "serviceAccount:$WORKER_MANAGER_ID" --profile default
```

## Phase B: worker network and staged SGs

Prerequisites: phase A receipt, fresh overlap/quota check, reviewed worker-monitoring integration,
and approval of the NAT charge. Proposed names are unique in the new folder.
Use [direct cross-folder subnet creation](https://yandex.cloud/en/docs/vpc/tutorials/multi-folder-vpc)
against the existing VPC; no existing subnet is moved.

```sh
yc vpc gateway create --name findme-worker-nat --folder-id "$WORKER_FOLDER_ID" --profile default --format json
yc vpc route-table create --name findme-worker-egress --network-id enpevjgdgdavmrv9ahb8 --route "destination=0.0.0.0/0,gateway-id=$WORKER_NAT_ID" --folder-id "$WORKER_FOLDER_ID" --profile default --format json
yc vpc subnet create --name findme-workers-b --zone ru-central1-b --network-id enpevjgdgdavmrv9ahb8 --range 10.131.0.0/24 --route-table-id "$WORKER_ROUTE_ID" --folder-id "$WORKER_FOLDER_ID" --profile default --format json
yc vpc security-group create --name findme-worker-runtime --network-id enpevjgdgdavmrv9ahb8 --folder-id "$WORKER_FOLDER_ID" --profile default --format json \
  --rule 'direction=egress,protocol=tcp,port=443,v4-cidrs=0.0.0.0/0' \
  --rule 'direction=egress,protocol=tcp,port=8443,v4-cidrs=10.129.0.34/32' \
  --rule 'direction=egress,protocol=tcp,port=53,v4-cidrs=10.131.0.2/32' \
  --rule 'direction=egress,protocol=udp,port=53,v4-cidrs=10.131.0.2/32'
```

No worker ingress is allowed. Confirm the subnet's effective DNS/metadata/NTP behavior
and the clean image's actual outbound needs before boot; additional egress is a reviewed
rule diff, not implicit permission to add ANY. Verify each resource's folder/network,
route and rules with its `get --id` command. Reject a cross-folder API error instead of
moving resources or expanding canonical Compute rights to work around it.

Stage a replacement canonical SG in the canonical folder, initially unattached:

```sh
yc vpc security-group create --name findme-canonical-worker-api --network-id enpevjgdgdavmrv9ahb8 --folder-id b1g2qttgfhb4gdunvlge --profile default --format json \
  --rule 'direction=ingress,protocol=tcp,port=22,v4-cidrs=0.0.0.0/0' \
  --rule 'direction=ingress,protocol=tcp,port=80,v4-cidrs=0.0.0.0/0' \
  --rule 'direction=ingress,protocol=tcp,port=443,v4-cidrs=0.0.0.0/0' \
  --rule "direction=ingress,protocol=tcp,port=8443,security-group-id=$WORKER_SG_ID" \
  --rule 'direction=egress,protocol=any,v4-cidrs=0.0.0.0/0'
yc vpc security-group update-rules --id enphgh6s669dv1647ggv --folder-id b1g2qttgfhb4gdunvlge --profile default \
  --add-rule "direction=ingress,protocol=tcp,port=22,security-group-id=$CANONICAL_NEW_SG_ID"
```

Public 22/80/443 and canonical egress preserve currently permitted access. The new
8443 rule is the only private API ingress. Keep the old image-origin source rule
`enp4qmr4d9h2v6eb48fm` during validation. Record the newly added rule ID for rollback.
This phase adds rules but does not switch the canonical NIC or start a listener.

Rollback before any VM boot: remove only the added image-origin rule by its recorded
rule ID, delete only the unattached new SGs, delete the empty new subnet, then its route
table and NAT gateway by receipt IDs. Confirm no attachments/operations first and
read back absence. NAT deletion ends its recurring charge. No existing default SG,
subnet, VM, disk or VPC is a cleanup target.

```sh
yc vpc security-group update-rules --id enphgh6s669dv1647ggv --delete-rule-id "$ORIGIN_ADDED_RULE_ID" --folder-id b1g2qttgfhb4gdunvlge --profile default
yc vpc security-group delete --id "$CANONICAL_NEW_SG_ID" --folder-id b1g2qttgfhb4gdunvlge --profile default
yc vpc security-group delete --id "$WORKER_SG_ID" --folder-id "$WORKER_FOLDER_ID" --profile default
yc vpc subnet delete --id "$WORKER_SUBNET_ID" --folder-id "$WORKER_FOLDER_ID" --profile default
yc vpc route-table delete --id "$WORKER_ROUTE_ID" --folder-id "$WORKER_FOLDER_ID" --profile default
yc vpc gateway delete --id "$WORKER_NAT_ID" --folder-id "$WORKER_FOLDER_ID" --profile default
```

## Phase C: canonical NIC switch, separate availability window

Do this only when the clean worker image, private TLS configuration and test worker
are ready for immediate private/public validation. Preserve a working SSH session;
capture the full current NIC SG list and both SG rule snapshots immediately before:

```sh
yc compute instance update-network-interface --id epdr5g3p24tdns9890nr --network-interface-index 0 --security-group-id "$CANONICAL_NEW_SG_ID" --folder-id b1g2qttgfhb4gdunvlge --profile default
```

The full list becomes exactly the new SG: leaving the old ANY-ingress SG attached
would defeat 8443 isolation. This can interrupt access if rules are wrong; no extra
resource charge is expected. Verify a new public SSH connection, HTTP/HTTPS health,
image-origin SSH through the canonical host, private authenticated TLS from the worker
SG, and public 8443 denial. Only then remove the obsolete image-origin source rule.

Before opening 8443, prepare its exact listener-disable command from the reviewed
deployment configuration. If private/public isolation fails, first disable that listener
and verify it is closed, then restore the previous NIC list:

```sh
yc compute instance update-network-interface --id epdr5g3p24tdns9890nr --network-interface-index 0 --security-group-id enpclrep8uilre076c6q --folder-id b1g2qttgfhb4gdunvlge --profile default
```

Recheck SSH/web/image-origin. Do not restore broad ingress while a private API listener
remains open. This phase is not executable until its listener rollback is concrete.

## Remaining artifacts before paid workers

The [handoff](2026-09-30-worker-folder-operational-handoff.md) remains the source for
secret/image/group creation and cutover. The next implementation package must supply
the sanitized OS image recipe and immutable ID, pinned worker secret version, exact
canonical observer/release identity grants, full provisioning JSON with both folders,
and checksum-bound dry-run/inspection receipts. No guessed IDs or secret values belong
in Git. The fleet token and GHCR read-only credential go through the private secret
path; the application secret's other fields and pgvector state remain intact.

## Existing Prometheus delivery and remaining worker integration

The [approved monitoring-as-code design](../superpowers/specs/2026-09-28-monitoring-as-code-design.md),
[implementation/runbook](../../deploy/monitoring/prometheus/README.md) and
[activation evidence](2026-09-28-monitoring-activation.md) establish the delivered
foundation in canonical folder `b1g2qttgfhb4gdunvlge`:

- Workspace `mon0c97qv2s5uju1ark8`, email channel `fbefs2ubu6sq0k0jvlch`
  (`findme-photo-operator-email`) and protected GitHub `monitoring` environment exist.
- `deploy/monitoring/prometheus/rules.yml` owns eleven public/TLS/Linux/HTTP/Commerce
  rules. `control.py` renders, validates, checks, applies and restores the owned rules
  through the documented API. Live application is explicit and bound to a Git revision.
- `alertmanager.yml` owns routing for the dedicated workspace. The accepted design
  restores routing from a known Git revision and explicitly reports server-side routing
  drift as unverified; it does not require an invented Alertmanager GET.
- The operator confirmed receipt of the recovery email in the `обсервабилити` chat on
  2026-09-30. The older activation document still says that receipt is pending. This is
  operator evidence for the existing notification drill, not a worker alert drill.

The initial version of this package incorrectly treated the older API feasibility audit
as proof that this foundation was still missing. No support request is needed merely to
reuse the delivered platform. Workspace/channel provisioning and its accepted routing
limitation have already been handled for the existing monitoring scope.

Worker coverage is a separate, concrete repository delta. Inspection at `e66a09d`
found no bulk/selfie rule in `rules.yml` or worker metric selector in `environment.json`.
The canonical agent renderer has an opt-in `--worker-telemetry` diagnostics route, but
that does not itself export native queue/capacity metrics or install worker alerts.
`worker_pool_metrics.py` still publishes the authoritative autoscaling series through
the native Monitoring writer. Keep that writer and its scale/retirement semantics.

The next worker-monitoring package should:

1. Reconcile the worker specification's native-alert requirement and ADR 0043's old
   routing prerequisites with the delivered Prometheus contract before changing behavior.
   Preserve immutable accepted ADR text; record any required superseding decision.
2. Reuse the existing workspace, apply/restore tooling and operator receiver. Define an
   additive observation path for queue age, fresh actual running membership, source
   timestamps and worker diagnostics through the canonical boundary. Do not infer that
   native series already exist in Prometheus or replace the autoscaling writer.
3. Add bulk/selfie cap-one, overdue-work and missing-observation rules to the owned Git
   package. Cover bulk-zero, warm selfie, stale capacity, total publisher loss and
   recovery with promtool fixtures; missing data must not become healthy zero.
4. Verify actual source timestamps and rule evaluation on the provisioned fleet, then
   controlled firing, missing-data and recovery delivery before customer cutover.
   Generic email-drill evidence does not substitute for those worker scenarios.

The observability chat also reported missing public-probe samples in Prometheus on
2026-09-30. That is a separate ingestion finding, not absence of the alert platform;
coordinate its resolution with that work. The existing apply preflight requires fresh
configured inputs, so check it before applying a combined rules file. This package
does not claim to have rerun cloud metric queries or fixed that source.

The agent prepares the worker integration and commands, using actual creation receipts.
The maintainer approves the exact access/paid batches and supplies credentials through
the private channel. Native worker control, application state and current alert rules
are unchanged by this documentation correction.

Architecture reconciliation: infrastructure remains within ADR 0046. The next worker
monitoring design must explicitly reconcile evaluator/routing requirements with the
existing observability design; this package does not silently supersede an ADR.
