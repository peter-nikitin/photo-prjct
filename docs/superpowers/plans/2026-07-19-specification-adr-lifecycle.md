# Specification and ADR Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ADR and architecture reconciliation mandatory when FindMe Photo specifications are prepared, approved, planned, and delivered.

**Architecture:** A new project-scoped `$write-spec` skill augments generic brainstorming with FindMe Photo source inspection and ADR-impact classification. Existing `$write-plan`, `$write-adr`, and `$deliver-operational-change` skills enforce the approval and completion gates, while architecture change rules and the plan template keep the lifecycle discoverable and repeatable.

**Tech Stack:** Markdown project skills, YAML skill metadata, pytest, Ruff, Codex skill validator

## Global Constraints

- The change conforms to accepted ADR 0004 and does not create or modify an architectural decision.
- Root `AGENTS.md` remains a navigation-only map.
- Accepted ADRs remain immutable except for spelling, formatting, and link corrections.
- Specification approval does not silently accept a new or superseding ADR.
- `docs/architecture.md` must not describe unimplemented behavior as current architecture.
- Existing specifications are not mechanically rewritten; the workflow applies prospectively.
- Do not add tests that assert Markdown prose.
- Use `skill-creator` and `superpowers:writing-skills` while creating and validating `$write-spec`.

---

### Task 1: Project specification authoring skill

**Files:**
- Create: `.agents/skills/write-spec/SKILL.md`
- Create: `.agents/skills/write-spec/agents/openai.yaml`
- Modify: `tests/test_repository_foundation.py`

**Interfaces:**
- Consumes: generic `superpowers:brainstorming`, `docs/architecture.md`, `docs/adr/README.md`, applicable ADRs, implementation evidence, and user approval.
- Produces: an approved spec with `Related architecture`, `Related ADRs`, and `ADR impact`, then hands control to `$write-plan`.

- [ ] **Step 1: Read the skill-authoring instructions**

Read these files completely before creating the project skill:

```bash
sed -n '1,360p' /Users/petrnikitin/.codex/skills/.system/skill-creator/SKILL.md
sed -n '1,420p' /Users/petrnikitin/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/writing-skills/SKILL.md
sed -n '1,240p' /Users/petrnikitin/.codex/skills/.system/skill-creator/references/openai_yaml.md
```

Expected: the complete authoring, trigger, metadata, and validation requirements are understood
before any skill file is written.

- [ ] **Step 2: Extend the structural test first**

Add `"write-spec",` to the `skill_name` tuple in
`test_project_skill_ui_configuration_is_valid`:

```python
    for skill_name in (
        "deliver-operational-change",
        "manage-yandex-cloud",
        "update-visual-design",
        "write-adr",
        "write-plan",
        "write-spec",
    ):
```

- [ ] **Step 3: Run the test and confirm the missing skill fails**

Run:

```bash
set -a
source /Users/petrnikitin/Documents/Sites/photo-prjct/.env
set +a
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest tests/test_repository_foundation.py::test_project_skill_ui_configuration_is_valid -q
```

Expected: FAIL with `FileNotFoundError` for `.agents/skills/write-spec/agents/openai.yaml`.

- [ ] **Step 4: Create the minimal project skill**

Create `.agents/skills/write-spec/SKILL.md` with this workflow and no implementation-plan detail:

```markdown
---
name: write-spec
description: Use when preparing, reviewing, or approving a FindMe Photo feature or system specification before implementation planning.
---

# Write a Project Specification

## Purpose

Augment the generic brainstorming workflow with FindMe Photo architecture and ADR reconciliation.
Produce an approved design whose architectural impact is explicit before `$write-plan` begins.

## Workflow

1. Use `superpowers:brainstorming` for intent, alternatives, incremental design approval, written
   review, and the transition to planning.
2. Before proposing approaches, read the relevant current, accepted, proposed, and open-decision
   sections of `docs/architecture.md`; `docs/adr/README.md`; every applicable ADR; linked plans,
   specifications, experiments, and jobs; and implementation/tests when behavior already exists.
3. Resolve contradictions before approval. Revise the design or classify it as superseding the
   affected ADR; never rely only on an ADR title or architecture summary.
4. Save the design under `docs/superpowers/specs/YYYY-MM-DD-topic-design.md` with exact `Related
   architecture`, `Related ADRs`, and `ADR impact` metadata.
5. Classify `ADR impact` as `None — reversible implementation detail`, `Conforms to ADR NNNN`,
   `Requires new ADR`, or `Supersedes ADR NNNN`. Use `none` only after checking the ADR index, and
   list independent durable decisions separately.
6. During self-review, verify scope, internal consistency, ambiguity, applicable ADR boundaries,
   and whether accepted architecture is described separately from unimplemented design.
7. Obtain user review of the written specification. Approval selects the design but does not
   silently accept a new or superseding ADR.
8. Invoke `$write-plan` after approval. Pass the approved specification and its ADR-impact
   classification so planning begins with ADR resolution.

## Boundaries

- Do not edit an accepted ADR to match a specification; use `$write-adr` to supersede it.
- Do not update current implemented architecture merely because a specification was approved.
- Do not require an ADR for reversible implementation detail.
- Do not mechanically rewrite old specifications; reconcile them when next used.
```

