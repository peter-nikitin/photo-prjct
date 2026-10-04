# FindMe Photo

FindMe Photo is an early-stage event photo marketplace for event-scoped photo discovery and protected
purchase downloads. The current repository is a Django/PostgreSQL prototype; the target MVP and its
unresolved decisions are documented rather than assumed to be implemented.

## Engineering documentation

- [Architecture](docs/architecture.md) — implemented system, accepted constraints, target MVP, and
  open decisions.
- [Architecture decisions](docs/adr/README.md) — durable decisions and the ADR template.
- [Implementation plans](docs/plans/README.md) — delivery-plan conventions and template.
- [Canonical deployment runbook](docs/runbooks/deployment.md) — automatic deployments,
  controlled privileged-package pauses, retries, rollback, and acceptance checks.
- [Local photo-processing worker check](docs/local-photo-processing-check.md) — manual real-Object-
  Storage verification before any deployment decision.
- [Project skills](.agents/skills) — repository-scoped workflows for writing ADRs and plans and for
  safely operating Yandex Cloud resources.

## Local development

Requirements: Git, Docker, Docker Compose, Python 3.12+, NVM, and Node 22. Python is required for
the clone helper and for running management commands and quality checks directly on the host.

For the worker's local, real-Object-Storage verification, follow
[Local photo-processing worker check](docs/local-photo-processing-check.md). The local Compose
profile starts the worker only for the finite check.

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
make static
make check
```

Local `make test` and the pytest portion of `make check` use four pytest workers by default. Set
`PYTEST_XDIST_WORKERS=0` for serial debugging. Test methods from one class stay on the same worker
to preserve Django class lifecycle boundaries. Both commands include the critical clone-deployed
contract but skip its exhaustive matrix. Run the exhaustive clone-deployed suite separately with:

```bash
make test-clone-deployed
```

`make static` reports Python formatting, lint, and type failures together. `make check` runs that
static gate plus coverage, Django, and migration checks. Its pytest portion skips the exhaustive
clone-deployed matrix; GitHub CI runs the full test selection with four workers. PostgreSQL must be
available on `localhost:5432`, matching CI.

### Verify public selfie search locally

Public selfie search is available for every published free event. Photo processing and face
embeddings are mandatory deployment prerequisites; the application and deployment fail fast when
either is unavailable. The host-process test below supplies this combined worker environment:

```dotenv
PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/2,1/selfie_query/2,2/generate_preview/1,2/face_embedding/3
PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,generate_preview
```

Compose uses separate `worker-bulk` and `worker-selfie` services. Configure their inputs with
`PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES` / `PHOTO_WORKER_BULK_PROCESSOR_TYPES` and
`PHOTO_WORKER_SELFIE_PROCESSOR_IDENTITIES` / `PHOTO_WORKER_SELFIE_PROCESSOR_TYPES`; Compose maps
these to the process variables above. `PHOTO_WORKER_REPLICAS` controls bulk replicas while the
deployment runs one separate selfie worker.

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
runs `photo_worker.model_smoke` during its build. For release-image verification, run the same smoke
against the exact rollout image digest:

```bash
docker run --rm --network none --entrypoint python "$WORKER_IMAGE" -m photo_worker.model_smoke
```

The original rollout's lifecycle, scratch-object preflight, smoke, and capacity requirements are
recorded in the [public selfie-search plan](docs/plans/2026-07-30-public-selfie-search.md#operational-impact-and-rollout).

### Verify selfie-search feedback storage on the canonical deployment

Selfie-search feedback is part of the terminal eligible result flow. The canonical web service
requires the dedicated private bucket, KMS key, and web-only credentials at startup. After these
are provisioned, run the explicit storage preflight with the deployed configuration:

```bash
cd /opt/photo-prjct
docker compose --project-name photo-prjct \
  --env-file .env \
  -f docker-compose.deployment.yml \
  -f docker-compose.https.yml \
  exec -T \
  web python manage.py verify_selfie_feedback_storage --confirm-real-storage
