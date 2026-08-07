# Environment-scoped Lockbox Secrets Design

## Status

Approved in conversation on 2026-08-07. Written review is pending.

- Related architecture: [`docs/architecture.md`](../../architecture.md), current GitHub Actions to
  GHCR to Yandex Cloud VM deployment topology, environment-variable configuration, and local Docker
  Compose development workflow
- Related engineering job:
  [`EJ-017 — Developer — Read environment-scoped secrets consistently`](../../engineering-jobs.md#ej-017--developer--read-environment-scoped-secrets-consistently)
- Related ADRs:
  [ADR 0003](../../adr/0003-docker-compose-yandex-cloud.md) and
  [ADR 0005](../../adr/0005-promote-images-through-staging.md)
- ADR impact: **Requires new ADR.** Selecting Yandex Lockbox as the authoritative store for
  environment secrets and GitHub workload identity federation as the CI trust boundary is a
  durable architecture decision not governed by an accepted ADR.
- Implementation plan: not written

## Problem

Staging credentials are currently entered into GitHub Actions Secrets. GitHub permits workflows to
consume those values but does not permit an authorized developer to read them back through the API
or `gh`. Other credentials also exist in an ignored root `.env`. The result is more than one
secret source, incomplete local credentials, manual copying, and separate rotation paths.

A developer cannot reliably run the staging-configured application locally without reconstructing
credentials from files or asking another system to disclose GitHub Secrets. A workflow that exports
GitHub Secrets into an artifact would work around GitHub's security model and create another secret
copy. It is not an acceptable design.

## Outcome

Yandex Lockbox becomes the only persistent source of stored staging secrets. An authorized local
developer and authorized staging GitHub Actions jobs read the same current, versioned payload using
different short-lived identities:

- the developer authenticates interactively through `yc`; and
- GitHub Actions exchanges its job OIDC token through Yandex Cloud workload identity federation.

The existing GitHub Actions deployment remains responsible for selecting and deploying immutable
images. The current VM and application do not become runtime Lockbox clients. Ordinary non-secret
configuration remains outside Lockbox.

## Success Criteria

The capability succeeds when:

- one logical `staging` environment has one authoritative versioned Lockbox secret;
- an explicitly authorized developer can start the local application with staging credentials
  through one supported command without copying those credentials into a repository or worktree
  `.env`;
- staging deployment and staging operational jobs obtain stored secrets through GitHub OIDC and
  Lockbox without a long-lived Yandex Cloud credential in GitHub;
- every stored staging secret used by those workflows is removed from GitHub Actions Secrets after
  migration validation;
- GitHub retains only its job-scoped `GITHUB_TOKEN` and non-secret identifiers/configuration;
- a payload version is schema-validated and used atomically for one invocation;
- secret values do not appear in command arguments, logs, step outputs, artifacts, caches, tracked
  files, or persistent local files;
- local-only overrides prevent accidental use of the staging database and unsafe network binding;
- failed authentication, retrieval, validation, materialization, or cleanup stops before SSH or a
  deployment mutation; and
- rotation, revocation, migration rollback, and later addition of another logical environment have
  documented contracts.

## Scope

### Included

- One logical environment named `staging`.
- One versioned Yandex Lockbox secret containing the complete staging secret payload.
- Resource-level IAM access for authorized project developers and one staging CI service account.
- A Yandex Cloud workload identity federation and federated credential for staging GitHub Actions.
- A repository-owned, non-secret environment manifest.
- One resolver shared by local and GitHub Actions entry points.
- A supported `make staging-local` command that uses staging secrets with mandatory local
  overrides and starts the existing local Compose services.
- Migration of all stored staging credentials from GitHub Actions Secrets, including VM SSH,
  registry, application, database, Object Storage, processing, feedback, monitoring, and
  certificate-contact values.
- Classification of values currently stored as GitHub Secrets but not confidential, such as host,
  username, database name, and application hostnames, as non-secret configuration.
- Staging deployment and staging operational workflows that currently consume those values.
- Secret inventory, rotation, revocation, migration, rollback, and incident-response documentation.
- Repository contract and security tests plus one authorized local smoke and one real staging
  deployment validation.
- A new ADR before implementation relies on this design as accepted architecture.

### Excluded

- A production Lockbox secret, production IAM, or production infrastructure.
- Deciding whether the current staging VM later becomes production.
- Provisioning a replacement staging VM.
- Moving feature flags, domain names, bucket names, model parameters, limits, and other ordinary
  GitHub Environment variables into Lockbox.
- Runtime payload retrieval by Django, a worker, Docker Compose service, or the VM.
- Automatic staging database cloning or direct local access to the staging database.
- A custom secret manager, secret UI, per-key ACL, or server-side local-development broker.
- Compatibility fallbacks or permanent dual-read behavior between GitHub Secrets and Lockbox.
- Production-ready multi-environment selection before a second real environment exists.
- Changing a credential value unless rotation is required to migrate or contain exposure safely.
- Runtime credential hygiene governed by
  [`EJ-018`](../../engineering-jobs.md#ej-018--maintainer--minimize-and-recover-runtime-credentials),
  including replacement of the canonical VM `.env`, Docker container metadata, Docker/sudo access,
  persistent registry authentication, shell-history containment, TLS-key lifecycle, VM
  metadata/service-account access, snapshots/backups, and end-to-end runtime recovery.

## Environment Identity

`staging` is a logical environment, not the name of a VM, domain, folder, or lifecycle class. Its
resource mapping may change without changing the consumer interface. If the current staging VM is
later promoted to production and another staging VM is created, the old Lockbox secret is not
renamed or reused as production. A separate production environment receives a separate secret,
manifest entry, service account, IAM binding, and deployment approval boundary.

This specification implements only `staging`. The manifest and resolver accept an explicit logical
environment so that adding another environment does not require a second secret-loading mechanism.
Unknown environments fail closed.

## Selected Design

### Secret authority

One Lockbox secret is the authoritative staging secret set. Each active Lockbox version is a
complete snapshot, not a partial overlay. Rotation publishes a new complete version only after its
keys pass the repository manifest. Consumers use the current version unless an operator performs a
documented diagnostic or rollback with an explicit version ID.

Lockbox contains confidential values. The following current categories belong in the payload:

- Django secret key;
- database password;
- public and private Object Storage access-key pairs;
- selfie-feedback Object Storage access-key pair;
- photo-processing worker token;
- VM SSH private key;
- GHCR read token;
- monitoring API key while that long-lived credential remains required; and
- the certificate notification email because it is personal operational contact data.

The exact entry names are governed by the manifest. Current values that are configuration rather
than credentials move to GitHub Environment variables or tracked safe defaults, including VM host
and username, database name and username, allowed hosts, GHCR username, bucket names, endpoints,
regions, feature flags, limits, model selections, and public domains. `GITHUB_TOKEN` remains a
GitHub-issued job-scoped token and is never copied to Lockbox.

No secret value or hash of a low-entropy secret is stored in the manifest, tests, inventory,
documentation, or GitHub variables.

### Non-secret environment manifest

The repository contains one reviewed manifest for `staging` with:

- logical environment name;
- stable Lockbox secret ID and expected Yandex Cloud folder ID;
- required and allowed payload entry names;
- the mapping from Lockbox entry names to application/deployment environment variable names;
- whether an entry is text or binary;
- local availability for each entry;
- the allowed entry projection for each named consumer;
- mandatory local override keys and their fixed safe values; and
- the workflows allowed to request this environment.

The Lockbox secret ID, folder ID, federation ID, service account ID, audience, and environment name
are identifiers, not credentials. They may be stored as reviewed repository configuration or
GitHub Environment variables. The manifest must not infer an environment from a public IP, VM name,
branch name, or the active `yc` profile.

Unknown payload entries are rejected. Missing entries, duplicate keys, binary/text type mismatch,
an unexpected secret or folder ID, and an empty required value are rejected. Inventory changes
therefore require a repository review and a complete new Lockbox version.

### Shared resolver

One repository resolver owns payload retrieval validation and materialization. Callers provide an
explicit logical environment, a manifest-declared consumer, and a command to run. The resolver does
not start Docker, connect by SSH, or implement deployment policy itself.

Its design-level contract is:

```text
resolve(environment, consumer, identity, command):
    load reviewed manifest for environment
    reject an unknown consumer
    obtain short-lived IAM authentication from identity
    resolve the current Lockbox version ID
    fetch that exact version once
    validate IDs, key set, types, uniqueness, and nonempty required values
    create a private temporary environment file
    materialize only the manifest projection allowed for consumer
    execute command with the temporary file path
    remove the file on success, error, or signal
```

The resolver records only sanitized stage markers and the non-secret version ID. It never records
payload values. A caller cannot ask for an arbitrary Lockbox secret ID, consumer projection, or
unreviewed override.

The payload must be parsed as structured Lockbox output. Shell evaluation, `source` of untrusted
payload text, dynamic variable names, and string-generated shell commands are forbidden. Values may
contain whitespace, quotes, newlines, equals signs, and shell metacharacters without changing the
resulting key/value boundary.

### Local development

`make staging-local` is the supported local entry point. It:

1. verifies the checkout is a local repository/worktree with the supported local Docker endpoint;
2. verifies `yc` exists and has an authenticated human identity authorized for the staging secret;
3. invokes the shared resolver for the explicit `staging` environment and `local-web` consumer;
4. creates a second private materialization containing mandatory local overrides;
5. starts the existing local `db` and `web` Compose services; and
6. reports only sanitized readiness or failure markers.

The local launcher always overrides at least:

- `DEBUG=True`;
- local-only `ALLOWED_HOSTS` and bind addresses;
- `DB_HOST=db` and the local Compose database port;
- local database name/user/password owned by the checkout; and
- any deployment-only target or host setting that could address the staging VM.

The fixed override allowlist is part of the manifest and cannot be disabled by a normal invocation.
Staging database, VM SSH, registry, monitoring, and deployment-only credentials are not required
locally and must not be materialized for the `local-web` consumer. The launcher may use real staging
Object Storage and application-service credentials required for a full functional smoke, so its
output must state that the process is staging-capable before launch.
It must not upload, mutate lifecycle configuration, rotate credentials, or run a real-storage
management command automatically.

Cloning staging data remains the separate existing `make db-clone-staging` workflow. The developer
chooses it explicitly before or after startup. Failure to clone data is not a secret-resolution
failure.

### GitHub Actions

Every staging job that needs stored secrets references the GitHub `staging` Environment and has the
minimum `id-token: write` permission needed to request its OIDC token. Build and pull-request jobs
do not receive this permission or a Lockbox payload.

The Yandex workload identity federation trusts GitHub's OIDC issuer and the intended audience. Its
federated credential binds one dedicated staging CI service account to the exact repository and
`staging` environment subject. The trust must also constrain the reusable/deployment workflow
identity when Yandex's supported claim matching permits it. Forks, pull-request subjects, unrelated
repositories, other GitHub environments, and arbitrary branch-only subjects cannot exchange a
token for this service account.

The service account has `lockbox.payloadViewer` on the one staging secret, not at folder or cloud
scope. It receives no general editor, compute, IAM, KMS, Object Storage, or Lockbox-management role.
The credentials retrieved from the payload retain their existing downstream permissions; this
design does not broaden them.

After OIDC exchange, the job calls the shared resolver with its manifest-declared consumer and
receives a private temporary environment file containing only that consumer's projection. Existing
deployment commands consume that file without expanding its values into workflow step definitions,
command arguments, step outputs, or generated artifacts. The deployment topology remains:

```text
GitHub staging job
    -> GitHub OIDC token
    -> Yandex workload identity federation
    -> short-lived IAM token
    -> exact staging Lockbox payload version
    -> private runner environment file
    -> SSH / apply-deployment.sh
    -> protected deployment environment on the staging VM
```

The built-in `GITHUB_TOKEN` continues to authenticate GitHub-native operations where appropriate.
The existing GHCR read token remains in Lockbox only for the remote VM registry login unless that
need is removed by a separate design.

### VM and runtime boundary

The VM, Django process, workers, and Compose services do not receive Yandex IAM permission to read
Lockbox. They continue to receive only their required environment variables through the existing
deployment contract. `apply-deployment.sh` retains ownership of mode-0600 promotion, candidate
preflight, service switch, readiness, deployment marker, and rollback.

This specification does not claim that the existing persistent VM environment or Docker metadata
is the desired long-term runtime secret design. It preserves that boundary only to keep the source
migration independent. The sanitized staging findings, production gate, and separate design trigger
are recorded under EJ-018; EJ-017 must not select or partially implement that future mechanism.

This keeps Lockbox availability out of application startup and request handling. A Lockbox outage
can block a new deployment or local launch but cannot stop an already deployed service.

## Security Invariants

- Only an explicitly bound project developer or the dedicated staging CI service account can read
  the staging payload.
- Human access is granted on the secret resource, not by a folder-wide payload-viewer role.
- The CI service account can read only the staging secret and cannot manage it.
- No stored Yandex service-account key, OAuth token, or API key is used to bootstrap GitHub access.
- One resolver invocation uses one explicit Lockbox version ID and never combines versions.
- Payload values never appear in process arguments, stdout, stderr, shell tracing, workflow step
  outputs, artifacts, caches, Docker image layers, Git history, or repository/worktree `.env`.
- Temporary files are created under `umask 077`, verified as mode `0600`, and removed on success,
  error, HUP, INT, and TERM.
- Secrets are passed only to the processes and Compose services allowed by the manifest. Worker
  isolation from database, Django, and permanent Object Storage credentials remains unchanged.
- Secret values are never used as test fixtures. Security tests use generated sentinels and assert
  their absence from captured output and process arguments.
- Local overrides are closed and mandatory. A normal developer command cannot target the staging
  database or staging deployment host.
- Access revocation is performed through IAM/federated credential removal. Payload rotation does
  not substitute for revoking an identity that no longer needs access.

Project contributors are trusted to read the full staging secret once granted resource-level IAM.
This increment does not implement per-key or per-developer secret segmentation. That deliberate
boundary matches the current small-team staging use case and must be revisited before production
access is introduced.

## Failure Semantics

The resolver fails closed before running its child command when:

- the logical environment is unknown;
- `yc` authentication is absent, expired, or points at an unauthorized identity;
- OIDC issuance or IAM token exchange fails;
- issuer, audience, subject, repository, environment, service account, folder, or secret identity
  does not match the reviewed contract;
- Lockbox is unavailable or returns no active/current version;
- the exact version cannot be fetched;
- an entry is missing, duplicate, unknown, empty when required, or has the wrong type;
- temporary-file creation, permission verification, materialization, or mandatory cleanup fails; or
- a caller requests an unapproved override or consumer mapping.

Failure output names only the stage and a stable sanitized reason code. It may include non-secret
resource and version IDs when useful for audit, but never a payload value, serialized response,
credential fragment, signed URL, or environment dump.

An already deployed application continues to run when Lockbox, Yandex IAM, GitHub OIDC, or the
resolver is unavailable. A deployment that has not reached the existing environment-promotion
boundary leaves the canonical VM environment and services unchanged. Failures after that boundary
use the existing deployment rollback contract.

Cleanup failure is an error even if the child command succeeded. The launcher reports the private
temporary path without its contents and requires manual containment; CI does not publish the path
as an artifact.

## Rotation and Revocation

Every rotation creates a complete candidate Lockbox version. Before it becomes current, automation
validates its key schema without printing values. After activation:

- new local and CI invocations use the new current version;
- running application processes retain the prior values until the next normal deployment/restart;
- one staging deployment verifies the new version; and
- the previous version remains available only for the bounded rollback period defined by the
  runbook, then follows the approved destruction policy.

Rotation of a downstream credential must coordinate Lockbox version activation with the provider's
credential overlap/revocation behavior. This specification does not assume every credential permits
two active values.

Developer revocation removes the resource-level secret binding. CI revocation removes or disables
the federated credential or service-account binding. Revocation verification must prove the old
identity cannot retrieve payload while the intended identity still can, without displaying either
payload.

## Migration and Rollback

Migration does not add a runtime fallback or dual-read resolver. The old and new stores overlap
only as an operational migration window:

1. Inventory current staging GitHub Secrets and classify secret versus non-secret values without
   reading unavailable values back from GitHub.
2. Create the accepted ADR, Lockbox secret, IAM bindings, CI service account, and workload identity
   federation only after fresh approval of IAM, access, and any pricing impact.
3. Populate one complete staging version through a protected operator path; never copy values via
   logs, command arguments, or artifacts.
4. Validate local human retrieval, CI OIDC exchange, manifest schema, and secret-free output while
   existing deployment workflows still use GitHub Secrets.
5. Switch the staging workflows in one reviewed release to use only the resolver and Lockbox.
6. Complete one successful staging deployment plus operational preflights and verify the deployed
   environment without exposing payload.
7. Exercise rollback by restoring the prior workflow revision while the old GitHub Secrets still
   exist, then reapply and revalidate the Lockbox workflow.
8. Delete every migrated GitHub Secret only after those gates pass. Move misclassified non-secret
   values to variables or tracked configuration.
9. Verify removed GitHub secret names are absent, Lockbox is authoritative, and local launch still
   succeeds.

Before step 8, rollback is the previous workflow revision backed by the still-present GitHub
Secrets. After step 8, rollback means an earlier validated Lockbox version or restoration of the
workflow, not repopulating GitHub Secrets as a standing secondary store. Emergency recreation in
GitHub requires an explicit incident decision and must be removed after recovery.

No Lockbox, IAM, federation, secret-version, or credential mutation is authorized merely by
approving this specification or its future plan. Each live access-changing operation requires the
project's normal fresh confirmation. Creation must also present current official pricing or state
that the price delta is unknown.

## Validation and Acceptance Evidence

### Repository evidence

Automated tests must prove:

- only declared logical environments and stable resource identities are accepted;
- exact-version retrieval prevents mixed-version payloads;
- valid text and binary values with shell metacharacters preserve their boundaries;
- missing, empty, duplicate, unknown, malformed, and wrong-type entries fail closed;
- unknown consumers fail closed and each valid consumer receives only its allowed key projection;
- temporary files use mode `0600` and are removed on success, child failure, resolver failure, and
  handled signals;
- generated sentinel secrets are absent from stdout, stderr, command arguments, workflow outputs,
  artifacts, rendered Compose diagnostics, and Git diffs;
- local overrides always replace database, debug, bind, and deployment-target settings and cannot
  be bypassed through ordinary command inputs;
- staging database credentials and deployment-only credentials do not reach the local web service;
- application and worker credential isolation remains unchanged;
- staging jobs request `id-token: write`, reference the `staging` Environment, and use the resolver;
- pull-request/build jobs do not receive OIDC permission or Lockbox access;
- workflow definitions no longer reference migrated `secrets.*` values after cutover, excluding
  the GitHub-issued `GITHUB_TOKEN`;
- the CI service-account contract grants only payload read access to the exact staging secret; and
- documentation enumerates rotation, revocation, migration, rollback, cleanup, and incident paths.

Tests use a fake `yc`/Lockbox boundary or recorded structural fixtures containing generated values.
They do not require live payloads for ordinary CI.

### Authorized environment evidence

Before EJ-017 advances to Delivered:

- an authorized developer runs `make staging-local` from an isolated worktree and reaches Django
  readiness with the local database and mandatory overrides;
- a staging GitHub job exchanges OIDC for a short-lived IAM token and reads the exact Lockbox
  payload through the resolver;
- one staging deployment completes its existing preflights, service switch, readiness, and marker
  using only Lockbox-sourced stored secrets;
- logs, workflow outputs, artifacts, process listings, retained temporary paths, and repository
  state contain no generated secret sentinel or real payload value;
- the migration rollback is exercised before GitHub Secrets are removed; and
- all migrated GitHub Secret names are confirmed absent after cutover.

EJ-017 may advance to Delivered only after both local and staging workflows use Lockbox and the old
stored GitHub Secrets are removed. It may advance to Validated only after repository tests, local
smoke, staging deployment, non-disclosure checks, and rollback/revocation evidence all pass.

## Architecture and ADR Reconciliation

The design conforms to ADR 0003 by retaining Docker Compose on the designated Yandex Cloud VM and
keeping environment configuration external to the application image. It conforms to ADR 0005 by
retaining automatic staging deployment, GitHub Environment boundaries, immutable-image selection,
and separate future production promotion.

Neither ADR selects a secret authority, defines local access to deployment credentials, or governs
GitHub-to-Yandex federated identity. A new ADR is therefore required before implementation. It must
decide at least:

- Yandex Lockbox as the authoritative store for environment secrets;
- one complete versioned secret per logical environment;
- human `yc` and GitHub workload identity federation as the two reader identities;
- resource-level payload-reader IAM and the no-runtime-fetch VM boundary;
- removal of migrated GitHub Secrets after validated cutover; and
- separate secrets and IAM for any future production environment.

Approval of this specification selects the product/system design but does not itself accept that
ADR or authorize cloud mutations.

## Alternatives Considered

### Keep VM SSH bootstrap secrets in GitHub

Rejected as the target state. It reduces initial migration risk but permanently retains two stored
secret authorities and two rotation paths. The approved migration window already preserves rollback
without making this split permanent.

### Export GitHub Secrets through a workflow artifact

Rejected. It works around the intentional non-readable GitHub Secrets interface, creates another
secret copy, complicates recipient encryption and expiry, and leaves GitHub as the authority that
local development cannot access directly.

### Let the VM retrieve Lockbox payload at runtime

Rejected for this increment. It does not solve the pre-SSH bootstrap or local-development problem,
adds Yandex IAM and Lockbox availability to VM startup/deployment, and changes the existing
GitHub-to-SSH deployment boundary without need.

### Store all environment configuration in Lockbox

Rejected. Feature flags, domains, bucket names, limits, and other non-secret values benefit from
visible review and do not need payload-reader protection. Lockbox remains the only **secret**
authority, not the only configuration system.

### Maintain a separate local-development secret set

Rejected for the current project. The approved requirement is to let authorized developers read
the actual staging credentials and apply safe local overrides. A copied development payload would
reintroduce synchronization and rotation drift.

## External Contracts

The design relies on these current provider contracts, which must be revalidated during planning
and immediately before live setup:

- [Yandex Cloud workload identity federation](https://yandex.cloud/en/docs/iam/concepts/workload-identity)
  exchanges an OIDC-compatible external token for a short-lived IAM token without creating a
  long-lived bootstrap key and documents GitHub-side Lockbox retrieval as a use case.
- [`yc lockbox payload get`](https://yandex.cloud/en/docs/cli/cli-ref/lockbox/cli-ref/payload/get)
  can retrieve the current or an explicit secret version and an individual key.
- [Yandex Lockbox secret access](https://yandex.cloud/en/docs/lockbox/operations/secret-access)
  supports resource access bindings.
- [GitHub Actions OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
  defines issuer, audience, repository, environment, workflow, and subject claims used to constrain
  trust.
- [GitHub OIDC for cloud providers](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-cloud-providers)
  requires `id-token: write` for jobs requesting a GitHub OIDC token and recommends environment
  protection rules.

If current provider behavior cannot enforce the approved repository and staging-environment trust
boundary, implementation stops for design revision rather than broadening the federated credential.
