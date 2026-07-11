# 0003: Deploy with Docker Compose to Yandex Cloud

- Status: Accepted
- Date: 2026-07-11
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The project needs repeatable deployment before product complexity grows. The repository already
builds a Docker image in GitHub Actions, publishes it to GHCR, and deploys Django plus PostgreSQL to
a Yandex Cloud VM with Docker Compose.

## Decision drivers

- Preserve an operational path that already exists.
- Minimize infrastructure complexity and recurring cost.
- Keep local and production service definitions conceptually aligned.
- Support additional containers without adopting an orchestrator prematurely.

## Considered options

1. Docker Compose on a Yandex Cloud VM.
2. Kubernetes or a managed container orchestrator.
3. Install Python and PostgreSQL directly on the VM.

## Decision

Build application images in GitHub Actions, publish them to GHCR, and deploy production containers
with Docker Compose on the designated Yandex Cloud VM. Environment configuration and secrets are
provided at deployment time and are not committed.

## Consequences

### Positive

- Small operational surface and repeatable container deployment.
- The current pipeline remains usable.

### Negative

- The VM is an explicit capacity and availability boundary.
- Rolling deployment, secret management, and failover require deliberate procedures.

### Follow-up

- Add HTTPS edge, health checks, monitoring, backups, and rollback evidence before launch.
- Revisit orchestration only when availability or scale targets exceed this topology.

## Validation and rollback

Validate with repeatable deployments, health checks, and tested database restore. A superseding
platform decision must define migration, rollback, cost, and operational ownership.

## References

- [Architecture: current architecture](../architecture.md#current-architecture--implemented)
