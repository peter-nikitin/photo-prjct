# 0004: Keep engineering knowledge in the repository

- Status: Accepted
- Date: 2026-07-11
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The product brief, current implementation, architectural choices, and future work must remain
distinguishable and reviewable. Decisions held only in conversations become difficult to discover
and drift away from code.

## Decision drivers

- Review architecture changes together with code.
- Give human and agentic contributors the same durable context.
- Avoid dependence on a private external knowledge system.
- Keep documentation lightweight enough for a small project.

## Considered options

1. Version architecture, ADRs, plans, and project skills in this repository.
2. Keep engineering knowledge in an external wiki only.
3. Use issues and commit messages without structured documents.

## Decision

Store architecture in `docs/architecture.md`, decisions in `docs/adr`, implementation plans in
`docs/plans`, and project workflows in `.agents/skills`. Link these sources rather than duplicating
their content. Update relevant documentation in the same change as the behavior it describes.

## Consequences

### Positive

- Knowledge is versioned, searchable, reviewable, and available offline.
- Project-scoped skills can enforce consistent authoring workflows.

### Negative

- Contributors must actively prevent stale documents and broken indexes.
- Repository reviews include documentation quality.

### Follow-up

- Validate document structure and skill metadata in CI.
- Add ADRs and plans only when they carry durable information.

## Validation and rollback

Review whether contributors can locate current decisions and whether CI catches structural drift.
External tools may mirror these documents, but a new ADR is required before changing the source of
truth.

## References

- [Architecture change rules](../architecture.md#change-rules)
