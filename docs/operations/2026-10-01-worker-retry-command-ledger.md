# Worker retry command ledger

Status: repository preparation only; no live authorization. Read [retry preparation](2026-10-01-worker-retry-preparation.md) first. Commands are gated steps, never an unattended batch. Preserve previous attempt evidence.

## Candidate binding

After approval of this PR's exact head and its green CI, merge with `gh pr merge <reviewed-pr-number> --merge --match-head-commit <reviewed-head-sha>`. Record its actual merge SHA as WORKER_ACTIVATION_SHA and require the normal local Deploy to succeed. Obtain WORKER_ACTIVATION_DIGEST from that exact build and OCI revision read-back. The final human approval names the real PR/head; these placeholders do not authorize another revision. If main advances before dispatch, stop and rebind/review; do not deploy the older a6eead0 or 5245f81 over newer production.

Fresh snapshot: profile default, cloud b1gmcsmr51o5kvp86l55, canonical folder b1g2qttgfhb4gdunvlge, worker folder b1gvs3prcd72lnivvdea. Refresh all quota/ACL/queue/serving baselines before the window; record additions only. The validated configuration template is adjacent in `2026-10-01-worker-retry-config.template.json`; use fresh ignored `.superpowers/sdd/retry-preparation/` receipts, never the first attempt's receipts.

## Scoped IAM additions

```sh
yc --profile default resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role monitoring.viewer --service-account-id aje21huml8igbm1llshe --format json
yc --profile default iam service-account add-access-binding --id ajecj75jr98l9j5ndo18 --folder-id b1gvs3prcd72lnivvdea --role iam.serviceAccounts.user --service-account-id aje21huml8igbm1llshe --format json
yc --profile default resource-manager cloud add-access-binding --id b1gmcsmr51o5kvp86l55 --role resource-manager.auditor --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder add-access-binding --id b1gvs3prcd72lnivvdea --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default compute instance add-access-binding --id epdr5g3p24tdns9890nr --folder-id b1g2qttgfhb4gdunvlge --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default compute disk add-access-binding --id epdrviptgj09r7ac478d --folder-id b1g2qttgfhb4gdunvlge --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role vpc.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder add-access-binding --id b1gvs3prcd72lnivvdea --role vpc.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default iam service-account add-access-binding --id aje21huml8igbm1llshe --folder-id b1gvs3prcd72lnivvdea --role viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default iam service-account add-access-binding --id ajecj75jr98l9j5ndo18 --folder-id b1gvs3prcd72lnivvdea --role iam.serviceAccounts.user --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default lockbox secret add-access-binding --id e6q85jjl76r45maigtfb --folder-id b1g2qttgfhb4gdunvlge --role lockbox.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default lockbox secret add-access-binding --id e6qimj7tffu1d2ur06o4 --folder-id b1gvs3prcd72lnivvdea --role lockbox.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
```

After successful group creation only, validate receipt-derived IDs/folder/name/labels
and bind them as WORKER_ACTIVATION_BULK_GROUP_ID / WORKER_ACTIVATION_SELFIE_GROUP_ID:

```sh
yc --profile default compute instance-group add-access-binding --id "$WORKER_ACTIVATION_BULK_GROUP_ID" --folder-id b1gvs3prcd72lnivvdea --role compute.editor --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default compute instance-group add-access-binding --id "$WORKER_ACTIVATION_SELFIE_GROUP_ID" --folder-id b1gvs3prcd72lnivvdea --role compute.editor --service-account-id aje62dg0p7tpn6tqu67d --format json
```

No IAM-admin/tokenCreator role. The exact-group editor can modify/delete only those
groups, not arbitrary canonical VMs. Existing manager worker-folder compute.editor
and canonical vpc.user are preserved, not added again.

After successful local restoration and complete worker cleanup, revoke only receipt
additions which were absent in the immediate pre-change ACL snapshot. Do not revoke a
role while recovery still relies on it. Exact rollback counterparts:

```sh
yc --profile default resource-manager folder remove-access-binding --id b1g2qttgfhb4gdunvlge --role monitoring.viewer --service-account-id aje21huml8igbm1llshe --format json
yc --profile default iam service-account remove-access-binding --id ajecj75jr98l9j5ndo18 --folder-id b1gvs3prcd72lnivvdea --role iam.serviceAccounts.user --service-account-id aje21huml8igbm1llshe --format json
yc --profile default resource-manager cloud remove-access-binding --id b1gmcsmr51o5kvp86l55 --role resource-manager.auditor --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder remove-access-binding --id b1gvs3prcd72lnivvdea --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default compute instance remove-access-binding --id epdr5g3p24tdns9890nr --folder-id b1g2qttgfhb4gdunvlge --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default compute disk remove-access-binding --id epdrviptgj09r7ac478d --folder-id b1g2qttgfhb4gdunvlge --role compute.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder remove-access-binding --id b1g2qttgfhb4gdunvlge --role vpc.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default resource-manager folder remove-access-binding --id b1gvs3prcd72lnivvdea --role vpc.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default iam service-account remove-access-binding --id aje21huml8igbm1llshe --folder-id b1gvs3prcd72lnivvdea --role viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default iam service-account remove-access-binding --id ajecj75jr98l9j5ndo18 --folder-id b1gvs3prcd72lnivvdea --role iam.serviceAccounts.user --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default lockbox secret remove-access-binding --id e6q85jjl76r45maigtfb --folder-id b1g2qttgfhb4gdunvlge --role lockbox.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
yc --profile default lockbox secret remove-access-binding --id e6qimj7tffu1d2ur06o4 --folder-id b1gvs3prcd72lnivvdea --role lockbox.viewer --service-account-id aje62dg0p7tpn6tqu67d --format json
```

