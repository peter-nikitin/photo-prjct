# 0032: Reconcile code-owned feature flags at startup

- Status: Accepted
- Date: 2026-08-22
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR 0028 makes database-backed `off` / `staff` / `on` release gates the mechanism for deploying
incomplete customer-facing behavior to the one canonical deployment. The database currently has no
authoritative way to discover which flag rows a release requires. A maintainer must create rows by
hand after deployment, while missing rows fail closed as `off`.

Feature definitions are code-owned, but their keys and descriptions are repeated as string literals
at call sites and in local bootstrap code. Per-flag data migrations would couple temporary release
controls to schema history. Creating rows during ordinary requests would introduce hidden writes and
races. A durable definition-delivery mechanism is required before feature-gated releases become the
normal workflow.

## Decision drivers

- A pull request must deliver every feature definition without activating customer behavior.
- Existing operator-selected `off`, `staff`, and `on` states must survive ordinary deployments.
- Removed feature branches must not leave actionable stale rows in Django Admin.
- Definition reconciliation must be visible, deterministic, idempotent, and fail closed during
  application startup.
- Django Admin remains the only interface for changing the state of an active definition.

## Considered options

1. Keep an authoritative code registry and reconcile it explicitly after migrations at startup.
2. Add a Django data migration for every feature definition and removal.
3. Reconcile definitions through a `post_migrate` signal.

## Decision

Select option 1.

Maintain one code registry of immutable feature definitions. Each definition contains a stable key
and operator-facing description. Production call sites pass a registry definition to the evaluation
service instead of an unregistered string key.

After `migrate` and before other application bootstrap or Gunicorn startup, run an explicit,
transactional reconciliation command. It validates the registry before mutation, creates missing
rows in `off`, updates descriptions, preserves every existing recognized row's state, and deletes
rows absent from the registry. Repeating the command produces the same database state. Any failure
prevents the candidate application from becoming healthy.

The registry cannot declare `staff` or `on`; activation remains a separate permission-protected
Django Admin action. Admin permits changing only `state`. It cannot add or delete definitions or
edit their keys and descriptions. Removing a definition is a code and deployment decision after its
call sites are removed. Existing Django Admin history remains durable after the row is reconciled
away.

This decision extends ADR 0028 without superseding it. Release gates remain temporary exposure
controls, not authorization, publication state, migration safety, or infrastructure capability
switches.

## Consequences

### Positive

- A merged feature arrives with an immediately visible, safely disabled Admin row.
- Deployments preserve deliberate operator activation while keeping definitions synchronized with
  the running image.
- Type-checked definition objects make unregistered production call sites harder to introduce.
- Removing a completed or rejected gate also removes its inactive Admin surface.
- Startup logs provide one explicit reconciliation result instead of implicit signal or request
  writes.

### Negative

- Application startup performs a small transactional database mutation after migrations.
- Removing a definition intentionally discards its current state. Rolling back to code that contains
  it recreates it as `off`, so rollback is safe but does not restore prior activation.
- A registry or reconciliation defect prevents the candidate web container from becoming healthy.
- Adding or removing a feature definition requires deployment even though changing its state does
  not.

### Follow-up

- Implement the registry, strict evaluation interface, reconciliation command, Admin restrictions,
  startup ordering, and focused deployment contracts.
- Inventory the canonical database before first rollout and verify that every deleted key is absent
  from the deployed code registry.
- Remove temporary feature definitions together with their final guarded branches after rollout is
  stable or the feature is rejected.

## Validation and rollback

Tests must prove first creation in `off`, preservation of existing states, description refresh,
stale-row deletion, idempotence, Admin immutability, strict call-site definitions, and startup order.
The first canonical rollout records sanitized created, updated, preserved, and deleted counts and
then verifies the resulting keys and states read-only.

Rollback deploys the previous image through the existing mechanism. Its registry reconciliation
removes definitions unknown to that image and recreates any restored definitions in `off`. Revisit
this decision if startup reconciliation materially delays availability, repeatedly deletes an
expected definition, or feature definitions require ownership outside the application release.

## References

- [ADR 0028](0028-operate-one-canonical-deployment.md)
- [Feature-flag definition reconciliation design](../superpowers/specs/2026-08-22-feature-flag-definition-reconciliation-design.md)
- [Current architecture](../architecture.md)
