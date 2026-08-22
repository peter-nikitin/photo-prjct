.PHONY: check db-clone-deployed hooks local-purchase-down local-purchase-up local-web static test test-clone-deployed worktree

BASE ?= origin/main
MYPY ?= .venv/bin/mypy
RUFF ?= .venv/bin/ruff
TESTS ?=
PYTEST_XDIST_WORKERS ?= 4

worktree:
	@test -n "$(NAME)" || { echo "NAME is required: make worktree NAME=<name> [BASE=<ref>]" >&2; exit 2; }
	.venv/bin/python scripts/create-worktree.py "$(NAME)" "$(BASE)"

hooks:
	.venv/bin/pre-commit install

test:
	sh scripts/run-in-test-env.sh .venv/bin/pytest -n $(PYTEST_XDIST_WORKERS) --dist loadscope -m "not clone_deployed_slow" $(TESTS)

static:
	@status=0; \
	$(RUFF) format --check . || status=1; \
	$(RUFF) check . || status=1; \
	$(MYPY) || status=1; \
	exit $$status

check: static
	sh scripts/run-in-test-env.sh .venv/bin/pytest -n $(PYTEST_XDIST_WORKERS) --dist loadscope -m "not clone_deployed_slow" --cov --cov-report=term-missing
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py check
	sh scripts/run-in-test-env.sh .venv/bin/python src/backend/manage.py makemigrations --check --dry-run

test-clone-deployed:
	sh scripts/run-in-test-env.sh .venv/bin/pytest tests/deployment/test_clone_deployed_database.py

db-clone-deployed:
	sh scripts/clone-deployed-db.sh

local-web:
	@sh scripts/local-web.sh

local-purchase-up:
	docker compose --project-name paid-photo-purchase-review -f docker-compose.yml -f docker-compose.local-purchase.yml --profile worker --profile commerce up --build -d --wait

local-purchase-down:
	docker compose --project-name paid-photo-purchase-review -f docker-compose.yml -f docker-compose.local-purchase.yml --profile worker --profile commerce down --remove-orphans
