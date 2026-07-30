# Локальная ручная проверка preview-first photo-processing worker

Эта инструкция проверяет сквозной путь с настоящими private Object Storage: браузер загружает JPEG через штатную страницу фотографа, Django подтверждает его и ставит `generate_preview`, отдельный worker получает временную GET-ссылку, создаёт нормализованный preview JPEG и загружает его только в попытку-scoped staging key. Django сам проверяет и публикует derivative, после чего ставит preview-backed `face_embedding` 2/2. Она не включает worker на staging или production. Перед решением о реальной VM используйте отдельную [оценку конфигурации VM](photo-processing-vm-sizing.md): эта ручная проверка собирает для неё нужные измерения, но сама не разрешает resize или включение worker.

После создания `.env` по инструкции ниже запустите быстрый автоматизированный preflight перед
ручной проверкой:

```bash
cd /Users/petrnikitin/Documents/Projects/photo-prjct/.worktrees/event-photo-processing-worker
docker compose up -d db
set -a; source .env; set +a
DB_HOST=127.0.0.1 DB_PORT=5432 ../../.venv/bin/pytest -q tests/processing/test_pipeline_e2e.py
```

Этот тест использует настоящий Django API и worker, но подменяет скачивание exact-object JPEG. Это детерминированная проверка контракта, **не** ручная проверка настоящего S3/Object Storage.

## Блокер активации: lifecycle для preview staging

До включения `PHOTO_PROCESSING_PREVIEW_ENABLED` на staging оператор обязан настроить правило
удаления только для временных preview-объектов. Целевое правило имеет ID
`expire-preview-staging-after-7-days`, prefix `processing-staging/previews/` и expiration
`days: "7"`. Оно не должно затрагивать опубликованные derivatives
(`derivatives/previews/`) или оригиналы. Пока это правило не применено и не проверено, activation
preview worker **заблокирована**.

На 2026-07-30 известен только non-secret mapping staging bucket: `hires-staging` — значение
GitHub repository variable `PRIVATE_MEDIA_S3_BUCKET`. Текущая lifecycle-конфигурация не
подтверждена: локальная interactive-auth `yc` истекла, и read-only listing вернул
`Unauthenticated`. Нельзя заменять lifecycle rules на основе этого имени или пустого файла.

### Read-only reauthentication и discovery

Сначала восстановите обычную интерактивную авторизацию профиля `default` в браузере, когда `yc`
попросит об этом. Не выводите и не сохраняйте token, не запускайте `yc config list`. Затем
выполните только следующие read-only команды и убедитесь, что context всё ещё staging:

```bash
yc config profile list
yc config get cloud-id
yc config get folder-id
yc storage bucket get hires-staging --full --folder-id b1g2qttgfhb4gdunvlge --format json > /private/tmp/hires-staging-before-20260730.json
```

Ожидаемые cloud/folder: `b1gmcsmr51o5kvp86l55` и `b1g2qttgfhb4gdunvlge`. Сохранённый snapshot
не коммитить и не отправлять в issue: он нужен как точная исходная точка и rollback evidence.
Сначала проверьте versioning. Lifecycle activation допустима только если полная readback запись
явно содержит `VERSIONING_DISABLED`: Yandex возвращает это точное JSON значение для bucket,
в котором versioning никогда не включался.

```bash
jq -e '.versioning == "VERSIONING_DISABLED"' /private/tmp/hires-staging-before-20260730.json
```

Если status равен `VERSIONING_ENABLED`, `VERSIONING_SUSPENDED`, отсутствует или имеет другое
значение, остановитесь. Правило expiration в versioned bucket не является доказательством удаления
noncurrent versions; отдельное reviewed решение об их cleanup, его data impact и approval нужны
до любой lifecycle mutation. Не добавляйте noncurrent action в этой задаче.

Только после успешной versioning-проверки сохраните lifecycle-only shape как исходный
rollback-файл. Все последующие сравнения используют именно этот `jq -S` canonical JSON shape:

