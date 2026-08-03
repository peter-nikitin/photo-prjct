# Worktree Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide one reliable command to create test-ready worktrees and stable Make targets for
Python verification.

**Architecture:** A standard-library Python helper owns worktree validation and initialization. A
small POSIX environment wrapper owns CI-like Django defaults, while Make exposes the supported
human and agent interface.

**Tech Stack:** Python 3.12 standard library, POSIX shell, GNU/BSD Make, pytest.

## Global Constraints

- Never copy, read, or link the repository root `.env`.
- Use the existing repository root `.venv`; do not install another environment per worktree.
- Use one final task commit after complete review and verification, per `AGENTS.md`.
- Do not change Django production settings or weaken required configuration.

---

### Task 1: Worktree bootstrap helper

**Files:**
- Create: `scripts/create-worktree.py`
- Create: `tests/test_create_worktree.py`

**Interfaces:**
- Consumes: `NAME`, optional `BASE`, repository root `.venv`, and `.env.example`.
- Produces: `scripts/create-worktree.py NAME [BASE]`, creating `.worktrees/NAME` on
  `codex/NAME` with linked `.venv` and local `.env`.

- [ ] Write tests for slug validation, preflight-before-Git behavior, exact `git worktree add`
      arguments, relative `.venv` symlink creation, and safe `.env.example` transformation.
- [ ] Run `.venv/bin/pytest -q tests/test_create_worktree.py` and confirm failure because the
      helper does not exist.
- [ ] Implement the smallest standard-library helper satisfying those contracts.
- [ ] Rerun the focused test and confirm it passes.

### Task 2: Stable verification entry points

**Files:**
- Create: `scripts/run-in-test-env.sh`
- Modify: `Makefile`
- Modify: `tests/test_repository_foundation.py`
- Modify: `AGENTS.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: optional caller-provided Django variables and optional `TESTS` Make variable.
- Produces: `make worktree`, `make test`, and `make check`.

- [ ] Add repository-foundation assertions for executable paths, CI-equivalent environment
      defaults, command forwarding, documented interfaces, and non-secret bootstrap behavior.
- [ ] Run the focused foundation tests and confirm they fail on the missing interfaces.
- [ ] Add the environment wrapper and Make targets; document the mandatory agent workflow and
      local developer commands.
- [ ] Rerun focused tests and shell syntax checks until green.

### Task 3: End-to-end verification and publication

**Files:**
- Verify all files listed above plus this design and plan.

**Interfaces:**
- Consumes: completed bootstrap and verification commands.
- Produces: reviewed commit and draft GitHub pull request.

- [ ] Use the public command to create a disposable smoke worktree and verify its `.venv`, `.env`,
      pytest executable, and Django settings import; remove only that named smoke worktree/branch.
- [ ] Run focused tests, Ruff, mypy, full Python coverage, Django checks, migration drift, and
      `git diff --check`.
- [ ] Self-review the complete diff and resolve any blocking findings.
- [ ] Stage the exact task files, create one commit, push `codex/worktree-bootstrap`, and open a
      draft PR against `main`.