Create `.agents/skills/write-spec/agents/openai.yaml` with:

```yaml
interface:
  display_name: "Write Specification"
  short_description: "Create architecture-aligned project specifications"
  default_prompt: "Use $write-spec to prepare a FindMe Photo specification and resolve its ADR impact before planning."
```

- [ ] **Step 5: Validate the skill and turn the structural test green**

Run:

```bash
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/python /Users/petrnikitin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/write-spec
set -a
source /Users/petrnikitin/Documents/Sites/photo-prjct/.env
set +a
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest tests/test_repository_foundation.py::test_project_skill_ui_configuration_is_valid -q
```

Expected: skill validation succeeds and the pytest reports `1 passed`.

- [ ] **Step 6: Commit the specification authoring skill**

```bash
git add .agents/skills/write-spec tests/test_repository_foundation.py
git commit -m "feat: add specification authoring workflow"
```

### Task 2: Approved specification to resolved ADR and plan

**Files:**
- Modify: `.agents/skills/write-plan/SKILL.md`
- Modify: `.agents/skills/write-adr/SKILL.md`
- Modify: `docs/plans/0000-template.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: an approved spec and one of the four exact `ADR impact` classifications from `$write-spec`.
- Produces: resolved ADR state, synchronized architecture when required, and a plan that links its approved specification and final ADRs.

- [ ] **Step 1: Add the post-spec gate to `$write-plan`**

Insert after source inspection and before normal goal/scope planning:

```markdown
2. When an approved specification exists, read it completely and resolve its `ADR impact` before
   creating a plan file:
   - `None — reversible implementation detail`: record that no ADR or architecture update is needed;
   - `Conforms to ADR NNNN`: verify the final text remains inside every cited ADR boundary;
   - `Requires new ADR`: invoke `$write-adr` and obtain explicit maintainer acceptance;
   - `Supersedes ADR NNNN`: invoke `$write-adr`, cross-link both records, and obtain explicit
     maintainer acceptance.
   Block planning when a required ADR is missing, still proposed without authority, contradictory,
   or unlinked. Specification approval alone is not ADR acceptance.
```

Renumber the remaining workflow steps. Require every plan to link the approved specification and
resolved ADR impact, and add this final-task rule:

```markdown
- End every implementation plan with architecture and ADR reconciliation after behavior
  verification and before push. Require one explicit outcome: no ADR impact, conformance,
  architecture update, new ADR, or superseding ADR.
```

- [ ] **Step 2: Clarify approved-spec evidence in `$write-adr`**

Extend workflow step 1 to read an applicable approved specification. Add after the authority gate:

```markdown
Approval of a specification is decision evidence, not automatic ADR acceptance. Keep a derived ADR
`Proposed` until the maintainer explicitly approves that architectural decision, unless the approval
request named the exact ADR decision being accepted.
```

Preserve the existing accepted-ADR immutability and cross-link requirements unchanged.

- [ ] **Step 3: Extend the plan template**

Add these metadata fields after `Owner`:

```markdown
- Related specification: link or `none`
- ADR impact: resolved classification and links
```

Replace the generic documentation checkbox with this final task after implementation tasks:

```markdown
### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, applicable ADRs, and
  `docs/architecture.md`.
