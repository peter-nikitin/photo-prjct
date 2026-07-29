# Локальная ручная проверка photo-processing worker

Эта инструкция проверяет сквозной путь с настоящими private Object Storage: браузер загружает два JPEG через штатную страницу фотографа, Django подтверждает их и создаёт jobs, отдельный worker получает временные GET-ссылки, извлекает EXIF и Django сохраняет точные статусы и неизменяемый отчёт мероприятия. Она не включает worker на staging или production. Перед решением о реальной VM используйте отдельную [оценку конфигурации VM](photo-processing-vm-sizing.md): эта ручная проверка собирает для неё нужные измерения, но сама не разрешает resize или включение worker.

После создания `.env` по инструкции ниже запустите быстрый автоматизированный preflight перед
ручной проверкой:

```bash
cd /Users/petrnikitin/Documents/Projects/photo-prjct/.worktrees/event-photo-processing-worker
docker compose up -d db
set -a; source .env; set +a
DB_HOST=127.0.0.1 DB_PORT=5432 ../../.venv/bin/pytest -q tests/processing/test_pipeline_e2e.py
```

Этот тест использует настоящий Django API и worker, но подменяет скачивание exact-object JPEG. Это детерминированная проверка контракта, **не** ручная проверка настоящего S3/Object Storage.

## Перед началом

Нужны Docker Desktop/Engine с Compose, рабочая `../../.venv`, и два небольших настоящих JPEG:

- `with-exif.jpg` с `DateTimeOriginal` в виде `YYYY:MM:DD HH:MM:SS`;
- `no-exif.jpg`, экспортированный как JPEG без EXIF-времени (не переименованный PNG).

Положите их, например, в игнорируемый `media/` в этом checkout. До загрузки проверьте EXIF из контейнера web (каталог checkout смонтирован в `/app`):

```bash
mkdir -p media/manual-processing
docker compose up -d db web
docker compose exec web python -c 'from PIL import Image; from pathlib import Path; import sys; image = Image.open(Path(sys.argv[1])); print(image.format, image.getexif().get(36867))' /app/media/manual-processing/with-exif.jpg
docker compose exec web python -c 'from PIL import Image; from pathlib import Path; import sys; image = Image.open(Path(sys.argv[1])); print(image.format, image.getexif().get(36867))' /app/media/manual-processing/no-exif.jpg
```

Первая команда должна напечатать `JPEG` и значение времени; вторая — `JPEG None`. Если у первого файла нет `DateTimeOriginal`, выберите другой снимок: v1 намеренно не пытается угадать время из имени файла или файловой системы.

Для этой проверки нужен настоящий private bucket и service-account, которому Django может подписывать GET/POST, проверять объекты и продвигать их в final key. В bucket CORS должен разрешать точный origin `http://localhost:8000` для browser upload. Worker не получает эти S3 credentials.

Создайте checkout-local `.env`, не перезаписывая существующий, и задайте минимум:

```dotenv
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,web
PHOTO_UPLOAD_ENABLED=True
PRIVATE_MEDIA_S3_BUCKET=<real-private-bucket>
PRIVATE_MEDIA_S3_ACCESS_KEY_ID=<real-access-key>
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY=<real-secret>
PRIVATE_MEDIA_ALLOWED_ORIGINS=http://localhost:8000
PHOTO_PROCESSING_ENABLED=True
PHOTO_PROCESSING_WORKER_TOKEN=<new-random-shared-token>
PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=120
PHOTO_WORKER_BUILD=capture-metadata-v1
PHOTO_WORKER_LEASE_SECONDS=120
```

Start with `test -f .env || cp .env.example .env`, then edit it. Generate the worker token locally
and paste it into `.env`; do not use the placeholder, commit it, or echo it again after saving:

```bash
../../.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep the default `DB_*` values for Compose. `PRIVATE_MEDIA_*` values belong only to `web`; the Compose `worker` service is deliberately given only API URL, the shared worker token, build, and lease duration.

## Проверка

Start the trusted services first, without a worker. This lets both test photos join the same collecting event run before its first claim seals the cohort.

```bash
docker compose up --build -d db web
docker compose logs -f web
```

Wait until migrations finish, leave logs with `Ctrl+C`, then create an administrator if this is a new local database:

```bash
docker compose exec web python manage.py createsuperuser
```

Open `http://localhost:8000/admin/`, create an Event (name, unique slug, start/end date, and city are required), and remember its slug. A draft event is sufficient for this check. Log in at `http://localhost:8000/photographer/login/`, then open `http://localhost:8000/photographer/uploads/`. A superuser has upload permission; another user must be in the `Photographer` group. Select that event, choose both JPEGs, and wait for the page to report the uploads as completed. This is the supported direct browser-to-storage confirmation flow; do not create `Photo` or `ProcessingJob` rows manually.