```bash
jq -S '{lifecycleRules: (.lifecycleRules // [])}' /private/tmp/hires-staging-before-20260730.json \
  > /private/tmp/hires-staging-lifecycle-before-20260730.json
jq -e 'has("lifecycleRules") and (.lifecycleRules | type == "array")' \
  /private/tmp/hires-staging-lifecycle-before-20260730.json
```

Если discovery не возвращает current state или эта проверка не проходит, остановитесь: lifecycle
activation остаётся blocked, а worker не включается.

### Подготовка изменения и approval gate

У установленного `yc storage bucket update` есть только
`--lifecycle-rules`/`--lifecycle-rules-from-file`: официальная документация Yandex Object Storage
предупреждает, что переданная lifecycle-конфигурация заменяет текущую. Безопасной additive команды
для одной rule нет. Поэтому после read-only snapshot вручную объедините **все** существующие
rules из `/private/tmp/hires-staging-lifecycle-before-20260730.json` с ровно одной новой rule в
отдельный, reviewable файл
`/private/tmp/hires-staging-lifecycle-reviewed-20260730.json`. Не удаляйте, не меняйте и не
переставляйте существующие rules.

Единственный новый элемент в его `lifecycleRules` должен быть точным JSON object ниже. У
установленного `yc storage bucket update --generate-lifecycle-skeleton` поле `expiration.days`
имеет строковый scalar, поэтому `"7"`, а не числовой `7`, является частью контракта:

```json
{
  "id": "expire-preview-staging-after-7-days",
  "enabled": true,
  "filter": {"prefix": "processing-staging/previews/"},
  "expiration": {"days": "7"}
}
```

До mutation другой оператор должен сравнить `before` и `reviewed` и подтвердить, что меняется
только этот exact object. В `reviewed` обязана быть **ровно одна** rule с target ID и она должна
целиком равняться этому object: дубликат, иной `enabled`, prefix, days или любое дополнительное
action/field запрещают update. Затем остановитесь и получите **новое явное approval
непосредственно перед следующей командой**. Approval плана или этой инструкции не является
approval на update: команда заменяет lifecycle configuration bucket и её price delta неизвестна.

До запроса approval сделайте эту точную локальную проверку merge; обе команды должны завершиться
успешно. Она доказывает отсутствие target ID в saved current state, exact equality единственной
target rule в reviewed и equality всех остальных canonical lifecycle rules:

```bash
jq -e '[.lifecycleRules[]? | select(.id == "expire-preview-staging-after-7-days")] | length == 0' \
  /private/tmp/hires-staging-lifecycle-before-20260730.json
jq -S '{lifecycleRules: (.lifecycleRules // [])}' \
  /private/tmp/hires-staging-lifecycle-reviewed-20260730.json \
  > /private/tmp/hires-staging-lifecycle-reviewed-canonical-20260730.json
jq -e '
  [.lifecycleRules[]? | select(.id == "expire-preview-staging-after-7-days")]
  == [{"id":"expire-preview-staging-after-7-days","enabled":true,
       "filter":{"prefix":"processing-staging/previews/"},"expiration":{"days":"7"}}]
' /private/tmp/hires-staging-lifecycle-reviewed-canonical-20260730.json
jq -S '{lifecycleRules: ((.lifecycleRules // [])
  | map(select(.id != "expire-preview-staging-after-7-days")))}' \
  /private/tmp/hires-staging-lifecycle-reviewed-canonical-20260730.json \
  > /private/tmp/hires-staging-lifecycle-reviewed-without-target-20260730.json
cmp -s /private/tmp/hires-staging-lifecycle-before-20260730.json \
  /private/tmp/hires-staging-lifecycle-reviewed-without-target-20260730.json
```

После этого отдельного approval точная команда применения:

```bash
yc storage bucket update hires-staging \
  --lifecycle-rules-from-file /private/tmp/hires-staging-lifecycle-reviewed-canonical-20260730.json \
  --folder-id b1g2qttgfhb4gdunvlge \
  --format json
```