```

The command checks the dedicated bucket contract and removes its generated scratch object. It is
covered by the repository's automated storage/deployment tests. It does not replace lifecycle
verification, the separate personal-data-policy reconciliation, or a canonical customer-path smoke.

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
`MaxRetentionSec=14day` and `SystemMaxUse=1G`, stable `web`, `worker-bulk`, `worker-selfie`, and `nginx` tags, and the
`selfie-search-summary.timer`. The cap can shorten effective history under heavy log volume; the
journal is operational evidence, not a backup.

Inspect bounded events and the latest summary without printing unrelated logs:

```bash
journalctl -u docker.service \
  CONTAINER_TAG='findme.service=web' \
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
cd /Users/petrnikitin/Documents/Projects/photo-prjct
git switch main
git pull --ff-only
test -f .env || cp .env.example .env
```

Before starting a fresh local web process, set the following values in that checkout's ignored
`.env`. These distinct bucket names and credentials are local test-only placeholders: they satisfy
configuration checks but cannot access Object Storage. Replace them with real, separately scoped
credentials before testing uploads or submitting feedback. The canonical deployment requires real
private-media and feedback storage configuration and a successful storage preflight. Keep the
approved feedback endpoint, region, 20 MiB upload limit, and 60-second download TTL defaults.

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,web
PRIVATE_MEDIA_S3_BUCKET=test-private-media
PRIVATE_MEDIA_S3_ACCESS_KEY_ID=test-private-access
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=test-private-secret
PRIVATE_MEDIA_ALLOWED_ORIGINS=http://localhost:8000
SELFIE_FEEDBACK_S3_BUCKET=test-feedback-media
SELFIE_FEEDBACK_S3_ACCESS_KEY_ID=test-feedback-access
SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY=test-feedback-secret
SELFIE_FEEDBACK_KMS_KEY_ID=test-feedback-kms
```

Then start the stack:

```bash
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

### Verify photographer uploads locally

Create a worktree-local configuration without overwriting an existing one:

```bash
test -f .env || cp .env.example .env
```

To test real browser-to-storage uploads, set these values in the current checkout's `.env`:

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
PRIVATE_MEDIA_S3_BUCKET=<private-bucket>
PRIVATE_MEDIA_S3_ACCESS_KEY_ID=<access-key>
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=<secret-key>
PRIVATE_MEDIA_ALLOWED_ORIGINS=http://localhost:8000
SELFIE_FEEDBACK_S3_BUCKET=test-feedback-media
SELFIE_FEEDBACK_S3_ACCESS_KEY_ID=test-feedback-access
SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY=test-feedback-secret
SELFIE_FEEDBACK_KMS_KEY_ID=test-feedback-kms
```

The bucket and credentials must be real, and its CORS policy must allow the exact
`http://localhost:8000` origin. The upload page requires an authenticated user with
`ingestion.upload_photos`; placeholder storage values cannot complete a real upload. The feedback
values above are local test-only placeholders for this upload check. A real feedback submission
requires its own dedicated bucket, credentials, KMS key, and storage preflight.

Start the local stack:

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

### Clone deployed data locally for migration development

This developer workflow replaces only the current checkout's local Compose database with a fresh
logical dump from the canonical deployment. It is destructive to that local database; it is not a deployed-data restore,
service-backup, or disaster-recovery procedure.

Before running it, create the checkout-local `.env`, ensure Docker and Docker Compose are available,
and confirm that `VM_SSH_TARGET` can connect to the deployed VM. Keep enough local disk space
for both the incoming deployed dump and a safety dump of the current local database. Logical dumps can
contain personal data: keep them on an encrypted developer disk, do not upload them to shared
services, and delete them manually when the migration branch no longer needs them.

The helper inspects the effective Docker context and the `DOCKER_CONTEXT`/`DOCKER_HOST` overrides
before confirmation or SSH. It accepts only local Unix sockets and loopback `tcp://` endpoints
(`127.0.0.0/8`, `[::1]`, or `localhost`); remote, SSH, HTTP(S), and unknown Docker endpoints are
rejected without printing the endpoint.

Run the one-command clone interactively so it displays the exact local Compose project and database
before asking for `yes`:

```bash
VM_SSH_TARGET=<user>@<deployed-host> make db-clone-deployed
```

For non-interactive automation, set the explicit confirmation only after independently confirming
that the current checkout is the intended local target:

```bash
VM_SSH_TARGET=<user>@<deployed-host> CONFIRM_REPLACE_LOCAL_DB=yes make db-clone-deployed
```

The command streams and validates a PostgreSQL custom-format dump before changing local data, writes
the deployed dump, checksum, and metadata under `var/backups/deployed/`, and first makes a local safety
dump in the same directory. A failed deployed-data restore
attempts to recover the original local database from its safety dump; all dumps remain available for
diagnosis.

Only one clone for the resolved Compose project/database may run at a time. The helper holds an
atomic SHA-256-keyed lock under the selected backup directory's `.locks/` directory before contacting
the deployed VM or replacing from a retained dump. A second process exits before SSH or SQL. If an interrupted
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

To retry from an existing retained dump without contacting the deployed VM, keep its matching `.sha256` sidecar
next to it and run:

```bash
DEPLOYED_DUMP_FILE=/absolute/path/to/<timestamp>.dump make db-clone-deployed
```