Exact-group bindings disappear with successfully deleted groups. If groups must remain
fenced for investigation, preserve the access needed for approved recovery; do not claim
complete cleanup or zero recurring cost. Revoking an exact-group grant while retaining
a group is a separate ledger entry with its validated receipt ID.

## Network

The following exact create/rule-add commands supersede the separate network-only
request. The new canonical SG ID and added origin SSH rule ID must
come from successful create/read-back receipts. Require no unexpected same-name SG.
Keep old SG and old origin rule for recovery; do not leave both canonical SGs attached.

```sh
yc --profile default --retry 0 vpc security-group create --name findme-canonical-worker-api --network-id enpevjgdgdavmrv9ahb8 --folder-id b1g2qttgfhb4gdunvlge --format json --rule 'direction=ingress,protocol=tcp,port=22,v4-cidrs=0.0.0.0/0' --rule 'direction=ingress,protocol=tcp,port=80,v4-cidrs=0.0.0.0/0' --rule 'direction=ingress,protocol=tcp,port=443,v4-cidrs=0.0.0.0/0' --rule 'direction=ingress,protocol=tcp,port=8443,security-group-id=enphe9sidnbnrmfk49eu' --rule 'direction=egress,protocol=any,v4-cidrs=0.0.0.0/0'
yc --profile default vpc security-group update-rules --id enphgh6s669dv1647ggv --folder-id b1g2qttgfhb4gdunvlge --format json --add-rule "direction=ingress,protocol=tcp,port=22,security-group-id=$WORKER_STAGE_CANONICAL_SG_ID"
yc --profile default compute instance update-network-interface --id epdr5g3p24tdns9890nr --folder-id b1g2qttgfhb4gdunvlge --network-interface-index 0 --security-group-id "$WORKER_STAGE_CANONICAL_SG_ID" --format json
```

Verify a fresh SSH connection, HTTPS health and canonical-to-image-origin SSH before
enabling private8443. Verify exact SG union and private source group, public8443 denial.
If restoration is needed, disable/verify absence of the private listener first, then:

```sh
yc --profile default compute instance update-network-interface --id epdr5g3p24tdns9890nr --folder-id b1g2qttgfhb4gdunvlge --network-interface-index 0 --security-group-id enpclrep8uilre076c6q --format json
```

Only after canonical NIC read-back and confirmed local health may the receipt-owned
origin rule and unattached new SG be deleted by the prior exact scoped commands.

```sh
yc --profile default vpc security-group update-rules --id enphgh6s669dv1647ggv --folder-id b1g2qttgfhb4gdunvlge --delete-rule-id "$WORKER_STAGE_ORIGIN_RULE_ID" --format json
yc --profile default vpc security-group delete --id "$WORKER_STAGE_CANONICAL_SG_ID" --folder-id b1g2qttgfhb4gdunvlge --format json
```

## Candidate release commands (receipt pins, awaiting execution approval)

Host bootstrap uses the observed GitHub deployment user `deploy`, not a guessed user.
The retained worker-metrics.sudoers.disabled contains only install/verify/remove for the fixed
helper. Existing selfie-observability sudo rules are untouched. Current source hashes
(must match final reviewed package and canonical copied files before installation):

| File | SHA256 |
| --- | --- |
| metrics-root-helper.sh | f9a77232428c0f9823efa82bf955277703b695f1e7cc3e546ab6fd52a3c2773e |
| metrics.py | bb284593ecc4c2a378ed2ae94af946a460376d95b72396eb32cbb52b3e7b79f3 |
| metrics.service | d156c2f88973870eee43e7fc712dbb7bd77315e65fc44c5386e183c67532ce46 |
| metrics.timer | 567a13460b898b12c9696ec1a5622c1a7af99fc56294e51adddf4e5fb095abb3 |
| worker-metrics.sudoers | 5520bcd4a06403d29412d3d6a8373d08e23a64c9fb9d02af09e1eb69c57a3934 |

The first attempt's helper/package remain installed but inactive and exactly match the
table above. Do not repeat the old absence-only installation or overwrite them. On the
canonical host, verify every hash/owner/mode against the table, then validate the existing
disabled sudoers payload before restoring only its include. Expected source files are
root-owned, helper0755, package directory0755 and source files0644. No timer is active.

