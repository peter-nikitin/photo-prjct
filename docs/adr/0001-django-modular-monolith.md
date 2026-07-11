# 0001: Use a Django modular monolith

- Status: Accepted
- Date: 2026-07-11
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The MVP combines catalog, ingestion, search, moderation, commerce, and operational workflows. The
existing repository already runs Django, while product boundaries and ML workload characteristics
are still being validated. Independent services would add deployment and consistency costs before
those boundaries are understood.

## Decision drivers

- Deliver the MVP with a small team and simple operations.
- Reuse the working Django application, admin, ORM, templates, and authentication capabilities.
- Preserve clear module boundaries without requiring distributed transactions.
- Allow specialized processing runtimes to evolve independently when evidence justifies it.

## Considered options

1. A Django modular monolith.
2. Separate API and domain microservices from the start.
3. Replace Django with a FastAPI backend.

## Decision

Build the initial product as a Django modular monolith. Organize capabilities into cohesive Django
apps and application services with explicit interfaces. Background workers or ML containers may be
separate processes, but Django remains the owner of product rules and transactional state.

## Consequences

### Positive

- Fast delivery using the current code and one transactional boundary.
- Simple local development, deployment, and debugging.
- Django admin can support early operator workflows.

### Negative

- Module discipline must be maintained inside one codebase.
- CPU/GPU-heavy processing cannot execute in request handlers.

### Follow-up

- Define module boundaries as product capabilities are implemented.
- Record a new ADR before extracting a separately deployed business service.

## Validation and rollback

Reconsider when measured scaling, release isolation, fault isolation, or team ownership requirements
cannot be met by separate processes or modules. Extraction must follow observed coupling and include
a migration plan.

## References

- [Architecture: accepted constraints](../architecture.md#accepted-constraints)
