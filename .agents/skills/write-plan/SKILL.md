---
name: write-plan
description: Use when planning multi-step, cross-cutting, migration-sensitive, or operationally significant work in the FindMe Photo repository.
---

# Write an Implementation Plan

## Purpose

Turn an approved outcome into decision-complete, verifiable work that follows the repository's
architecture and accepted ADRs.

## Operational fast lane

Do not require a plan file for a small, reversible single VM or domain change that is already
governed by an accepted ADR and the existing deployment entrypoint. Record the scope and acceptance
checks in the pull request instead. Take a scope checkpoint and return to the normal planning
workflow when the change introduces multi-environment coordination, persistent release state, a
data migration, a pricing-affecting cloud action, or a conflict with an accepted ADR.

## Workflow

1. Inspect the relevant implementation, tests, deployment configuration, `docs/architecture.md`,
   `docs/adr/README.md`, and applicable ADRs. Resolve discoverable facts from the repository.
2. When an approved specification exists, read it completely and resolve its `ADR impact` before
   creating a plan file:
   - `None — reversible implementation detail`: record that no ADR or architecture update is needed;
   - `Conforms to ADR NNNN`: verify the final text remains inside every cited ADR boundary;
   - `Requires new ADR`: invoke `$write-adr` and obtain explicit maintainer acceptance;
   - `Supersedes ADR NNNN`: invoke `$write-adr`, cross-link both records, and obtain explicit
     maintainer acceptance.
   Block planning when a required ADR is missing, still proposed without authority, contradictory,
   or unlinked. Specification approval alone is not ADR acceptance.
3. Confirm the goal, scope boundaries, acceptance criteria, compatibility requirements, and
   operational constraints. Stop for user input only when a missing choice materially changes the
   outcome.
4. Detect architectural decisions hidden in the request. Invoke `$write-adr` before planning when
   the work requires a durable choice that is neither accepted nor safely reversible. Never let a
   plan silently override an accepted ADR.
5. Copy `docs/plans/0000-template.md` to `docs/plans/YYYY-MM-DD-topic.md`. Link the approved
   specification, exact architecture sections, and resolved ADR impact; write `none` only after
   checking.
6. Decompose work into independently verifiable tasks. Name exact paths, interfaces, data flow,
   failure handling, migrations, and commands where they are known. For behavior changes, order
   steps as failing test, minimal implementation, targeted verification, then regression checks.
7. Specify observable acceptance criteria and exact verification commands. Include expected
   outcomes rather than vague instructions such as “test thoroughly.”
8. Describe configuration, deployment order, compatibility, monitoring, and rollback. State `None`
   explicitly when a section has no runtime effect.
9. End every plan with architecture and ADR reconciliation after behavior verification and before
   push. Require one explicit outcome: no ADR impact, conformance, architecture update, new ADR, or
   superseding ADR.
10. Remove unresolved implementation choices. If an open question remains, mark the plan blocked or
   return to the decision owner instead of leaving the implementer to guess.

## Quality rules

- Use English and lowercase hyphenated filenames.
- Prefer cohesive behavior-level tasks over a long file-by-file inventory.
- Keep the production dependency and Docker deployment model stable unless the request changes it.
- Do not introduce proposed services or technologies merely because they appear in the target
  architecture.
- Update related documentation in the same plan when delivered behavior changes architectural facts.
