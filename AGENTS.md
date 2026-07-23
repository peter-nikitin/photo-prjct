# AGENTS.md

## Project

FindMe Photo is an event photo marketplace where customers discover photos from events and
photographers and operators publish and manage event photos.

## Where to find information

- [Product jobs](docs/product-jobs.md) records customer-facing jobs and their evidence-backed status.
- [Engineering jobs](docs/engineering-jobs.md) records engineering and operational capabilities and
  their evidence-backed status.
- [Architecture](docs/architecture.md) describes the system architecture and its current boundaries.
- [Architecture decision records](docs/adr/) contain durable architecture decisions.
- [Implementation plans](docs/plans/) contain decision-complete plans for multi-step work.
- [Agent skills](.agents/skills/) contain reusable project-specific guidance.

## Subagent delegation

- An implementer subagent must perform its assigned task itself and must not spawn, dispatch, or
  delegate to another agent, including a reviewer.
- An implementer's self-review means inspecting its own work without creating another agent.
- Only the root controller may dispatch an independent reviewer after the implementer has produced
  the task diff and report.
- Review fixes return to the implementer, and re-review returns to the same reviewer when available;
  do not create an additional reviewer for the same task.
- The root controller must inspect the agent tree while delegated work is active and interrupt any
  unplanned nested agent before using its result.

## Subagent Git boundary

- Implementer and reviewer subagents must not run `git add`, `git commit`, `git commit --amend`,
  `git push`, or otherwise modify the Git index, history, branches, tags, or remotes.
- An implementer leaves its task changes unstaged, writes its report, and returns control to the
  root controller after tests and self-review pass.
- The root controller prepares a reviewable working-tree diff, including new untracked task files,
  without requiring an implementer commit.
- Review fixes remain unstaged and return to the same implementer. Re-review uses the updated
  working-tree diff and the same reviewer when available.
- Only after the reviewer approves the complete task and the root controller reruns final
  verification may the root controller stage the exact task files and create one task commit.
- A task must not receive intermediate implementation or review-fix commits; all approved task
  changes are consolidated into that single final commit.

## Subagent model selection

- A subagent whose primary role is writing or modifying code must use a model one capability tier
  below the root controller's model by default.
- The root controller selects reviewer models independently according to the size, complexity, and
  risk of the diff; the lower-tier implementer rule does not automatically apply to reviewers.
- The root controller may use the same or a higher-capability model for implementation only when
  the task has exceptional complexity or risk. State the reason before dispatching that implementer.
- Model selection must be explicit in every implementer and reviewer dispatch; do not rely on
  inherited defaults.
