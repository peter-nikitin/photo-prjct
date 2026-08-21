---
name: write-plan
description: Use when planning multi-step, cross-cutting, migration-sensitive, or operationally significant work in the FindMe Photo repository.
---

# Write an Implementation Plan

## Purpose

Turn an approved outcome into decision-complete, verifiable work that follows the repository's
architecture and accepted ADRs.

## Document contract

An implementation plan answers **in what sequence we will deliver and verify the approved
outcome**. When an approved specification exists, treat it as the authoritative source for design
decisions, scope, constraints, behavior, schemas, algorithms, failure semantics, and acceptance
criteria. Link to the applicable specification sections instead of restating them.

A plan contains:

- ordered, cohesive implementation tasks and their dependencies;
- exact files to create or modify when known;
- interfaces only where one task produces something a later task consumes;
- a concise failing-test, implementation, and targeted-verification cycle for behavior changes;
- exact verification commands and expected outcomes;
- operational ordering, rollout, rollback, and architecture reconciliation; and
- unresolved blockers that prevent execution.

When execution will use subagents, reference `$execute-implementation-plan` once. Keep stable
worktree, role, Git, report, review-loop, and model-selection rules in that skill instead of
copying them into the plan. Plans contain only task-specific ownership, requirements, interfaces,
dependencies, verification, and operational ordering.

For each task, state the deliverable, affected paths, prerequisite specification sections, and
observable completion check. Do not embed production implementation code or complete test bodies.
Use short pseudocode only when execution order itself is otherwise ambiguous; design-level
algorithms belong in the specification.

When no approved specification exists, the plan may briefly state scope and acceptance criteria
needed to make the sequence understandable. When one exists, those sections contain a link plus
task-specific deltas only.

This project contract overrides generic planning templates that require code snippets in every
step. A plan is implementation-ready because decisions live in its approved specification and its
steps are executable, not because it duplicates the future patch.

## Worker/state/artifact plan gate

When the observable plan scope changes a worker contract, durable processing state, or a
generated/derived artifact, retain and complete the template's exact
`Worker/state/artifact release safeguards` section. Complete its seven checkable slots: Live-state
inventory; Compatibility matrix; Reviewed data-state migration or reset semantics; End-to-end
contract sizing; Previous-snapshot upgrade rehearsal; Staged activation and rollback order; and
Supported bounded operational commands. A plan is blocked when any slot outcome is unknown; return
the unknown to its decision owner before implementation. The template is the structural source of
truth; use the [2026-07-31 staging processing-state reset postmortem](../../../docs/postmortems/2026-07-31-staging-processing-state-reset.md)
for rationale rather than copying its prose.

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
3. Confirm that the specification resolves the goal, scope boundaries, acceptance criteria,
   compatibility requirements, and operational constraints. Stop for user input only when a
   missing choice materially changes the outcome; update the specification rather than deciding it
   inside the plan.
4. Detect architectural decisions hidden in the request. Invoke `$write-adr` before planning when
   the work requires a durable choice that is neither accepted nor safely reversible. Never let a
   plan silently override an accepted ADR.
5. Copy `docs/plans/0000-template.md` to `docs/plans/YYYY-MM-DD-topic.md`. Link the approved
   specification, exact architecture sections, and resolved ADR impact; write `none` only after
   checking. Apply the Worker/state/artifact plan gate when its observable condition is true.
6. Decompose work into independently verifiable tasks. Name exact paths, cross-task interfaces,
   migrations, and commands where they are known. Reference approved data flow and failure handling
   instead of copying them. For behavior changes, order steps as failing test, minimal
   implementation, targeted verification, then regression checks.
7. Specify observable acceptance criteria and exact verification commands. Include expected
   outcomes rather than vague instructions such as “test thoroughly.”
8. Describe configuration, deployment order, compatibility, monitoring, and rollback. State `None`
   explicitly when a section has no runtime effect.
9. End every plan with architecture and ADR reconciliation after behavior verification and before
   push. Require one explicit outcome: no ADR impact, conformance, architecture update, new ADR, or
   superseding ADR.
10. Remove unresolved implementation choices. If an open question remains, mark the plan blocked or
   return to the decision owner instead of leaving the implementer to guess.
11. For subagent execution, add one instruction to use `$execute-implementation-plan`; do not repeat
    its orchestration contract in the plan or individual tasks.

## Quality rules

- Use English and lowercase hyphenated filenames.
- Prefer cohesive behavior-level tasks over a long file-by-file inventory.
- Keep every approved decision in one place: the specification. A plan references that decision
  and adds sequence, ownership, dependencies, and verification.
- During self-review, compare the plan with its specification and remove paragraphs that explain
  the same behavior without adding an implementation dependency or action.
- Keep the production dependency and Docker deployment model stable unless the request changes it.
- Do not introduce proposed services or technologies merely because they appear in the target
  architecture.
- Update related documentation in the same plan when delivered behavior changes architectural facts.