```sh
sudo sha256sum /usr/local/sbin/findme-worker-pool-metrics /usr/local/lib/findme-worker-pool-metrics-package/metrics.py /usr/local/lib/findme-worker-pool-metrics-package/metrics.service /usr/local/lib/findme-worker-pool-metrics-package/metrics.timer /var/lib/findme-worker-activation-bootstrap/worker-metrics.sudoers.disabled
sudo stat -c '%U %a %n' /usr/local/sbin/findme-worker-pool-metrics /usr/local/lib/findme-worker-pool-metrics-package /usr/local/lib/findme-worker-pool-metrics-package/metrics.py /usr/local/lib/findme-worker-pool-metrics-package/metrics.service /usr/local/lib/findme-worker-pool-metrics-package/metrics.timer
sudo test ! -e /etc/sudoers.d/findme-worker-pool-metrics
sudo visudo -cf /var/lib/findme-worker-activation-bootstrap/worker-metrics.sudoers.disabled
sudo install -o root -g root -m 0440 /var/lib/findme-worker-activation-bootstrap/worker-metrics.sudoers.disabled /etc/sudoers.d/findme-worker-pool-metrics
sudo visudo -c
sudo -l -U deploy
```

A changed candidate helper hash or any unexpected owner/mode stops this reuse route.
Reconcile the new package explicitly instead of installing over an unknown file.
Archive rollback will preserve the existing disabled payload: after supported abort has
removed the runtime collector, compare the active sudoers hash to the reviewed hash and
move the exact include to the fresh retry archive, never overwrite the first archive.

Before enabling receiver, refresh empty worker group/VM/disk inventories, local health,
no private8443 listener, no active release journal/recovery directory and no Deploy in
flight. Three residual first-attempt files were observed on 2026-10-01. Preserve them
under the deployment lock, using a new archive directory; the hashes below must still
match before any move. Run on the canonical host as the operator after approval:

```sh
sudo flock -n /opt/photo-prjct/.deployment.lock sh -c '
set -eu
test ! -e /opt/photo-prjct/.deployment-recovery
test ! -e /opt/photo-prjct/worker-pools-release.json
test ! -e /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01
cd /opt/photo-prjct
printf "%s\n" \
"25580bd7e5f5ce0c0142a01661ad11dbb1557b3f0c55b7104c2b249c54e1530e  worker-pool-create-manifest.json" \
"03f11237a5a8a331f75f56dea45f05b47aa7b756991c2c59e23a4ca86e245162  worker-pool-bound-manifest.json" \
"b751c6c0dc4dd40002599f0bc4340b52b9e912783d76f859f99a7ebce8f20315  worker-pools-observation.json" | sha256sum -c -
install -d -o root -g root -m 0700 /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01
mv worker-pool-create-manifest.json worker-pool-bound-manifest.json worker-pools-observation.json /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01/
'
```

Archive creation is exclusive; if it already exists, reconcile its receipt instead of
choosing another name automatically. The old journal archive and packages stay untouched.
Reverting this pre-receiver archive move means moving only the same three files back
under the deployment lock after proving all destinations absent and no active release;
do not restore stale observations during an active retry.

Create an operator transfer directory; retain the returned absolute path in
WORKER_BOOTSTRAP_TRANSFER_DIR. No new helper/source installation is needed:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=15 -l petrnikitin 111.88.151.64 'mktemp -d /tmp/findme-worker-retry.XXXXXX'
```

WORKER_ACTIVATION_SHA must be the reviewed merged main commit; WORKER_ACTIVATION_DIGEST
must be its already-built worker digest. These are receipt-derived placeholders, not
permission to select a different candidate during execution. Initial ordinary local
Deploy builds the images once. No subsequent phase rebuilds them.

```sh
gh workflow run deploy.yml --ref main -f deployment_sha="$WORKER_ACTIVATION_SHA" -f worker_pool_activation=receiver -f worker_pool_worker_digest="$WORKER_ACTIVATION_DIGEST"
gh workflow run deploy.yml --ref main -f deployment_sha="$WORKER_ACTIVATION_SHA" -f worker_pool_activation=stage -f worker_pool_worker_digest="$WORKER_ACTIVATION_DIGEST"
gh workflow run deploy.yml --ref main -f deployment_sha="$WORKER_ACTIVATION_SHA" -f worker_pool_activation=activate -f worker_pool_worker_digest="$WORKER_ACTIVATION_DIGEST"
gh workflow run deploy.yml --ref main -f deployment_sha="$WORKER_ACTIVATION_SHA" -f worker_pool_activation=complete -f worker_pool_worker_digest="$WORKER_ACTIVATION_DIGEST"
```

These commands are separate gated phases, not a batch to execute without intervening
evidence. Receiver precedes group creation; stage follows complete create receipts and
bound manifest installation; activate follows monitoring acceptance. `complete` runs only
after bounded real processing/provider lifecycle acceptance and closes the original-local
recovery window. Until then the same pinned abort remains available. Scoped recovery:

Before abort after an interrupted receiver or stage, stop new Deploy submissions and read
the durable journal on the canonical host under its deployment lock. Run this there as
the operator; a busy lock, missing journal, mismatched installed manifest, unexpected
phase or candidate pin is a stop. The output contains only the phase, recovery presence,
candidate SHA/digest and recorded checksums, not the manifest contents:

```sh
sudo flock -n /opt/photo-prjct/.deployment.lock python3 -c '
import json
from pathlib import Path

