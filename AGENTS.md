# AGENTS.md

## General

- Do not preserve backward compatibility. Remove obsolete paths instead of adding compatibility layers, fallbacks, or migrations.
- Choose the simplest implementation that fully meets the current requirements. Avoid speculative abstractions, configuration, and indirection.
- Grow the system in layers. Start from the smallest version that works end to end, and add each new capability on top of a product that already works. Never trade a working product for unfinished complexity.
- Keep components modular and concerns clearly separated.
- Prefer established, well-maintained libraries when they reduce overall complexity or improve reliability. Do not reimplement common functionality without a clear reason.
- Lean on the dependencies already in the project before writing your own implementation or adding packages. Do not assume a library lacks a capability without checking its documentation and types.
- Make architectural decisions for the long term. Do not accept a stopgap that only works for now and is meant to be replaced later.

## Project

FindMe Photo is an event photo marketplace where customers discover photos from events and
photographers and operators publish and manage event photos.

## Worktrees and local Python

- Create feature worktrees only through `make worktree NAME=<name> [BASE=<ref>]` from the main
  checkout. The command creates `.worktrees/<name>` on `codex/<name>`, links the root `.venv`, and
  creates a local test-safe `.env` without copying root secrets.
- Run Python tests through `make test` or `make test TESTS="<selectors>"`. Run the complete Python
  quality suite through `make check`.
- Do not rely on virtual-environment activation or global `python`, `pytest`, `ruff`, or `mypy`.
  When a direct command is necessary, use the explicit `.venv/bin/...` executable.
- Do not copy or link the main checkout's `.env` into a worktree. Each worktree owns its ignored
  local `.env`.

## Where to find information

- [Product jobs](docs/product-jobs.md) records customer-facing jobs and their evidence-backed status.
- [Engineering jobs](docs/engineering-jobs.md) records engineering and operational capabilities and
  their evidence-backed status.
- [Architecture](docs/architecture.md) describes the system architecture and its current boundaries.
- [Architecture decision records](docs/adr/) contain durable architecture decisions.
- [Implementation plans](docs/plans/) contain decision-complete plans for multi-step work.
- [Agent skills](.agents/skills/) contain reusable project-specific guidance.

## Delivery focus and test scope

- Optimize for the shortest safe delivery of the requested critical path. A finding blocks the
  current task only when it affects an accepted requirement, the critical path, an existing
  production path, a regression in existing behavior, security, privacy, irreversible data loss, or
  a failure scenario that is realistic in the current system.
- Do not expand the current task to defend against a merely technically possible scenario when the
  repository has no production path that can trigger it. Do not add speculative infrastructure,
  concurrency handling, compatibility behavior, or exhaustive state coverage without current
  evidence or an accepted requirement.
- Record a useful non-blocking finding as a separate Markdown artifact under `docs/future-work/`
  instead of implementing it in the current task. The artifact must state the observed gap, why it
  is non-blocking now, and the concrete trigger that should bring it back into scope. Do not create
  an artifact for a vague idea without an actionable trigger.
- Tests must cover the critical path and the realistic failure and regression paths changed by the
  task. Additional tests need a concrete risk or contract they protect; coverage percentage alone
  is not sufficient justification.
- Repository-wide branch coverage is a regression guard, not a per-task completeness target.
  Reviewers must classify findings as `blocking` or `future` using the criteria above. A `future`
  finding does not prevent approval or delivery.

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