Ожидаемый availability impact: отсутствует; это не должно менять доступ к уже опубликованным
derivatives или оригиналам. При разрешённом `VERSIONING_DISABLED` data impact: удаляются только
staging objects под указанным prefix, которые старше 7 дней, в staging; восстановить уже
удалённый объект нельзя. Price delta неизвестна до актуальной оценки Yandex. При любой неясности,
ошибке update, отличии current state или versioning status не применяйте файл и вернитесь к
read-only discovery.

### Проверка и rollback

Сразу после успешного update прочитайте bucket заново и проверьте ровно одну целевую rule:

```bash
yc storage bucket get hires-staging --full --folder-id b1g2qttgfhb4gdunvlge --format json > /private/tmp/hires-staging-after-20260730.json
jq -e '.versioning == "VERSIONING_DISABLED"' /private/tmp/hires-staging-after-20260730.json
jq -S '{lifecycleRules: (.lifecycleRules // [])}' /private/tmp/hires-staging-after-20260730.json \
  > /private/tmp/hires-staging-lifecycle-after-canonical-20260730.json
jq -e '
  [.lifecycleRules[]? | select(.id == "expire-preview-staging-after-7-days")] as $rules
  | $rules == [{"id":"expire-preview-staging-after-7-days","enabled":true,
                "filter":{"prefix":"processing-staging/previews/"},"expiration":{"days":"7"}}]
' /private/tmp/hires-staging-lifecycle-after-canonical-20260730.json
jq -S '{lifecycleRules: ((.lifecycleRules // [])
  | map(select(.id != "expire-preview-staging-after-7-days")))}' \
  /private/tmp/hires-staging-lifecycle-after-canonical-20260730.json \
  > /private/tmp/hires-staging-lifecycle-after-without-target-20260730.json
cmp -s /private/tmp/hires-staging-lifecycle-before-20260730.json \
  /private/tmp/hires-staging-lifecycle-after-without-target-20260730.json
```

Перед объявлением gate passed сравните все non-target lifecycle rules с сохранённым `before`
snapshot. Если validation не проходит, не включайте preview processing. Rollback тоже заменяет
lifecycle configuration и требует нового явного approval непосредственно перед ним. После такого
approval восстановите сохранённый точный rule set, затем перечитайте и сравните его:

```bash
yc storage bucket update hires-staging \
  --lifecycle-rules-from-file /private/tmp/hires-staging-lifecycle-before-20260730.json \
  --folder-id b1g2qttgfhb4gdunvlge \
  --format json
yc storage bucket get hires-staging --full --folder-id b1g2qttgfhb4gdunvlge --format json > /private/tmp/hires-staging-rollback-check-20260730.json
jq -e '.versioning == "VERSIONING_DISABLED"' /private/tmp/hires-staging-rollback-check-20260730.json
jq -S '{lifecycleRules: (.lifecycleRules // [])}' /private/tmp/hires-staging-rollback-check-20260730.json \
  > /private/tmp/hires-staging-lifecycle-rollback-check-20260730.json
cmp -s /private/tmp/hires-staging-lifecycle-before-20260730.json \
  /private/tmp/hires-staging-lifecycle-rollback-check-20260730.json
```

Успешный rollback восстанавливает только configuration; он не возвращает staging objects, уже
удалённые семидневным правилом. При разрешённом `VERSIONING_DISABLED` data impact lifecycle rule
ограничен staging preview objects старше семи дней. При любом другом или unknown status activation
остаётся blocked до отдельного reviewed решения о noncurrent cleanup.

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
PHOTO_PROCESSING_PREVIEW_ENABLED=True
PHOTO_PROCESSING_FACE_ENABLED=True
PHOTO_PROCESSING_WORKER_TOKEN=<new-random-shared-token>
PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS=120
PHOTO_WORKER_BUILD=capture-metadata-v1
PHOTO_WORKER_LEASE_SECONDS=120
PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/1,2/generate_preview/1,2/face_embedding/2
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

