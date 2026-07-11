# FindMe Photo

FindMe Photo is an early-stage event photo marketplace for event-scoped photo discovery and protected
purchase downloads. The current repository is a Django/PostgreSQL prototype; the target MVP and its
unresolved decisions are documented rather than assumed to be implemented.

## Engineering documentation

- [Architecture](docs/architecture.md) — implemented system, accepted constraints, target MVP, and
  open decisions.
- [Architecture decisions](docs/adr/README.md) — durable decisions and the ADR template.
- [Implementation plans](docs/plans/README.md) — delivery-plan conventions and template.
- [Project skills](.agents/skills) — repository-scoped workflows for writing ADRs and plans.

## Local development

Requirements: Python 3.12+, Docker, and Docker Compose.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
docker compose up --build
```

The application is available at `http://localhost:8000`. Local Compose supplies PostgreSQL at
`db:5432`; application configuration comes from `.env`.

## Quality checks

Activate the virtual environment and export the variables from `.env`, or run the application checks
inside Compose. The CI-equivalent commands are:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov --cov-report=term-missing
python src/backend/manage.py check
python src/backend/manage.py makemigrations --check --dry-run
```

Install fast local hooks with:

```bash
pre-commit install
pre-commit run --all-files
```

The hooks format and lint Python files. CI remains authoritative and also runs types, tests, Django
checks, migration drift detection, and repository skill-structure tests.

## Deployment

Production deployment behavior is unchanged:

1. Configure GitHub Actions secrets and the VM.
2. Trigger the `Deploy` workflow.
3. CI builds and pushes the Docker image to GHCR.
4. The workflow deploys `docker-compose.prod.yml` to `/opt/photo-prjct/` on the Yandex Cloud VM.

The web container runs migrations and `collectstatic` before starting Gunicorn. Production secrets
must never be committed; `.env.example` documents only the configuration shape.