root = Path("/opt/photo-prjct")
journal = json.loads((root / "worker-pools-release.json").read_text())
phase = journal["phase"]
receiver_phases = {"receiver-prepared", "receiver-staged", "receiver-aborted"}
bound_phases = {"prepared", "staging", "staged", "activating", "verified", "rolling-back-local", "rolled-back-local"}
assert journal.get("previous") is None and phase in receiver_phases | bound_phases
recovery_present = (root / ".deployment-recovery").is_dir()
assert phase == "receiver-prepared" or recovery_present
candidate = journal["candidate"]
creation = json.loads((root / "worker-pool-create-manifest.json").read_text())
assert creation == candidate["creation_manifest"]
assert creation["configuration"]["worker_build"] == candidate["worker_build"]
assert creation["configuration"]["worker_image"] == candidate["worker_image"]
bound = None
if phase in bound_phases:
    bound = json.loads((root / "worker-pool-bound-manifest.json").read_text())
    assert bound == candidate["manifest"]
    assert bound["configuration"]["worker_build"] == candidate["worker_build"]
    assert bound["configuration"]["worker_image"] == candidate["worker_image"]
print(json.dumps({"phase": phase, "recovery_present": recovery_present, "pending": journal.get("pending") is not None, "worker_build": candidate["worker_build"], "worker_image": candidate["worker_image"], "creation_checksum": creation["checksum"], "bound_checksum": bound["checksum"] if bound else None}, sort_keys=True))
'
```

Compare `worker_build` and `worker_image` with the same recorded
WORKER_ACTIVATION_SHA and `ghcr.io/peter-nikitin/photo-prjct-worker@` plus
WORKER_ACTIVATION_DIGEST. Compare `creation_checksum` to the recorded
WORKER_ACTIVATION_CREATE_CHECKSUM and, when present, `bound_checksum` to
WORKER_ACTIVATION_BOUND_CHECKSUM. Reconcile any pending provider operation
before retrying.
For `receiver-prepared`, `receiver-staged`, or a resumable `receiver-aborted` with
`.deployment-recovery` still present, reconcile every external create/delete operation
to terminal status and prove empty worker groups, VMs and disks. Restore the exact
creation manifest/checksum recorded above; the bound manifest is accepted for `stage`
only while the journal remains in a receiver phase:

```sh
gh variable set WORKER_POOL_RELEASE_MANIFEST --body /opt/photo-prjct/worker-pool-create-manifest.json
gh variable set WORKER_POOL_RELEASE_CHECKSUM --body "$WORKER_ACTIVATION_CREATE_CHECKSUM"
test "$(gh variable get WORKER_POOL_RELEASE_MANIFEST --json value --jq .value)" = /opt/photo-prjct/worker-pool-create-manifest.json
test "$(gh variable get WORKER_POOL_RELEASE_CHECKSUM --json value --jq .value)" = "$WORKER_ACTIVATION_CREATE_CHECKSUM"
```

For `prepared`, `staging`, `staged`, `activating`, `verified`,
`rolling-back-local`, or `rolled-back-local`, retain the bound manifest/checksum,
reconcile pending work, and follow the supported fence/drain/local recovery. Do not
replace either candidate pin. Restore these variables only if their read-back differs
from the exact bound manifest and WORKER_ACTIVATION_BOUND_CHECKSUM recorded above:

```sh
set -eu
current_manifest="$(gh variable get WORKER_POOL_RELEASE_MANIFEST --json value --jq .value)"
current_checksum="$(gh variable get WORKER_POOL_RELEASE_CHECKSUM --json value --jq .value)"
if [ "$current_manifest" != /opt/photo-prjct/worker-pool-bound-manifest.json ] || [ "$current_checksum" != "$WORKER_ACTIVATION_BOUND_CHECKSUM" ]; then
    gh variable set WORKER_POOL_RELEASE_MANIFEST --body /opt/photo-prjct/worker-pool-bound-manifest.json
    gh variable set WORKER_POOL_RELEASE_CHECKSUM --body "$WORKER_ACTIVATION_BOUND_CHECKSUM"
