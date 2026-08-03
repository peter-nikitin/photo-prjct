# Worktree Bootstrap Design

## Goal

Make every project worktree immediately usable for Python checks without copying secrets or
requiring agents to remember virtual-environment paths and Django settings.

## Design

Add one supported worktree creation command, `make worktree NAME=<name> [BASE=<ref>]`. A small
Python helper creates `.worktrees/<name>` on `codex/<name>`, links the repository root `.venv`,
creates a worktree-local `.env` from tracked `.env.example`, and replaces only local test-safe
values (`SECRET_KEY` and `DB_HOST`). It refuses an unsafe name, an existing target, or a missing
root virtual environment before changing Git state. It never reads or links the root `.env`.

Add one environment wrapper used by `make test` and `make check`. The wrapper supplies the same
required Django variables as CI, using existing environment values when explicitly provided, and
executes repository-local tools by explicit `.venv/bin/...` paths. This makes host checks
independent of shell activation and of the Compose-oriented `DB_HOST=db` in `.env.example`.

## Interfaces

- `make worktree NAME=example BASE=origin/main` creates branch `codex/example` at
  `.worktrees/example`; `BASE` defaults to `origin/main`.
- `make test` runs the full Python pytest suite.
- `make test TESTS="path::selector"` runs a focused pytest selection.
- `make check` runs Ruff formatting/lint, mypy, pytest with coverage, Django system checks, and
  migration drift checks under the CI-like environment.

## Safety and errors

The helper accepts only lowercase slug names containing letters, digits, and hyphens. Existing
branches or directories are left to Git to reject without overwriting. If setup after `git
worktree add` fails, the error identifies the incomplete worktree; no destructive automatic
cleanup is attempted. Local `.env` contains placeholders only and is ignored by Git.

## Verification

Tests exercise name validation, safe `.env` generation without root-secret access, relative venv
linking, Git command construction, and Makefile contracts. A real smoke creates a temporary
worktree through the public command and proves `.venv/bin/pytest --version` plus `manage.py check`
under CI-like settings, then removes the smoke worktree and branch through normal Git commands.
