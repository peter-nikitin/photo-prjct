# FindMe Photo

FindMe Photo is an early-stage event photo marketplace for event-scoped photo discovery and protected
purchase downloads. The current repository is a Django/PostgreSQL prototype; the target MVP and its
unresolved decisions are documented rather than assumed to be implemented.

## Engineering documentation

- [Architecture](docs/architecture.md) — implemented system, accepted constraints, target MVP, and
  open decisions.
- [Architecture decisions](docs/adr/README.md) — durable decisions and the ADR template.
- [Implementation plans](docs/plans/README.md) — delivery-plan conventions and template.
- [Project skills](.agents/skills) — repository-scoped workflows for writing ADRs and plans and for
  safely operating Yandex Cloud resources.

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

The existing preemptible Yandex Cloud VM is the staging environment. A push to `main` runs `Deploy
staging`, builds `ghcr.io/peter-nikitin/photo-prjct:<commit-sha>`, deploys it to staging, waits for
Compose health checks, and records the successful image reference.

The first deployment after the staging migration removes the legacy `photo-prjct` and failed
`photo-prjct-staging` Compose projects together with their database volumes, then writes
`/opt/photo-prjct/.staging-reset-v1`. Later deployments preserve the staging database volume. Remove
that marker manually only when another explicitly approved destructive staging reset is intended.

Create GitHub Environments named `staging` and `production`. Each environment owns separate values
for `VM_HOST`, `VM_USER`, `VM_SSH_KEY`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `GHCR_USERNAME`, and `GHCR_READ_TOKEN`. Configure required reviewers on `production`.

`Promote production` is manually dispatched with the successfully staged commit SHA. It verifies
that SHA against the marker on staging, pauses for the production Environment approval, checks out
the selected revision, and deploys the same GHCR image without rebuilding it. Production remains
unavailable until a separate non-preemptible VM is approved and provisioned.

The web container runs migrations and `collectstatic` before starting Gunicorn. Host `.env` files,
GitHub secrets, and cloud credentials must never be committed. Use the project
`manage-yandex-cloud` skill for inventory or infrastructure operations; it requires fresh manual
confirmation for every change that may affect Yandex Cloud charges.
