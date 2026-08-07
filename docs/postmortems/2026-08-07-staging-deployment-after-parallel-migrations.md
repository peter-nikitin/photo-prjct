# Staging deployment failure after observability and migration changes

- **Date:** 2026-08-07
- **Status:** resolved
- **Environment:** staging (`findme-photo.ru`)
- **Affected release:** `2e34b34ea78f379e7eef2fc965c6b35469008283`
- **Original failed run:** [31016863725](https://github.com/peter-nikitin/photo-prjct/actions/runs/31016863725)
- **Recovery runs:** [31186501249](https://github.com/peter-nikitin/photo-prjct/actions/runs/31186501249), [31189208166](https://github.com/peter-nikitin/photo-prjct/actions/runs/31189208166)
- **Customer impact:** the new release was not activated for approximately two days. The deployment rollback kept the previous application available, and the public health endpoint continued to return HTTP 200 during recovery.

## Summary

The deployment of PR #111 failed for two independent reasons that appeared sequentially.

First, PR #111 changed the root-owned selfie observability package. The repository copy on the VM no longer matched the installed package, so the deployment correctly failed closed and asked for an explicit operator bootstrap. This was an intentional security boundary, but the required operator action was not treated as a release prerequisite before merge.

After the package was updated, the next deployment reached database migration and exposed a second issue. PR #112 had already deployed a migration named `0003_optional_feedback_contact`. While resolving the parallel migration history, PR #111 renamed the same migration to `0005_optional_feedback_contact` and introduced different `0003` and `0004` migrations. The staging database therefore contained the effect and history of the old `0003`, but Django considered the renamed `0005` unapplied and tried to remove a constraint that no longer existed.

Recovery required updating the observability bootstrap, confirming that the database schema already matched `0005`, marking only that migration as applied with `--fake`, and rerunning the normal deployment. No customer records or application tables were manually edited.

## Timeline

All times below are Moscow time (UTC+3).

- **2026-08-05 17:05** — during the successful deployment of PR #112, staging applied `selfie_search.0003_optional_feedback_contact`, which made feedback contact optional and removed `selfie_feedback_contact_nonempty_chk`.
- **2026-08-05 17:45** — PR #111 merged as `2e34b34`. Its migration sequence contained a different `0003`, a new `0004`, and the former PR #112 migration renamed to `0005_optional_feedback_contact`.
- **2026-08-05 17:48** — automatic run 31016863725 stopped with `Selfie observability bootstrap is missing or stale`. Image build, pull, and the private-media preflight had succeeded. The previous release remained active.
- **2026-08-07** — investigation confirmed that only the installed observability package was stale; the host observability service itself verified successfully.
- The operator bootstrap installed the reviewed package from `/opt/photo-prjct/deploy/selfie-observability` into its root-owned location. Exact comparison and `SELFIE_OBSERVABILITY_HOST_VERIFIED` passed.
- **2026-08-07 17:16** — recovery run 31186501249 passed the former bootstrap blocker. It applied the new `0003_result_provenance_and_clusters` and `0004_remove_selfie_search_candidate`, then failed while applying `0005_optional_feedback_contact` because the constraint had already been removed by the previously deployed migration.
- The deployment reconciled the previous application and worker profile. Public health remained available.
- Read-only database inspection confirmed that `contact` was already `varchar(254) NOT NULL` and that `selfie_feedback_contact_nonempty_chk` was absent, exactly matching the intended final schema.
- `selfie_search.0005_optional_feedback_contact` was marked applied with Django's `--fake` option. This changed migration history only; it did not run schema SQL or modify customer data.
- **2026-08-07 17:49** — run 31189208166 completed successfully. The deployed-image marker, web image, two worker containers, Compose health checks, observability verification, and public HTTPS health all pointed to `2e34b34`.

## What happened

### 1. The observability package required a separate privileged release step

Normal deployments copy repository files to `/opt/photo-prjct`, but they deliberately do not execute those mutable files as root. The observability package is installed into root-owned paths only through `deploy/bootstrap-selfie-observability.sh`, an explicit operator action. `deploy/apply-deployment.sh` compares every managed source file with the installed package and refuses to deploy when they differ.

PR #111 changed `deploy/selfie-observability/summarize.py`. That made the installed package stale by design. The code and its tests described the required bootstrap, but the merge/deployment workflow had no release gate that made the operator action visible before the automatic deployment began.

This failure was therefore not a malfunction of the guard. The guard prevented a release whose runtime observability code did not match the reviewed commit. The process failure was allowing a known two-phase operational change to look like an ordinary merge-to-deploy change.

### 2. An already applied migration identity was changed

Django identifies a migration by application label and filename, not by the operations inside the file. Once `selfie_search.0003_optional_feedback_contact` was applied to staging, that identity became part of the durable database history.

The parallel work in PR #111 also introduced a migration numbered `0003`. The conflict was resolved by moving the PR #112 migration to `0005` and making it depend on the new `0004`. That produced a valid linear history for a fresh database and passed repository tests, but it changed the identity of a migration that had already run on staging.

On staging, Django saw both facts:

- `0003_optional_feedback_contact` had run and already removed the constraint;
- `0005_optional_feedback_contact` had not run, because it had a different filename.

The operations were not idempotent. Applying `0005` attempted to remove the same named constraint again, and PostgreSQL correctly returned `constraint ... does not exist`.

The correct merge shape after PR #112 had reached an environment was to preserve both existing `0003` migration files as immutable leaves, add an explicit Django merge migration, and put later operations after that merge. Renumbering an applied migration was no longer safe.

## Why existing checks did not catch it

### Repository and CI tests used fresh migration history

Fresh databases contained only the migration filenames present in the final branch. They never reproduced the historical sequence in which `0003_optional_feedback_contact` had already been applied before it was renamed. The final migration graph was internally consistent, so graph and fresh-install checks passed.

The missing assertion was not another fresh-database migration test. It was an immutability check against the base branch and a deployed-history upgrade test for parallel migrations.

### Deployment verified the observability package late

The exact file comparison was effective and its error was actionable, but it ran inside `apply-deployment.sh` after image build, transfer, pull, and an application preflight. The workflow did not classify a root-owned package change before merge or before spending time on the automatic deployment.

### The two failures masked each other

The first fail-closed guard prevented migration execution, so the migration-history defect could not appear until the operator corrected the observability package and reran deployment. Treating the first message as the only root cause would have produced a second surprise even with a correct first fix.

## Contributing factors

- PR #111 and PR #112 both changed the same Django application's migration frontier and were merged close together.
- Migration conflict resolution optimized for a clean linear graph rather than preserving identities already released to staging.
- Review and CI inspected repository state, not the transition from the currently deployed migration ledger.
- The rollout plan included observability changes but did not turn the privileged bootstrap into an explicit pre-merge release checkpoint.
- The automatic deploy job is a large sequential boundary. A correct early guard can hide a later independent failure until the next run.
- The failed deployment was not investigated immediately, extending the time during which `main` and staging differed.

## What worked

- The observability comparison failed closed and named the required operator command.
- Immutable images were built and retained, making retries deterministic.
- The deployment rollback restored the previous web and worker profile after the migration failure.
- The public HTTPS health endpoint remained healthy during investigation and recovery.
- Django migrations committed `0003` and `0004` consistently before `0005` failed; their state could be inspected rather than guessed.
- Schema inspection proved that `--fake` was safe for this one migration. The recovery did not delete a constraint blindly or edit application data.
- The final deployment independently verified Compose health, observability, the deployed-image marker, and public health.

## What could be improved

- A merged migration file must be treated as immutable once any shared environment may have applied it.
- Parallel migration resolution must start by inspecting `origin/main` and the migration ledger of the environment that auto-deploys from it.
- Changes to the privileged observability package need a visible release classification and a prepared operator step, not an incidental failure in the normal deploy.
- Deployment output should expose named phases so that an early controlled stop and a later application defect are not reported as the same generic `Apply staging deployment` failure.
- A failed automatic deployment should create an immediate owner-visible follow-up rather than relying on someone noticing the red workflow later.

## Prevention plan

### P0 — protect migration identities in pull requests

Add a CI check that compares migration paths with the pull request base and fails when an existing migration file is deleted or renamed. The check should permit new migration files and explicit merge migrations, but it should not infer safety from matching operations or Git rename similarity.

**Completion evidence:** a fixture or script test demonstrates that the PR #111 shape—removing `0003_optional_feedback_contact.py` and adding the same operations under `0005_optional_feedback_contact.py`—fails, while adding two parallel leaves plus a merge migration passes.

**Repository status: complete.** [`scripts/check_migration_immutability.py`](../../scripts/check_migration_immutability.py),
[`tests/test_migration_immutability.py`](../../tests/test_migration_immutability.py), the pull-request
step in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), and the repository-foundation
contract test cover the prohibited modification, deletion, and rename cases and the additive-leaf /
merge controls. No PR or CI run link is recorded yet.

### P0 — require an explicit parallel-migration procedure

Update the engineering guidance for migration conflicts:

1. fetch current `origin/main`;
2. preserve every migration filename already present there;
3. inspect current migration leaves;
4. use `makemigrations --merge` or an equivalent explicit merge node;
5. test the upgrade from the base branch's migration frontier, not only a fresh database;
6. never resolve a conflict by renumbering or renaming a migration that may already have deployed.

**Completion evidence:** the guidance is referenced by the PR checklist or contributor workflow, and the next parallel migration change includes an upgrade-path test from the base frontier.

**Repository status: incomplete.** The procedure is documented in the
[migration-conflict runbook](../../runbooks/django-migration-conflicts.md) and linked from EJ-002,
but this checkout has no PR-checklist or contributor-workflow reference and no subsequent
base-frontier upgrade-path change to evidence the second requirement.

### P1 — classify privileged-package changes before deployment

Add a lightweight workflow check that detects changes under `deploy/selfie-observability/` and reports that a privileged host-package update is required. The release should expose a deliberate two-phase sequence:

1. prepare and verify the reviewed commit's root-owned package through an operator-authorized workflow or documented operator action;
2. run the normal immutable-image deployment.

Do not weaken the existing root trust boundary by allowing the normal deploy user to install mutable repository files as root automatically.

**Completion evidence:** a test change to `summarize.py` produces an explicit required-action check before the deploy job, and an unchanged package takes the ordinary path without operator work.

**Repository status: partial.** The classifier, controlled-pause conditions, and ordinary-path
conditions are covered by
[`test_staging_deployment_pauses_privileged_package_pushes_before_building`](../../tests/test_repository_foundation.py)
and the workflow contract in [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml).
The separately reviewed harmless `summarize.py` change and an actual push pause / manual-bootstrap
run have not been performed, so the stated end-to-end completion evidence is not claimed.

### P1 — add a read-only pre-deploy phase

Before application reconciliation, report separate checks for:

- privileged-package parity;
- candidate migration plan against the established database;
- current deployed image and rollback availability.

The migration check must be read-only. It should make the base-to-candidate transition visible, but it must not replace migration immutability checks or claim that `showmigrations --plan` proves SQL execution will succeed.

**Completion evidence:** workflow output names the failed boundary without requiring the operator to search a combined SSH log, and no preflight mutates the database.

**Repository status: complete.** The read-only command and pre-mutation ordering are covered by
[`verify_migration_history.py`](../../src/backend/picflow/management/commands/verify_migration_history.py),
[`test_failed_candidate_migration_history_stops_before_any_deployment_mutation`](../../tests/deployment/test_deployment_scripts.py),
and [`test_deployment_migration_history_preflight_is_versioned_and_read_only`](../../tests/test_repository_foundation.py).
No live staging preflight result is recorded.

### P1 — alert on failed automatic staging deployment

Create a bounded notification or tracked issue for a failed `main` deployment, including the run URL, commit, failed phase, and whether rollback/public health succeeded. Avoid sending secrets or raw logs.

**Completion evidence:** a controlled failed workflow produces one actionable notification and a later successful retry closes or resolves it.

**Repository status: partial.** The standard-library reconciler and its API fixtures cover create,
deduplication, update, close, validation isolation, and sanitized failures in
[`tests/test_reconcile_staging_deploy_issue.py`](../../tests/test_reconcile_staging_deploy_issue.py);
the workflow permissions, phase extraction, and non-authoritative behavior are covered by
[`test_staging_deployment_issue_reconciliation_is_bounded_and_non_authoritative`](../../tests/test_repository_foundation.py).
No controlled failed deployment, retry, or live notification-drill result is recorded.

### P2 — improve phase observability in `apply-deployment.sh`

Emit stable sanitized phase markers for package verification, image pull, storage preflight, migration, Compose reconciliation, health, marker commit, and rollback. Preserve the current single-VM Compose architecture and rollback behavior.

**Completion evidence:** deployment-script tests assert the phase and rollback markers for a migration failure and an observability-package mismatch.

**Repository status: partial.** Migration preflight and post-mutation rollback markers are asserted
by [`test_failed_candidate_migration_history_stops_before_any_deployment_mutation`](../../tests/deployment/test_deployment_scripts.py)
and [`test_post_mutation_compose_failure_reports_the_recovery_outcome`](../../tests/deployment/test_deployment_scripts.py).
The stale-observability preflight test asserts the host-mutation boundary but does not assert its
phase/result marker, so the prevention item is not marked complete. No live phase stream is recorded.

## Explicit non-actions

- Do not make `DROP CONSTRAINT` broadly tolerant with `IF EXISTS` to conceal migration-history divergence. That would address this symptom while preserving an incorrect ledger.
- Do not let the normal deployment silently overwrite root-owned observability code. The explicit trust boundary worked as designed.
- Do not replace Docker Compose or the single staging VM because of this incident. Neither caused the failure.
- Do not add a general compatibility layer for arbitrary historical migrations. Preserve released migration identities and test the concrete base-to-candidate path instead.
- Do not treat a successful fresh database build as evidence that an established environment can upgrade safely.

## Lessons

Deployment correctness is a property of a transition, not only of the target commit. PR #111 was coherent on a fresh checkout, yet unsafe relative to staging's already applied migration ledger and incomplete relative to its privileged host package.

Fail-closed checks are valuable, but a controlled stop must be represented in the release process before it becomes a surprising red deployment. Likewise, migration files stop being ordinary source files once they are merged into an auto-deployed branch: their names are durable database state.

The most useful prevention is therefore not “more tests” in the abstract. It is to test and review the two boundaries that actually failed:

1. reviewed repository assets versus privileged installed host assets;
2. the deployed migration frontier versus the candidate migration graph.