Before starting the worker, inspect the precise preview state. Substitute the event slug only in the `CHECK_EVENT_SLUG` value:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); rows = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="generate_preview").order_by("photo_id").values("photo_id", "status", "current_run_id", "current_job_id", "current_attempt_id", "accepted_attempt_id", "queued_at", "processing_at", "succeeded_at", "failed_at", "next_attempt_at")); print(json.dumps(rows, default=str, indent=2))'
```

There should be two `queued` rows with the same `current_run_id`. Their `face_embedding` states must still be `not_requested`: do not infer that state from missing faces or objects. Start a **preview-only** worker for this finite first phase:

```bash
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/generate_preview/1 docker compose --profile worker up --build -d worker
docker compose logs -f worker
```

Expected worker log sequence is `claimed`, `started`, then `succeeded` for one preview at a time. It must contain opaque IDs, stable lifecycle names and durations only: a presigned URL, its query string, the worker token, and S3 credentials must not appear. Leave logs with `Ctrl+C` after both previews finish.

Stop the preview-only worker before inspecting the hand-off. This preserves the face jobs in their queued state for the next explicit phase:

```bash
docker compose --profile worker stop worker
```

Run the preview-state query again. Expected terminal results are:

- both `generate_preview` states and current jobs are `succeeded`, and each has the same non-null `accepted_attempt_id` as its published `preview-small-v1` derivative;
- no original key, staging key, signed URL, checksum, image bytes, or EXIF appears in a public gallery response;
- each `face_embedding` state is now `queued`, its job identity is `(contract_version=2, processor_version=2)`, and its persisted fingerprint says `media_kind: preview-small-v1`.

Prove the last transition from PostgreSQL rather than worker logs or an Object Storage listing:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoDerivative, PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); previews = list(PhotoDerivative.objects.filter(photo__event=event, variant="preview-small-v1").order_by("photo_id").values("photo_id", "accepted_attempt_id", "byte_size", "width", "height")); faces = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="face_embedding").order_by("photo_id").values("photo_id", "status", "current_job__contract_version", "current_job__processor_version", "current_job__input_fingerprint__media_kind")); print(json.dumps({"previews": previews, "faces": faces}, default=str, indent=2))'
```

The command must report one derivative per confirmed photo and `queued`, `2`, `2`, and `preview-small-v1` for every face row. It intentionally prints no storage key, grant, checksum, source metadata, or image bytes. Do not start an all-identities worker before this check: it can legitimately claim face work immediately and make the hand-off unobservable.

Only after recording the queued hand-off, run a separate **face-only** phase and then stop it too:

```bash
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/face_embedding/2 docker compose --profile worker up --build -d worker
docker compose logs -f worker
docker compose --profile worker stop worker
```

Confirm that the same face rows have terminal `succeeded` state after this second phase. A failure,
retry, or a claim with another processor identity is a failed manual check.

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); rows = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="face_embedding").order_by("photo_id").values("photo_id", "status", "accepted_attempt_id", "current_job__contract_version", "current_job__processor_version")); print(json.dumps(rows, default=str, indent=2))'
```

Read the event-scoped immutable evidence without changing it:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import EventProcessingRun; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); run = EventProcessingRun.objects.filter(event=event, processor_type="generate_preview").latest("created_at"); print(json.dumps({"run_id": str(run.id), "status": run.status, "configuration": run.configuration, "report": run.report}, default=str, indent=2))'
```

The run must be `closed`, with one immutable report for this exact event cohort. Its report has `cohort_size: 2`, a denominator of 2, two successes, retry/stale counts, accepted output byte/dimension/download/compute/upload-duration summaries, bounded warnings and stable failures. It must not contain an original or derivative key, staging identity, signed grant, image bytes, EXIF value, checksum value, or face result. Treat the persisted durations as measurements; do not compare them to a fixed wall-clock threshold. Worker concurrency is one, so the two attempts are deliberately serial.

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
