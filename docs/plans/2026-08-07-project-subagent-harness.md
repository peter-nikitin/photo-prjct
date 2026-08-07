# Project Subagent Harness Implementation Plan

- Date: 2026-08-07
- Status: Implemented
- Owner: project maintainer
- Related specification:
  [`2026-08-07-project-subagent-harness-design.md`](../superpowers/specs/2026-08-07-project-subagent-harness-design.md)
- Related architecture: `none` — repository development tooling only
- Related ADRs: `none`
- ADR impact: None — repository development tooling only.

## Goal

Deliver the approved reusable project harness for implementation and review subagents.

## Scope

None beyond the approved specification.

## Acceptance criteria

- Executable plans reference one project skill instead of restating stable subagent instructions.
- Implementer, reviewer, and re-review dispatches have compact task-local input contracts.
- The project skill resolves generic SDD's commit workflow in favor of the repository's unstaged
  working-tree review boundary.
- Worker selection is explicit and based on task shape and risk.

## Implementation

### Task 1: Add the reusable execution skill and planning integration

**Files:** `.agents/skills/execute-implementation-plan/**`, `.agents/skills/write-plan/SKILL.md`,
`AGENTS.md`, and `tests/test_repository_foundation.py`.

- **Specification:** Design, Execution contract, and Plan contract.
- **Depends on:** Existing project skills, `make worktree`, and repository subagent policy.
- **Produces:** One reusable execution entrypoint and three role templates.

- [x] Add a failing repository test for the skill structure and role-template contracts.
- [x] Run the focused test and confirm failure because the skill does not exist.
- [x] Add the smallest complete project skill, UI metadata, and role templates.
- [x] Replace repeated planning guidance with one execution-skill reference.
- [x] Run the focused test and skill validator successfully.

### Final task: Architecture and ADR reconciliation

- [x] Confirm the change affects development workflow only and requires no architecture or ADR
  update.
- [x] Record the reconciliation outcome in the pull request.

## Verification

- `make test TESTS="tests/test_repository_foundation.py"` passes.
- Skill frontmatter and `agents/openai.yaml` parse successfully.
- `git diff --check` reports no whitespace errors.

## Operational impact and rollout

None. The skill is available to Codex after the branch is merged and a later session loads the
repository skills catalog.

## Rollback

Revert the change; it contains no runtime or data migration.

## Open questions

None.
