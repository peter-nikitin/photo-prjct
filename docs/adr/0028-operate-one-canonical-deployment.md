# 0028: Operate one canonical deployment

- Status: Accepted
- Date: 2026-08-19
- Deciders: project maintainers
- Supersedes: [0005](0005-promote-images-through-staging.md),
  [0026](0026-use-lockbox-for-environment-secrets.md)
- Superseded by: none

## Context

The VM originally designated as staging now holds valuable data, serves the canonical public
domain, and receives real traffic. Active code, secrets, monitoring, bucket names, and runbooks
still describe it as staging, while the separately modeled production deployment was never
provisioned. Every merge to `main` already changes the customer-serving application, so the
staging-to-production promotion model no longer describes the system.

The current delivery rate and availability requirement do not justify a second continuously
operated deployment. The immediate product need is to deploy incomplete customer-facing code
without exposing it before maintainer acceptance. Environment variables can hide capabilities but
require a deployment to change exposure and do not provide an authenticated staff-only acceptance
state.

The false two-environment model also creates lasting naming ambiguity. Replacing it with another
name such as `production`, `live`, or `stable` would still add an unnecessary qualifier to the only
canonical deployment.

## Decision drivers

- Keep unfinished customer-facing behavior unavailable to ordinary users.
- Permit maintainer acceptance against the real application data without another deployment.
- Keep infrastructure and recurring cost proportionate to one low-traffic deployment.
- Make active names describe the system without retaining obsolete environment identities.
- Preserve immutable images, CI, deployment preflights, health checks, rollback, Lockbox authority,
  and least-privilege OIDC.
- Avoid speculative multi-environment abstractions before a test deployment is required.

## Considered options

1. Provision a separate staging VM and promote staging-verified images to production.
2. Keep one deployment and use deployment-time environment switches for unfinished features.
3. Keep one canonical deployment and use database-backed `off` / `staff` / `on` runtime release
   gates.

## Decision

Select option 3.

Operate one canonical deployment on the designated Yandex Cloud VM. A merge to `main` may build and
deploy its immutable image automatically after CI. There is no staging promotion workflow and no
second deployed release identity.

Incomplete customer-facing behavior is deployed only behind a database-backed runtime release
gate. Gates fail closed, support `off`, authenticated active staff, and public states, and are
changed through permission-protected Django Admin. They gate every authoritative public entry
point and side effect. They are not authorization policy and do not make migrations, shared code,
worker protocols, or destructive operations safe.

The sole deployment has no environment qualifier in active workflows, Compose identity, secret
consumers, monitoring dimensions, bucket names, or runbooks. Do not substitute `production`,
`live`, `stable`, or another singleton name. Local development and automated tests remain local and
test execution modes, not deployed environment identities.

Use one complete Yandex Lockbox secret as the sole persistent authority for deployed credentials.
GitHub Actions obtains short-lived access through workload identity federation restricted to the
repository, `refs/heads/main`, and an exact workflow allowlist. No GitHub Environment is required.
The VM and runtime containers continue to receive projected deployment configuration and do not
read Lockbox directly.

This decision supersedes ADR 0005's staging and production separation and all of ADR 0026's
per-environment secret identity. It supersedes only ADR 0011's use of Compose project and GitHub
Environment identity for cross-environment isolation; ADR 0011's shared HTTPS overlay, certificate,
health, successful-image, and rollback decisions remain accepted. It supersedes only ADR 0018's
constant environment metric label; managed monitoring and independent public probes remain
accepted.

A future test deployment requires a new decision based on a current need. It will use explicit
`test`-qualified resources and isolated compute, data, credentials, buckets, and deployment
configuration. The canonical deployment remains unqualified.

While one maintainer owns development and operations, that maintainer retains full interactive
access to the canonical Lockbox secret and project cloud resources. A supported `local-web`
projection may supply deployed application and media credentials ephemerally, with mandatory
loopback and local-database overrides and without a persistent repository or worktree secret file.
Real database snapshots and private media may be used locally by that authorized maintainer through
explicit commands. Reconsider the human role and projection boundaries before granting a second
person infrastructure access.

## Consequences

### Positive

- Incomplete features can be accepted on real data without exposure to ordinary users.
- Runtime acceptance does not require rebuilding or redeploying the application.
- There is one deployed SHA, one data authority, one secret authority, and one set of operational
  names.
- Current cost and operational effort remain proportionate to the actual traffic and release rate.
- A later test deployment is introduced deliberately instead of preserved speculatively.
- The sole maintainer can diagnose and reproduce deployed behavior without an artificial
  environment access boundary.

### Negative

- CI, feature gates, health checks, and rollback do not provide staging isolation from a bad
  migration, shared-code regression, background side effect, or resource exhaustion.
- Staff acceptance occurs against real customer data and must preserve existing privacy and access
  controls.
- Full maintainer access makes endpoint isolation, ephemeral secret projection, and careful local
  command boundaries more important; it does not provide separation of duties.
- Automatic deployment from `main` can interrupt the only customer-serving deployment.
- Renaming the existing persistent identities requires controlled maintenance and verified
  recovery.
- A later test deployment will require new environment-aware configuration rather than activating
  a prebuilt abstraction.

### Follow-up

- Implement the approved single-deployment design in independent milestones.
- Keep feature gates temporary and remove each gate after public rollout is stable or the feature
  is rejected.
- Reconsider a separate test deployment when release frequency, availability objectives,
  migration risk, representative-data needs, or concurrent feature acceptance makes isolation
  cheaper than the residual risk.
- Reconsider the current VM topology when measured availability or recovery requirements exceed a
  single VM.

## Validation and rollback

Validate runtime `off`, staff-only, and public exposure; direct endpoint and side-effect denial;
generic immutable-image deployment; repository-, branch-, and workflow-restricted OIDC; Lockbox
projection non-disclosure; public HTTPS health; monitoring; prior-image rollback; and recovery of
every persistent identity cutover.

Rollback of a feature uses `off`. Rollback of an ordinary release uses the prior successful image.
Persistent naming and schema cutovers retain their source data and old identity until the canonical
path passes focused live checks. Reconsider this decision if feature gates repeatedly fail to
contain unfinished behavior, a deployment causes unacceptable customer impact, or a second
deployment becomes operationally cheaper than the accepted residual risk.

## References

- [Single deployment and runtime feature gates design](../superpowers/specs/2026-08-18-single-deployment-runtime-feature-gates-design.md)
- [Current architecture](../architecture.md#current-architecture--implemented)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0005](0005-promote-images-through-staging.md)
- [ADR 0011](0011-use-minimal-shared-https-rollout.md)
- [ADR 0018](0018-use-managed-yandex-monitoring.md)
- [ADR 0026](0026-use-lockbox-for-environment-secrets.md)