This mode verifies the checksum and PostgreSQL custom archive before any local SQL, then uses the
same confirmation, safety dump, replacement, recovery, and Django validation path. It never modifies
the supplied dump or checksum, and it does not require or contact `VM_SSH_TARGET`.

The dated [direct staging database plan](docs/plans/2026-07-22-local-read-only-staging-database.md) is a
historical plan, not an implemented workflow. Never point normal Django or Compose startup at the deployed VM: the
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

The hook formats and lints staged Python files, then runs full-project mypy. Before handoff or
review, run `.venv/bin/pre-commit run --files <task Python files>` for the exact changed Python
files. If the commit-time hook changes a file, stage the result and repeat the commit. Run
`.venv/bin/pre-commit run --all-files` only when intentionally checking the whole repository. CI
uses the same aggregated static gate and also runs tests, Django checks, migration drift detection,
and repository skill-structure tests.

## Deployment

The customer-serving Yandex Cloud VM is the one unqualified canonical deployment. **Deploy**
classifies each `main` change: documentation-only changes do nothing; backend changes publish and
deploy the SHA-tagged web/import images without touching photo workers; worker changes publish a
worker image and advance its `latest` pointer after image smoke. The reusable worker base contains
the pinned models and heavy dependencies. Running worker hosts warm and switch containers in place;
new VMs start from `latest`. Mixed changes publish both components. The workflow verifies the
immutable web image and `https://findme-photo.ru/health/`. See the
[deployment runbook](docs/runbooks/deployment.md) and
[worker-pool runbook](docs/runbooks/worker-pools.md) for operations and recovery.

The worker alert profile in [`deploy/monitoring/prometheus/environment.json`](deploy/monitoring/prometheus/environment.json)
is enabled in Git to prepare the next worker launch. This change has not applied those rules to
live Managed Prometheus and does not establish live alert delivery or remote worker acceptance.
The reviewed, exact-revision Monitoring workflow and its live evidence gates still govern activation.

One Lockbox manifest, [`deploy/environment-secrets.json`](deploy/environment-secrets.json), supplies
secret projections to `deploy`, `remote-check`, `public-monitor`, and `local-web`. Non-secret
configuration is held in repository variables. There is no GitHub Environment and no
`DEPLOYMENT_TARGET`; do not create aliases or separate deployment copies to hide unfinished work.

The shared HTTPS overlay issues a certificate only when none exists, redirects HTTP to HTTPS, and
proxies to private Django. Certificate/account state is kept in persistent Docker volumes; Certbot
attempts renewal every 12 hours. Verify the edge with:

```bash
curl -I http://<public-domain>/
curl --fail https://<public-domain>/health/
```

The first command must return a canonical 308 redirect and the second must return
`{"status": "ok"}` with normal TLS trust validation. Validate renewal on the activated VM with the
same Compose project and both `docker-compose.deployment.yml` and `docker-compose.https.yml` by running
`certbot renew --dry-run` in the Certbot service.

Changing `PUBLIC_DOMAIN` or `PUBLIC_DOMAIN_ALIAS` does not automatically replace an existing
certificate. Treat such a change as maintenance: back up the environment certificate volume,
remove the named certificate explicitly, and rerun deployment once to issue the new name set.

### Verify photographer upload storage on the canonical deployment

Configure these reviewed repository variables before deployment:

- variable `PRIVATE_MEDIA_S3_BUCKET` with the separate private bucket name;
- variable `PRIVATE_MEDIA_ALLOWED_ORIGINS` with the exact public origin, currently
  `https://findme-photo.ru`;
- secrets `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` and `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` for the
  least-privilege service account.

The canonical web startup validates private storage configuration. After deployment, run the
storage contract inside the deployed web container; the probe creates and removes only its
temporary objects:

```bash
cd /opt/photo-prjct
docker compose --project-name photo-prjct \
  --env-file .env \
  -f docker-compose.deployment.yml \
  -f docker-compose.https.yml \
  exec -T web \
  sh -lc 'python manage.py verify_private_upload_storage --confirm-real-storage --origin "$PRIVATE_MEDIA_ALLOWED_ORIGINS"'
```

Deployment validates private configuration and host `crontab`/`flock`, then installs one daily
03:17 host-time cleanup entry. Upload access continues to depend on Django permission, batch
ownership, and the exact private-storage grant and confirmation checks. For an application failure,
redeploy the prior successful image; preserve confirmed rows and private originals.

The web container runs migrations and `collectstatic` before starting Gunicorn. Host `.env` files,
GitHub secrets, and cloud credentials must never be committed. Use the project
`manage-yandex-cloud` skill for inventory or infrastructure operations; it requires fresh manual
confirmation for every change that may affect Yandex Cloud charges.