- [ ] Update implemented architecture facts when boundaries, topology, or status changed.
- [ ] Stop for a decision instead of contradicting an accepted ADR; supersede rather than edit it.
- [ ] Record the reconciliation outcome in the pull request.
```

- [ ] **Step 4: Add the lifecycle to architecture change rules**

Append these rules to `docs/architecture.md` under `## Change rules`:

```markdown
- Before approving a specification, read the ADR index and applicable ADRs and record exact related
  architecture, related ADRs, and ADR impact.
- After specification approval and before planning, resolve required new or superseding ADRs with
  explicit decision authority; specification approval alone does not accept an ADR.
- Before completing delivery, reconcile implemented behavior with the approved specification,
  applicable ADRs, and this document. Update implemented facts, or supersede a changed decision
  instead of editing an accepted ADR.
```

- [ ] **Step 5: Validate the modified skills and manually exercise all classifications**

Run:

```bash
for skill_dir in .agents/skills/write-spec .agents/skills/write-plan .agents/skills/write-adr; do
  /Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/python \
    /Users/petrnikitin/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$skill_dir"
done
git diff --check
```

Review these scenarios against the exact workflow text:

1. CSS spacing change -> `None`; plan records no ADR update.
2. Upload API remains request-driven -> `Conforms to ADR 0014`; plan verifies the boundary.
3. Stage 3 selects Celery/Redis -> `Requires new ADR`; plan creation blocks pending explicit ADR acceptance.
4. Stage 2 introduces a worker -> `Supersedes ADR 0014`; plan creation blocks pending a cross-linked accepted ADR.

Expected: each scenario has exactly one classification and unambiguous next action; the two durable
decision scenarios cannot reach plan creation without explicit ADR acceptance.

- [ ] **Step 6: Commit the spec-to-plan lifecycle**

```bash
git add .agents/skills/write-plan/SKILL.md .agents/skills/write-adr/SKILL.md docs/plans/0000-template.md docs/architecture.md
git commit -m "docs: gate planning on ADR reconciliation"
```

### Task 3: Completion reconciliation and final verification

**Files:**
- Modify: `.agents/skills/deliver-operational-change/SKILL.md`
- Test: `tests/test_repository_foundation.py`

**Interfaces:**
- Consumes: verified delivered behavior plus the applicable spec, ADRs, and architecture state.
- Produces: an explicit PR reconciliation result for both planned work and the operational fast lane.

- [ ] **Step 1: Add fast-lane reconciliation before PR creation**

Insert this step before the existing draft-PR step in `Implement and verify` and renumber the PR
step:

```markdown
5. Re-read applicable ADRs and `docs/architecture.md` after verification. Record one result: no ADR
   impact, conformance, architecture update, new ADR, or superseding ADR. Update implemented facts
   in the same change; stop for explicit authority rather than contradicting an accepted ADR.
```

- [ ] **Step 2: Validate every project skill**

Run:

```bash
for skill_dir in .agents/skills/*; do
  [ -f "$skill_dir/SKILL.md" ] || continue
  /Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/python \
    /Users/petrnikitin/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$skill_dir"
done
```

Expected: every project skill reports successful validation.

- [ ] **Step 3: Run focused executable verification**

Run:

```bash
set -a
source /Users/petrnikitin/Documents/Sites/photo-prjct/.env
set +a
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff format --check tests/test_repository_foundation.py
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff check tests/test_repository_foundation.py
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest tests/test_repository_foundation.py -q
git diff --check
```

Expected: Ruff succeeds, repository-foundation tests pass, and `git diff --check` prints nothing.

- [ ] **Step 4: Perform final architecture and ADR reconciliation**

Compare the final diff with ADR 0004 and the approved specification. Expected outcome:

```text
Conforms to ADR 0004. No new or superseding ADR is required. docs/architecture.md change rules are
updated because the repository workflow boundary changed; current runtime architecture is unchanged.
```

- [ ] **Step 5: Commit the fast-lane completion gate**

```bash
git add .agents/skills/deliver-operational-change/SKILL.md
git commit -m "docs: reconcile ADRs before delivery completion"
```

- [ ] **Step 6: Push and open a draft pull request**

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git push -u origin adr-spec-lifecycle
```

Open one draft PR targeting `main`. Its body must state the workflow gap, the prospective lifecycle,
the ADR 0004 conformance result, executable validation, and that root `AGENTS.md` remains unchanged.
