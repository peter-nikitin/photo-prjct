# Single Deployment and Runtime Feature Gates Implementation Plan

- Date: 2026-08-19
- Status: Approved on 2026-08-19
- Owner: project maintainer
- Related specification:
  [`docs/superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md`](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md)
- Related ADRs:
  [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0006](../adr/0006-yandex-object-storage-media.md),
  [ADR 0011](../adr/0011-use-minimal-shared-https-rollout.md),
  [ADR 0013](../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0018](../adr/0018-use-managed-yandex-monitoring.md),
  [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md),
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md)
- ADR impact: implements accepted ADR 0028, which supersedes ADR 0005 and ADR 0026 and the
  environment-identity parts of ADR 0011 and ADR 0018. The remaining listed ADR boundaries stay
  in force.

## Goal

Deliver the approved [single-deployment outcome](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#outcome)
in independently reversible milestones: runtime release gates and staff event preview first,
then removal of false environment names, and finally the persistent cloud, Compose, and Django
catalog identity cutovers.

## Scope

Implement the approved specification without scope changes. No data migration changes existing
event publication statuses; after deployment the maintainer reviews them in Django Admin.

Execution must use `$execute-implementation-plan`. Each task below is a separate reviewed change
and, where stated, a separate maintenance operation. Tasks 5, 6, and 7 must never share a
maintenance window.

## Acceptance criteria

Use the specification's [acceptance criteria](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#acceptance-criteria).
In addition:

- every code milestone passes `make check` and its focused checks before merge;
- existing `draft` events remain hidden from every non-staff user during and after deployment, and
  the maintainer can reclassify them to `unavailable` afterward without a second deployment;
- every cloud mutation has a fresh read-only inventory, an explicit maintainer approval naming the
  target, a focused live check, and a retained rollback source;
- active names are checked after the final cutover; accurate historical records are excluded from
  the cleanup.

## Implementation

### Task 1: Add database-backed runtime release gates

**Files:**

- Create `src/backend/feature_flags/__init__.py`.
- Create `src/backend/feature_flags/apps.py`.
- Create `src/backend/feature_flags/models.py`.
- Create `src/backend/feature_flags/services.py`.
- Create `src/backend/feature_flags/admin.py`.
- Create `src/backend/feature_flags/migrations/__init__.py`.
- Create `src/backend/feature_flags/migrations/0001_initial.py`.
- Create `src/backend/feature_flags/tests/__init__.py`.
- Create `src/backend/feature_flags/tests/test_services.py`.
- Create `src/backend/feature_flags/tests/test_admin.py`.
- Modify `src/backend/config/settings.py`.

- **Specification:**
  [Runtime feature-gate model](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#runtime-feature-gate-model).
- **Depends on:** None.
- **Produces:** `feature_flags.services.is_enabled(key: str, user: AbstractBaseUser | AnonymousUser) -> bool`;
  the only application-facing gate evaluation interface.

- [ ] Add failing model and service tests for `off`, `staff`, and `on`; missing rows; anonymous,
  inactive, ordinary, and active-staff users; and a database change taking effect on the next
  evaluation without cache invalidation.
- [ ] Add failing admin tests proving view/change permission follows Django model permissions and
  that changes appear in Django Admin history.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests"` and confirm failures are caused only
  by the missing module and interface.
- [ ] Implement one `FeatureFlag` model with a stable unique key, operator description, state,
  and timestamps; register it in Django Admin; add the app and migration; implement fail-closed
  uncached evaluation.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests"`; expect all feature-gate tests to
  pass.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests src/backend/config/tests"`; expect no
  regression in settings or authentication behavior.
- [ ] Document in the pull request that this task supplies a release primitive only; it does not
  convert infrastructure switches or event publication into feature flags.

### Task 2: Add `unavailable` and staff-only event preview

**Files:**

- Create `src/backend/picflow/access.py`.
- Create `src/backend/picflow/migrations/0011_event_publication_states.py`.
- Modify `src/backend/picflow/models.py`.
- Modify `src/backend/config/views.py`.
- Modify `src/backend/config/context_processors.py`.
- Modify `src/backend/selfie_search/views.py`.
- Modify `src/backend/selfie_search/services/submission.py`.
- Modify `src/backend/selfie_search/services/results.py`.
- Modify `src/backend/selfie_search/services/feedback.py` if feedback authorization is currently
  resolved below the view.
- Modify `src/backend/templates/catalog/event_catalog.html`.
- Modify `src/backend/templates/catalog/event_detail.html` without overwriting unrelated existing
  work in that file.
- Modify `src/backend/static/ui/catalog.css`.
- Modify `src/backend/picflow/tests/test_models.py`.
- Modify `src/backend/picflow/tests/test_views.py`.
- Modify `src/backend/picflow/tests/test_admin.py`.
- Modify `src/backend/selfie_search/tests/test_views.py`.
- Modify `src/backend/selfie_search/tests/test_submission.py`.
- Modify `src/backend/selfie_search/tests/test_results.py`.
- Modify `src/backend/selfie_search/tests/test_feedback_submission.py`.
- Modify `tests/visual/visual.spec.js` and only the snapshots affected by the new staff warning.

- **Specification:**
  [Staff-visible event publication state](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#staff-visible-event-publication-state).
- **Depends on:** None; it is durable product state and does not consume Task 1's interface.
- **Produces:** `EventQuerySet.site_visible_to(user)` as the single catalog-owned event access
  decision used by every site seam.

- [ ] Add failing queryset tests for the exact matrix: `unavailable` is absent for everyone;
  `draft` is present only for authenticated active `is_staff`; `published` is present for all
  callers. Include inactive staff and ordinary authenticated users.
- [ ] Add failing endpoint tests across catalog, detail, preview media, original download, selfie
  upload, gallery-face submission, search creation/polling/result retrieval, and feedback. Assert
  `404` for unauthorized requests and assert that storage signing and write services were not
  called.
- [ ] Add failing presentation tests for exact copy
  `Черновик — виден только администраторам` on draft cards and detail, no warning on published
  pages, and no Yandex Metrika script or pixel in a staff-preview response.
- [ ] Add failing model/admin tests for the Russian labels, `unavailable` as both Python and
  database default, valid timezone requirements for `draft` and `published`, and no automated row
  rewrite in migration `0011`.
- [ ] Run the focused Python tests listed in this task and confirm they fail on the new state and
  access behavior.
- [ ] Implement the three statuses and schema-only migration. Do not add `RunPython`, raw data SQL,
  a compatibility status, or an automatic `draft` rewrite.
- [ ] Implement `site_visible_to(user)` and replace every request-facing `.published()` lookup
  listed above. Keep `.published()` only for non-site processing whose contract genuinely means
  public publication, and cover each retained call with a focused test.
- [ ] Pass the current request user into service boundaries that currently re-fetch an event by
  publication status, so direct endpoints and bearer-result paths cannot bypass the shared access
  decision.
- [ ] Mark draft-preview requests in request context and make the analytics context processor
  return no counter for them; do not rely on JavaScript to suppress analytics after page load.
- [ ] Render the exact warning and update focused visual snapshots.
- [ ] Run:
  `make test TESTS="src/backend/picflow/tests/test_models.py src/backend/picflow/tests/test_views.py src/backend/picflow/tests/test_admin.py src/backend/selfie_search/tests/test_views.py src/backend/selfie_search/tests/test_submission.py src/backend/selfie_search/tests/test_results.py src/backend/selfie_search/tests/test_feedback_submission.py"`;
  expect all state, authorization, storage-side-effect, and analytics assertions to pass.
- [ ] Run `npm run test:visual`; expect the catalog/detail snapshots, including authenticated staff
  preview coverage, to pass.
- [ ] Deploy and live-check all three states with an anonymous session, an ordinary user, and an
  active staff session. Confirm no Metrika request on staff draft pages and no signed URL or selfie
  side effect from unauthorized direct calls.
- [ ] After deployment, review existing `draft` events in Django Admin and manually change those
  that nobody should see to `unavailable`. Confirm the change is immediate and does not require a
  restart or another deployment.

### Task 3: Remove environment identity from deployment source code

**Files:**

- Modify `.github/workflows/deploy.yml`.
- Modify `.github/workflows/monitor-public-health.yml`.
- Rename `.github/workflows/staging-face-embedding-benchmark.yml` to
  `.github/workflows/face-embedding-benchmark.yml`.
- Delete `.github/workflows/promote-production.yml`.
- Rename `deploy/environment-secrets/staging.json` to `deploy/environment-secrets.json`.
- Rename `deploy/run-staging-remote.sh` to `deploy/run-remote.sh`.
- Rename `scripts/staging-local.sh` to `scripts/local-web.sh`.
- Rename `scripts/clone-staging-db.sh` to `scripts/clone-deployed-db.sh`.
- Rename `scripts/reconcile_staging_deploy_issue.py` to `scripts/reconcile_deploy_issue.py`.
- Rename `docker-compose.prod.yml` to `docker-compose.deployment.yml`.
- Delete `docker-compose.staging.yml` and `deploy/nginx/staging.conf` after moving any still-used
  responsibility into the canonical Compose/Nginx files.
- Modify `Makefile`, `deploy/apply-deployment.sh`, `scripts/run-with-environment-secrets.py`,
  `scripts/verify-environment-secret-projection.py`, `docker-compose.yml`,
  `docker-compose.https.yml`, `src/backend/config/settings.py`,
  `src/backend/config/metrics.py`, and the active callers of the
  renamed scripts.
- Modify `deploy/monitoring/unified-agent.yml.template`, `deploy/monitoring/dashboard.json`,
  `deploy/monitoring/alerts.md`, and the selfie-observability scripts that emit an environment
  dimension.
- Rename and modify the matching tests under `tests/deployment/`,
  `tests/test_reconcile_staging_deploy_issue.py`, `src/backend/config/tests/test_metrics.py`, and
  `tests/test_repository_foundation.py`.

- **Specification:**
  [Canonical deployment identity](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#canonical-deployment-identity)
  and [Release model](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#release-model).
- **Depends on:** Task 1 is deployed before incomplete features begin relying on runtime gates.
- **Produces:** generic source names and a single `deploy/environment-secrets.json` manifest with
  consumers `deploy`, `remote-check`, `public-monitor`, and `local-web`.

- [ ] Rename the focused tests first and change their expectations to the generic workflow,
  consumer, script, Compose, logging, and metric contracts. Confirm they fail against the existing
  names.
- [ ] Make the workflow display name and concurrency group `Deploy`; remove GitHub Environment
  declarations and environment-only branches; preserve CI dependency, immutable-image handling,
  migration/private-media preflights, allowlisted progress output, health checks, successful-image
  marker, and prior-image rollback.
- [ ] During this source transition, allow generic deployment only through `workflow_dispatch`.
  Restore the automatic `main` trigger only after Task 5 has activated and verified the canonical
  Compose identity.
- [ ] Change the manifest to one secret authority and the four approved consumers. Keep
  `local-web` projection ephemeral, loopback-bound, `DEBUG=True`, and local-PostgreSQL-only. Keep
  cloning a snapshot, launching local web, and operating deployed resources as separate commands.
- [ ] Remove `DEPLOYMENT_TARGET`, accepted `staging`/`production` values, marker files, and the
  constant environment log/metric label where they carry only deployment identity.
- [ ] Update the public monitor and dashboards to aggregate on the remaining service, route,
  method, and status dimensions without silently changing alert thresholds.
- [ ] Run:
  `make test TESTS="tests/deployment tests/monitoring src/backend/config/tests/test_metrics.py tests/test_reconcile_deploy_issue.py tests/test_repository_foundation.py"`;
  expect all renamed operational contracts to pass.
- [ ] Run `tests/deployment/validate-nginx.sh`; expect the canonical Nginx/Compose combination to
  validate.
- [ ] Run `make check`; expect the complete code milestone to pass before merge.
- [ ] Merge without dispatching the new workflow. Task 4 must update external secret/OIDC identity,
  and Task 5 must perform the persistent Compose cutover, before automatic deployment resumes.

### Task 4: Cut over GitHub, Lockbox, and workload identity

**Files:**

- Modify `deploy/environment-secrets.json` only if read-only discovery finds an external identifier
  different from the reviewed one.
- Modify current operational documentation referenced by the generic workflow; defer final broad
  terminology cleanup to Task 8.

- **Specification:**
  [Canonical deployment identity](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#canonical-deployment-identity)
  and [Failure and recovery boundaries](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#failure-and-recovery-boundaries).
- **Depends on:** Task 3 merged with automatic deployment paused.
- **Produces:** repository-level GitHub variables, one generically named Lockbox secret, and an OIDC
  credential whose subject is restricted to `repo:peter-nikitin/photo-prjct:ref:refs/heads/main`.

- [ ] Reauthenticate the Yandex Cloud CLI if needed. Then perform read-only discovery of service
  account `ajeaekiue94ogksguh0h`, federation `ajeula3gd46omgf9jiko`, Lockbox secret
  `e6q85jjl76r45maigtfb`, its roles/versions, current federated credentials, GitHub repository and
  Environment variables, and the exact workflow-ref allowlist. Record identifiers and metadata,
  never secret values.
- [ ] Stop and obtain explicit approval naming the Lockbox secret, new federated credential, and
  GitHub variable scope before mutation.
- [ ] Create the new branch-scoped federated credential with
  `yc iam workload-identity federated-credential create --service-account-id ajeaekiue94ogksguh0h --federation-id ajeula3gd46omgf9jiko --external-subject-id repo:peter-nikitin/photo-prjct:ref:refs/heads/main`.
  Keep the prior credential during validation.
- [ ] Rename secret `e6q85jjl76r45maigtfb` to the generic reviewed name without creating another
  persistent secret or secret version.
- [ ] Copy non-secret deployment coordinates from GitHub Environment `staging` to repository
  variables, and update any repository values that still name the old manifest consumers. Do not
  print projected secrets.
- [ ] Dispatch the generic read-only/preflight path and confirm OIDC issuance, exact workflow
  allowlisting, Lockbox projection, non-disclosure, and VM remote check.
- [ ] Prove that a non-`main` ref and an unallowlisted workflow cannot obtain the projection.
- [ ] Only after focused validation, obtain separate approval and delete the obsolete federated
  credential and GitHub Environment. Re-list external state to prove there is one active authority.
- [ ] If validation fails, restore the workflow/variables to the previous path before removing any
  old credential or Environment.

### Task 5: Cut over the persistent Compose identity and activate generic deployment

**Files:**

- Create `deploy/cutover-compose-identity.sh`.
- Create `tests/deployment/test_compose_identity_cutover.py`.
- Modify `.github/workflows/deploy.yml` after live cutover to restore the automatic `main` trigger.
- Modify `docker-compose.deployment.yml`, `docker-compose.https.yml`, and
  `deploy/apply-deployment.sh` only for issues found by the cutover rehearsal.

- **Specification:**
  [Persistent Docker identity cutover](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#persistent-docker-identity-cutover).
- **Depends on:** Tasks 3 and 4; automatic deployment remains paused.
- **Produces:** canonical Compose project `photo-prjct`, `photo-prjct_pgdata`, canonical ACME
  volumes, and a verified generic deployment entrypoint.

- [ ] Add tests for exact source/destination project and volume identities, source-preservation,
  destination-absence preflight, stopped-writer requirement, absolute backup directory, PostgreSQL
  dump/restore, certificate copy, post-restore checks, and fail-closed handling of unexpected
  volumes.
- [ ] Implement the one-time cutover helper with dry-run and explicit confirmation modes. It must
  never delete source volumes and must not accept arbitrary project or volume targets.
- [ ] Rehearse against disposable Compose volumes and run
  `make test TESTS="tests/deployment/test_compose_identity_cutover.py tests/deployment/test_deployment_scripts.py"`;
  expect the successful path and every destructive precondition to pass.
- [ ] Immediately before the live window, read-only inventory the exact current containers,
  networks, PostgreSQL/Let's Encrypt/ACME volumes, disk space, database size/counts/migration set,
  certificate names/expiry, current image digest, and successful-image marker.
- [ ] Name the exact source and destination volumes, backup path, downtime, and rollback image;
  obtain explicit approval for the maintenance operation.
- [ ] Stop web and worker writers, create and verify the database dump and certificate backup,
  initialize the canonical volumes, restore data/certificates, and start the canonical Compose
  project through the generic entrypoint.
- [ ] Verify database row counts and migrations, worker startup, private-media preflight, public
  HTTPS health, Nginx routing, certificate renewal dry-run, monitoring ingestion/public probe, and
  prior-image rollback while the source volumes remain untouched.
- [ ] If any check fails, stop the canonical project and restart the old project with the old
  volumes and image; retain both backups for diagnosis.
- [ ] After acceptance, restore `main` push deployment in `.github/workflows/deploy.yml`, run full
  CI, merge, observe one automatic generic deployment, and verify the deployed SHA.
- [ ] Retire old containers/networks only after acceptance. Keep old persistent volumes until the
  final cleanup task receives separate destructive approval.

### Task 6: Migrate Object Storage buckets one at a time

**Files:**

- Create `scripts/copy-object-storage-bucket.py`.
- Create `tests/deployment/test_object_storage_copy.py`.
- Modify `src/backend/processing/storage.py`.
- Modify `src/backend/processing/views.py`.
- Modify `src/backend/processing/services/previews.py`.
- Modify the matching processing storage/view/service tests and
  `tests/processing/test_pipeline_e2e.py`.
- Modify `docs/local-photo-processing-check.md`.
- Modify the repository variables consumed by `deploy/environment-secrets.json` after each cutover.
- Modify the approved feedback lifecycle digest wherever the manifest or verification tests store
  it.
- Modify focused deployment/storage tests only when the canonical name is a checked contract.

- **Specification:**
  [Object Storage bucket identity](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#object-storage-bucket-identity).
- **Depends on:** Task 5. Each subtask below is a separate approved change and deployment.
- **Produces:** three new canonical buckets, unchanged durable object keys, no database migration,
  and no dual configured or writable authority.

- [ ] Add failing tests for an allowlisted role-based copy tool with exact source/target names,
  dry-run, absolute manifest directory, empty-target preflight, paginated current-object inventory,
  bounded concurrency, source-ETag conditional copy, metadata preservation, complete key/size
  comparison, public `public-read` ACL, feedback KMS encryption, resumable delta inventory, and
  fail-closed handling of unknown prefixes or targets. The tool must obtain credentials only from
  the existing ephemeral environment projection and never write them to a manifest or log.
- [ ] Implement `scripts/copy-object-storage-bucket.py` with exactly three role modes and no
  arbitrary bucket arguments. Public copies all current objects; private copies only `originals/`
  and `derivatives/`; feedback copies all current objects and requires KMS key
  `abjjca35o900fng2nk6v`. Each successful conditional copy is recorded in a non-secret JSONL
  manifest so a final run copies only source keys added or changed after the initial inventory.
- [ ] Replace new candidate keys `processing-staging/previews/...` with
  `processing-pending/previews/...` in code, validation, tests, current runbooks, and the target
  lifecycle rule. Do not rewrite or copy old temporary objects. Preserve the seven-day expiry.
- [ ] Run
  `make test TESTS="tests/deployment/test_object_storage_copy.py src/backend/processing/tests/test_storage.py src/backend/processing/tests/test_views.py tests/processing/test_pipeline_e2e.py"`;
  expect copy safety and the new candidate prefix to pass.
- [ ] Before each bucket, recapture read-only source JSON for resource ID, object counts/sizes by
  durable and temporary prefix, ACL and representative object ACL, policy, CORS, lifecycle,
  encryption/KMS, versioning, maximum size, private endpoints, and current application variable.
  Record no object contents or credentials.
- [ ] Confirm that the target name is not present, then present the exact bucket-create/configure
  commands, source/target, expected duplicate storage, request cost, access impact, and rollback.
  Obtain explicit pricing and access approval immediately before creating each bucket. Name
  availability is proven only by successful creation because names are globally unique.
- [ ] Configure the empty target completely before copy. Public uses STANDARD, 100 GiB, versioning,
  the source account grants, and `public-read` object ACLs. Private uses STANDARD, 200 GiB, source
  account grants/CORS/private-endpoint posture, seven-day `processing-pending/previews/` expiry,
  and 24-hour `selfie-search/` expiry. Feedback uses STANDARD, 50 GiB, the source account grant,
  30-day expiry, and KMS key `abjjca35o900fng2nk6v`. Verify target configuration read-only.
- [ ] Migrate public media first from `project-storage-dev-2026` to
  `findme-photo-public-media-b1g2qttg`. Copy and verify all eight-current-object equivalents,
  including key, size, content type, source-conditional success, sampled SHA-256, and anonymous
  GET. Switch `MEDIA_S3_PUBLIC_BUCKET`, deploy, and verify cover URLs and public ACL behavior.
  Historical non-current versions remain only in the source bucket.
- [ ] In a later operation, migrate feedback from
  `findme-selfie-feedback-staging-b1g2qttg` to
  `findme-photo-selfie-feedback-b1g2qttg`. Temporarily disable feedback, wait 60 seconds for grants,
  copy and verify current keys/sizes plus sampled SHA-256 and KMS headers, recalculate the lifecycle
  approval digest, switch `SELFIE_FEEDBACK_S3_BUCKET`, deploy, and verify consented write/read,
  anonymous denial, exact KMS encryption, and lifecycle before re-enabling feedback.
- [ ] Migrate private media last from `hires-staging` to
  `findme-photo-private-media-b1g2qttg`. Run the initial conditional copy of `originals/` and
  `derivatives/` while the source remains configured. Then enter a brief full web/worker
  maintenance stop, drain or resolve active processing attempts and searches, and wait ten minutes
  for already issued photographer upload grants. Do not add a new maintenance switch solely for
  this operation. Do not copy `processing-staging/`, `incoming/`, or `selfie-search/`. Copy the
  manifest delta and require exact durable source/target key and size equality, successful
  conditional-copy evidence, representative SHA-256 equality, and anonymous denial.
- [ ] Switch `PRIVATE_MEDIA_S3_BUCKET`, deploy, start workers, and re-enable entry points. Verify
  original upload/confirmation, preview worker publication under `processing-pending/`, accepted
  derivative delivery, original download, selfie upload/search cleanup, private-media preflight,
  and absence of writes to the source bucket.
- [ ] After each cutover, remove the source from application configuration but keep it intact and
  readable for 14 days, at least two successful deployments, and the focused rollback check. Old
  signed and cached URLs therefore continue to work while new requests use the target.
- [ ] On any mismatch, pause writers, restore the prior bucket variable and feedback digest when
  applicable, redeploy, and re-run the source-path checks. Do not modify or delete the source and do
  not proceed to the next bucket.
- [ ] After every 14-day retention gate, recapture source/target evidence, name the exact old bucket
  and recoverable manifest, disclose the irreversible data loss and current duplicate-storage cost,
  and obtain separate destructive approval before deleting its objects and bucket. Deletion is not
  part of the cutover approval.

### Task 7: Rename the Django app and PostgreSQL schema identity to `catalog`

**Files:**

- Rename `src/backend/picflow/` to `src/backend/catalog/`, including its tests, management commands,
  migration helpers, and migrations `0001` through the then-current head.
- Modify all project Python imports, Django app dependencies, settings, URLs, templates, commands,
  tests, raw SQL, migration dependencies, historical model references, and current operational
  documentation that refer to the active `picflow` identity.
- Create `src/backend/catalog/management/commands/cutover_catalog_identity.py`.
- Create `src/backend/catalog/tests/test_catalog_identity_cutover.py`.
- Modify `scripts/check_migration_immutability.py` and
  `tests/test_migration_immutability.py` with one exact reviewed whole-graph identity transition.
- Modify deployment migration preflight tests and code only as needed to recognize the explicit
  one-time cutover mode.

- **Specification:**
  [Catalog module identity](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#catalog-module-identity).
- **Depends on:** Tasks 1–6 accepted. This is its own maintenance window and does not share a
  release with a Compose or bucket mutation.
- **Produces:** one `catalog` package/app/migration graph and a transactional, fail-closed existing-
  database identity cutover.

- [ ] Build a checked exact manifest of deployed project migration rows and every PostgreSQL table,
  sequence, index, and constraint whose identity starts with `picflow`; derive and review each
  `catalog` target and reject collisions or unknown objects.
- [ ] Add failing integration tests that create representative rows, permissions, content types,
  and admin log references under the old graph, run the cutover, and assert preserved primary keys,
  row counts, foreign keys, permissions, timestamps, and references under only `catalog` names.
- [ ] Add failing tests for an unexpected migration row/schema object, an existing target object,
  duplicate content type, count mismatch, and transaction rollback with no partial rename.
- [ ] Rename the package and rewrite the entire project migration graph without compatibility
  imports, `AppConfig.label`, alias package, fallback table names, or dual app registration.
- [ ] Update migration immutability verification for the exact reviewed identity rewrite. Keep all
  later mutation detection active; do not add a general ignore rule for historical migrations.
- [ ] Implement the one-time management command. It must verify the expected manifest before the
  first mutation, perform schema/migration/content-type changes in one database transaction,
  preserve content-type IDs, introspect and rename every approved object, and verify all invariants
  before commit.
- [ ] Test a fresh PostgreSQL database from zero with the rewritten `catalog` graph and an existing-
  graph fixture through the cutover. Run:
  `make test TESTS="src/backend/catalog/tests/test_catalog_identity_cutover.py tests/test_migration_immutability.py tests/deployment/test_deployment_scripts.py"`;
  expect both database histories to converge with no pending/conflicting migration.
- [ ] Run `make check`; expect all imports, tests, migration checks, and current command references
  to pass under `catalog` only.
- [ ] Rehearse backup, command execution, verification, and restore against a fresh clone of the
  deployed database. Compare exact migration rows, schema identities, counts, foreign keys,
  content types, permissions, and admin log references before requesting the live window.
- [ ] Immediately before live cutover, inventory the exact database and image again. Name the
  backup artifact, current/target image, expected object manifest, downtime, and rollback; obtain
  explicit approval.
- [ ] Stop web and worker writers, create and verify a full database backup, run the cutover command
  from the reviewed target image, run `migrate --check`, then start that same image.
- [ ] Verify admin, catalog/detail/media/download, upload/processing, selfie search/results/feedback,
  permissions, worker health, public HTTPS, and monitoring before accepting the new identity.
- [ ] If the transaction has not committed, let it roll back. After commit, rollback means stopping
  writers and restoring the captured database backup together with the previous image; never run
  the old image against the new schema identity.

### Task 8: Remove one-time paths and reconcile architecture

**Files:**

- Delete `deploy/cutover-compose-identity.sh` and its one-time test after the Compose source volumes
  are explicitly retired.
- Delete `scripts/copy-object-storage-bucket.py` and
  `tests/deployment/test_object_storage_copy.py` after all three source buckets are explicitly
  retired; retain permanent storage contract/lifecycle tests.
- Delete `src/backend/catalog/management/commands/cutover_catalog_identity.py` and its one-time
  cutover tests after the deployed database and recovery drill are accepted; keep permanent fresh-
  database and migration-immutability coverage.
- Modify `docs/architecture.md`.
- Modify `docs/engineering-jobs.md`.
- Modify `docs/product-jobs.md` for the delivered staff event-preview capability.
- Modify current runbooks and README files returned by the active-name scan.
- Reconcile `docs/adr/README.md`, ADR 0028, and the superseded status/cross-links of ADR 0005 and
  ADR 0026 only if delivered behavior differs from the already accepted records.

- **Specification:**
  [Delivery isolation](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#delivery-isolation)
  and final [Acceptance criteria](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md#acceptance-criteria).
- **Depends on:** Every prior milestone accepted and stable; destructive cleanup still requires its
  own approval.
- **Produces:** current architecture and runbooks that describe the implemented one-deployment
  system, with no obsolete active identity or permanent cutover scaffolding.

- [ ] Re-run read-only inventories for old Docker volumes, obsolete OIDC/GitHub identity, Lockbox
  authority, bucket names, workflow names, and database schema identity. Name exact deletion
  targets and retained backups; obtain explicit approval before deleting recoverable sources.
- [ ] Retire obsolete persistent resources only after their documented retention window and one
  successful restore/rollback drill. State what was deleted and which backup remains recoverable.
- [ ] Remove one-time code once it can no longer be needed for rollback. Do not retain hidden legacy
  aliases, external-volume mappings, old app labels, or generic migration-check bypasses.
- [ ] Search active source, configuration, tests, and current documentation for `staging`,
  `production`, `live`, `stable`, and `picflow`. Classify every hit as an accurate historical
  record, an unrelated domain word, or a blocking active identity; remove every blocking hit.
- [ ] Update `docs/architecture.md` from planned constraints to implemented facts, and update the
  engineering/product job evidence with the exact checks performed.
- [ ] Compare delivered behavior with the approved specification and ADR 0028. If it differs,
  stop for a decision and supersede the ADR rather than editing away its accepted decision.
- [ ] Run every command in the final verification section, require green CI, dispatch/observe the
  generic deployment, verify the deployed SHA and focused live matrix, and record the architecture
  reconciliation outcome in the pull request.

## Verification

Run at each code milestone:

```bash
make check
npm run test:visual
```

Expected: Python quality/tests, migration immutability, JavaScript checks, and visual contracts pass;
no snapshot changes exist outside the reviewed staff-preview surfaces.

Run after the final source cleanup:

```bash
make test TESTS="src/backend/feature_flags/tests src/backend/catalog/tests src/backend/selfie_search/tests tests/deployment tests/monitoring tests/test_migration_immutability.py tests/test_repository_foundation.py"
tests/deployment/validate-nginx.sh
rg -n "staging|production|live|stable|picflow" .github deploy scripts src tests Makefile docker-compose*.yml docs/architecture.md docs/engineering-jobs.md docs/product-jobs.md
git diff --check
```

Expected:

- all selected tests and Nginx validation pass;
- every remaining search result is explicitly reviewed as accurate historical prose or an unrelated
  word, with no active deployment, bucket, Compose, secret, metric, command, import, app, migration,
  or PostgreSQL identity remaining;
- `git diff --check` prints no errors.

The final live check records:

- deployed Git SHA and immutable image digest;
- anonymous, ordinary-user, active-staff, and inactive-staff behavior for unavailable/draft/
  published events;
- runtime feature transition `off -> staff -> on -> off` without restart and without unauthorized
  side effects;
- database migration state and representative row counts;
- public HTTPS, certificate renewal dry-run, worker health, private/public/feedback storage checks,
  monitoring ingestion, and independent public probe;
- successful deployment of a harmless `main` change through the generic `Deploy` workflow and
  successful prior-image rollback exercise.

## Operational impact and rollout

The delivery order is mandatory:

1. deploy runtime feature gates;
2. deploy staff preview, then manually reclassify existing drafts that should be unavailable;
3. merge generic deployment source with automatic deployment paused;
4. cut over GitHub/Lockbox/OIDC;
5. cut over Compose identity and restore automatic deployment;
6. migrate public, feedback, and private buckets in three separate operations, then retain each
   source for its fixed rollback window;
7. cut over `picflow` database/application identity to `catalog` in its own maintenance window;
8. retire retained sources and reconcile current documentation.

Tasks 4–7 change external or persistent state. At execution time, use the applicable cloud or
operational delivery skill for fresh read-only discovery and require explicit approval immediately
before every mutation. Current CLI authentication and resource facts must be treated as drift-prone;
the identifiers in this plan are expected targets, not a substitute for preflight.

There is no compatibility promise for obsolete names. Temporary coexistence is allowed only as a
bounded rollback condition during a cutover; after acceptance there is one writable authority and
one active name.

## Rollback

- Feature release: set its row to `off`; use application rollback as well if shared code is faulty.
- Event preview: change affected events to `unavailable`; restore the previous image if the shared
  access interface or analytics suppression is faulty. The schema-only status migration need not be
  reversed for exposure rollback.
- GitHub/Lockbox/OIDC: retain the old credential and Environment until the generic path passes;
  restore workflow variables before deleting the new credential on failure.
- Compose: keep the old project, volumes, verified database/certificate backups, and prior image;
  stop the canonical project before restarting the old one.
- Bucket: pause writes, restore the source bucket variable and prior lifecycle digest, redeploy,
  and verify before resuming. The source remains intact for 14 days; target deletion is not needed
  for rollback and requires separate approval if later requested.
- Catalog: before transaction commit, rollback is transactional; after service activation, stop
  writers and restore the verified database backup and previous image as one unit.

No rollback uses simultaneous writable old and new authorities. No retained rollback source is
deleted in the same operation that activates its replacement.

## Open questions

None.
