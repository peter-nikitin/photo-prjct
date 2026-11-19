---
name: write-adr
description: Use when proposing, recording, revisiting, rejecting, or superseding a durable architecture decision in the FindMe Photo repository.
---

# Write an Architecture Decision Record

## Purpose

Record durable architectural choices. Use the
[documentation scope in AGENTS.md](../../../AGENTS.md#documentation-scope) to decide where to
record each fact.

## Workflow

1. Read `docs/architecture.md`, `docs/adr/README.md`, all ADRs relevant to the topic, and any linked
   approved specification, plan, or experiment. Inspect the current code when the decision depends
   on implemented behavior.
2. Identify the durable architectural choice and its meaningful alternatives before drafting an
   ADR. Use the documentation scope to place implementation details, including updates to details
   recorded in older ADRs.
3. Stop and request the missing decision when product intent, legal policy, experimental evidence,
   or authority is required. Never turn an open question into an accepted decision by assumption.
   Approval of a specification is decision evidence, not automatic ADR acceptance. Keep a derived
   ADR `Proposed` until the maintainer explicitly approves that architectural decision, unless the
   approval request named the exact ADR decision being accepted.
4. Allocate the next unused four-digit number from `docs/adr/README.md`. Use
   `NNNN-short-title.md`; never reuse a number or rename an accepted record.
5. Copy `docs/adr/0000-template.md`. State context and decision drivers before options. Include only
   viable options, make boundaries explicit, and record positive and negative consequences.
6. Set the initial status to `Proposed` unless the user or maintainers explicitly approve
   acceptance. To change an accepted decision, write a new ADR and cross-link both records with
   `Supersedes` and `Superseded by`.
7. Add the record to `docs/adr/README.md` in numeric order. Update `docs/architecture.md` only when
   the decision changes its system summary, status, boundary, or open-decision list.
8. Validate filenames, links, status consistency, and the absence of contradictions. Report any
   follow-up experiment or implementation plan separately.

## Quality rules

- Write new ADRs in Russian with concise, neutral language; leave existing English ADRs as they are.
- Explain why the decision is appropriate now; do not write a retrospective narrative.
- Keep detailed task sequences, commands, and file inventories in temporary plans; retain only
  accepted durable decisions and their reasons in the ADR after merge.
- Treat accepted ADR content as immutable except for spelling, formatting, and link corrections.
- Preserve unresolved storage, queue, vector, ML, payment, and biometric choices until evidence and
  authority exist.
