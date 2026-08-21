# Runtime credential hygiene and recovery

## Observed gap

EJ-017 and ADR 0028 established a single Lockbox authority and one canonical deployment. That
design trigger has fired: the repository does not yet have an accepted EJ-018 decision for the
runtime credential lifecycle across `docker-compose.deployment.yml`, the host, containers,
registry authentication, TLS material, backups, and recovery.

The 2026-08-07 sanitized inspection is historical input, not a current canonical-host inventory.
This documentation change performs no inventory and makes no claim about the current host,
credential surfaces, cloud IAM, snapshots, backups, or operator access.

## Why it is non-blocking

EJ-017 delivers the upstream secret authority and supported projections, but it does not decide how
runtime components receive, retain, rotate, revoke, recover, or prove non-disclosure of their
credentials. Selecting a runtime mechanism now would couple restart, rollback, Compose, Certbot,
backup, and disaster-recovery behavior without the evidence or approved design required for EJ-018.

## Revisit trigger

Perform a read-only canonical-host inventory, then prepare and accept an EJ-018 design before any
runtime credential cleanup, rotation, host access change, Compose change, backup/restore change, or
credential-delivery implementation. An accepted EJ-018 design after that inventory is the next
implementation trigger.

## Likely scope

Start with a fresh read-only canonical-host inventory across repository workflows, Docker metadata,
VM metadata/service-account IAM, volumes, snapshots/images/backups, operator accounts, and recovery
procedures. The resulting EJ-018 specification must then select and reconcile:

- the minimum credential projection for web, database, each worker, Nginx, Certbot, monitoring, and
  deployment tooling;
- whether runtime values use persistent protected files, Docker secrets/configs, file-based
  application settings, tmpfs materialization, runtime Lockbox retrieval, or another supported
  mechanism;
- restart behavior when GitHub, Yandex IAM, or Lockbox is unavailable;
- registry login without an indefinitely retained reusable token;
- Docker/sudo/SSH membership and the effective root-equivalent trust boundary;
- TLS-key ownership, renewal, backup, and recovery;
- snapshot and backup inclusion, encryption, access, retention, restore, and credential rotation;
- secret-safe shell/operator workflows and containment of historical exposure;
- lost-device, lost-account, break-glass, rotation, revocation, rollback, and disaster-recovery
  drills; and
- automated non-disclosure, permissions, restart, rollback, restore, and live-environment evidence.

Treat this as an architecture and operations capability, not a cleanup script. Do not mutate or
delete host state merely to satisfy the inventory; any history cleanup, access removal, credential
rotation, Docker reconfiguration, snapshot change, or runtime migration requires the accepted
EJ-018 design, its approved implementation plan, and live safety gates.
