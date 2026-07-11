# AGENTS.md

## Project Overview

Personal photo project.

Current stage: development-foundation hardening on top of an early Django prototype. The existing
`Event` and `Photo` models are preliminary and must not be treated as the final domain model.

## Sources of Truth

- `docs/architecture.md` describes implemented, accepted, proposed, and deferred architecture.
- `docs/adr/` records durable decisions. Accepted ADRs are immutable; supersede them with a new ADR.
- `docs/plans/` stores decision-complete implementation plans for multi-step work.
- `.agents/skills/write-adr` and `.agents/skills/write-plan` define project authoring workflows.

Keep these sources synchronized with relevant code changes. Do not turn an open architectural
question into an implementation choice without an ADR.

## Technology Stack

### Backend

- Python
- Django
- PostgreSQL
- Environment-based configuration
- Docker for deployment

### Infrastructure

- Yandex Cloud VM
- Docker Compose on the server
- GitHub Actions for CI/CD
- Container-based deployment

## Architectural Principles

### Current Priority

Focus on establishing a reliable development and deployment workflow before implementing domain logic.

Order of work:

1. Backend bootstrap
2. Environment management
3. Database integration
4. Containerization
5. CI/CD
6. Production deployment
7. Monitoring and operational readiness
8. Domain modeling
9. Application features

### Configuration Management

- All configuration comes from environment variables.
- Secrets must never be committed to the repository.
- Local, CI, and production environments should use the same configuration model.

### Deployment Strategy

- Build Docker images in CI.
- Deploy automatically to a Yandex Cloud VM.
- Production environment runs via Docker Compose.
- Deployment should be repeatable and require minimal manual intervention.

### Infrastructure Philosophy

Prefer simple and maintainable solutions over complex cloud-native infrastructure.

Current target architecture:

GitHub → CI/CD → Yandex Cloud VM → Docker Compose → Django + PostgreSQL

No Kubernetes, service mesh, or microservices at this stage.

## Development Environment

- Local development currently uses Python virtual environments.
- Production uses Docker containers.
- PostgreSQL should be available through Docker.
- All repository changes must be made in a git worktree, not directly in the main checkout.
- Commits must be created only on a dedicated non-main branch.
- After finishing work, open a pull request for review instead of merging directly.
- Repository should ignore:
  - virtual environments
  - IDE files
  - local environment files
  - Python cache files
  - build artifacts
  - local database files

### Required Checks

Install production and development dependencies with `pip install -r requirements-dev.txt`. Before
considering a change complete, run:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov --cov-report=term-missing
python src/backend/manage.py check
python src/backend/manage.py makemigrations --check --dry-run
```

Use environment variables matching `.env.example`. Tests and CI use PostgreSQL; do not add SQLite-only
shortcuts that hide production database behavior.

## Django Baseline

The project already went through initial Django configuration issues:

- Environment variables loading
- Database configuration
- ALLOWED_HOSTS
- ROOT_URLCONF
- TEMPLATES
- STATIC_URL
- SECRET_KEY

The application successfully starts with `runserver`.

## Near-Term Goals

1. Run PostgreSQL in Docker locally.
2. Connect Django to PostgreSQL.
3. Apply migrations.
4. Containerize backend.
5. Create GitHub Actions pipeline.
6. Configure automatic deployment to Yandex Cloud VM.
7. Verify end-to-end deployment flow.

## Long-Term Goals

After infrastructure is stable:

- Design domain model
- Implement photo-related business capabilities
- Introduce authentication if needed
- Add media storage strategy
- Add observability and operational tooling
- Scale architecture only when justified by real requirements

## Decision Log

- Infrastructure-first approach is intentional.
- Domain design is deferred.
- Docker is the deployment standard.
- Yandex Cloud VM is the primary hosting platform.
- CI/CD is considered a required foundation, not an optional improvement.
- Simplicity is preferred over premature scalability.
- Detailed decisions and their consequences are maintained in `docs/adr/`; this summary does not
  replace them.
