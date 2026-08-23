# 0033: Keep durable knowledge and test executable contracts

- Status: Proposed
- Date: 2026-08-23
- Deciders: project maintainers
- Supersedes: ADR 0004
- Superseded by: none

## Context

FindMe Photo stores architecture, ADRs, plans, job registries, runbooks, and project agent skills in
the repository. This keeps decisions discoverable, but ADR 0004's follow-up to validate document
structure and skill metadata in CI expanded into tests for exact prose, copied commands, CSS
fragments, historical names, and duplicated configuration claims.

Those checks make harmless documentation and implementation refactors fail together. They also
make narrative text appear as authoritative as executable scripts, manifests, settings, workflows,
and product behavior. The maintenance cost now conflicts with ADR 0004's original driver to keep
documentation lightweight for a small project.

## Decision drivers

- Preserve durable, reviewable architecture decisions beside the implementation.
- Keep executable safety contracts close to the behavior that can cause product or operational
  harm.
- Reduce CI coupling to wording and duplicated narrative state.
- Give human and agentic contributors one discoverable source for durable project context.
- Spend routine verification time on critical product, privacy, persistence, and rollback paths.

## Considered options

1. Retain ADR 0004 and continue expanding exact repository-document tests.
2. Keep durable engineering decisions in Git while limiting CI to stable structure and executable
   contracts.
3. Remove structured repository knowledge and rely on conversations, issues, or an external wiki.

## Decision

Select option 2.

Keep current architecture, ADRs, active implementation plans, evidence-backed job registries, and
project-specific agent workflows in the repository. Link these sources and remove obsolete or
duplicated narrative material rather than testing it into permanence.

CI may validate stable machine-readable properties: ADR file/index/status/link consistency,
supported agent-skill metadata schemas, and local-link integrity. CI must not treat exact prose,
copied shell commands, section wording, historical counts, CSS fragments, or semantic agreement
between duplicated narratives as executable contracts.

Executable product and operational authority lives in code, schemas, scripts, manifests, settings,
and workflows. Tests assert their observable outcomes, invariants, and forbidden side effects.
Documentation links to those sources and is reviewed when a change alters a durable decision,
current architecture boundary, accepted operator workflow, or evidence-backed job status.

## Consequences

### Positive

- Durable decisions remain versioned, searchable, reviewable, and available to every contributor.
- Documentation edits and harmless refactors cause less unrelated CI churn.
- Tests concentrate on behavior and stable structure rather than wording.
- Executable and narrative sources have clearer authority.

### Negative

- CI cannot prove that narrative explanations are semantically current.
- Reviewers must identify changes that alter durable architecture or operator guidance.
- Some stale prose may survive until the affected area is reviewed.

### Follow-up

- Replace exact-text repository tests with structural validation and ordinary review guidance.
- Add a shared changed-path suite selector and an agent verification skill.
- Compare documentation-test churn, test runtime, and escaped documentation defects after the
  refactor.

## Validation and rollback

Validate the decision by confirming that contributors and agents can still locate current accepted
decisions, structural drift fails CI, executable critical paths retain regression coverage, and
routine implementation changes no longer require unrelated prose updates.

Reconsider if missing or stale repository knowledge repeatedly causes incorrect implementation,
unsafe operation, or contradictory accepted decisions. Restoring broad semantic documentation
enforcement or moving the source of truth requires a superseding ADR; individual high-risk
machine-readable contracts may be added without changing this decision.

## References

- [Pareto test-suite refactor design](../superpowers/specs/2026-08-23-pareto-test-suite-refactor-design.md)
- [Architecture change rules](../architecture.md#change-rules)
- [ADR 0004](0004-repository-engineering-knowledge.md)
