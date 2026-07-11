# 0005: Promote immutable images through staging

- Status: Accepted
- Date: 2026-07-11
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR 0003 establishes Docker Compose deployment to a Yandex Cloud VM, but the current workflow has
one generic target and rebuilds operational intent around a mutable `latest` tag. The project needs
safe delivery before production data and users exist. The available VM is preemptible and therefore
suited to validation, not production availability.

## Decision drivers

- Validate every release in a production-like environment before production.
- Keep the accepted GitHub Actions, GHCR, Yandex Cloud VM, and Docker Compose architecture.
- Avoid rebuilding or changing an artifact between staging and production.
- Prevent an automated merge from changing production.
- Delay billable production configuration until workload and recovery evidence exists.

## Considered options

1. Keep one manually deployed VM for every environment.
2. Run staging and production as separate Compose projects on one VM.
3. Use the current preemptible VM for staging and a later separate non-preemptible VM for production.

## Decision

Use the current preemptible VM as staging. A merge to `main` builds an image identified by commit SHA
and deploys it automatically to staging. Production will run on a separate non-preemptible VM and
will receive only an image that the staging deployment recorded successfully. Production promotion
requires a manual workflow dispatch and approval through the GitHub `production` environment.

Staging and production have separate hosts, credentials, configuration, Compose projects, database
volumes, and recovery lifecycles. Production sizing and provisioning remain deferred until the
evidence gate in the related design is satisfied. Any command that can change Yandex Cloud charges
requires explicit manual confirmation immediately before execution.

## Consequences

### Positive

- The same immutable image is tested and promoted.
- A staging failure blocks production promotion.
- Staging preemption cannot become a production outage.
- Production cost decisions remain evidence-based.

### Negative

- Two environments require separate secrets and operating procedures.
- A single production VM remains an availability boundary.
- Production promotion and recovery require explicit operator involvement.

### Follow-up

- Harden the current VM as disposable staging.
- Collect production sizing, cost, RTO, RPO, backup, and restore evidence.
- Provision production only after a separately confirmed billable change.

## Validation and rollback

Validate automatic staging deployment, same-image manual promotion, health-check failure behavior,
application rollback, and database restore before admitting production data. Reconsider this decision
if measured recovery or availability requirements cannot be met by separate Compose VMs.

## References

- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [Deployment design](../superpowers/specs/2026-07-11-staging-production-deployment-design.md)
- [Architecture evolution stages](../architecture.md#evolution-stages)
