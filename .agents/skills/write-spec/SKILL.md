---
name: write-spec
description: Use when preparing, reviewing, or approving a FindMe Photo feature or system specification before implementation planning.
---

# Write a Project Specification

## Purpose

Augment design work with FindMe Photo architecture and ADR reconciliation. Produce an approved
specification whose architectural impact is explicit before `$write-plan` begins.

**REQUIRED SUB-SKILL:** Use `superpowers:brainstorming` for intent, alternatives, incremental design
approval, written review, and transition to planning.

## Workflow

1. Before proposing approaches, read the relevant current, accepted, proposed, and open-decision
   sections of `docs/architecture.md`; `docs/adr/README.md`; every applicable ADR; linked plans,
   specifications, experiments, and jobs; and implementation/tests when behavior already exists.
2. Resolve contradictions before approval. Revise the design or classify it as superseding the
   affected ADR; never rely only on an ADR title or architecture summary.
3. Save the design under `docs/superpowers/specs/YYYY-MM-DD-topic-design.md` with exact `Related
   architecture`, `Related ADRs`, and `ADR impact` metadata.
4. During self-review, verify scope, internal consistency, ambiguity, applicable ADR boundaries,
   and separation of accepted architecture from unimplemented design.
5. Obtain user review of the written specification. Approval selects the design but does not
   silently accept a new or superseding ADR.
6. Invoke `$write-plan` after approval. Pass the approved specification and its ADR-impact
   classification so planning begins with ADR resolution.

## Required ADR impact

| Classification | Use when |
| --- | --- |
| `None — reversible implementation detail` | No durable architecture choice is made. |
| `Conforms to ADR NNNN` | The design stays inside accepted decisions. |
| `Requires new ADR` | Approval selects a durable choice not yet governed by an accepted ADR. |
| `Supersedes ADR NNNN` | Approval changes an accepted decision. |

Use `Related ADRs: none` only after checking the ADR index. Multiple conforming ADRs may share one
classification; list independent new or superseding decisions separately.

## Boundaries

- Do not edit an accepted ADR to match a specification; use `$write-adr` to supersede it.
- Do not update current implemented architecture merely because a specification was approved.
- Do not require an ADR for reversible implementation detail.
- Do not mechanically rewrite old specifications; reconcile them when next used.
