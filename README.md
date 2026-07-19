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

Requirements: Git, Docker, Docker Compose, NVM, and Node 22. Python 3.12+ is required only for
running management commands and quality checks directly on the host.

The `main` checkout and a feature worktree use separate source directories and Compose projects, so
each directory needs its own ignored `.env` file. Both configurations expose PostgreSQL on port
`5432` and Django on port `8000`; stop one before starting the other unless you intentionally change
the port mappings.

### Prepare Node.js

The repository uses Node 22 for JavaScript unit tests and local npm commands. With
[NVM](https://github.com/nvm-sh/nvm) installed, prepare the pinned major version once per checkout:

```bash
nvm install
nvm use
node --version
npm ci
```

`node --version` must report `v22.x.x`. NVM reads `.nvmrc`, matching GitHub Actions and the
containerized visual-test environment.

### Run the `main` version

Use the repository's main checkout for the latest merged version:

```bash
cd /Users/petrnikitin/Documents/Sites/photo-prjct
git switch main
git pull --ff-only
test -f .env || cp .env.example .env
docker compose up --build -d
docker compose logs -f web
```

Do not overwrite an existing `.env`; update it from `.env.example` instead. The container entrypoint
applies migrations and collects static files automatically. Once the web service has started, leave
the logs with `Ctrl+C` and create an administrator if the local database is new:

```bash
docker compose exec web python manage.py createsuperuser
```

Open the application at `http://localhost:8000/` and Django Admin at
`http://localhost:8000/admin/`.

### Run the photographer-upload worktree version

The in-progress photographer-upload implementation lives in a separate worktree. If it already
exists, enter it directly:

```bash
cd /Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/stage-2-photographer-upload
git status --short --branch
```

To create that worktree from the pull-request branch when it does not exist, run from the main
checkout:

```bash
cd /Users/petrnikitin/Documents/Sites/photo-prjct
git fetch origin stage-2-implementation-plan
git worktree add -b stage-2-photographer-upload \
  .worktrees/stage-2-photographer-upload origin/stage-2-implementation-plan
cd .worktrees/stage-2-photographer-upload
```

Create a worktree-local configuration without overwriting an existing one:

```bash
test -f .env || cp .env.example .env
```

The upload feature is disabled by default. To test real browser-to-storage uploads, set these values
in the worktree's `.env`:

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
PHOTO_UPLOAD_ENABLED=True
PRIVATE_MEDIA_S3_BUCKET=<private-bucket>
PRIVATE_MEDIA_S3_ACCESS_KEY_ID=<access-key>
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=<secret-key>
PRIVATE_MEDIA_ALLOWED_ORIGINS=http://localhost:8000
```

The bucket and credentials must be real, and its CORS policy must allow the exact
`http://localhost:8000` origin. With the feature disabled, the rest of the application still runs,
but `/photographer/uploads/` returns 404. With placeholder storage values, the page may render but a
real upload will not complete.

Start the worktree version:

```bash
docker compose up --build -d
docker compose logs -f web
```

The entrypoint applies migrations, creates the `Photographer` permission group, and collects static
files. For a fresh database, create a superuser:

```bash
docker compose exec web python manage.py createsuperuser
```

Use Django Admin at `http://localhost:8000/admin/` to create at least one event, then open
`http://localhost:8000/photographer/uploads/`. A superuser already has upload permission; a regular
user must belong to the `Photographer` group.

### Stop a local version

Run this from the same checkout or worktree that started Compose:

```bash
docker compose down
```

This keeps the PostgreSQL volume. Do not add `-v` unless deleting the local database is intentional.

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
staging`, builds `ghcr.io/peter-nikitin/photo-prjct:<commit-sha>`, deploys it behind the shared HTTPS
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

The shared HTTPS overlay issues a certificate only when none exists, redirects HTTP to HTTPS, and
proxies to private Django. Certificate/account state is kept in persistent Docker volumes; Certbot
attempts renewal every 12 hours. Verify the edge with:

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

### Enable photographer uploads on staging

Keep `PHOTO_UPLOAD_ENABLED=False` for the first deployment of an ingestion-capable image. Configure
these GitHub Environment or repository values before that deployment:

- variable `PRIVATE_MEDIA_S3_BUCKET` with the separate private bucket name;
- variable `PRIVATE_MEDIA_ALLOWED_ORIGINS` with the exact public origin, currently
  `https://findme-photo.ru`;
- secrets `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` and `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` for the
  least-privilege service account.

After the disabled deployment is healthy, run the opt-in storage contract inside the staging web
container. The one-process override keeps the public upload routes disabled while the probe creates
and removes its temporary objects:

```bash
cd /opt/photo-prjct
docker compose --project-name photo-prjct-staging \
  --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.https.yml \
  exec -T -e PHOTO_UPLOAD_ENABLED=True web \
  sh -lc 'python manage.py verify_private_upload_storage --confirm-real-storage --origin "$PRIVATE_MEDIA_ALLOWED_ORIGINS"'
```

Only after this command succeeds, set `PHOTO_UPLOAD_ENABLED=True` and redeploy the same reviewed
revision. Enabled deployment validates private configuration and host `crontab`/`flock`, then
installs one daily 03:17 host-time cleanup entry. Disable and redeploy to hide all upload routes and
remove only that managed cron block; confirmed database rows and private originals remain intact.

`Promote production` is manually dispatched with the successfully staged commit SHA. It verifies
that SHA against the marker on staging, pauses for the production Environment approval, checks out
the selected revision, and deploys the same GHCR image without rebuilding it. Production remains
unavailable until a separate non-preemptible VM is approved and provisioned.

The web container runs migrations and `collectstatic` before starting Gunicorn. Host `.env` files,
GitHub secrets, and cloud credentials must never be committed. Use the project
`manage-yandex-cloud` skill for inventory or infrastructure operations; it requires fresh manual
confirmation for every change that may affect Yandex Cloud charges.
