# 0002: Use PostgreSQL as the system of record

- Status: Accepted
- Date: 2026-07-11
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Events, photos, processing states, manual corrections, carts, orders, entitlements, and audit records
require relational integrity and transactional updates. The existing Django application is already
configured for PostgreSQL.

## Decision drivers

- Strong transactions and constraints for commerce and moderation.
- Mature Django ORM and migration support.
- Operational simplicity for the initial deployment.
- Ability to add JSON, full-text, or vector capabilities after evaluation.

## Considered options

1. PostgreSQL as the transactional system of record.
2. A document database as the primary store.
3. Separate databases for each proposed module.

## Decision

Use PostgreSQL as the authoritative store for product and operational metadata. Binary media belongs
in private object storage once that capability is selected. Search indexes and ML stores are derived
and rebuildable; they do not become authoritative product state.

## Consequences

### Positive

- One consistent source for business rules and entitlements.
- Straightforward migrations, backups, and local parity.

### Negative

- High-volume derived vectors or search workloads may later require a specialized store.
- Schema and index changes require deployment-safe migration practices.

### Follow-up

- Decide media storage and vector search separately after representative measurements.
- Establish backup, restore, and migration-safety procedures before production commerce.

## Validation and rollback

Reconsider a derived workload only when load tests show PostgreSQL cannot meet documented targets.
Product truth remains in PostgreSQL unless a superseding ADR includes consistency and migration rules.

## References

- [Architecture: target MVP](../architecture.md#target-mvp-architecture--proposed)