Before starting the worker, inspect the precise current states. Substitute the event slug only in the `CHECK_EVENT_SLUG` value:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); rows = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="capture_metadata").order_by("photo_id").values("photo_id", "status", "current_run_id", "current_job_id", "current_attempt_id", "accepted_attempt_id", "queued_at", "processing_at", "succeeded_at", "failed_at", "next_attempt_at")); print(json.dumps(rows, default=str, indent=2))'
```

There should be two `queued` rows with the same `current_run_id`. Now opt in the separate worker:

```bash
docker compose --profile worker up --build -d worker
docker compose logs -f worker
```

Expected worker log sequence is `claimed`, `started`, then `succeeded` for one photo at a time. It must contain opaque IDs, stable lifecycle names and durations only: a presigned URL, its query string, the worker token, and S3 credentials must not appear. Leave logs with `Ctrl+C` after both photos finish.

Run the state query again. Expected terminal results are:

- both photo states and their current jobs are `succeeded`;
- the EXIF photo's accepted attempt has a normalized UTC `capture_time`, normally with `source_field: DateTimeOriginal` and `timezone_state: inferred_none` when no offset is present;
- the EXIF-free photo also succeeds, with `capture_time: null`, `source_field: null`, `timezone_state: not_applicable`, and warning `capture_time_missing`.

Read the event-scoped immutable evidence without changing it:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import EventProcessingRun, ProcessingAttempt; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); run = EventProcessingRun.objects.filter(event=event, processor_type="capture_metadata").latest("created_at"); attempts = list(ProcessingAttempt.objects.filter(run=run).order_by("created_at").values("id", "photo_id", "status", "accepted", "worker_build", "download_duration_ms", "compute_duration_ms", "total_duration_ms", "result", "error_code", "lease_expires_at", "terminal_at")); print(json.dumps({"run_id": str(run.id), "status": run.status, "configuration": run.configuration, "report": run.report, "attempts": attempts}, default=str, indent=2))'
```

The run must be `closed`, with one immutable report for this exact event cohort. Its report has `cohort_size: 2`, a denominator of 2, two successes, capture-time counts one-with/one-without, and the persisted min/median/max accepted `total_duration_ms`. Treat these measured values as the timing result; do not compare them to a fixed wall-clock threshold. Worker concurrency is one, so the two attempts are deliberately serial.

## Stop and rollback

Stop the worker first. This preserves all jobs, attempts, reports, originals, and the local PostgreSQL volume for inspection:

```bash
docker compose --profile worker stop worker
docker compose down
```

For a local functional rollback, set `PHOTO_PROCESSING_ENABLED=False` in the ignored `.env` and start only `db web` again. Do not use `docker compose down -v` unless deleting the local database and all evidence is intentional.

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| Upload page is 404 or access is denied | Confirm `PHOTO_UPLOAD_ENABLED=True`; log in as a superuser or add the user to `Photographer`. |
| Browser upload fails before confirmation | `PRIVATE_MEDIA_ALLOWED_ORIGINS` and the bucket CORS rule must be exactly `http://localhost:8000`; confirm real private bucket credentials and retry the page flow. |
| Worker logs `worker_unauthorized` | Confirm `PHOTO_PROCESSING_ENABLED=True` and a nonempty random `PHOTO_PROCESSING_WORKER_TOKEN` in the same root `.env`; recreate `web` and `worker` with `docker compose --profile worker up -d --force-recreate web worker`. Never print either token. |
| Worker logs `storage_unavailable` | The object was already confirmed, so first inspect web logs and the private bucket credentials/end point. Django, not worker, signs the GET; verify the final object still exists and that the service account can sign a GET for it. |
| `Invalid HTTP_HOST header` | Include `localhost,127.0.0.1,web` in `ALLOWED_HOSTS`, then recreate `web`. |
| A worker stops during a real job | After its 120-second lease expires, start a worker again. Its next `claim` recovers the expired attempt; the state becomes `retry_wait` for the configured 30–35 second backoff and is then claimed again, up to three total attempts. Query the state/attempt commands above rather than inferring recovery from logs. |
| Docker cannot pull the public Python base image | Repair the local Docker credential helper/login for public pulls, then rerun the build. Do not weaken Docker's credential configuration globally or add registry secrets to this repository. |

The expected isolated image check is also available when diagnosing a build/start problem:

```bash
docker compose --env-file .env.example --profile worker build worker
docker compose --env-file .env.example --profile worker run --rm --no-deps worker
```

The first command builds the worker image. The second is intentionally expected to exit with `ValueError: worker API URL and token are required`: the example configuration has no token, proving the process fails before it can request work. It is not the real-S3 manual test above.