fi
test "$(gh variable get WORKER_POOL_RELEASE_MANIFEST --json value --jq .value)" = /opt/photo-prjct/worker-pool-bound-manifest.json
test "$(gh variable get WORKER_POOL_RELEASE_CHECKSUM --json value --jq .value)" = "$WORKER_ACTIVATION_BOUND_CHECKSUM"
```

Re-read the phase under the lock immediately before dispatch; if it changed, select the
matching branch again. After that read-back, use only the original candidate pins:

```sh
gh workflow run deploy.yml --ref main -f deployment_sha="$WORKER_ACTIVATION_SHA" -f worker_pool_activation=abort -f worker_pool_worker_digest="$WORKER_ACTIVATION_DIGEST"
```

A `receiver-aborted` journal without a recovery directory has no resumable abort; record
that terminal state instead. A failed or timed-out workflow is never itself proof that
no mutation happened.

All four activation repository variables were absent at baseline. Following successful
abort and local-health verification, delete only variables introduced by this receipt:

```sh
gh variable delete PHOTO_WORKER_PLACEMENT
gh variable delete WORKER_POOL_RELEASE_MANIFEST
gh variable delete WORKER_POOL_RELEASE_CHECKSUM
gh variable delete WORKER_POOL_PRIVATE_API_IPV4
```

## Prepared provisioning and monitoring commands

From this worktree, materialize `worker-create-config.json` from
`docs/operations/2026-10-01-worker-retry-config.template.json`
only after the reviewed application SHA and worker OCI digest have been bound. All other
fields must equal the reviewed template. The old worker-base-image config is obsolete.
The local prepare command emits the release manifest (including embedded host sources)
and checksum; it is not the same file as the input configuration.

```sh
test ! -e .superpowers/sdd/retry-preparation/worker-create-config.json
umask 077
jq --arg sha "$WORKER_ACTIVATION_SHA" --arg image "ghcr.io/peter-nikitin/photo-prjct-worker@$WORKER_ACTIVATION_DIGEST" '.worker_build=$sha | .worker_image=$image' docs/operations/2026-10-01-worker-retry-config.template.json > .superpowers/sdd/retry-preparation/worker-create-config.json
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-create-config.json
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-create-config.json --profile default --inspect
```

Save the generated manifest privately as `worker-create-manifest.json`, record its
`checksum` as WORKER_ACTIVATION_CREATE_CHECKSUM, and transfer/install the exact file owned
by deploy mode0600 at `/opt/photo-prjct/worker-pool-create-manifest.json`. Read back its
content SHA256 and embedded checksum before setting the following repository variables:

```sh
scp .superpowers/sdd/retry-preparation/worker-create-manifest.json "petrnikitin@111.88.151.64:$WORKER_BOOTSTRAP_TRANSFER_DIR/worker-create-manifest.json"
ssh -o BatchMode=yes -l petrnikitin 111.88.151.64 "sudo test ! -e /opt/photo-prjct/worker-pool-create-manifest.json && sudo install -o deploy -g deploy -m 0600 '$WORKER_BOOTSTRAP_TRANSFER_DIR/worker-create-manifest.json' /opt/photo-prjct/worker-pool-create-manifest.json && sudo sha256sum /opt/photo-prjct/worker-pool-create-manifest.json"
gh variable set PHOTO_WORKER_PLACEMENT --body remote
gh variable set WORKER_POOL_PRIVATE_API_IPV4 --body 10.129.0.34
gh variable set WORKER_POOL_RELEASE_MANIFEST --body /opt/photo-prjct/worker-pool-create-manifest.json
gh variable set WORKER_POOL_RELEASE_CHECKSUM --body "$WORKER_ACTIVATION_CREATE_CHECKSUM"
```

Run pinned receiver and verify local serving/private receiver before this paid command.
The receipt path must not already exist; a timeout or partial response forbids rerun:

```sh
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-create-config.json --profile default --apply "$WORKER_ACTIVATION_CREATE_CHECKSUM" --receipt .superpowers/sdd/retry-preparation/worker-provision-receipt.json
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-create-config.json --profile default --status
```

Reconcile both receipt operation IDs with `yc --profile default operation get <receipt-operation-id>
--format json` until terminal success. From successful status derive only bulk/selfie
`id` and `baseline` into `worker-bound-config.json`; every other field must exactly match
the receiver config. Prepare/inspect the bound manifest, never re-apply to create again:

```sh
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-bound-config.json
.venv/bin/python deploy/worker-pools/provision.py --config .superpowers/sdd/retry-preparation/worker-bound-config.json --profile default --inspect
```

Install exact bound manifest deploy:deploy0600 at
`/opt/photo-prjct/worker-pool-bound-manifest.json`, verify its file/embedded checksums,
and record WORKER_ACTIVATION_BOUND_CHECKSUM. Apply exact-group canonical grants from the
IAM section before canonical stage needs to PATCH groups. Then:

```sh
scp .superpowers/sdd/retry-preparation/worker-bound-manifest.json "petrnikitin@111.88.151.64:$WORKER_BOOTSTRAP_TRANSFER_DIR/worker-bound-manifest.json"
ssh -o BatchMode=yes -l petrnikitin 111.88.151.64 "sudo test ! -e /opt/photo-prjct/worker-pool-bound-manifest.json && sudo install -o deploy -g deploy -m 0600 '$WORKER_BOOTSTRAP_TRANSFER_DIR/worker-bound-manifest.json' /opt/photo-prjct/worker-pool-bound-manifest.json && sudo sha256sum /opt/photo-prjct/worker-pool-bound-manifest.json"
gh variable set WORKER_POOL_RELEASE_MANIFEST --body /opt/photo-prjct/worker-pool-bound-manifest.json
gh variable set WORKER_POOL_RELEASE_CHECKSUM --body "$WORKER_ACTIVATION_BOUND_CHECKSUM"
```

Run pinned stage only after helper bootstrap. Once current real source clocks and nodes
are fresh, use the reviewed exact-main Monitoring revision:

```sh
gh workflow run monitoring.yml --ref main -f revision="$WORKER_ACTIVATION_SHA" -f action=check
gh workflow run monitoring.yml --ref main -f revision="$WORKER_ACTIVATION_SHA" -f action=apply
gh workflow run monitoring.yml --ref main -f revision="$WORKER_ACTIVATION_SHA" -f action=drill-run
```

Record the exact drill workflow run as WORKER_ACTIVATION_DRILL_RUN_ID. If main advances,
status/cleanup dispatch uses the freshly verified current workflow SHA as
WORKER_MONITORING_CURRENT_SHA; the workflow validates and checks out the trusted original
drill source revision from its receipt run, never substituting current predicates for
old ownership proof:

```sh
gh workflow run monitoring.yml --ref main -f revision="$WORKER_MONITORING_CURRENT_SHA" -f action=drill-status -f drill_run_id="$WORKER_ACTIVATION_DRILL_RUN_ID"
gh workflow run monitoring.yml --ref main -f revision="$WORKER_MONITORING_CURRENT_SHA" -f action=drill-cleanup -f drill_run_id="$WORKER_ACTIVATION_DRILL_RUN_ID"
```

Drill success proves evaluator states only. Require actual recipient evidence; no
notification receipt means no final acceptance. Never infer delivery from FIRING.
Only after source/evaluator/notification proof may activate open remote processing;
only after legitimate bulk/selfie results and provider lifecycle proof may complete
close recovery. No historical reprocessing command is included.

## Conditional recovery commands

Monitoring rollback uses the existing supported local control command; there is no
invented workflow `restore` action. Before the live window, create an isolated local
SDK environment and render the reviewed unchanged routing. The only monitoring environment diff
in this PR is worker_alerts_enabled; routing source and receiver IDs are unchanged.
Record the rendered routing hash and compare it with the last accepted Git routing
revision before any apply. If another writer changed routing, stop and reconcile rather
than overwriting its state. Require no queued/running Monitoring write during recovery.

```sh
WORKER_MONITORING_TOOLS_DIR=$(mktemp -d /tmp/findme-worker-monitoring-tools.XXXXXX)
.venv/bin/python -m venv "$WORKER_MONITORING_TOOLS_DIR/venv"
"$WORKER_MONITORING_TOOLS_DIR/venv/bin/pip" install -r deploy/monitoring/prometheus/requirements.txt
"$WORKER_MONITORING_TOOLS_DIR/venv/bin/python" deploy/monitoring/prometheus/control.py render --output "$WORKER_MONITORING_TOOLS_DIR/render"
```

Save the exact apply run ID as WORKER_ACTIVATION_MONITORING_APPLY_RUN_ID, validating
repository, workflow, main and candidate SHA. Download its target-bound pre-mutation
backup into a fresh directory before continuing to cutover:

```sh
gh run download "$WORKER_ACTIVATION_MONITORING_APPLY_RUN_ID" --name "monitoring-backup-$WORKER_ACTIVATION_SHA" --dir "$WORKER_MONITORING_TOOLS_DIR/backup"
```

After exact temporary drill cleanup and confirmation of the saved target IDs, conditional
rollback is the following single supported restore. It restores owned dashboard fields,
production rules and the reviewed unchanged routing, never deleting the workspace:

```sh
"$WORKER_MONITORING_TOOLS_DIR/venv/bin/python" deploy/monitoring/prometheus/control.py restore --identity yc --backup "$WORKER_MONITORING_TOOLS_DIR/backup" --routing-file "$WORKER_MONITORING_TOOLS_DIR/render/alertmanager.yml"
```

The existing yc identity is captured internally, not printed. Require its configured
profile still be default immediately before invocation. An unavailable/incomplete backup
stops further activation; never substitute a guessed previous rule set. Local restoration
does not revert the Git profile: record worker alerts as not-live-applied, and do not run
ordinary Monitoring apply again until a reviewed follow-up disables the worker profile
or successfully resumes the intended activation.

Cleanup research: created groups set deletionProtection=true. Installed `yc compute
instance-group update --help` has no dedicated disable-deletion-protection switch;
do not invent `--deletion-protection=false`. Official API supports a narrow PATCH on
`/compute/v1/instanceGroups/{receipt-id}` with body
`{"updateMask":"deletionProtection","deletionProtection":false}`. Prefer this exact
field mask over reapplying a full stale instance template merely to permit deletion.
The final command must capture the existing profile token without printing it, verify
receipt ID/folder/name/ownership labels, record the operation before proceeding, and
reconcile it to completion. Only then delete the two receipt-owned groups, sequentially:

With OPPORTUNISTIC, unprotect can remain pending if the group already targets zero
but the unused VM is still running. Before submission, capture exact group/member/boot
disk IDs and verify remote claims fenced, zero current remote leases and local serving.
If this same operation waits for that exact unused member, the approval includes one
bounded stop of that receipt-owned VM (bulk or selfie), never the canonical VM:

```sh
yc --profile default --retry 0 compute instance stop --id "$WORKER_ACTIVATION_CLEANUP_INSTANCE_ID" --folder-id b1gvs3prcd72lnivvdea --async --format json
```

Write an exclusive submission-uncertain receipt before that stop; populate and reconcile
its operation ID. Do not stop a successor or retry the PATCH. Re-read membership after
any uncertainty, and retain access needed to finish cleanup. Stop is conditional recovery
only, not permission to kill a successful serving worker.

Before each protection change, write a private recovery receipt naming the pool, exact
group ID and `submission_uncertain`; keep it if the response is lost. Execute this once
with WORKER_ACTIVATION_CLEANUP_POOL=bulk and once with =selfie, separately, reconciling
each returned operation before moving on. Never loop/retry uncertain calls automatically.
This is the narrow operator recovery API command, not a product release bypass:

```sh
WORKER_ACTIVATION_CLEANUP_POOL=bulk .venv/bin/python - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path('deploy/worker-pools').resolve()))
from provision import Cloud, LABELS, identifier, write_receipt

