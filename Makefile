.PHONY: check db-clone-staging hooks test test-clone-staging worktree

BASE ?= origin/main
TESTS ?=

worktree:
	@test -n "$(NAME)" || { echo "NAME is required: make worktree NAME=<name> [BASE=<ref>]" >&2; exit 2; }
	.venv/bin/python scripts/create-worktree.py "$(NAME)" "$(BASE)"

hooks:
	.venv/bin/pre-commit install

test:
	sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_staging_slow" $(TESTS)

check:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .
	.venv/bin/mypy
	sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_staging_slow" --cov --cov-report=term-missing
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run

test-clone-staging:
	sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_clone_staging_database.py

db-clone-staging:
	sh scripts/clone-staging-db.sh
