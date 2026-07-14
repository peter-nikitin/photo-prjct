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

## Visual Design Workflow

- Django templates under `src/backend/templates/` and assets under `src/backend/static/ui/` are the
  production UI source of truth.
- Use `$update-visual-design` for changes to templates, CSS, icons, responsive layouts, visual
  snapshots, and new UI concepts.
- Keep unfinished screens only in the test-only gallery under `tests/visual/`; do not add production
  routes or working-looking controls before backend behavior exists.
- Never create `src/proto`, another prototype/archive directory, or standalone demo HTML.
- Run visual snapshots through the public npm commands; they build the pinned Docker visual-test
  environment and must not be replaced with host-browser baselines.

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

### Deployment Domains

- Assign `https://findme-photo.ru/` to the current single active environment. Treat it as the
  canonical public URL while DNS and HTTPS activation follow the deployment workflow.
- When staging and production are split into separate live environments, keep
  `https://findme-photo.ru/` for production and use `https://staging.findme-photo.ru/` for staging.
- Using the root domain during the transition does not make the current preemptible staging VM
  production-ready, imply that DNS/TLS rollout is complete, or relax the production readiness
  gates.

### Infrastructure Philosophy

Prefer simple and maintainable solutions over complex cloud-native infrastructure.

Current target architecture:

GitHub → CI/CD → Yandex Cloud VM → Docker Compose → Django + PostgreSQL

No Kubernetes, service mesh, or microservices at this stage.

## Operational Change Fast Lane

For a small, reversible DNS, public-domain, HTTPS, Nginx, Certbot, Compose, GitHub Actions, or
single-VM change, default to the smallest reversible change that fits the existing deployment
entrypoint and accepted ADRs.

- Use one worktree, one branch, and one pull request for implementation and related documentation.
  Do not create a separate documentation PR.
- Prefer one implementation pass, one combined requirements-and-quality review, and one final
  proportional verification run.
- Take a scope checkpoint before adding multi-environment orchestration, a new service, persistent
  release state, a DNS or certificate-management platform, data migration, a pricing-affecting
  cloud change, or behavior that conflicts with an accepted ADR. Confirm that expansion with the
  user before implementing it.
- Existing approval gates still apply. In particular, a plan does not authorize cloud mutations or
  pricing-affecting changes.

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

Verification must be proportional to the changed surface and its risk. Always run `git diff --check`.

- Documentation-only changes (`*.md`, ADRs, plans, and skills): do not run Python, Django, database,
  or coverage checks by default. Run a targeted repository-contract test only when the change affects
  documented file paths, required structure, or workflow contracts.
- Python application changes: run Ruff, mypy when typed surfaces change, and the smallest relevant
  pytest selection. Expand to the full suite when shared behavior or test infrastructure changes.
- Models, migrations, Django settings, dependencies, or database integration: run the complete check
  set below against PostgreSQL.
- Deployment, Compose, shell, or GitHub Actions changes: run the relevant syntax/configuration checks
  and targeted repository-contract tests. Add application checks only when runtime behavior or
  application configuration changes.
- Run the complete check set for broad, cross-cutting, release-critical, or uncertain changes, or
  when explicitly requested. CI remains the authoritative full regression check for pull requests.

Install production and development dependencies with `pip install -r requirements-dev.txt` when the
selected checks require them. The complete check set is:

```bash
ruff format --check .
ruff check .
mypy
pytest --cov --cov-report=term-missing
python src/backend/manage.py check
python src/backend/manage.py makemigrations --check --dry-run
npm run test:visual
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
