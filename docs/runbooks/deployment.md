# Canonical deployment

The customer-serving Yandex Cloud VM is the single unqualified canonical deployment. `main`
delivers through the GitHub Actions workflow **Deploy**, using Compose project `photo-prjct` and
the `docker-compose.deployment.yml` and `docker-compose.https.yml` overlays. It is not a staging
or promotion target; this repository has no GitHub Environment deployment boundary.

## Ordinary automatic deployment

A `main` push that does not change the privileged observability package builds immutable application
and worker images and applies them to the deployed VM. A manual application deployment must supply
an exact 40-character commit as `deployment_sha`; the workflow rejects a missing, malformed,
unavailable, or non-commit object. Leave `verify_paused_observability_release=false` for an
ordinary retry and leave `configure_monitoring_agent` disabled unless that separate operation is
the purpose of the dispatch.

Review the **Deploy** workflow result and run the acceptance checks below. Do not SSH to invoke
`deploy/apply-deployment.sh` directly or use a mutable checkout as a deployment source.

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
runbook's application deployment step. If readiness fails, Deploy restores the previous Compose
profile and does not alter Order, grant, delivery, or feature-flag rows.

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

`deploy/apply-deployment.sh` preserves the previous profile and attempts rollback after a
post-mutation failure. A red workflow is not proof that the VM is unavailable, and a green rollback
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

After an ordinary deployment or corrected retry, verify the deployed marker, Compose health,
observability package, application-level observability, and public health:

```bash
ssh -l petrnikitin 111.88.151.64 'sudo cat /opt/photo-prjct/deployed-image'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sudo docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml -f docker-compose.https.yml ps'
ssh -l petrnikitin 111.88.151.64 'sudo /usr/local/sbin/findme-selfie-observability verify'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sh deploy/verify-selfie-observability.sh'
curl -fsS https://findme-photo.ru/health/
```

If `COMMERCE_WORKER_ENABLED=True`, also run:

```bash
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sh deploy/run-commerce-worker-health.sh'
```

Accept the release only when `deployed-image` matches the requested immutable image, expected
Compose services are healthy, the Commerce worker health command succeeds when enabled,
observability checks succeed, and public health returns `{"status": "ok"}`. If a check fails,
investigate without speculative application-data, storage, or root-package mutation.
