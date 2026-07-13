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
staging`, builds `ghcr.io/peter-nikitin/photo-prjct:<commit-sha>`, deploys it to the HTTP staging
edge, verifies that the running web container uses that exact image and that Nginx serves `/health/`,
then records the successful image reference.

Normal deployments reuse the `photo-prjct-staging` Compose project and preserve its
`photo-prjct-staging_pgdata` database volume. Database reset is not part of the deployment workflow.

Create GitHub Environments named `staging` and `production`. Each environment owns separate values
for `VM_HOST`, `VM_USER`, `VM_SSH_KEY`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `GHCR_USERNAME`, and `GHCR_READ_TOKEN`. Configure required reviewers on `production`.

For staging, add `PUBLIC_DOMAIN` as an Environment variable. It is used only for a VM-local HTTP
health probe through Nginx while the domain's public DNS record is unroutable. The staging workflow
does not read `ENABLE_HTTPS` or `LETSENCRYPT_EMAIL`, and it does not start Certbot.

For a future production HTTPS edge, add `PUBLIC_DOMAIN` as an Environment variable and
`LETSENCRYPT_EMAIL` as an Environment secret. The domain's A record must point to the target VM and
ports 80/443 must be reachable. The production overlay issues a missing Let's Encrypt certificate,
then Nginx redirects HTTP to HTTPS and proxies to the private Django container. Certificate/account
state is kept in persistent Docker volumes; Certbot attempts renewal every 12 hours. Verify a
deployed production edge with:

```bash
curl -I http://<public-domain>/
curl --fail https://<public-domain>/health/
```

The first command must return a redirect and the second must return `{"status": "ok"}`. To validate
renewal without changing a certificate, run `docker compose --project-name photo-prjct-staging
--env-file .env -f docker-compose.prod.yml exec certbot certbot renew --dry-run` on the VM.

### Staging DNS limitation

The staging probe proves that the VM edge works, not that the domain is publicly reachable. Repair
the A record before provisioning production HTTPS; then validate the production redirect, HTTPS
health, certificate subject, and Certbot dry-run renewal. See
[ADR 0009](docs/adr/0009-separate-staging-http-edge.md).

`Promote production` is manually dispatched with the successfully staged commit SHA. It verifies
that SHA against the marker on staging, pauses for the production Environment approval, checks out
the selected revision, and deploys the same GHCR image without rebuilding it. Production remains
unavailable until a separate non-preemptible VM is approved and provisioned.

The web container runs migrations and `collectstatic` before starting Gunicorn. Host `.env` files,
GitHub secrets, and cloud credentials must never be committed. Use the project
`manage-yandex-cloud` skill for inventory or infrastructure operations; it requires fresh manual
confirmation for every change that may affect Yandex Cloud charges.
