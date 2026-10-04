# Automatic host reconciliation foundation

Shared deployment workflow/transport changes are orchestration inputs and publish no images on
their own. They are consumed by the next release selected by effective Docker/application inputs.

Main merges select cloud, canonical host, public exporter/probe and image-origin agent inputs
independently. Cloud uses the existing Monitoring workflow/environment identity. Host changes use
only the existing Deploy, Deploy public health probe and Deploy isolated image origin OIDC
identities and SSH transports. Image-origin automatic runs set `monitoring_only=true` and never
restart its application containers. Documentation and test changes contact no host/cloud resource.

`reconcile.py` is an operator-installed root-owned entrypoint. Its only argument is a lowercase
40-character main commit SHA. It fetches `refs/heads/main` from the fixed project GitHub HTTPS URL
into a private root-owned bare repository, checks commit ancestry and reads only the allowlisted
regular Git blobs. It executes no script from `/opt/photo-prjct`, runner archive, or mutable staging
owned by deploy. It records the exact SHA/file manifest, requires the configured VM metadata
identity and existing files/units, retains a backup, applies the existing packages, and restores
owned files/unit states on failure. It does not build images or manage product containers.
The applied collectors still observe the existing canonical deployment tree.

## One-time operator steps before merge activation

These steps change privileged host authorization and are deliberately not executed by the PR.
On each existing canonical/public VM, using an operator account with sudo:

1. Review the exact merged helper revision and install it root-owned, mode `0755`, as
   `/usr/local/sbin/findme-observability-reconcile`.
2. Create `/etc/findme-observability-reconcile.json`, owned by root, mode `0600`, with exactly
   `role` (`canonical` or `public`), `instance_id` (the existing VM metadata ID),
   `folder_id` (`b1g2qttgfhb4gdunvlge`) and `workspace_id` (`mon0c97qv2s5uju1ark8`).
   Review these identities against the existing host and approved cloud resources.
3. Confirm `/usr/bin/python3`, Git, PyYAML, Unified Agent and all currently installed collector
   files/units are available. This reconciler provisions no missing package. For a private GitHub
   repository, provision root-only **read-only** Git authentication for the fixed HTTPS repository
   using the team's existing approved credential mechanism. Git credentials never come from deploy
   input or workflow environment. Test noninteractive fixed-repository `git ls-remote` as root.
4. Validate a root-owned sudoers fragment with `visudo -cf` before installing it mode `0440`:
   `deploy ALL=(root) NOPASSWD: /usr/local/sbin/findme-observability-reconcile *`
   Substitute the existing controlled SSH user if different. Grant no shell, interpreter,
   mutable script path or `SETENV`. The helper validates one exact SHA before network access.
5. Run the helper for the reviewed main SHA and check its receipt and fresh exporter samples.
   Keep the current existing telemetry installation in place until this succeeds.
6. In GitHub, remove the Monitoring environment's required human reviewer once, retain its
   main-only branch policy and exact-workflow OIDC binding. No workflow in this PR alters IAM,
   notification recipients, billable resources, or environment protection.

After foundation activation every selected main merge runs automatically. A foundation failure
fails the observability run visibly. It does not silently skip activation or mark the cloud/host
as reconciled. The installed bootstrap first authenticates the exact regular helper Git blob, then hands off
to that revision's transaction function under the same host lock. That function supplies this
run's allowlist, managed backup inventory and apply logic; it does not reacquire or release the
bootstrap lock. After successful package application the reviewed helper replaces the installed
entrypoint atomically. Its old version is backed up and restored if the transaction fails.

## Evidence and recovery

Cloud always uploads available backup artifacts on apply failure. Rule/dashboard read-back and
fresh evaluator snapshots are performed by `control.py`; routing PUT success remains distinct
from delivery proof. Host backups and `source.json`/`state.json` remain under
`/var/lib/findme-observability-reconcile/backup-SHA-TIMESTAMP`; `receipt.json` is written only after
successful apply. The exporter installer retains its own transactional backup as well.

For recovery, run the relevant manual workflow with an exact reviewed main revision, or invoke
the installed helper as an operator with that prior main SHA. The helper permits main ancestors
for this purpose. Failed application restores the previous owned files/unit states; restoration
failure requires operator inspection of the retained backup. A prior revision does not change
product data or queue authority. Manual probe disable/rollback and image-origin repair remain
explicit recovery actions. An unrelated cloud/host failure does not roll back Django or worker
releases. A combined Django release waits for host completion and then performs its existing
privileged-package identity check before application activation.

### Combined host and cloud changes

On a cloud-changing main push, Monitoring waits for each selected host job from the
fixed repository's `push` run on `main` at the exact same source SHA. The bounded
30-minute wait requires the host job itself to succeed; an unrelated application
job does not decide host readiness. Missing, failed, skipped, or cancelled required
host jobs stop cloud apply visibly. Cloud-only changes make no host-job API calls.
After selected host jobs succeed, a read-only six-minute fresh-sample preflight
allows the public agent's five-minute collection interval to complete before any
cloud write. A timeout stops apply. Manual reviewed-revision recovery retains its
existing explicit preflight and does not wait for historical host runs. No new
OIDC subject, IAM permission, or deployment credentials are introduced.
