# 0026: Use Lockbox for environment secrets

- Status: Accepted
- Date: 2026-08-07
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Staging secrets are currently split between GitHub Actions Secrets and local ignored files. GitHub
workflows can consume their stored values, but an authorized developer cannot retrieve the same
set for local development. This creates multiple secret authorities, incomplete local setup, and
separate inventory and rotation paths.

The application already receives environment configuration at deployment time under ADR 0003,
and ADR 0005 keeps staging deployment in GitHub Actions. Neither decision selects a secret store,
defines developer access, or establishes the GitHub-to-Yandex identity boundary. The choice must
also remain valid if the current staging installation later becomes production and a new staging
environment is provisioned.

## Decision drivers

- Give authorized local developers and staging automation access to the same authoritative secret
  set without copying secrets into repository files.
- Avoid storing a long-lived Yandex Cloud bootstrap credential in GitHub.
- Preserve the current GitHub Actions, SSH, Docker Compose, and deployment-time configuration
  boundaries.
- Keep authorization least-privileged and fail closed when identity or payload validation fails.
- Allow a future production environment without reusing staging secrets or IAM boundaries.

## Considered options

1. Use Yandex Lockbox as the sole persistent authority for environment secrets, with human `yc`
   access and GitHub workload identity federation.
2. Keep deployment secrets in GitHub and export or copy them for local development.
3. Let the Yandex Cloud VM retrieve Lockbox secrets at application or deployment runtime.
4. Maintain separate staging and local-development secret sets.

## Decision

Use Yandex Lockbox as the sole persistent authority for stored environment secrets. Each logical
environment has one independent Lockbox secret whose active version is a complete payload. The
current implementation covers only the logical `staging` environment. Any future production
environment receives a separate secret, service account, IAM bindings, federation boundary, and
deployment approval boundary; a staging secret is never renamed or reused as production.

Authorized developers read staging through their interactive `yc` identity. Staging GitHub Actions
jobs use GitHub OIDC and Yandex Cloud workload identity federation to obtain short-lived access as
one dedicated CI service account. Human and CI access use resource-level payload-reader bindings on
the staging secret. GitHub stores no permanent Yandex bootstrap credential.

A repository-owned manifest and resolver validate the exact secret identity, complete payload
version, schema, consumer projection, and local safety overrides before invoking a consumer. After
a validated migration and rollback drill, migrated GitHub Actions Secrets are removed. GitHub's
job-scoped `GITHUB_TOKEN` and non-secret environment configuration remain outside Lockbox.

The VM, Django process, workers, and Docker Compose services do not read Lockbox and receive no
Lockbox IAM permission. Deployment continues to materialize the consumer-specific environment
through the existing SSH and `apply-deployment.sh` contract. Runtime credential persistence and
recovery remain a separate decision under EJ-018.

## Consequences

### Positive

- Local and CI consumers use one versioned secret authority and one reviewed schema.
- GitHub-to-Yandex authentication has no stored cloud credential to rotate or recover.
- Resource-level IAM and consumer projections limit the access granted to automation and local
  processes.
- Lockbox or IAM unavailability blocks only new local launches and deployments, not an already
  running application.
- A later production environment can be added without changing the consumer model or inheriting
  staging credentials.

### Negative

- Local staging-capable development requires Yandex Cloud connectivity, `yc` authentication, and
  explicit developer IAM access.
- Deployment now depends on GitHub OIDC, Yandex workload identity federation, IAM, and Lockbox
  availability before reaching the VM.
- The initial migration requires a bounded dual-store window and a tested rollback before old
  GitHub Secrets can be deleted.
- Contributors granted payload-reader access can read the full staging secret; per-key human ACLs
  are not provided by this increment.

### Follow-up

- Implement and validate EJ-017 through the approved specification and implementation plan.
- Revalidate provider claim matching, CLI behavior, and pricing immediately before live setup.
- Design EJ-018 separately before changing persistent VM environment delivery, Docker metadata,
  registry authentication, or runtime recovery.
- Write a separate ADR before introducing production secrets or changing the no-runtime-fetch
  boundary.

## Validation and rollback

Validate repository schema and non-disclosure tests, an authorized local launch with mandatory
local overrides, GitHub OIDC exchange restricted to the staging environment, exact-version
Lockbox retrieval, one successful staging deployment, revocation evidence, and a migration rollback
drill. Confirm that all migrated GitHub Secret names are absent after cutover.

Before GitHub Secrets are removed, rollback restores the prior workflow revision and its existing
secret references. After removal, rollback uses an earlier validated Lockbox version or restores
workflow code without establishing GitHub Secrets as a standing secondary authority. Reconsider
this decision if the provider cannot enforce the repository and staging-environment trust boundary
or if production access requires materially different isolation.

## References

- [Environment-scoped Lockbox secrets design](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0005](0005-promote-images-through-staging.md)
- [Architecture: accepted constraints](../architecture.md#accepted-constraints)
- [EJ-017](../engineering-jobs.md#ej-017--developer--read-environment-scoped-secrets-consistently)
- [EJ-018](../engineering-jobs.md#ej-018--maintainer--minimize-and-recover-runtime-credentials)
