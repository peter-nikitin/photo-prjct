# Specification and ADR Reconciliation Design

- Date: 2026-07-19
- Status: Approved design
- Owner: project maintainer
- Related architecture: [Document purpose](../../architecture.md#document-purpose),
  [Architectural status vocabulary](../../architecture.md#architectural-status-vocabulary), and
  [Change rules](../../architecture.md#change-rules)
- Related ADRs: [ADR 0004](../../adr/0004-repository-engineering-knowledge.md)
- ADR impact: `Conforms to ADR 0004`

## Goal

Make architecture and ADR reconciliation an explicit gate when FindMe Photo specifications are
prepared, approved, converted into implementation plans, and finally delivered.

## Problem

The repository already requires plan authors and operational-change authors to read relevant ADRs,
and the ADR workflow explains how to record or supersede a decision. The specification workflow is
not project-specific, however. Its generic context review does not explicitly require reading
`docs/architecture.md`, the ADR index, and applicable ADRs. After a written specification is
approved, it transitions directly to planning without an explicit ADR-impact resolution gate.

Some existing specifications compensate with related-ADR metadata or an ADR gate, but that practice
is not consistent or required. Completion workflows likewise verify code and tests without always
reconciling delivered facts with the approved specification, architecture summary, and applicable
ADRs.

## Decision boundaries

This change operationalizes ADR 0004: architecture remains in `docs/architecture.md`, decisions in
`docs/adr`, plans in `docs/plans`, and project workflows in `.agents/skills`. It does not change
those sources of truth, edit accepted ADR content, or introduce a new architectural choice that
requires another ADR.

The workflow applies prospectively. Existing specifications do not need mechanical metadata-only
rewrites. They are reconciled when they are next used for planning, implementation, or architecture
review.

## Specification preparation

A new project skill, `$write-spec`, augments the generic brainstorming workflow for FindMe Photo.
Before the author proposes approaches or writes a design, it requires them to read:

1. the relevant current, accepted, proposed, and open-decision sections of
   `docs/architecture.md`;
2. `docs/adr/README.md` and every ADR applicable to the proposed boundary;
3. linked plans, specifications, experiments, and product or engineering jobs;
4. current implementation and tests when the proposal depends on implemented behavior.

The written specification must include these metadata fields:

- `Related architecture`: exact links to relevant sections;
- `Related ADRs`: exact links, or `none` only after the ADR index has been checked;
- `ADR impact`: exactly one of the classifications below.

| ADR impact | Meaning |
| --- | --- |
| `None — reversible implementation detail` | The specification makes no durable architecture choice. |
| `Conforms to ADR NNNN` | The design stays inside one or more accepted decisions. |
| `Requires new ADR` | Approval selects a durable choice not governed by an accepted ADR. |
| `Supersedes ADR NNNN` | Approval changes an existing accepted decision. |

Multiple conforming ADR numbers may be listed in one `Conforms` classification. A specification
with more than one independent new or superseding decision may list each decision separately, but
must not combine unrelated decisions into one future ADR.

During spec self-review, the author checks the proposed design against the complete applicable ADR
text, not only the ADR title or architecture summary. A contradiction blocks approval until the
design is revised or explicitly classified as superseding the affected ADR.

## Approval and ADR resolution

User approval of a specification selects the design but does not silently accept an ADR. After the
written specification is approved, `$write-plan` performs ADR reconciliation before creating an
implementation plan:

1. For `None`, record that no ADR or architecture change is required.
2. For `Conforms`, verify the final approved text still stays within the cited ADR boundaries.
3. For `Requires new ADR`, invoke `$write-adr`, create a `Proposed` ADR from the approved design, and
   request explicit maintainer acceptance before planning relies on it as authoritative.
4. For `Supersedes`, invoke `$write-adr`, create a new cross-linked ADR, and request explicit
   maintainer acceptance before replacing the earlier decision.
5. Update `docs/architecture.md` only when an accepted decision changes its summary, status,
   boundary, or open-decision list. Do not describe unimplemented behavior as current architecture.

Planning is blocked while a required ADR remains missing, proposed without the required authority,
contradictory, or unlinked. Once the impact is resolved, the plan links the approved specification,
the final applicable ADRs, and the relevant architecture sections.

## Implementation completion

Every plan includes a final architecture and ADR reconciliation task after behavior verification and
before push. The implementer compares delivered behavior with the approved specification,
applicable ADRs, and architecture status:

- update implemented facts in `docs/architecture.md` when system boundaries, topology, or status
  changed;
- leave accepted ADRs unchanged when delivery conforms;
- stop and obtain a decision when delivery would contradict an accepted ADR;
- create a new superseding ADR rather than editing an accepted ADR when the approved decision must
  change;
- report one explicit result in the pull request: no ADR impact, conformance, architecture update,
  new ADR, or superseding ADR.

The small operational fast lane may omit a specification and plan, but its project skill performs
the same reconciliation before opening the pull request.

## Repository changes

### New project skill

Create `.agents/skills/write-spec/` with:

- `SKILL.md` for triggers, source inspection, ADR-impact classification, approval semantics, spec
  self-review, and handoff to `$write-plan`;
- `agents/openai.yaml` with the same required UI metadata as existing project skills.

### Existing project workflows

- Extend `$write-plan` with the post-spec ADR-resolution gate and mandatory final reconciliation
  task.
- Extend `$write-adr` to treat an approved specification as decision evidence while preserving
  explicit acceptance authority and accepted-ADR immutability.
- Extend `$deliver-operational-change` with pre-PR architecture and ADR reconciliation for work that
  legitimately bypasses a specification and plan.
- Extend `docs/plans/0000-template.md` with a related-specification field, resolved ADR-impact field,
  and a final reconciliation task.
- Extend `docs/architecture.md` change rules with the specification and completion lifecycle.

Root `AGENTS.md` remains a navigation-only map. No changing workflow detail or enumerated skill list
is added there.

## Validation

- Add `write-spec` to the existing structural project-skill UI validation.
- Run the repository skill validator against the new skill and each modified skill.
- Run the repository-foundation test suite and normal formatting/lint checks for changed executable
  files.
- Review one `None`, one `Conforms`, one `Requires new ADR`, and one `Supersedes` scenario against
  the written workflow to confirm each has an unambiguous next action.
- Do not add tests that merely assert Markdown prose. Existing executable validation remains the
  repository-contract boundary.

## Rollback

Revert the workflow, template, architecture-rule, and structural-test changes together. Existing
ADRs, specifications, and implementation plans remain valid because the change is prospective and
does not rewrite their content or status.
