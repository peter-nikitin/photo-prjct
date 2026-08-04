.PHONY: check db-clone-staging hooks test worktree

BASE ?= origin/main
TESTS ?=

worktree:
	@test -n "$(NAME)" || { echo "NAME is required: make worktree NAME=<name> [BASE=<ref>]" >&2; exit 2; }
	.venv/bin/python scripts/create-worktree.py "$(NAME)" "$(BASE)"

hooks:
	.venv/bin/pre-commit install

test:
	sh scripts/run-in-test-env.sh .venv/bin/pytest $(TESTS)

check:
	.venv/bin/ruff format --check .
	.venv/bin/ruff check .
	.venv/bin/mypy
	sh scripts/run-in-test-env.sh .venv/bin/pytest --cov --cov-report=term-missing
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run

db-clone-staging:
	sh scripts/clone-staging-db.sh
