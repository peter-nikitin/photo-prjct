.PHONY: check db-clone-deployed hooks local-web test test-clone-deployed worktree

BASE ?= origin/main
TESTS ?=

worktree:
	@test -n "$(NAME)" || { echo "NAME is required: make worktree NAME=<name> [BASE=<ref>]" >&2; exit 2; }
	.venv/bin/python scripts/create-worktree.py "$(NAME)" "$(BASE)"

hooks:
	.venv/bin/pre-commit install

test:
	sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_deployed_slow" $(TESTS)

check:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .
	.venv/bin/mypy
	sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_deployed_slow" --cov --cov-report=term-missing
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run

test-clone-deployed:
	sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_clone_deployed_database.py

db-clone-deployed:
	sh scripts/clone-deployed-db.sh

local-web:
	@sh scripts/local-web.sh
