# Canonical deployment

The customer-serving Yandex Cloud VM is the single unqualified canonical deployment. `main`
delivers through the GitHub Actions workflow **Deploy**, using Compose project `photo-prjct` and
the `docker-compose.deployment.yml` and `docker-compose.https.yml` overlays. It is not a staging
or promotion target; this repository has no GitHub Environment deployment boundary.

## Ordinary automatic deployment

A `main` push classifies web (including Commerce), import and photo-worker inputs separately.
Changed components build immutable SHA images and then advance their own GHCR `latest` pointers:
`photo-prjct:latest`, `photo-prjct-import-worker:latest` and
`photo-prjct-worker:latest`. Commerce uses the web image. Documentation-only changes build no
image and contact no application host. A web change hands off Django on the canonical VM; an import
change reconciles import; a photo-worker change activates running pool members through CI. Mixed
changes perform only the affected actions. The worker model/dependency base is reused by input
hash. Publication and deployment are serialized. A failed release needs an explicit retry; a later
documentation push does not retry it.

The canonical web image is pulled and pinned to its local image ID before the handoff, so a later
`latest` move cannot change the candidate. Deployment has two equivalent Django services, `web`
and `web-next`. The deployment setup runs once, starts the unselected slot without mutating the
database on service startup, verifies its health, switches the existing Nginx public/private/import
upstream by graceful reload, and drains accepted requests on the predecessor before stopping it.
Shared, versioned static assets remain available across the switch. On first activation from the
old single-slot layout, `web` keeps serving while `web-next` warms; Nginx and PostgreSQL stay up.

A manual deployment must supply
an exact 40-character commit as `deployment_sha`; the workflow rejects a missing, malformed,
unavailable, or non-commit object. Leave `verify_paused_observability_release=false` for an
ordinary retry. The selected commit is classified against its parent, preserving web-only or
worker-only retries. Leave `configure_monitoring_agent` disabled unless that separate operation is
the purpose of the dispatch.

Review the **Deploy** workflow result and run the acceptance checks below. Do not SSH to invoke
`deploy/apply-deployment.sh` directly or use a mutable checkout as a deployment source.

