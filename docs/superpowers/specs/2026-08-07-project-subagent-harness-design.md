# Project Subagent Harness Design

- Date: 2026-08-07
- Status: Approved
- Owner: project maintainer
- ADR impact: None — repository development tooling only.

## Goal

Make implementation-plan execution reuse one FindMe Photo subagent contract instead of repeating
worktree, Git, review, reporting, and model-selection instructions in every plan and dispatch.

## Design

Add a project skill named `execute-implementation-plan`. It adapts
`superpowers:subagent-driven-development` to the repository rule that subagents leave changes
unstaged and the root controller owns review packages, verification, commits, pushes, and pull
requests.

The skill owns three compact role templates: implementer, reviewer, and re-reviewer. Dispatches fill
only task-local paths, prior interfaces, ambiguity resolutions, and a model-selection reason. The
templates own stable project rules, so plans link the skill without copying its orchestration text.

Model routing uses task shape and risk. `luna_worker` handles exact mechanical work; the standard
worker handles multi-file integration; exceptional ML, privacy, security, concurrency, and
destructive work may use root-level capability with an explicit reason. Review capability follows
diff risk independently of implementer selection.

## Execution contract

- Work starts in a worktree created with `make worktree`.
- One implementer works at a time in the shared task worktree and may not delegate.
- The implementer follows red-green TDD, self-reviews, leaves changes unstaged, and writes a report.
- Root inspects the agent tree and prepares a working-tree review package including untracked files.
- One independent reviewer classifies each finding as `blocking` or `future`.
- Blocking fixes and scoped re-review return to the same agents when available.
- Root runs fresh verification and alone stages, commits, pushes, and opens the pull request.
- Code completion, pull-request publication, CI, merge, deployment, and live verification remain
  distinct terminal states.
- Heavy Django and visual suites never run concurrently in the same repository environment.

## Plan contract

The project `write-plan` skill tells executable plans to reference
`execute-implementation-plan`. Plans retain task-specific requirements, dependencies, checks, and
operational ordering; they omit generic subagent and Git boilerplate.

## Verification

Repository foundation tests validate the project skill metadata, role-template inputs, and key
project-specific execution boundaries. Skill validation additionally checks YAML/frontmatter and
the complete repository diff.
