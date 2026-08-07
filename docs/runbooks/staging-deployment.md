# Staging deployment

The staging workflow deploys immutable application and worker images from `main` to the existing
single-VM Compose stack. It does not install repository files as root. The root-owned selfie
observability package is a separate reviewed operator action.

## Ordinary automatic deployment

For a `main` push that does not change the privileged observability package, **Deploy staging**
builds immutable images and applies them automatically. Review the workflow result and perform the
acceptance checks below. An application deployment dispatched manually must supply an exact
40-character commit as `deployment_sha`; the workflow rejects a missing, malformed, unavailable,
or non-commit object instead of silently deploying the moving `main` tip. Leave
`verify_paused_observability_release=false` for an ordinary manual deployment, and leave
`configure_monitoring_agent` disabled unless the dispatch is only for that separate operation.

## Controlled privileged-package pause

The workflow classifies a push as requiring a pause when the push range changes either
`deploy/bootstrap-selfie-observability.sh` or `deploy/selfie-observability/**`. A first push with
an all-zero `before` SHA also pauses. This is conservative: it never guesses that a privileged
package is unchanged when no comparison range exists.

The classifier succeeds and writes the required action to the workflow summary. Build and deploy
are intentionally skipped, so this is a visible release pause rather than a failed application
deployment. The classifier does not read staging secrets, connect to the VM, or install anything.

After staging approval, the separate **Stage paused observability source** job checks out the exact
paused SHA, creates a source checksum manifest, verifies that manifest against the classifier's
digest, and copies only the bootstrap script, observability package, SHA marker, and checksum
manifest. It writes them below the SHA-specific path
`/opt/photo-prjct/privileged-observability-releases/<paused-sha>/`. It does not build an image,
copy application or Compose files, read application secrets, run SSH commands, install root-owned
files, change `.env`, or change `deployed-image`.

Wait for that staging job to succeed. Then use the concrete paused SHA and manifest digest printed
in the classifier's workflow summary; do not substitute the mutable `/opt/photo-prjct/deploy`
source. The summary provides these exact commands with both placeholders filled:

```bash
ssh -l petrnikitin 111.88.151.64
cd /opt/photo-prjct/privileged-observability-releases/<paused-sha>
test "$(cat staging-observability-release-sha)" = "<paused-sha>"
printf '%s  staging-observability-source.sha256\n' '<manifest-sha256>' | sha256sum --check -
sha256sum --check staging-observability-source.sha256
DEPLOY_ROOT=/opt/photo-prjct/privileged-observability-releases/<paused-sha> sh /opt/photo-prjct/privileged-observability-releases/<paused-sha>/deploy/bootstrap-selfie-observability.sh
sudo /usr/local/sbin/findme-selfie-observability verify
```

The SHA check rejects a wrong staged release. The two checksum checks bind the transferred source
files to the manifest emitted for that exact reviewed commit before the bootstrap copies validated
inputs to root-owned paths and installs the narrow helper permission. Do not create a temporary SSH
key, copy a different checkout into the staged path, use the normal deployment job to install files
as root, or add automatic root installation to GitHub Actions.

After `verify` succeeds, run the exact dispatch command printed in the paused workflow summary:

```bash
gh workflow run deploy.yml --ref main -f deployment_sha=<paused-sha> -f verify_paused_observability_release=true
```

The manual workflow validates that `deployment_sha` is the exact commit object, checks it out for
both build and deploy, tags both images with it, and copies deployment source from it. Before the
normal copy or any application mutation, the staging SSH preflight verifies the SHA marker,
classifier manifest digest, staged file checksums, installed root-owned package equality, and
root-helper health for the same SHA. Do not omit the verification flag for a controlled-pause
retry, dispatch a branch name instead of the SHA, or bypass the workflow by running
`deploy/apply-deployment.sh` directly.

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

For an automatic `main` deployment or an explicit manual application retry using `deployment_sha`,
the workflow maintains at most one open GitHub issue titled
`[staging deployment] main is not deployed`. The first failed build or deploy creates it; later
failed runs add a bounded update to the same exact-title issue. A later successful automatic or
manual application deployment adds its update and closes that issue. Monitoring-only and
notification-validation dispatches do not run production reconciliation. If there is no matching
open issue, success does nothing.

Before reading or changing the production issue, the reconciler reads the repository's current
`main` head through the GitHub API. If the deployment SHA is no longer that head, the run is stale
and reconciliation exits successfully after that single lookup. It does not list, create, comment
on, or close the production issue. This prevents an older workflow that finishes late from
overwriting the notification state for a newer `main`. A manual retry can close the production
issue only when its exact `deployment_sha` is still the current `main` head.

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
production notification issue and does not perform the production `main`-head guard lookup.

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