pool = os.environ['WORKER_ACTIVATION_CLEANUP_POOL']
assert pool in {'bulk', 'selfie'}
receipt = json.loads(Path('.superpowers/sdd/retry-preparation/worker-provision-receipt.json').read_text())
assert receipt['folder_id'] == 'b1gvs3prcd72lnivvdea'
assert receipt['canonical_folder_id'] == 'b1g2qttgfhb4gdunvlge'
group_id = identifier(receipt['groups'][pool]['id'])
token = subprocess.run(['yc', '--profile', 'default', 'iam', 'create-token'], check=True, capture_output=True, text=True, timeout=20).stdout.strip()
assert token and not any(char.isspace() for char in token)
cloud = Cloud(token)
group = cloud.get('instanceGroups/' + group_id, view='FULL')
assert group['id'] == group_id and group['folderId'] == 'b1gvs3prcd72lnivvdea'
assert group['name'] == 'findme-photo-worker-' + pool
assert group['labels'] == LABELS | {'pool': pool}
assert group['deletionProtection'] is True
recovery_path = Path('.superpowers/sdd/retry-preparation') / ('unprotect-' + pool + '-receipt.json')
recovery = {'pool': pool, 'group_id': group_id, 'folder_id': 'b1gvs3prcd72lnivvdea', 'state': 'submission_uncertain'}
write_receipt(recovery_path, recovery, exclusive=True)
operation = cloud.mutate('PATCH', 'instanceGroups/' + group_id, {'updateMask': 'deletionProtection', 'deletionProtection': False})
recovery.update(state='submitted', operation_id=identifier(operation['id']))
write_receipt(recovery_path, recovery)
print(json.dumps(recovery))
PY
```

The second exact invocation differs only in the leading assignment
`WORKER_ACTIVATION_CLEANUP_POOL=selfie`. After terminal success and read-back false:

```sh
yc --profile default --retry 0 compute instance-group delete --id "$WORKER_ACTIVATION_BULK_GROUP_ID" --folder-id b1gvs3prcd72lnivvdea --async --format json
yc --profile default --retry 0 compute instance-group delete --id "$WORKER_ACTIVATION_SELFIE_GROUP_ID" --folder-id b1gvs3prcd72lnivvdea --async --format json
```

Those lines are conditional recovery, not permission to delete any successful serving
fleet. Fence/drain/local recovery and terminal operation reconciliation come first.
Read all worker-folder VM/disk pages afterward; absence of a group alone does not prove
disk cleanup. Retained disks require exact instance/boot-disk ownership receipt and a
separate explicit cleanup entry; never delete by folder-wide glob.
Sources: https://yandex.cloud/en/docs/compute/operations/instance-groups/enable-deletion-protection
and https://yandex.cloud/en/docs/compute/instancegroup/api-ref/InstanceGroup/update .

## Bounded provider and processing acceptance

Before customer cutover, with both remote pools paused and local processing serving,
read the exact bulk member and record its boot disk. The group must be the receipt-owned
bulk group; require one running member, no active remote leases, fresh collector data
and no pending provider operation. This single recreation is the provider-failure
rehearsal, not a second application release or permission to destroy processing work:

```sh
yc --profile default compute instance-group list-instances --id "$WORKER_ACTIVATION_BULK_GROUP_ID" --folder-id b1gvs3prcd72lnivvdea --format json
yc --profile default compute instance get --id "$WORKER_ACTIVATION_BULK_INSTANCE_ID" --folder-id b1gvs3prcd72lnivvdea --format json
yc --profile default --retry 0 compute instance-group rolling-recreate --id "$WORKER_ACTIVATION_BULK_GROUP_ID" --instance-ids "$WORKER_ACTIVATION_BULK_INSTANCE_ID" --folder-id b1gvs3prcd72lnivvdea --async --format json
```

The instance ID comes only from the validated group membership response. Save the
recreate operation before proceeding. OPPORTUNISTIC deliberately cannot stop a running
VM, and paused claims cannot spontaneously grant idle retirement. After a successful
recreate submission (not terminal completion), recheck the exact original member still
belongs to the group, remote claims are paused and no remote attempts hold leases; then
explicitly stop that one VM to trigger the pending replacement:

```sh
yc --profile default --retry 0 compute instance stop --id "$WORKER_ACTIVATION_BULK_INSTANCE_ID" --folder-id b1gvs3prcd72lnivvdea --async --format json
```

Save the stop operation separately, reconcile both exact operation IDs with
`yc --profile default operation get <receipt-operation-id> --format json`, and never
change the policy to PROACTIVE. Prepare private exclusive submission-uncertain receipts
before both commands, just as for other provider writes; populate operation IDs on
response and retain uncertain receipts after loss. If the original instance already
stopped/disappeared, reconcile the existing recreation instead of stopping its successor.
This forced stop is authorized only for the unused staged bulk member, never a serving
worker or canonical VM. The documented trigger is described in
[Yandex rolling recreation](https://yandex.cloud/en/docs/compute/operations/instance-groups/rolling-recreate).
Do not retry an uncertain submission. Bound this
rehearsal to 20 minutes within the four-hour total; require replacement RUNNING, exact
candidate identity and fresh registration, old member gone, old boot disk absent from
the full disk inventory, and no more than three worker disks at any point. Do not
change size policies to repair a failure. Local processing remains available throughout.

After staged acceptance and alert notification proof, activate remote processing via
the pinned workflow above. Read-only canonical snapshots before/after each real job:

```sh
ssh -o BatchMode=yes -l petrnikitin 111.88.151.64 'sudo docker compose --env-file /opt/photo-prjct/.env -f /opt/photo-prjct/docker-compose.deployment.yml exec -T web python manage.py report_worker_pool_state --json'
ssh -o BatchMode=yes -l petrnikitin 111.88.151.64 'sudo docker compose --env-file /opt/photo-prjct/.env -f /opt/photo-prjct/docker-compose.deployment.yml exec -T web python manage.py report_worker_pool_telemetry --json'
yc --profile default compute instance-group list --folder-id b1gvs3prcd72lnivvdea --format json
yc --profile default compute instance list --folder-id b1gvs3prcd72lnivvdea --format json
yc --profile default compute disk list --folder-id b1gvs3prcd72lnivvdea --format json
```

The human supplies one legitimate small upload and a matching selfie through the
existing UI, and confirms the expected result. This is not an event-scoped canary:
all other eligible production jobs may run after cutover. No artificial DB enrollment,
historical reprocessing or new credentials are used. Allow at most 10 minutes per small
control job to reach its existing terminal state; unexpected failure, missing provenance,
unhealthy public endpoint or stale telemetry stops acceptance. Preserve only nonsecret
job IDs/timing/result counts in the receipt, not customer photos or embeddings.

Observe bulk returning to zero and its boot disk disappearing, then request a second
legitimate small upload to prove wakeup and accepted processing. Bound each idle/wakeup
observation to 20 minutes; failure to settle is a failed gate, not authority to enlarge
the window or edit native thresholds. Require selfie floor one throughout. Only then
may the pinned complete action remove original local recovery. Human notification
receipts and control inputs are still required; approval alone is not live evidence.

## Cleanup completion and ordinary deployment unblock

Successful abort/local health is not by itself complete cleanup. After both owned
groups, VMs and disks are proven absent, private8443 is closed, the runtime collector is
removed, IAM/network/Monitoring rollback is verified, inspect the release journal.
Archive it only if it has `phase=rolled-back-local`, `previous=null` and no active
recovery directory; preserve all other states for investigation. This explicit cleanup
prevents the failure that blocked ordinary Deploy after the first attempt:

```sh
sudo flock -n /opt/photo-prjct/.deployment.lock sh -c '
set -eu
test ! -e /opt/photo-prjct/.deployment-recovery
test -d /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01
test ! -e /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01/worker-pools-release.json
jq -e ".phase == \"rolled-back-local\" and .previous == null" /opt/photo-prjct/worker-pools-release.json >/dev/null
mv /opt/photo-prjct/worker-pools-release.json /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01/worker-pools-release.json
'
```

If supported abort already removed the active journal, record absence instead of running
the move. A different phase is a stop, not authority to erase the guard. An existing
recovery directory means recovery is unfinished and this command must not run.

After verifying the exact active sudoers content, with no collector/release remaining:

```sh
sudo test ! -e /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01/worker-metrics.sudoers.disabled
sudo mv /etc/sudoers.d/findme-worker-pool-metrics /var/lib/findme-worker-activation-bootstrap/retry-2026-10-01/worker-metrics.sudoers.disabled
sudo visudo -c
```

Retain both generations of recovery evidence and the inactive root helper/package.
Verify ordinary deployment preconditions read-only; do not launch an unrelated release
as a cleanup test. Record any billable or fenced remainder precisely.

## Authority and cost

The remaining execution gate is explicit approval of the actual reviewed PR/head and
this entire ledger, including ordinary local Deploy, receipt-derived final candidate
pins, the four-hour acceptance window, scoped abort/cleanup and successful steady-state
retention. Repository preparation authorizes none of these operations.
Initial1+1, cap bulk0..1 and selfie1..1; one serial replacement and at most three worker
disks. Maximum continuous steady VM+disk increment is 5648.45 RUB/730h at the refreshed
2026-10-01 rates; conservative four-hour compute+disk allowance53.98 RUB is not a total
billing cap. Native/Prometheus metrics and traffic are additional and usage-dependent.
No backfill, model or pgvector change. Preserve PR250's deployed schema and behavior.
