# Environment-scoped Lockbox Secrets Implementation Plan

- Date: 2026-08-07
- Status: Approved
- Owner: project maintainer
- Related specification:
  [`2026-08-07-environment-scoped-lockbox-secrets-design.md`](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md)
- Related architecture: [`docs/architecture.md`](../architecture.md), environment-variable
  configuration, GitHub Actions deployment, and local Docker Compose boundaries
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0005](../adr/0005-promote-images-through-staging.md), and
  [ADR 0026](../adr/0026-use-lockbox-for-environment-secrets.md)
- ADR impact: Conforms to accepted ADR 0026. The final reconciliation must preserve one complete
  Lockbox secret per logical environment, human `yc` and GitHub OIDC reader identities,
  resource-level payload access, removal of migrated GitHub Secrets, and no runtime Lockbox reader.

## Goal

Deliver the approved
[EJ-017 outcome](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#outcome)
for the single logical `staging` environment: authorized local development and staging automation
read the same validated Lockbox payload without persistent local copies or long-lived Yandex Cloud
credentials in GitHub.

## Scope

Implements the approved specification without scope changes. Production and the runtime credential
hygiene recorded as EJ-018 remain excluded.

## Acceptance criteria

All repository and authorized-environment evidence in the
[specification](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#validation-and-acceptance-evidence)
must pass. Delivery additionally requires the operational sequence in this plan to complete in
order: preflight, resource creation, parallel-store validation, repository cutover, successful
staging deployment, rollback drill, reapplied cutover, GitHub Secret deletion, and final revocation
probe.

## Agentic execution

Use `$execute-implementation-plan` for every repository implementation task. Each task below has an
exclusive primary file set and its own independent review gate. The root controller owns live cloud
and GitHub mutations, passes only non-secret identifiers to implementers, and does not dispatch a
task until all dependencies are complete. Tasks 2 and 3 are logically parallel after Task 1, but
execute sequentially in this shared worktree so their independent diffs and reviews remain
unambiguous.

## Cross-task interfaces

- `deploy/environment-secrets/staging.json` is the reviewed authority for the logical environment,
  Lockbox/folder/federation/service-account identifiers, allowed workflow identities, complete key
  schema, consumer projections, and fixed local overrides.
- `scripts/run-with-environment-secrets.py` exposes one command boundary:
  `--environment staging --consumer <name> --identity <yc|github-oidc> -- <child command>`.
- Valid consumers are `local-web`, `staging-deploy`, `staging-remote-check`, and
  `staging-public-monitor`. Each receives only its manifest projection.
- The resolver passes one mode-0600 environment-file path to the child through the non-secret
  `FINDME_ENV_FILE` environment variable. The child never receives the IAM or OIDC token.
- Local identity captures `yc iam create-token` without printing it. GitHub identity requests the
  configured audience from `ACTIONS_ID_TOKEN_REQUEST_URL`, exchanges the resulting JWT at Yandex's
  OAuth token-exchange endpoint for the manifest service account, and keeps both tokens in memory.
- The resolver reads Lockbox metadata to verify the expected folder and active version, then reads
  that exact `versionId` once through the payload API. It uses Python's standard library only.
- Environment files use Docker Compose syntax with unquoted `KEY=value` records whose values are
  encoded according to the manifest. Binary entries are written only to separate mode-0600 files
  referenced by a projected path variable; they are never forced into text environment records.

## Implementation

### Operational gate A: Confirm provider contract and create non-secret resource identities

**Ownership:** Root controller/operator only; no implementation subagent.

**Repository files:** None.

- **Specification:** [GitHub Actions](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#github-actions),
  [Security invariants](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#security-invariants),
  [Migration and rollback](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#migration-and-rollback),
  and [External contracts](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#external-contracts).
- **Depends on:** Accepted ADR 0026 and explicit approval immediately before any live mutation.
- **Produces:** Exact staging folder, secret, service-account, federation, audience, and federated-
  credential identifiers; confirmed GitHub OIDC subject; current Lockbox price evidence; no secret
  payload yet.

- [ ] Revalidate the linked official Yandex Cloud and GitHub contracts. Confirm that Yandex can
  bind the exact GitHub `staging` environment subject and that the repository's current immutable
  or name-based `sub` format is known before creating a federated credential.
- [ ] Inventory GitHub `staging` Environment secret and variable names with `gh` without requesting
  or printing values. Classify every name against the specification's secret/non-secret list.
- [ ] Inspect existing Yandex Cloud resources read-only and current Lockbox pricing. Present the
  exact creates, IAM grants, access scope, and price delta for fresh approval.
- [ ] After approval, create one staging Lockbox secret with no application payload, one dedicated
  staging CI service account, one GitHub OIDC federation, and one federated credential bound to the
  exact repository-and-`staging`-environment subject.
- [ ] Grant the CI account only `lockbox.payloadViewer` on the exact secret. Grant each approved
  human the same role on that secret resource; do not grant folder-wide payload access.
- [ ] Read back resource metadata and access bindings as JSON. Confirm the expected folder, issuer,
  audience, subject, service account, and exact resource-level role, and confirm no service-account
  key exists.

### Task 1: Add the manifest and fail-closed shared resolver

**Ownership:** One security-sensitive implementer; root-capability model is justified because this
task handles secret boundaries, process exposure, and cleanup on signals. Independent review uses a
root-capability reviewer.

**Files:**

- Create: `deploy/environment-secrets/staging.json`
- Create: `scripts/run-with-environment-secrets.py`
- Create: `tests/deployment/test_environment_secrets.py`

- **Specification:** [Environment identity](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#environment-identity),
  [Non-secret environment manifest](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#non-secret-environment-manifest),
  [Shared resolver](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#shared-resolver),
  [Security invariants](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#security-invariants),
  and [Failure semantics](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#failure-semantics).
- **Depends on:** Operational gate A supplies exact non-secret identifiers and verified OIDC claim
  values.
- **Produces:** The cross-task manifest and resolver interfaces listed above.

- [ ] Add failing tests with fake `yc` and HTTP boundaries for unknown environments/consumers,
  identity failure, metadata mismatch, missing active version, and exact-version payload retrieval.
- [ ] Add failing schema tests for valid text/binary values and values containing spaces, quotes,
  newlines, equals signs, and shell metacharacters; cover missing, empty, duplicate, unknown,
  malformed, and wrong-type entries.
- [ ] Add failing projection tests proving each consumer receives exactly its allowlist and that
  deployment-only, database, SSH, registry, and monitoring entries cannot reach `local-web`.
- [ ] Add failing security tests proving mode `0600`, `umask 077`, cleanup on success, child error,
  resolver error, HUP, INT, and TERM, and sentinel absence from stdout, stderr, process arguments,
  GitHub output syntax, and retained files.
- [ ] Run `make test TESTS="tests/deployment/test_environment_secrets.py"`; expect failure because
  the manifest and resolver do not exist.
- [ ] Implement the smallest standard-library resolver satisfying the shared interfaces. Use
  structured JSON parsing and direct argument arrays; forbid `shell=True`, `eval`, payload
  `source`, dynamic environment names, caller-supplied IDs, and arbitrary overrides.
- [ ] Run `make test TESTS="tests/deployment/test_environment_secrets.py"`; expect all selected
  tests to pass with no sentinel in captured output or repository state.
- [ ] Run `.venv/bin/ruff format --check scripts/run-with-environment-secrets.py tests/deployment/test_environment_secrets.py`,
  `.venv/bin/ruff check scripts/run-with-environment-secrets.py tests/deployment/test_environment_secrets.py`,
  and `.venv/bin/mypy`; expect zero exits.
- [ ] Self-review, package the complete unstaged task diff, obtain independent approval, and let the
  root controller create the task's single commit.

### Task 2: Add the safe staging-local entry point

**Ownership:** One implementation subagent owns only the local launcher, Make target, and its tests.

**Files:**

- Create: `scripts/staging-local.sh`
- Create: `tests/deployment/test_staging_local.py`
- Modify: `Makefile`

- **Specification:** [Local development](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#local-development)
  and local portions of [Validation evidence](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#validation-and-acceptance-evidence).
- **Depends on:** Task 1 resolver and `local-web` projection.
- **Produces:** `make staging-local`, which runs existing local `db` and `web` services with the
  resolved file and a second private file containing non-bypassable local overrides.

- [ ] Add failing tests for repository/worktree validation, supported local Docker endpoint,
  missing/expired `yc`, sanitized failures, and exact invocation of the Task 1 resolver.
- [ ] Add failing tests proving fixed `DEBUG=True`, local-only hosts/binding, `DB_HOST=db`, local
  database identity, and removal of deployment targets override any inherited process environment
  or payload value and cannot be disabled by user arguments.
- [ ] Add failing tests proving the launcher neither writes repository/worktree `.env` nor invokes
  staging clone, SSH, deployment, lifecycle, upload, or real-storage management commands.
- [ ] Run `make test TESTS="tests/deployment/test_staging_local.py"`; expect failure because the
  launcher and Make target do not exist.
- [ ] Implement the launcher as a narrow orchestration wrapper around the Task 1 resolver and
  existing Compose services. Pass both private env files explicitly to Compose, print the approved
  staging-capable warning and sanitized readiness marker, and trap cleanup failures.
- [ ] Run `make test TESTS="tests/deployment/test_staging_local.py tests/test_worktree_commands.py tests/test_create_worktree.py"`;
  expect all selected tests to pass and existing worktree `.env` isolation to remain unchanged.
- [ ] Self-review, package the complete unstaged task diff, obtain independent approval, and let the
  root controller create the task's single commit.

### Task 3: Move staging GitHub jobs to OIDC and resolver projections

**Ownership:** One multi-file integration implementer owns the staging workflow helpers, affected
workflow definitions, and workflow contract tests. Use a root-capability reviewer because workflow
changes can disclose credentials or mutate staging.

**Files:**

- Create: `deploy/run-staging-remote.sh`
- Create: `tests/deployment/test_staging_workflow_secrets.py`
- Modify: `.github/workflows/deploy.yml`
- Modify: `.github/workflows/monitor-public-health.yml`
- Modify: `.github/workflows/promote-production.yml` only in the `verify-staging` job; leave the
  nonexistent production environment's `promote` job unchanged and disabled from this migration
- Modify: `tests/test_repository_foundation.py`
- Modify: `tests/deployment/test_deployment_scripts.py`
- Modify: `tests/deployment/test_monitoring_contract.py`

- **Specification:** [GitHub Actions](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#github-actions),
  [VM and runtime boundary](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#vm-and-runtime-boundary),
  and workflow portions of [Validation evidence](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#validation-and-acceptance-evidence).
- **Depends on:** Task 1 resolver and manifest projections. Independent of Task 2 files.
- **Produces:** Staging jobs with job-local `id-token: write`, `environment: staging`, resolver
  invocation, file-based SSH key handling, and no migrated `secrets.*` references.

- [ ] Add failing structural tests that enumerate every staging secret consumer and require
  `environment: staging`, job-local `contents: read` plus `id-token: write`, the expected manifest
  consumer, and no OIDC permission on build or pull-request jobs.
- [ ] Add failing tests that reject migrated `${{ secrets.* }}` expressions everywhere in staging
  jobs except GitHub's built-in `secrets.GITHUB_TOKEN`; require non-secret host/user/database/domain
  values to come from reviewed variables or tracked defaults.
- [ ] Add failing tests for the remote helper's exact argv/file boundaries, SSH host-key policy,
  SCP/SSH failure before mutation, mode-0600 SSH key, stdin/file environment transfer, sanitized
  output, and cleanup on every exit path.
- [ ] Run `make test TESTS="tests/deployment/test_staging_workflow_secrets.py tests/test_repository_foundation.py tests/deployment/test_deployment_scripts.py tests/deployment/test_monitoring_contract.py"`;
  expect failures for current GitHub Secret references and missing OIDC/resolver steps.
- [ ] Replace staging appleboy secret interpolation with audited shell commands driven by
  `FINDME_ENV_FILE`; never publish a token or secret as an action output. Preserve candidate
  preflight, service switch, readiness, deployment marker, and rollback ownership in
  `deploy/apply-deployment.sh`.
- [ ] Route deploy/configure-monitoring, private-storage checks, selfie-storage checks,
  selfie-feedback checks, production's staging-image verification, and public monitoring through
  their smallest manifest projections. Keep build jobs GitHub-native and retain
  `secrets.GITHUB_TOKEN` only for GHCR publication.
- [ ] Run the focused command above; expect all selected tests to pass and no migrated staging
  secret reference or secret-bearing workflow output to remain.
- [ ] Run `make test TESTS="tests/deployment/test_environment_secrets.py tests/deployment/test_staging_local.py tests/deployment/test_staging_workflow_secrets.py tests/test_repository_foundation.py tests/deployment/test_deployment_scripts.py tests/deployment/test_monitoring_contract.py"`;
  expect the full repository secret-delivery contract to pass.
- [ ] Self-review, package the complete unstaged task diff, obtain independent approval, and let the
  root controller create the task's single commit.

### Task 4: Add the operator runbook and inventory contract

**Ownership:** One documentation-focused implementer after Tasks 1-3 stabilize names and commands.

**Files:**

- Create: `docs/runbooks/environment-secrets.md`
- Create: `docs/runbooks/environment-secrets-inventory.md`
- Modify: `docs/operations.md`
- Modify: `tests/test_repository_foundation.py`

- **Specification:** [Rotation and revocation](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#rotation-and-revocation),
  [Migration and rollback](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#migration-and-rollback),
  and [Failure semantics](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md#failure-semantics).
- **Depends on:** Tasks 1-3 exact commands and consumer names.
- **Produces:** Executable inventory, setup, rotation, revocation, rollback, recovery, cleanup, and
  incident procedures with sanitized evidence fields.

- [ ] Add failing documentation contract tests requiring every manifest key and consumer to appear
  in the inventory, every migrated GitHub Secret name to have an owner/destination/rotation trigger,
  and every required runbook procedure to have preflight, success, rollback, and non-disclosure
  checks.
- [ ] Run `make test TESTS="tests/test_repository_foundation.py"`; expect failure for absent runbook
  and inventory contracts.
- [ ] Write the runbooks using exact repository commands and resource-scoped `yc`/`gh` checks.
  Commands that change IAM, Lockbox versions, GitHub Secrets, credentials, or cost must stop for
  fresh operator approval; payload entry values must enter through protected files or interactive
  input, never arguments or logs.
- [ ] Document lost-device/account recovery as identity revocation plus restored human access by a
  surviving cloud organization administrator; do not treat payload rotation as identity
  revocation. Document the dependency on separately maintained break-glass ownership without
  inventing EJ-018 runtime recovery.
- [ ] Run `make test TESTS="tests/test_repository_foundation.py"`; expect all documentation contract
  tests to pass.
- [ ] Self-review, package the complete unstaged task diff, obtain independent approval, and let the
  root controller create the task's single commit.

### Operational gate B: Populate and validate Lockbox without changing workflow readers

**Ownership:** Root controller/operator only; no implementation subagent.

**Repository files:** None.

- **Depends on:** Tasks 1 and 4; fresh approval before payload/IAM mutation.
- **Produces:** One complete current staging payload, local human retrieval evidence, CI OIDC
  retrieval evidence, and retained GitHub Secrets for rollback.

- [ ] Collect every current value through protected operator channels, classify non-secrets into
  GitHub Environment variables, and build one complete candidate payload matching the manifest.
- [ ] Validate the candidate locally without printing values, then create and activate one complete
  Lockbox version. Record only secret ID, version ID, key names, timestamps, and sanitized results.
- [ ] Run `make staging-local` from this isolated worktree. Confirm Django readiness uses the local
  database and mandatory overrides, then stop the services and confirm no temporary file remains.
- [ ] Run a manually dispatched, non-mutating staging OIDC preflight using each required consumer.
  Confirm the expected subject, service account, secret/version IDs, projections, and secret-free
  logs while the deployed workflow still reads GitHub Secrets.

### Operational gate C: Cut over, drill rollback, and remove GitHub Secrets

**Ownership:** Root controller/operator only; no implementation subagent.

**Repository files:** None during live operations.

- **Depends on:** Tasks 1-4 reviewed and committed; Operational gate B passed; final whole-branch
  review and `make check` green; fresh approval before deployment, rollback, secret deletion, or IAM
  changes.
- **Produces:** Validated Lockbox-only staging workflows and removal of the old persistent GitHub
  secret authority.

- [ ] Merge the reviewed cutover revision while retaining old GitHub Secrets. Observe one automatic
  staging deployment through exact-version retrieval, existing preflights, service switch,
  readiness, and deployed-image marker.
- [ ] Inspect workflow logs, outputs, artifacts, runner process diagnostics, retained paths, VM
  transfer boundary, and repository state for secret disclosure. Record only sanitized markers and
  non-secret IDs.
- [ ] Drill rollback by restoring the prior workflow revision while GitHub Secrets still exist;
  confirm staging health and the expected deployed marker. Reapply the Lockbox revision and repeat
  successful deployment verification.
- [ ] Run staging operational checks, including public monitoring and the enabled manual storage
  preflights, through their Lockbox projections.
- [ ] After a separate deletion approval, delete every migrated GitHub Environment Secret. Confirm
  the names are absent, non-secrets exist only as variables/tracked configuration, and
  `GITHUB_TOKEN` remains GitHub-issued.
- [ ] Prove the intended local and CI identities still retrieve the exact current version. Exercise
  revocation with a disposable/test binding or a controlled remove-and-restore sequence; prove the
  revoked identity fails without displaying payload and the intended identity remains successful.

### Final task: Architecture, ADR, and job reconciliation

**Ownership:** Root controller after live validation.

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md`
- Modify: `docs/plans/2026-08-07-environment-scoped-lockbox-secrets.md`

- [ ] Compare delivered behavior and evidence with the specification and accepted ADRs 0003, 0005,
  and 0026. Stop for a new decision instead of changing an accepted ADR.
- [ ] Update architecture implemented facts only after repository implementation exists; do not
  claim live staging activation before Operational gate C passes.
- [ ] Advance EJ-017 to `Delivered` only after Lockbox is authoritative and migrated GitHub Secrets
  are absent. Advance it to `Validated` only after local, CI, deployment, rollback, non-disclosure,
  and revocation evidence all pass. Append exactly one history row per status transition.
- [ ] Keep EJ-018 `Candidate`; record no runtime `.env`, Docker metadata, registry-login, TLS,
  snapshot, or recovery claim in EJ-017.
- [ ] Set this plan to `Completed` only when all repository and operational gates are evidenced.
- [ ] Run final documentation links/status checks and record the ADR reconciliation outcome in the
  pull request.

## Verification

Run before the whole-branch review and again after review fixes:

```text
make test TESTS="tests/deployment/test_environment_secrets.py tests/deployment/test_staging_local.py tests/deployment/test_staging_workflow_secrets.py tests/test_repository_foundation.py tests/deployment/test_deployment_scripts.py tests/deployment/test_monitoring_contract.py tests/test_worktree_commands.py tests/test_create_worktree.py"
make check
git diff --check
```

Expected outcomes: all selected tests and the complete quality suite pass; Django reports no system
or migration drift; the diff has no whitespace errors; generated sentinel values are absent from
captured output, arguments, workflow outputs, artifacts, retained temporary files, and Git state.
Do not run overlapping complete suites while a subagent is active.

Authorized environment checks are the exact gates B and C above. Repository tests use fake identity
and HTTP boundaries and never require a real secret payload.

## Operational impact and rollout

The rollout creates one billable Lockbox secret plus IAM federation/service-account resources,
adds resource-level human and CI payload access, migrates staging GitHub jobs and local development,
and finally deletes migrated GitHub Environment Secrets. No production resource or runtime
Lockbox dependency is created. Every live mutation requires fresh approval; approving this plan
does not itself authorize resource creation, IAM changes, payload writes, deployments, credential
revocation, or GitHub Secret deletion.

Compatibility is intentionally temporary and operational only: GitHub Secrets remain available
during gates A and B and the first cutover/rollback drill, but repository code has no dual-read
fallback. After gate C, Lockbox is the only persistent staging secret authority.

## Rollback

Before GitHub Secret deletion, revert to the prior workflow revision and its retained GitHub
Environment Secrets, then restore the last known healthy application image through the existing
deployment rollback. A failed local or CI resolution does not mutate the VM.

After deletion, select an earlier complete validated Lockbox version or revert workflow code while
retaining the Lockbox reader. Do not repopulate GitHub Secrets as a standing fallback. If OIDC trust
cannot enforce the accepted subject boundary, disable/remove the federated credential and stop the
cutover for design revision. Resource cleanup after an abandoned rollout requires separate approval
and must preserve evidence needed for incident review.

## Open questions

None. Exact cloud-generated identifiers are execution outputs of Operational gate A, not design
choices; they must be supplied to Task 1 without exposing payload values.
