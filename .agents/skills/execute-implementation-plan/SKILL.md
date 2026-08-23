---
name: execute-implementation-plan
description: Use when implementing an approved FindMe Photo plan with implementer and reviewer subagents in the current session.
---

# Execute an Implementation Plan

Adapt `superpowers:subagent-driven-development` to this repository. Its ledger, task-brief, and
review-loop concepts apply; this skill overrides its commit-based handoff. Subagents leave all
changes unstaged. Only the root controller changes Git state.

**REQUIRED SUB-SKILLS:** Use `superpowers:using-git-worktrees`,
`superpowers:subagent-driven-development`, `superpowers:test-driven-development`, and
`superpowers:verification-before-completion`.

## Setup

1. Create or verify the isolated worktree with `make worktree NAME=<name> [BASE=<ref>]`.
2. Read the approved plan once and keep its SDD ledger under `.superpowers/sdd/`.
3. Extract one task brief. Never paste the whole plan or accumulated session history into a
   dispatch.
4. Run one implementer at a time. Inspect the agent tree while delegated work is active and stop
   unplanned nested agents.

## Worker selection

Always specify the role, model, and reason in the dispatch.

| Task shape | Implementer |
| --- | --- |
| Exact, mechanical, one or two files | `luna_worker` |
| Multi-file integration, migration, or debugging | `worker`, one capability tier below root |
| Exceptional ML, privacy, security, concurrency, or destructive risk | `worker` at root capability; state why |

Choose reviewer capability independently from diff size and risk. Use a root-capability reviewer
for high-risk changes and the final whole-branch review. A small scoped re-review may use a cheaper
model.

## Task loop

Before implementation, review, or handoff test selection, implementers and reviewers use
`$select-verification-suites`. It owns selector invocation, final-package fingerprints, evidence
reuse, and the boundary between role checks and the root/CI final gates. With the current
whole-package fingerprint, any fingerprint change invalidates every earlier suite result; do not
apply an affected-check exception until executable suite-scoped fingerprints exist.

1. Fill [implementer-prompt.md](implementer-prompt.md) with task-local paths and decisions.
2. The implementer performs red-green TDD, self-reviews, and records exact commands, exit statuses,
   summaries, and that final GREEN followed the last task-file change. It returns `DONE`,
   `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`.
3. Verify the report and working tree. Run `scripts/review-package.py OUTPUT` to capture the full
   working-tree diff, including untracked task files.
4. Fill [reviewer-prompt.md](reviewer-prompt.md). The reviewer classifies every finding as
   `blocking` or `future` under `AGENTS.md`.
5. Return blocking fixes to the same implementer and use
   [re-review-prompt.md](re-review-prompt.md) with the same reviewer when available.
6. After approval, root reuses only complete evidence with the exact final-package fingerprint;
   otherwise it runs each missing selected layer once before staging the exact task files and
   creating the one task commit.
7. After all task and review-fix loops, root reruns the selector and fingerprint on the final
   branch, runs `make check` once, and ensures every selector-required expensive target has GREEN
   evidence for that exact fingerprint. Selector output decides whether visual checks are
   required. CI is post-push repetition only.

Subagents never run `git add`, commit, amend, push, merge, or change branches.

Do not run overlapping full Django or visual suites in the shared repository environment.

## Completion boundary

Record these independently: implementation complete, review approved, committed, pull request
opened, CI passed, merged, deployed, and live verified. Never infer a later state from an earlier
one.
