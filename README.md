# FindMe Photo

FindMe Photo is an early-stage event photo marketplace for event-scoped photo discovery and protected
purchase downloads. The current repository is a Django/PostgreSQL prototype; the target MVP and its
unresolved decisions are documented rather than assumed to be implemented.

## Engineering documentation

- [Architecture](docs/architecture.md) — implemented system, accepted constraints, target MVP, and
  open decisions.
- [Architecture decisions](docs/adr/README.md) — durable decisions and the ADR template.
- [Implementation plans](docs/plans/README.md) — delivery-plan conventions and template.
- [Staging deployment runbook](docs/runbooks/staging-deployment.md) — automatic deployments,
  controlled privileged-package pauses, retries, rollback, and acceptance checks.
- [Local photo-processing worker check](docs/local-photo-processing-check.md) — manual real-Object-
  Storage verification before any deployment decision.
- [Project skills](.agents/skills) — repository-scoped workflows for writing ADRs and plans and for
  safely operating Yandex Cloud resources.

## Local development

Requirements: Git, Docker, Docker Compose, Python 3.12+, NVM, and Node 22. Python is required for
the clone helper and for running management commands and quality checks directly on the host.

For the opt-in worker's local, real-Object-Storage verification, follow
[Local photo-processing worker check](docs/local-photo-processing-check.md). It leaves the worker
disabled until explicitly started with its Compose profile.

The `main` checkout and a feature worktree use separate source directories and Compose projects, so
each directory needs its own ignored `.env` file. Both configurations expose PostgreSQL on port
`5432` and Django on port `8000`; stop one before starting the other unless you intentionally change
the port mappings.

Create a feature worktree from the main checkout with the supported bootstrap command:

```bash
make worktree NAME=my-change
cd .worktrees/my-change
```

`BASE` defaults to `origin/main`; override it with `BASE=<ref>` when necessary. The command creates
branch `codex/<name>`, links the main checkout's ignored `.venv`, creates a worktree-local `.env`
from `.env.example` with safe host-test values, installs the shared pre-commit hook, and verifies
Python, pytest, and Django settings. It never reads or copies the main checkout's `.env`.

Run a focused or normal local Python test selection without activating the virtual environment or
manually supplying Django settings:

```bash
make test TESTS="tests/test_repository_foundation.py"
make test
make check
```

Local `make test` and the pytest portion of `make check` include the critical clone-staging contract
but skip its exhaustive matrix. Run the exhaustive clone-staging suite separately with:

```bash
make test-clone-deployed
```

`make check` runs local Python formatting, lint, type, coverage, Django, and migration checks. Its
pytest portion skips the exhaustive clone-staging matrix; GitHub CI's raw pytest coverage command
runs the full test selection. PostgreSQL must be available on `localhost:5432`, matching CI.

### Verify public selfie search locally

Public selfie search is available for every published free event. Photo processing and face
embeddings are mandatory deployment prerequisites; the application and deployment fail fast when
either is unavailable. The approved worker contract uses these ordered values:

```dotenv
PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/2,1/selfie_query/2,2/generate_preview/1,2/face_embedding/3
PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,generate_preview
```

With a disposable local PostgreSQL database, locally available SCRFD/SFace files, and a
true-JPEG file, run the host-process application/worker boundary without committing or printing the
artifact paths:

```bash
PHOTO_WORKER_SCRFD_MODEL_PATH=/absolute/path/to/det_10g.onnx \
PHOTO_WORKER_SFACE_MODEL_PATH=/absolute/path/to/sface.onnx \
SELFIE_SEARCH_E2E_JPEG_PATH=/absolute/path/to/single-face.jpg \
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
SECRET_KEY=local-not-a-secret DEBUG=False ALLOWED_HOSTS=localhost,127.0.0.1 \
.venv/bin/pytest -q tests/processing/test_selfie_search_e2e.py -m face_models
```

The test runs real SCRFD/SFace inference for the submitted selfie query. Its gallery side uses
deterministic accepted embedding fixtures for historical stored v1 and current preview-backed v3
face evidence; the preview-first fixture
publishes a verified `2/generate_preview/1` derivative and follows production enrollment into
`2/face_embedding/3`. It also covers exact event-scoped ranking, selfie cleanup, stable bearer
results, the narrow paid-result media exception for both gallery generations, and unchanged normal
paid-gallery denial. It skips when its required local JPEG or model file is absent; a skip is not
real-model evidence.

