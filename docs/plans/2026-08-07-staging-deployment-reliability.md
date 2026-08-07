# Staging Deployment Reliability Implementation Plan

- Date: 2026-08-07
- Status: Draft
- Owner: project maintainer
- Related specification: [staging deployment incident report](../postmortems/2026-08-07-staging-deployment-after-parallel-migrations.md)
- Related architecture: [current deployment architecture](../architecture.md#current-architecture--implemented), [accepted deployment constraints](../architecture.md#accepted-constraints)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md), [ADR 0005](../adr/0005-promote-images-through-staging.md)
- ADR impact: Conforms to ADR 0003 and ADR 0005. The work adds transition checks and recovery visibility around the existing GitHub Actions, GHCR, and single-VM Compose path; it does not change deployment topology, artifact promotion, privileged-host ownership, or rollback authority.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal

Prevent the two staging deployment failure classes recorded on 2026-08-07, fail before environment mutation when the deployed migration ledger is incompatible with the candidate image, and make every later deployment failure actionable without weakening rollback or privileged-host boundaries.

## Scope

Implement all P0–P2 actions from the incident report as one layered reliability increment:

- reject edits, deletion, and renaming of migration files already present on the pull request base;
- document the only supported parallel-migration resolution procedure;
- turn a root-owned selfie-observability package change into a successful controlled pause that requires the existing explicit operator bootstrap before manual deployment;
- compare the established database migration ledger with the candidate graph and print the candidate migration plan before mutation;
- emit stable sanitized deployment phase/result markers;
- create or update one GitHub issue when an automatic `main` deployment fails and close it after a later successful deployment.

Preserve the existing application, worker, database, Object Storage, observability, HTTPS, Compose, environment-secret, immutable-image, and rollback contracts. Do not introduce migration compatibility shims, tolerant schema SQL, a new service, a new VM credential, automatic root installation, or a deployment platform abstraction.

## Acceptance criteria

1. Pull-request CI fails when a numbered migration file present at the base SHA is modified, deleted, or renamed, and passes when a new migration or explicit merge migration is added without changing base migration identities.
2. Contributor guidance requires preserving merged migration filenames, inspecting leaves, adding an explicit merge node, and testing the base-frontier upgrade.
3. A push that changes the privileged observability package completes with a named controlled-pause result and skips image build and application deployment. The existing operator bootstrap plus a manual `workflow_dispatch` remains the only route forward.
4. An ordinary push with no privileged-package change follows the existing automatic build and deploy path.
5. Before `mutation_started=1`, the candidate image rejects any `(app, migration)` recorded in the established database but absent from the candidate migration graph, then prints `showmigrations --plan` for operator evidence. Neither check writes to the database.
6. Deployment logs expose the last entered sanitized phase and final success/failure plus rollback outcome without secrets, object keys, bearer paths, database values, or environment contents.
7. One open GitHub issue represents the current automatic staging deployment failure. Repeated failures append bounded evidence to that issue; a later successful deployment closes it. Notification failure never changes application deployment or rollback results.
8. Focused tests, the complete repository quality suite, a normal staging rollout, deployed-image verification, Compose health, observability verification, and public HTTPS health all pass.

## Global constraints

- Use Python 3.12, Django 6, the existing shell deployment entrypoint, GitHub Actions, GHCR, and Docker Compose; add no dependency.
- Run Python checks through `make test` or `make check`, not global tools.
- Treat every numbered migration already present on the PR base as immutable source and durable database identity.
- Keep `/usr/local/lib/findme-selfie-observability-package` and `/usr/local/sbin/findme-selfie-observability` root-owned and operator-updated.
- All pre-deploy checks added by this plan are read-only and run before `mutation_started=1`.
- Stable markers contain only enumerated phase/result values and rollback status.
- The notification path uses `GITHUB_TOKEN` with only `actions: read`, `contents: read`, and `issues: write`; it receives no staging environment secrets.

## Implementation

### Task 1: Protect merged migration identities and document parallel migration resolution

**Files:**

- Create: `scripts/check_migration_immutability.py`
- Create: `tests/test_migration_immutability.py`
- Create: `docs/runbooks/django-migration-conflicts.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_repository_foundation.py`
- Modify: `docs/engineering-jobs.md`

- **Specification:** incident report sections “An already applied migration identity was changed”, “Repository and CI tests used fresh migration history”, and both P0 actions.
- **Depends on:** None.
- **Produces:** CLI contract `python scripts/check_migration_immutability.py --base <sha> --head <sha>` with exit `0` for additive migration changes and exit `1` with sorted repository-relative paths for modified or removed base migration files.

- [ ] Add isolated temporary-Git-repository tests proving that an unchanged base migration plus a new leaf passes; modifying a base migration fails; deleting it fails; and a Git rename is treated as deletion plus addition and fails even when Git similarity detection would call it a rename. Include `__init__.py`, non-migration Python files, and newly added merge migrations as passing controls.
- [ ] Run `make test TESTS="tests/test_migration_immutability.py"` and confirm the new tests fail because the checker does not exist.
- [ ] Implement the checker with the standard library and `git diff --name-status --no-renames <base> <head> -- src/backend`. Match only `src/backend/<app>/migrations/[0-9]*.py`; reject `M` and `D`, allow `A`, print no file contents, and return `2` for invalid revisions or malformed Git output.
- [ ] Configure `actions/checkout` in `.github/workflows/ci.yml` with sufficient history and add a pull-request-only `Check migration immutability` step using `${{ github.event.pull_request.base.sha }}` and `${{ github.event.pull_request.head.sha }}`. Keep the existing `makemigrations --check --dry-run` step because graph/model drift and identity immutability are separate contracts.
- [ ] Extend `tests/test_repository_foundation.py` to assert the pull-request-only step, exact base/head inputs, checkout history, and preservation of the existing migration-drift check.
- [ ] Write `docs/runbooks/django-migration-conflicts.md` with the exact sequence: fetch `origin/main`; list leaves with `showmigrations`/`migrate --plan`; preserve every filename from the base; use `makemigrations --merge` or an explicit empty merge node; add later operations after the merge; test a database stopped at the base frontier upgrading to the candidate; never rename, renumber, edit, or squash a merged migration.
- [ ] Update EJ-002 in `docs/engineering-jobs.md` to include migration-identity protection and link the checker, tests, CI step, and runbook.
- [ ] Run `make test TESTS="tests/test_migration_immutability.py tests/test_repository_foundation.py"`; expect all selected tests to pass and the checker fixture to report only path-level diagnostics.

### Task 2: Turn privileged-package changes into a controlled release pause

**Files:**

- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_repository_foundation.py`
- Create: `docs/runbooks/staging-deployment.md`
- Modify: `README.md`

- **Specification:** incident report sections “The observability package required a separate privileged release step”, “Deployment verified the observability package late”, P1 “classify privileged-package changes before deployment”, and the explicit non-action against automatic root overwrite.
- **Depends on:** Task 1's contributor guidance only; no runtime interface dependency.
- **Produces:** workflow job output `requires_observability_bootstrap` and two terminal workflow paths: ordinary automatic deployment or successful controlled pause followed by the existing operator bootstrap and manual dispatch.

- [ ] Add workflow-contract tests asserting that a push classifier checks the range `${{ github.event.before }}..${{ github.sha }}` for `deploy/bootstrap-selfie-observability.sh` and `deploy/selfie-observability/**`, exports `requires_observability_bootstrap`, and writes the exact operator action to `$GITHUB_STEP_SUMMARY` without reading staging secrets.
- [ ] Add tests asserting that push-triggered build/deploy jobs are skipped when the output is `true`, run when it is `false`, and always run for an explicit deployment `workflow_dispatch`. Preserve the separate monitoring-agent dispatch conditions.
- [ ] Update the deploy workflow checkout for the classifier with history sufficient for the push range. Treat an all-zero `github.event.before` as a required operator pause rather than guessing that the package is unchanged.
- [ ] Implement `Classify staging release`: use `git diff --quiet` on the exact privileged-package paths, export a lowercase `true`/`false`, and print a concise controlled-pause summary containing the commit SHA, `ssh -l petrnikitin 111.88.151.64`, `DEPLOY_ROOT=/opt/photo-prjct sh /opt/photo-prjct/deploy/bootstrap-selfie-observability.sh`, the root-helper `verify` command, and the manual deploy workflow name. Do not execute SSH or install anything in this job.
- [ ] Make build and deploy depend on the classifier and use explicit conditions for push, ordinary manual deployment, and monitoring-only dispatch. A privileged-package push must finish green with build/deploy marked skipped so that the release is visibly paused rather than reported as a failed application deployment.
- [ ] Write `docs/runbooks/staging-deployment.md` covering ordinary automatic deploy, controlled privileged-package pause, operator bootstrap, manual retry, migration-preflight failure, rollback verification, deployed-image/Compose/observability/public-health acceptance, and the prohibition on temporary SSH keys or automated root installation. Link it from `README.md`.
- [ ] Run `make test TESTS="tests/test_repository_foundation.py"`; expect workflow parsing and all existing staging/monitoring dispatch contracts to pass.

### Task 3: Validate the deployed migration ledger and expose deployment phases before mutation

**Files:**

- Create: `src/backend/picflow/management/commands/verify_migration_history.py`
- Create: `src/backend/picflow/tests/test_verify_migration_history_command.py`
- Modify: `deploy/apply-deployment.sh`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/test_repository_foundation.py`

- **Specification:** incident report P1 “add a read-only pre-deploy phase”, P2 “improve phase observability”, and the explicit non-actions against tolerant schema SQL and general migration compatibility layers.
- **Depends on:** Task 1 defines migration identity as the protected contract; Task 2 documents the operator-visible deployment sequence.
- **Produces:** read-only Django command `manage.py verify_migration_history`; log markers `DEPLOY_PHASE=<enumerated-value>` and `DEPLOY_RESULT=<success|failure> phase=<value> rollback=<not-needed|succeeded|failed>`.

- [ ] Add command tests with `MigrationLoader` and `MigrationRecorder` fixtures for: every applied migration present on disk; one applied migration missing from disk; multiple missing identities sorted deterministically; unapplied disk migrations allowed; database/loader errors returning a sanitized `CommandError`. Assert no migration executor or schema editor is called.
- [ ] Run `make test TESTS="src/backend/picflow/tests/test_verify_migration_history_command.py"` and confirm failure because the command does not exist.
- [ ] Implement the command using Django's default database connection, `MigrationLoader(connection, ignore_no_migrations=True)`, and `MigrationRecorder(connection).applied_migrations()`. Fail with application label and migration name only when an applied identity is absent from `loader.disk_migrations`; print `migration-history-ok` on success; never call `migrate`, mutate the recorder, or inspect application rows.
- [ ] Extend the deployment test harness so the candidate one-off command can return success, a missing applied migration, or a database error. Add a regression scenario matching the incident: the ledger contains `selfie_search.0003_optional_feedback_contact`, the candidate graph does not, and deployment exits before environment promotion, observability install, Nginx stop, Compose reconciliation, or marker changes.
- [ ] Add an enumerated `phase()` helper near `fail()` in `deploy/apply-deployment.sh`. Emit phases for `validate`, `snapshot`, `candidate-pull`, `private-media-preflight`, `migration-preflight`, `observability-preflight`, `observability-reconcile`, `certificate`, `compose-reconcile`, `local-health`, `worker-health`, `public-health`, `observability-verify`, and `commit`. The helper accepts only those literal call sites and prints no dynamic data.
- [ ] Before `verify_observability_bootstrap` and before `mutation_started=1`, run the candidate image with the mode-0600 requested environment and overridden entrypoint: first `python manage.py verify_migration_history`, then `python manage.py showmigrations --plan`. Treat either nonzero result as `Candidate migration preflight failed` and preserve the previous environment unchanged.
- [ ] Update the exit trap to emit exactly one final result marker. If no mutation began, report `rollback=not-needed`; if recovery ran, report `rollback=succeeded` or `rollback=failed`; after marker/observability commit, report success at phase `commit`. Do not change existing recovery commands or their ordering.
- [ ] Add deployment-script tests for phase ordering, success, a pre-mutation migration-history failure, a post-mutation Compose failure with successful rollback, and a recovery failure. Assert markers contain none of the fake secrets, database values, object keys, URLs with bearer paths, or raw container logs.
- [ ] Update repository-foundation checks for the new command's versioned location and shell syntax contract.
- [ ] Run `make test TESTS="src/backend/picflow/tests/test_verify_migration_history_command.py tests/deployment/test_deployment_scripts.py tests/test_repository_foundation.py"`; expect all selected tests to pass with the incident fixture failing before mutation and existing rollback cases unchanged.

### Task 4: Reconcile one bounded GitHub issue with automatic staging deployment state

**Files:**

- Create: `scripts/reconcile_staging_deploy_issue.py`
- Create: `tests/test_reconcile_staging_deploy_issue.py`
- Modify: `.github/workflows/deploy.yml`
- Modify: `tests/test_repository_foundation.py`
- Modify: `docs/runbooks/staging-deployment.md`

- **Specification:** incident report P1 “alert on failed automatic staging deployment” and the requirement to avoid secrets or raw logs.
- **Depends on:** Task 3's stable phase markers.
- **Produces:** CLI contract `python scripts/reconcile_staging_deploy_issue.py --repository <owner/name> --token-env GITHUB_TOKEN --mode <production|validation> --conclusion <success|failure> --sha <sha> --run-url <url> --phase <phase>`. Production mode uses the exact title `[staging deployment] main is not deployed`; validation mode uses the exact title `[staging deployment validation] notification drill` and never reads or mutates the production-titled issue.

- [ ] Add HTTP-client unit tests for first failure creating one issue; a repeated failure commenting on the same issue; a successful later run commenting and closing it; success with no open issue doing nothing; exact-title matching ignoring unrelated issues; pagination bounded to the first 100 open repository issues; HTTP/rate-limit/malformed-response errors returning nonzero without printing the token or response body.
- [ ] Run `make test TESTS="tests/test_reconcile_staging_deploy_issue.py"` and confirm failure because the reconciler does not exist.
- [ ] Implement the script with `urllib.request` and the standard library only. Accept the token through the named environment variable, use GitHub's REST API, send a fixed user agent, and bound every request timeout. Select only the two fixed titles through `--mode`; do not accept arbitrary issue titles. Issue bodies/comments contain only the 40-character commit SHA, Actions run URL on `github.com`, enumerated phase, and UTC timestamp. Reject any other repository, URL host, mode, conclusion, SHA, or phase shape before making a request.
- [ ] Add an `always()` notification job for push-triggered deploy workflows with only `actions: read`, `contents: read`, and `issues: write`. It must not use the `staging` environment and must receive no application, VM, database, storage, or SSH secrets.
- [ ] Derive `build` when the build job failed; otherwise read the failed deploy log with `gh run view "$GITHUB_RUN_ID" --log-failed`, extract only the last exact `DEPLOY_PHASE=<enumerated-value>` marker, and fall back to `unknown`. Pass the successful `commit` phase directly without fetching logs. Never include raw log text in the issue.
- [ ] Mark only the notification step `continue-on-error: true` and emit an Actions warning on reconciliation failure. The build/deploy conclusion and live rollback state remain authoritative; GitHub issue availability cannot turn a successful deployment red or mask a failed deployment.
- [ ] Extend workflow-contract tests for push-only execution, minimal permissions, exact argument forwarding, `continue-on-error`, absence of staging secrets/environment, and the phase parser's allowlist/fallback behavior.
- [ ] Add a manual `workflow_dispatch` validation input that skips build/deploy and exercises the reconciler with a validation-specific repository issue title: create, update, close, then assert no validation issue remains open. Keep this path behind the existing staging environment approval and document the drill; it must never use the production incident title or application secrets.
- [ ] Update the staging deployment runbook with issue interpretation, notification degradation, deduplication, close behavior, and the validation drill.
- [ ] Run `make test TESTS="tests/test_reconcile_staging_deploy_issue.py tests/test_repository_foundation.py"`; expect API fixtures and workflow contracts to pass.

### Task 5: Reconcile architecture, engineering evidence, and end-to-end delivery

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/postmortems/2026-08-07-staging-deployment-after-parallel-migrations.md`
- Modify: `README.md` only if Tasks 1–4 introduced a link not already added in Task 2.

- **Specification:** the complete incident report and all P0–P2 completion evidence.
- **Depends on:** Tasks 1–4 passing focused verification.
- **Produces:** one evidence-backed repository statement of the delivered transition checks, controlled pause, notification behavior, and unchanged architecture/ADR boundary.

- [ ] Update the implemented deployment section in `docs/architecture.md`: numbered base migrations are immutable in PR CI; privileged observability changes pause automatic deployment; candidate migration history and plan are read-only pre-mutation checks; stable phases feed one non-blocking GitHub issue; the existing image, Compose, root ownership, and rollback topology is unchanged.
- [ ] Update EJ-003 in `docs/engineering-jobs.md` with the new acceptance behavior and exact code/test/workflow evidence. Keep `Validated` only after repository checks and a successful staging rollout; use `Delivered` before live evidence exists.
- [ ] Mark each incident-report prevention item complete only when its stated completion evidence exists. Add PR and deployment run links after delivery; do not rewrite the historical causes or timeline.
- [ ] Run `make check`; expect Ruff format/check, mypy, the complete pytest suite, Django checks, migration drift, JavaScript tests, and visual regression to pass.
- [ ] Run `git diff --check`; expect no whitespace errors.
- [ ] Re-read ADR 0003, ADR 0005, and `docs/architecture.md`. Record `Conforms to ADR 0003 and ADR 0005; no ADR creation or supersession required` in the PR.

## Verification

Run in this order from the implementation worktree:

```bash
make test TESTS="tests/test_migration_immutability.py tests/test_reconcile_staging_deploy_issue.py src/backend/picflow/tests/test_verify_migration_history_command.py tests/test_repository_foundation.py tests/deployment/test_deployment_scripts.py"
make check
git diff --check
```

Expected local result: every selected regression and the complete quality suite pass; migration drift reports no changes; the staged diff has no whitespace errors.

After PR CI is green and the PR is merged, verify the ordinary no-package-change rollout:

```bash
gh run watch <deploy-run-id> --exit-status
ssh -l petrnikitin 111.88.151.64 'sudo cat /opt/photo-prjct/deployed-image'
ssh -l petrnikitin 111.88.151.64 'cd /opt/photo-prjct && sudo env DEPLOYMENT_TARGET=staging docker compose --project-name photo-prjct-staging --env-file .env -f docker-compose.prod.yml -f docker-compose.https.yml ps'
ssh -l petrnikitin 111.88.151.64 'sudo /usr/local/sbin/findme-selfie-observability verify'
curl -fsS https://findme-photo.ru/health/
```

Expected live result: workflow success; deployed-image equals the merge SHA image; database, web, and Nginx are healthy; configured worker replicas are running; `SELFIE_OBSERVABILITY_HOST_VERIFIED` is present; public health returns `{"status": "ok"}`.

Then perform two controlled workflow drills:

1. Dispatch notification validation. Expect one validation issue to be created, updated, closed, and absent from open issues without affecting deployment state.
2. On a follow-up test branch, change only a harmless comment in `deploy/selfie-observability/summarize.py` and merge only after review. Expect the push workflow to finish in controlled-pause mode with build/deploy skipped. Run the documented operator bootstrap, verify the package, manually dispatch deployment, and confirm the ordinary live checks above. Revert the harmless comment in the same delivery if it has no documentation value.

Do not induce a schema, Compose, health, storage, or public-service failure on staging to validate this plan.

## Operational impact and rollout

No data migration, cloud resource, pricing, DNS, TLS, Object Storage, service topology, or runtime feature change is required.

Roll out Tasks 1–4 in one PR because the notification consumes the phase-marker interface and the runbook must describe the final workflow atomically. Before merge, confirm that the PR does not itself modify `deploy/selfie-observability/**`; if it does, use the new controlled-pause sequence. Merge only after the full CI suite passes. Observe the automatic staging deployment through live acceptance, then run the bounded notification drill. Validate the privileged-package pause with the separately reviewed harmless follow-up described above; do not add operator credentials to GitHub.

The deployment issue is operational metadata only. It contains no product data and is not a monitoring or audit-log replacement. The existing external health probe and rollback evidence remain authoritative.

## Rollback

- Revert the implementation PR to restore the previous CI and deployment workflow. Existing immutable images, Compose volumes, database migrations, root-owned package, and deployed-image marker remain untouched by the repository revert.
- If the migration-identity check blocks a legitimate PR, fix the migration graph with an additive merge migration; do not bypass or disable the check.
- If the controlled-pause classifier is wrong, do not force deployment. Verify the diff and use manual dispatch only after the existing root-package comparison passes.
- If candidate migration preflight fails, leave staging on the previous image, inspect the applied ledger and candidate graph read-only, and correct source migration identities. Never use `--fake` without independent schema proof and explicit operator approval.
- If phase markers or issue reconciliation malfunction, the existing apply result and rollback remain authoritative. Disable or revert only the non-blocking notification job while retaining migration and package guards.
- Closing or reopening the GitHub issue has no effect on deployment state; verify the deployed-image marker and public health before any manual issue correction.

## Open questions

None. The approved incident report, existing root-ownership boundary, current GitHub Actions deployment path, and accepted ADRs determine the implementation choices above.
