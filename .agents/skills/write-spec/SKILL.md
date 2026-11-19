---
name: write-spec
description: Use when preparing, reviewing, or approving a FindMe Photo feature or system specification before implementation planning.
---

# Write a Project Specification

## Purpose

Augment design work with FindMe Photo architecture and ADR reconciliation. Produce an approved
specification whose architectural impact is explicit before `$write-plan` begins. Follow the
[documentation scope in AGENTS.md](../../../AGENTS.md#documentation-scope).

**REQUIRED SUB-SKILL:** Use `superpowers:brainstorming` for intent, alternatives, incremental design
approval, written review, and transition to planning.

## Document contract

A specification answers **what we are building and why**. It is the authoritative source for:

- intended outcome and observable behavior;
- scope and explicit boundaries;
- selected design and rejected alternatives;
- components and data flow at the design level;
- durable constraints, invariants, compatibility, privacy, and failure semantics;
- acceptance criteria; and
- architecture and ADR impact.

Record enough technical detail to make the selected behavior unambiguous. Include an interface,
schema, algorithm rule, or short pseudocode fragment only when it is part of the approved design
contract.

The specification does not schedule the work. Implementation order, file-by-file edits, TDD
cycles, command checklists, reviewer gates, rollout steps, and ordinary implementation examples
belong in the implementation plan. A plan may link back to a specification section; therefore the
specification must not anticipate the plan by duplicating those mechanics.

## Workflow

1. Before proposing approaches, read the relevant sections of `docs/architecture.md`, the ADR
   index and applicable decisions, and the implementation and tests for existing behavior.
2. Resolve conflicts with accepted architectural choices before approval. Read the decision text
   to distinguish the choice from historical implementation details. Revise the design or
   classify the architectural choice it supersedes.
3. Save the design temporarily under ignored `docs/superpowers/specs/YYYY-MM-DD-topic-design.md`
   with exact `Related architecture`, `Related ADRs`, and `ADR impact` metadata. Write prose in Russian.
4. During self-review, verify scope, internal consistency, ambiguity, applicable ADR boundaries,
   separation of accepted architecture from unimplemented design, and compliance with the document
   contract above.
5. Obtain user review of the written specification. Approval selects the design but does not
   silently accept a new or superseding ADR.
6. Invoke `$write-plan` after approval. Pass the approved specification and its ADR-impact
   classification so planning begins with ADR resolution.
7. Before merge, update the relevant long-lived documents using the documentation scope. Remove
   the specification from the Git index and final commit.

## Required ADR impact

| Classification | Use when |
| --- | --- |
| `None — reversible implementation detail` | No durable architecture choice is made. |
| `Conforms to ADR NNNN` | The design stays within an accepted architectural decision. |
| `Requires new ADR` | The design introduces a durable architectural choice. |
| `Supersedes ADR NNNN` | The design changes an accepted architectural choice. |

Use `Related ADRs: none` only after checking the ADR index. Multiple conforming ADRs may share one
classification; list independent new or superseding decisions separately.

## Boundaries

- Use `$write-adr` when an accepted architectural choice needs a successor.
- Old specifications remain available in Git history. Reconcile them only when needed.