The one-time worker access and updater installation is a separate operation before merging the
worker-changing package, after claims are paused/drained. It installs the public half of the
existing CI `VM_SSH_KEY` for the restricted worker user, limits worker SSH ingress to the canonical
VM's security-group ID and explicitly recreates the sole selfie managed instance at cap one; bulk
remains at zero. Prove a one-shot activation of the current worker image before the new image and
web protocol are published. The
[worker-pool runbook](worker-pools.md#one-time-ci-push-cutover-at-cap-one) covers this transition.
Ordinary Deploy does not change group templates or recreate VMs. A worker-changing Deploy requires
that private access cutover to have been completed and verified.
The historical fleet receipt and shared web/worker SHA are not deployment gates.

After a release commits, the apply script removes Docker images unused by any container
from the canonical VM. It skips this cleanup on failed deployments. The workflow prints
`DEPLOY_IMAGE_PRUNE_RESULT=success` or `failure`.
A cleanup failure does not change the committed release result;
the next deployment retries cleanup. An earlier immutable release image can be pulled again from
GHCR for an operator-requested rollback.

## Commerce worker and Postbox email

The Commerce worker is activated only by the canonical **Deploy** workflow. Keep the paid feature
flags (`paid-events`, `paid-watermarked-previews`, `paid-photo-cart`, `paid-photo-purchase`, and
`paid-photo-payment-simulator`) in Django Admin state `staff` for staff acceptance; deployment
configuration is not the public exposure control.

Set repository variables for the non-secret runtime contract:

```text
COMMERCE_WORKER_ENABLED=True
COMMERCE_PUBLIC_ORIGIN=https://findme-photo.ru
COMMERCE_PAYMENT_GATEWAY_FACTORY=commerce.payment_simulator.payment_simulator_gateway_factory
COMMERCE_EMAIL_SENDER_FACTORY=commerce.postbox_email_sender.postbox_email_sender_factory
COMMERCE_WORKER_FACTORY=commerce.runtime.commerce_worker_factory
COMMERCE_EMAIL_FROM_ADDRESS=orders@findme-photo.ru
COMMERCE_SUPPORT_CONTACT=support@findme-photo.ru
COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS=300
```

`COMMERCE_ORDER_ACCESS_SIGNING_SECRET`, `COMMERCE_POSTBOX_API_KEY_ID`, and
`COMMERCE_POSTBOX_API_KEY_SECRET` are Lockbox payload entries for the `deploy` consumer only. Do
not store them as GitHub variables or pass them in workflow inputs. Production Compose projects
Postbox credentials only to `commerce-worker`; the web container receives the order signing
secret, support contact, public origin, and non-secret factory settings needed for checkout and
order-return pages.

Those three Lockbox entries are optional only while `COMMERCE_WORKER_ENABLED=False`, so the dark
main deploy remains merge-compatible before Postbox credentials are provisioned. The enabled apply
path fails closed before any deployment mutation when any of them is absent, or when
`COMMERCE_PUBLIC_ORIGIN` is anything other than exactly `https://$PUBLIC_DOMAIN`.

Before enabling the worker, verify the Postbox sender identity and DNS authentication outside this
runbook's application deployment step. If readiness fails, Deploy reports a failed release. Once
Commerce profile reconciliation has begun, the previous profile is not automatically restored;
read back the selected web slot, actual Commerce containers and health before recovery. A readiness
failure alone is not evidence that Order, grant, delivery or feature-flag rows changed.

## Controlled privileged-package pause

The workflow pauses when its push range changes `deploy/bootstrap-selfie-observability.sh` or
`deploy/selfie-observability/**`; an all-zero first-push base also pauses. The classifier writes
the required action to the workflow summary and skips build and deployment.

After approval, first dispatch the exact reviewed SHA to **Stage privileged observability source**:

```bash
gh workflow run deploy.yml --ref main -f deployment_sha=<paused-sha> -f stage_paused_observability_release=true
```

That job checks out the exact paused SHA, creates a checksum manifest, and copies only reviewed files below
`/opt/photo-prjct/privileged-observability-releases/<paused-sha>/`. It does not read application
secrets, change `.env`, build an image, or change `deployed-image`.

Use the exact SHA and manifest digest printed in the summary:

```bash
ssh -l petrnikitin 111.88.151.64
cd /opt/photo-prjct/privileged-observability-releases/<paused-sha>
test "$(cat observability-release-sha)" = "<paused-sha>"
printf '%s  observability-source.sha256\n' '<manifest-sha256>' | sha256sum --check -
sha256sum --check observability-source.sha256
DEPLOY_ROOT=/opt/photo-prjct/privileged-observability-releases/<paused-sha> sh /opt/photo-prjct/privileged-observability-releases/<paused-sha>/deploy/bootstrap-selfie-observability.sh
sudo /usr/local/sbin/findme-selfie-observability verify
```

Then dispatch the exact reviewed SHA:

```bash
gh workflow run deploy.yml --ref main -f deployment_sha=<paused-sha> -f verify_paused_observability_release=true
```

Do not create a temporary SSH key, install the root-owned package through the normal deployment
job, or bypass the workflow.

## Migration-preflight or deployment failure

Treat a migration-preflight failure as a stopped release. Do not use `--fake`, destructive SQL,
renumbering, editing, or squashing migrations merely to retry. Inspect the deployed VM's recorded
migration ledger and follow the [Django migration-conflict runbook](django-migration-conflicts.md).

`deploy/apply-deployment.sh` permits automatic web recovery only when the previous web's bounded,
read-only `ProcessingAttempt` query succeeds against the current database. Before
`processing.0016` drops `ProcessingAttempt.worker_build`, a schema-compatible previous web can be
restored after that probe. Once the drop has committed, the old web is incompatible even if the
overall migration command reports failure; the same probe blocks its restoration. A failed or
uncertain probe preserves the observed selected route and slots without claiming they are healthy,
retains the candidate package and `.deployment-recovery`, and saves the candidate inputs as
`.deployment-recovery/candidate.env` (mode 0600). Keep claims paused and recover
forward with a compatible new-protocol candidate or code fix. Do not claim that an old SHA is a safe
rollback after the column drop, and preserve the snapshot until forward recovery is verified.

Use the explicit canonical forward-recovery dispatch, never delete/rename the recovery gate or
invoke the application script manually. After reviewing the exact compatible new-protocol SHA,
run (replace the placeholder with its complete 40-character commit SHA):

```sh
gh workflow run deploy.yml --ref main -f deployment_sha=<EXACT_COMPATIBLE_SHA> -f recover_forward=true
```

This mode publishes the reviewed web image only, acquires the canonical deployment lock, and
consumes the retained private `candidate.env`. It keeps the candidate's configuration, secrets and
import image identity; only the approved web release identity and transient registry credential
come from the dispatch.
The original candidate SHA can be retried, or a reviewed compatible forward-fix SHA can be selected.
Before package replacement, recovery validates the image's OCI SHA, build-independent model/claim
interface, current DB compatibility and both pools' paused claims. Native AdaFace capability and
normal migration, import, Commerce, public health and observability checks still apply.
Any failed/uncertain recovery retains the original snapshot, installed compatible candidate,
selected route and observed slot state for inspection; it never restores an incompatible pre-cutover
image. Only verified commit
removes `candidate.env`, the recovery gate and retained predecessor package. Leave all unrelated
dispatch options disabled. Read back the recovered image/health with the acceptance commands below
before continuing the one-time worker cutover and explicitly unpausing claims.

For an ordinary handoff failure before Nginx switches, the selected predecessor remains serving.
After a switch, recovery may select the still-running predecessor only when its schema probe proves
compatibility. An uncertain switch or failed predecessor drain retains the selected route and both
slots for inspection; do not stop a slot that may own accepted requests. The workflow's
`DEPLOY_RESULT` phase and rollback field identify what was attempted. A completed release is
reverted through an explicit compatible image deployment using the same workflow. PostgreSQL
upgrades, incompatible migrations, certificate reissue, VM reboot and resize are maintenance
operations outside the ordinary zero-downtime handoff.

A red workflow is not proof that the VM is unavailable, and a green rollback
is not proof that the candidate was applied. Preserve the workflow URL and named failed phase,
then verify the previous state before a corrected retry.

## Deployment issue notification

For an automatic `main` deployment or an exact-SHA retry, the workflow maintains at most one open
GitHub issue titled `[deployment] main is not deployed`. A successful current-main deployment
updates and closes it. Monitoring-only and notification-validation dispatches do not reconcile this
issue. The issue is a notification aid, not deployment state: job conclusions, `DEPLOY_RESULT`,
rollback evidence, and the checks below remain authoritative.

Issue updates contain only the immutable SHA, Actions URL, enumerated phase, and UTC time. They
must not contain a token, secret, raw deployment log, VM detail, database value, or storage data.

## Acceptance checks

After an ordinary deployment or corrected retry, verify the workflow's final `DEPLOY_RESULT`,
selected Nginx slot, actual running image, Compose health, observability package, application-level
observability, and public health. On the canonical VM, these checks are read-only:

```bash
ssh -l petrnikitin 111.88.151.64 'sudo cat /opt/photo-prjct/deployed-image'
ssh -l petrnikitin 111.88.151.64 'sudo python3 /opt/photo-prjct/deploy/web-slot.py --root /opt/photo-prjct selected'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps'
ssh -l petrnikitin 111.88.151.64 'sudo /usr/local/sbin/findme-selfie-observability verify'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sh deploy/verify-selfie-observability.sh'
curl -fsS https://findme-photo.ru/health/
```

To compare the actual selected web container with the committed local image ID, run on the
canonical VM after the first two-slot deployment:

```sh
cd /opt/photo-prjct
selected_slot="$(sudo python3 deploy/web-slot.py --root /opt/photo-prjct selected)"
selected_container="$(sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps -q "$selected_slot")"
sudo docker inspect --format '{{.Image}} {{.State.Status}} {{.State.Health.Status}}' "$selected_container"
sudo cat deployed-image
```

If `COMMERCE_WORKER_ENABLED=True`, also run:

```bash
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sh deploy/run-commerce-worker-health.sh'
```

For a web release, compare the selected slot's container image ID with `deployed-image` and the
workflow's requested image; inspect the predecessor and deployment drain phase to ensure accepted
requests finished before it stopped. Confirm Nginx and PostgreSQL were not restarted during the
handoff from the workflow and bounded container events; a green workflow alone cannot establish
live zero downtime. For import and worker releases, check their own running image IDs rather than
assuming the web marker represents them. The [worker-pool runbook](worker-pools.md) covers private
API readiness, serving generation and scale-from-zero. Accept only when expected services are
healthy, the Commerce health command succeeds when enabled, observability checks succeed and
public health returns `{"status": "ok"}`. If a check fails, investigate without speculative
application-data, storage or root-package mutation.
