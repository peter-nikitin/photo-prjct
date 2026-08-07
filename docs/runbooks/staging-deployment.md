# Staging deployment

The staging workflow deploys immutable application and worker images from `main` to the existing
single-VM Compose stack. It does not install repository files as root. The root-owned selfie
observability package is a separate reviewed operator action.

## Ordinary automatic deployment

For a `main` push that does not change the privileged observability package, **Deploy staging**
builds immutable images and applies them automatically. Review the workflow result and perform the
acceptance checks below. A manual **Deploy staging** dispatch uses the same deployment path; leave
`configure_monitoring_agent` disabled unless the dispatch is only for that separate operation.

## Controlled privileged-package pause

The workflow classifies a push as requiring a pause when the push range changes either
`deploy/bootstrap-selfie-observability.sh` or `deploy/selfie-observability/**`. A first push with
an all-zero `before` SHA also pauses. This is conservative: it never guesses that a privileged
package is unchanged when no comparison range exists.

The classifier succeeds and writes the required action to the workflow summary. Build and deploy
are intentionally skipped, so this is a visible release pause rather than a failed application
deployment. The classifier does not read staging secrets, connect to the VM, or install anything.

Before bootstrapping, an operator must ensure `/opt/photo-prjct` contains the reviewed paused
commit named in the workflow summary. Use the established operator-controlled checkout/update
procedure and confirm its commit; the automatic paused workflow has deliberately not copied the
new package to the VM.

From a host with existing operator access, perform the reviewed action:

```bash
ssh -l petrnikitin 111.88.151.64
cd /opt/photo-prjct
DEPLOY_ROOT=/opt/photo-prjct sh /opt/photo-prjct/deploy/bootstrap-selfie-observability.sh
sudo /usr/local/sbin/findme-selfie-observability verify
```

The bootstrap copies validated inputs to root-owned paths and installs only the narrow helper
permission. Do not create a temporary SSH key, use the normal deployment user to run the bootstrap
as root, or add automatic root installation to GitHub Actions.

After `verify` succeeds, manually dispatch **Deploy staging** with
`configure_monitoring_agent=false`. That run copies the ordinary deployment files, builds the
immutable images, and applies the application release. Do not bypass the manual dispatch by
running `deploy/apply-deployment.sh` outside the workflow.

## Migration-preflight or migration failure

Treat a migration-preflight failure as a stopped release. Do not use `--fake`, `DROP ... IF
EXISTS`, renaming, renumbering, editing, or squashing a migration merely to retry. Inspect the
candidate against staging's recorded migration ledger and follow the
[Django migration-conflict runbook](django-migration-conflicts.md). Any recovery that changes the
ledger must be reviewed as a specific compatibility decision before a new deployment is dispatched.

## Failed deployment and rollback

`deploy/apply-deployment.sh` preserves the prior deployment profile and attempts rollback after a
post-mutation failure. A red application deployment is not proof that staging is down, and a green
rollback is not proof that the candidate was activated. Preserve the workflow URL and the named
failed phase, then verify the prior state before deciding on a corrected retry.

## Deployment issue notification

For an automatic `main` deployment, the workflow maintains at most one open GitHub issue titled
`[staging deployment] main is not deployed`. The first failed build or deploy creates it; later
failed runs add a bounded update to the same exact-title issue. A later successful deployment adds
its update and closes that issue. If there is no matching open issue, success does nothing.

The issue is a notification aid, not deployment state. The build and deploy job conclusions,
`DEPLOY_RESULT` marker, rollback result, and acceptance checks remain authoritative. A notification
failure emits an Actions warning but cannot make a healthy deployment red or make a failed
deployment green. The reconciler reads only the first 100 open repository issues and matches the
fixed title exactly, so unrelated issues and pull requests are not touched.

Issue updates contain only the immutable 40-character commit SHA, Actions run URL, enumerated
deployment phase, and UTC time. They contain no token, application secret, raw deploy log, VM
detail, database value, or storage data. Build failures report phase `build`; failed deploys use the
last exact `DEPLOY_PHASE` marker from the failed log, or `unknown` if no valid marker is available.

## Notification validation drill

Use the manual **Deploy staging** workflow with `validate_deploy_issue=true` to exercise the
notification path. This route still requires the existing `staging` environment approval, skips the
application build and deploy jobs, and receives no VM, database, application, or storage secret.
It creates, updates, then closes only `[staging deployment validation] notification drill`, and
finally checks that no issue with that validation title remains open. It never reads or mutates the
production notification issue.

If the final validation assertion fails, retain the workflow URL and inspect the validation issue
manually. Do not use the validation title as evidence that production staging is unhealthy, and do
not close the production issue through the drill.

## Acceptance checks

After an ordinary deployment or corrected manual retry, check the deployed marker, Compose health,
root-owned observability package, application-level observability, and public health. Compare the
marker with the merge commit's immutable image SHA.

```bash
ssh -l petrnikitin 111.88.151.64 'sudo cat /opt/photo-prjct/deployed-image'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sudo env DEPLOYMENT_TARGET=staging docker compose --project-name photo-prjct-staging --env-file .env -f docker-compose.prod.yml -f docker-compose.https.yml ps'
ssh -l petrnikitin 111.88.151.64 'sudo /usr/local/sbin/findme-selfie-observability verify'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sh deploy/verify-selfie-observability.sh'
curl -fsS https://findme-photo.ru/health/
```

Accept the release only when `deployed-image` matches the requested immutable image, the expected
Compose services are healthy, both observability verifications succeed, and public health returns
`{"status": "ok"}`. If any check fails, keep the release unaccepted and investigate without
changing application data, storage, or root-owned package contents speculatively.
