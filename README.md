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

The current single active environment is assigned the canonical public URL
`https://findme-photo.ru/`. After staging and production are provisioned as separate live
environments, `https://findme-photo.ru/` remains the production URL and staging uses
`https://staging.findme-photo.ru/`. This records the intended domain routing; it does not mean DNS
or TLS rollout is already complete. The current VM remains operationally staging during the
transition even though it is assigned the root domain.

Normal deployments reuse the `photo-prjct-staging` Compose project and preserve its
`photo-prjct-staging_pgdata` database volume. Database reset is not part of the deployment workflow.

Create GitHub Environments named `staging` and `production`. Each environment owns separate values
for `VM_HOST`, `VM_USER`, `VM_SSH_KEY`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `GHCR_USERNAME`, and `GHCR_READ_TOKEN`. Configure required reviewers on `production`.

For every public environment, configure `PUBLIC_DOMAIN` as an Environment variable and optionally
configure `PUBLIC_DOMAIN_ALIAS`. For the current assignment, the intended values are
`findme-photo.ru` and `www.findme-photo.ru`. An environment that uses the HTTPS overlay must also
have `LETSENCRYPT_EMAIL` as an Environment secret. HTTP-only environments do not receive or read
that secret.

The preparation release deliberately leaves staging on its existing HTTP overlay and does not
request a certificate. HTTPS activation is a separate release after the staging Environment values,
public DNS, and ports 80/443 are verified. The shared HTTPS overlay then issues a certificate only
when none exists, redirects HTTP to HTTPS, and proxies to private Django. Certificate/account state
is kept in persistent Docker volumes; Certbot attempts renewal every 12 hours. Verify an activated
edge with:

```bash
curl -I http://<public-domain>/
curl --fail https://<public-domain>/health/
```

The first command must return a canonical 308 redirect and the second must return
`{"status": "ok"}` with normal TLS trust validation. Validate renewal on the activated VM with the
same Compose project and both `docker-compose.prod.yml` and `docker-compose.https.yml` by running
`certbot renew --dry-run` in the Certbot service.

Changing `PUBLIC_DOMAIN` or `PUBLIC_DOMAIN_ALIAS` does not automatically replace an existing
certificate. Treat such a change as maintenance: back up the environment certificate volume,
remove the named certificate explicitly, and rerun deployment once to issue the new name set.

`Promote production` is manually dispatched with the successfully staged commit SHA. It verifies
that SHA against the marker on staging, pauses for the production Environment approval, checks out
the selected revision, and deploys the same GHCR image without rebuilding it. Production remains
unavailable until a separate non-preemptible VM is approved and provisioned.

The web container runs migrations and `collectstatic` before starting Gunicorn. Host `.env` files,
GitHub secrets, and cloud credentials must never be committed. Use the project
`manage-yandex-cloud` skill for inventory or infrastructure operations; it requires fresh manual
confirmation for every change that may affect Yandex Cloud charges.