This host-process test does not activate Docker Compose or prove the rollout image. The existing
worker image packages pinned official SCRFD and OpenCV Zoo SFace files at immutable container paths and
runs `photo_worker.model_smoke` during its build. Before enabling `selfie_query`, run the same smoke
against the exact rollout image digest:

```bash
docker run --rm --network none --entrypoint python "$WORKER_IMAGE" -m photo_worker.model_smoke
```

Then apply and verify the exact `selfie-search/` lifecycle, run the explicit scratch-object
preflight, and execute the staging smoke and capacity measurements in the
[public selfie-search rollout](docs/plans/2026-07-30-public-selfie-search.md#operational-impact-and-rollout).

### Verify selfie-search feedback storage on staging

Selfie-search feedback is implemented but remains disabled by default. After the dedicated private
bucket, KMS key, and web-only credentials have been provisioned, run the explicit preflight while
the deployed `.env` still has `SELFIE_FEEDBACK_ENABLED=False`; export the bucket, access-key,
secret-key, and KMS-key variables for the command as the deployment workflow does:

```bash
cd /opt/photo-prjct
test "$(sed -n 's/^SELFIE_FEEDBACK_ENABLED=//p' .env | head -n 1)" = False
docker compose --project-name photo-prjct-staging \
  --env-file .env \
  -f docker-compose.prod.yml \
  -f docker-compose.https.yml \
  exec -T \
  -e SELFIE_FEEDBACK_ENABLED=True \
  -e SELFIE_FEEDBACK_S3_BUCKET \
  -e SELFIE_FEEDBACK_S3_ACCESS_KEY_ID \
  -e SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY \
  -e SELFIE_FEEDBACK_KMS_KEY_ID \
  web python manage.py verify_selfie_feedback_storage --confirm-real-storage
```

The command checks the dedicated bucket contract and removes its generated scratch object. It is
covered by the repository's automated storage/deployment tests; passing it does not enable feedback
or replace the separate policy, lifecycle-mutation, staging smoke, and activation gates.

### Operate selfie-search observability

Before the first observability rollout, or whenever its host package changes, an operator with
existing root access installs the reviewed package and the narrow `deploy` sudo rule:

```bash
DEPLOY_ROOT=/opt/photo-prjct sh deploy/bootstrap-selfie-observability.sh
```

The bootstrap copies all executable inputs to root-owned paths. Routine deployments can then invoke
only the fixed helper actions `install`, `verify`, `rollback`, `commit`, and the UUID-validated
`verify-probe`; they never execute files
from the deploy-owned checkout as root. The supported deployment entrypoint installs and verifies a persistent system journal capped by
`MaxRetentionSec=14day` and `SystemMaxUse=1G`, stable `web`, `worker`, and `nginx` tags, and the
`selfie-search-summary.timer`. The cap can shorten effective history under heavy log volume; the
journal is operational evidence, not a backup.

Inspect bounded events and the latest summary without printing unrelated logs:

```bash
journalctl -u docker.service \
  CONTAINER_TAG='findme.service=web findme.environment=staging' \
  --since '24 hours ago' --grep '"event":"selfie_' -o cat
journalctl -u selfie-search-summary.service --since '14 days ago' -o cat \
  | grep '"event":"selfie_search_daily_summary"'
systemctl status selfie-search-summary.timer
```

Recompute one Moscow calendar date without changing application or database state:

```bash
sudo /usr/local/lib/findme-selfie-observability/run-daily-summary.sh 2026-08-03
```

Current submission/probe/worker events remain schema v1; ranking and terminal events are schema v2
and carry only bounded direct/cluster-expanded/final counts, anchor/cluster totals, opaque corpus
version/hash, expansion duration, and a fixed outcome. The summary's `expansion` object reports
eligible/helped searches, p50/p95 added photos and expansion time, outcomes, versions/hashes, and
rates with explicit integer numerators and denominators. Historical v1 ranking/terminal expansion
metrics are `not_available`, never fabricated zeroes; mismatches or missing ranking/terminal pairs
make `complete=false`. A `search_unavailable` search caused by an empty direct cohort clears
corpus identity and expansion duration in both v2 events together; source counts remain zero and
that no-cohort observation is excluded from eligible expansion aggregates.

The root helper verifies effective policy and timer state; the unprivileged
`deploy/verify-selfie-observability.sh` verifies Compose tags and an emitted probe. Do not paste raw journal output into tickets;
record only the bounded summary and sanitized diagnostics.

For the complete incident workflow, use the [selfie-search log-analysis runbook](docs/runbooks/selfie-search-log-analysis.md).

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

### Clone staging data locally for migration development

This developer workflow replaces only the current checkout's local Compose database with a fresh
logical dump from staging. It is destructive to that local database; it is not a staging restore,
service-backup, or disaster-recovery procedure.

Before running it, create the checkout-local `.env`, ensure Docker and Docker Compose are available,
and confirm that `VM_SSH_TARGET` can connect to the staging host. Keep enough local disk space
for both the incoming staging dump and a safety dump of the current local database. Logical dumps can
contain personal data: keep them on an encrypted developer disk, do not upload them to shared
services, and delete them manually when the migration branch no longer needs them.

The helper inspects the effective Docker context and the `DOCKER_CONTEXT`/`DOCKER_HOST` overrides
before confirmation or SSH. It accepts only local Unix sockets and loopback `tcp://` endpoints
(`127.0.0.0/8`, `[::1]`, or `localhost`); remote, SSH, HTTP(S), and unknown Docker endpoints are
rejected without printing the endpoint.

Run the one-command clone interactively so it displays the exact local Compose project and database
before asking for `yes`:

```bash
VM_SSH_TARGET=<user>@<staging-host> make db-clone-deployed
```

For non-interactive automation, set the explicit confirmation only after independently confirming
that the current checkout is the intended local target:

```bash
VM_SSH_TARGET=<user>@<staging-host> CONFIRM_REPLACE_LOCAL_DB=yes make db-clone-deployed
```

The command streams and validates a PostgreSQL custom-format dump before changing local data, writes
the staging dump, checksum, and metadata under `var/backups/deployed/`, and first makes a local safety
dump in the same directory. The artifacts are mode `0600` and ignored by Git. A failed staging restore
attempts to recover the original local database from its safety dump; all dumps remain available for
diagnosis.

Only one clone for the resolved Compose project/database may run at a time. The helper holds an
atomic SHA-256-keyed lock under the selected backup directory's `.locks/` directory before contacting
staging or replacing from a retained dump. A second process exits before SSH or SQL. If an interrupted
process leaves a stale lock, first verify that no clone is running, then use only the exact `rmdir`
command printed by the helper; it never deletes another process's lock automatically.

If the checkout's normal `web` service is running, the helper stops it before the local safety dump
and database replacement. Failure to stop it aborts before `DROP DATABASE`. Once stopped, the normal
service remains stopped on success or any later failure; validation uses only entrypoint-overridden
one-off containers. After the clone reports successful validation, restart normal local development
explicitly:

```bash
docker compose up -d web
```

After a successful restore, read-only, entrypoint-overridden one-off `web` containers verify database
connectivity and `django_migrations`, inspect `showmigrations --plan`, and run
`makemigrations --check --dry-run`. The clone stops with an actionable message if there are no applied
migrations, the database names migrations absent from the checkout (update the branch), or the
checkout has unapplied migration/model drift. It never starts the normal web entrypoint and never runs
`migrate`.

To retry from an existing retained dump without contacting staging, keep its matching `.sha256` sidecar
next to it and run:

```bash
DEPLOYED_DUMP_FILE=/absolute/path/to/<timestamp>.dump make db-clone-deployed
```

This mode verifies the checksum and PostgreSQL custom archive before any local SQL, then uses the
same confirmation, safety dump, replacement, recovery, and Django validation path. It never modifies
the supplied dump or checksum, and it does not require or contact `VM_SSH_TARGET`.

The [direct staging database plan](docs/plans/2026-07-22-local-read-only-staging-database.md) is a
draft plan, not an implemented workflow. Never point normal Django or Compose startup at staging: the
image entrypoint runs migrations and other mutations.

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

New worktrees install fast local hooks automatically. Install or repair the shared hook in an
existing checkout with:

```bash
make hooks
```

The hook formats and lints staged Python files. If it changes a file, stage the result and repeat the
commit. Run `.venv/bin/pre-commit run --all-files` only when intentionally checking the whole
repository. CI remains authoritative and also runs types, tests, Django checks, migration drift
detection, and repository skill-structure tests.

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
