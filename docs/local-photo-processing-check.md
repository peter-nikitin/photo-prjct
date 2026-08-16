# Локальная ручная проверка preview-first photo-processing worker

Эта инструкция проверяет сквозной путь с настоящими private Object Storage: браузер загружает JPEG через штатную страницу фотографа, Django подтверждает его и ставит `generate_preview`, отдельный worker получает временную GET-ссылку, создаёт нормализованный preview JPEG и загружает его только в попытку-scoped staging key. Django сам проверяет и публикует derivative, после чего ставит preview-backed `face_embedding` 2/3 с SCRFD. Она не включает worker на staging или production. Перед решением о реальной VM используйте отдельную [оценку конфигурации VM](photo-processing-vm-sizing.md): эта ручная проверка собирает для неё нужные измерения, но сама не разрешает resize или включение worker.

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
PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/2,2/generate_preview/1,2/face_embedding/3
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
- each `face_embedding` state is now `queued`, its job identity is `(contract_version=2, processor_version=3)`, and its persisted fingerprint says `media_kind: preview-small-v1`.

Prove the last transition from PostgreSQL rather than worker logs or an Object Storage listing:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoDerivative, PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); previews = list(PhotoDerivative.objects.filter(photo__event=event, variant="preview-small-v1").order_by("photo_id").values("photo_id", "accepted_attempt_id", "byte_size", "width", "height")); faces = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="face_embedding").order_by("photo_id").values("photo_id", "status", "current_job__contract_version", "current_job__processor_version", "current_job__input_fingerprint__media_kind")); print(json.dumps({"previews": previews, "faces": faces}, default=str, indent=2))'
```

The command must report one derivative per confirmed photo and `queued`, `2`, `3`, and `preview-small-v1` for every face row. It intentionally prints no storage key, grant, checksum, source metadata, or image bytes. Do not start an all-identities worker before this check: it can legitimately claim face work immediately and make the hand-off unobservable.

Only after recording the queued hand-off, run a separate **face-only** phase and then stop it too:

```bash
PHOTO_WORKER_PROCESSOR_IDENTITIES=2/face_embedding/3 docker compose --profile worker up --build -d worker
docker compose logs -f worker
docker compose --profile worker stop worker
```

Confirm that the same face rows have terminal `succeeded` state after this second phase. A failure,
retry, or a claim with another processor identity is a failed manual check.

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import PhotoProcessingState; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); rows = list(PhotoProcessingState.objects.filter(photo__event=event, processor_type="face_embedding").order_by("photo_id").values("photo_id", "status", "accepted_attempt_id", "current_job__contract_version", "current_job__processor_version")); print(json.dumps(rows, default=str, indent=2))'
```

## Небольшая проверка gallery/SCRFD и selfie v2

После успешного gallery-пути запустите worker с обычным набором product identities и типом
`selfie_query`, затем на публичной странице этого free event отправьте только своё тестовое селфи.
Не используйте customer media, не сохраняйте URL результата и не печатайте worker logs с query
parameters.

```bash
PHOTO_WORKER_PROCESSOR_IDENTITIES=1/capture_metadata/2,2/generate_preview/1,2/face_embedding/3 \
PHOTO_WORKER_PROCESSOR_TYPES=selfie_query,face_embedding,capture_metadata,generate_preview \
  docker compose --profile worker up --build -d worker
```

The gallery rows must retain contract `2` / face-embedding `3`; the resulting selfie job and claim
must identify contract `1` / `selfie_query` `2`. A successful foreground-face search or the stable
terminal `no_face_detected`/`multiple_faces_detected` outcome is acceptable. A model-load,
result-contract, or worker-claim error fails the local check. Stop the worker after recording this
small acceptance result; it is not a staging or production deployment authorization.

Read the event-scoped immutable evidence without changing it:

```bash
docker compose exec -e CHECK_EVENT_SLUG=manual-processing web python manage.py shell -c 'import json, os; from picflow.models import Event; from processing.models import EventProcessingRun; event = Event.objects.get(slug=os.environ["CHECK_EVENT_SLUG"]); run = EventProcessingRun.objects.filter(event=event, processor_type="generate_preview").latest("created_at"); print(json.dumps({"run_id": str(run.id), "status": run.status, "configuration": run.configuration, "report": run.report}, default=str, indent=2))'
```

The run must be `closed`, with one immutable report for this exact event cohort. Its report has `cohort_size: 2`, a denominator of 2, two successes, retry/stale counts, accepted output byte/dimension/download/compute/upload-duration summaries, bounded warnings and stable failures. It must not contain an original or derivative key, staging identity, signed grant, image bytes, EXIF value, checksum value, or face result. Treat the persisted durations as measurements; do not compare them to a fixed wall-clock threshold. Worker concurrency is one, so the two attempts are deliberately serial.

## Локальный benchmark пропускной способности face embedding

Этот benchmark создаёт отдельный конечный cohort и не меняет обычные `face_embedding` jobs,
состояния или векторы. Зафиксированный experiment — ровно 114 photos: baseline с одной replica,
затем replay того же закрытого cohort с двумя replicas. Не печатайте логи worker для этой
проверки: они не нужны для метрик и могут содержать operational identifiers.

```bash
# Baseline: ровно 114 photos, одна benchmark replica.
docker compose exec web python manage.py run_face_embedding_benchmark \
  --event <event-slug> --limit 114 --label baseline-one-replica
PHOTO_WORKER_PROCESSOR_IDENTITIES=3/face_embedding_benchmark/1 \
  docker compose --profile worker up --build -d --scale worker=1 worker

# Дождитесь закрытия baseline run, остановите одну replica и сохраните его UUID локально.
docker compose --profile worker stop worker

# Replay: повторяет те же 114 photos с двумя replicas.
docker compose exec web python manage.py run_face_embedding_benchmark \
  --source-run <closed-baseline-run-uuid> --label replay-two-replicas
PHOTO_WORKER_PROCESSOR_IDENTITIES=3/face_embedding_benchmark/1 \
  docker compose --profile worker up --build -d --scale worker=2 worker
```

### Staging: ручной запуск через GitHub Actions

Перед запуском сохраните текущие значения staging variables `PHOTO_WORKER_PROCESSOR_IDENTITIES`,
`PHOTO_WORKER_REPLICAS` и `PHOTO_PROCESSING_PREVIEW_ENABLED`. Для baseline установите
`3/face_embedding_benchmark/1`, `1` и `False` соответственно, выполните normal staging deploy,
затем вручную запустите workflow **Staging face-embedding benchmark** с `operation=baseline` и
slug event. Он создаёт ровно 114 benchmark jobs и печатает `BENCHMARK_RUN_ID`; дождитесь закрытия
этого run. Для replay установите replicas `2`, снова выполните normal staging deploy, затем
запустите тот же workflow с `operation=replay` и baseline UUID. `operation=report` печатает только
агрегированные closed-run метрики для указанного UUID; UUID, event/configuration, source links,
job/attempt/photo IDs и storage details в вывод не попадают.

Workflow принимает только slug/UUID, проверяет single benchmark identity, ожидаемую replica count
и `PHOTO_PROCESSING_PREVIEW_ENABLED=False` на VM, а в контейнере запускает только Django management
commands. После измерений восстановите сохранённые `PHOTO_WORKER_PROCESSOR_IDENTITIES`,
`PHOTO_WORKER_REPLICAS` и `PHOTO_PROCESSING_PREVIEW_ENABLED`, затем вручную запустите normal staging
deploy до возвращения обычной обработки фото.

После закрытия выбранного run получите только aggregate-метрики через Django. Команда ниже
является read-only (`SELECT`), не выводит ID, object keys, tokens, URLs, embeddings или vectors;
она включает sample/retry/expired/stale/lease/error-code counts, input-size buckets и все
сохранённые timing percentiles. Benchmark contract намеренно не сохраняет исходные image
dimensions, поэтому `representative_dimension_distribution` честно возвращает
`not_collected_by_benchmark_contract`: не подменяйте его object key, EXIF или результатом лица.
Замените placeholder UUID только в переменной окружения.

```bash
BENCHMARK_RUN_ID=<closed-run-uuid> docker compose exec -e BENCHMARK_RUN_ID web \
  python manage.py shell -c '
import os
from django.db import connection
sql = """
WITH selected AS (
  SELECT id, created_at, closed_at
  FROM processing_eventprocessingrun
  WHERE id = %s AND status = '\''closed'\''
    AND contract_version = 3 AND processor_type = '\''face_embedding_benchmark'\''
    AND processor_version = 1
), jobs AS (
  SELECT j.id, j.status, j.created_at, j.input_fingerprint
  FROM processing_processingjob j JOIN selected r ON r.id = j.run_id
), terminal_jobs AS (
  SELECT status FROM jobs WHERE status IN ('\''succeeded'\'', '\''failed'\'', '\''cancelled'\'')
), attempts AS (
  SELECT a.job_id, a.status, a.error_code, a.created_at, a.claimed_at,
         a.lease_expires_at, a.terminal_at, a.download_duration_ms,
         a.compute_duration_ms, a.total_duration_ms, a.result
  FROM processing_processingattempt a JOIN selected r ON r.id = a.run_id
), measurements AS (
  SELECT download_duration_ms, compute_duration_ms, total_duration_ms,
         NULLIF(result #>> '\''{timings,model_load_ms}'\'', '\'''\'')::numeric AS model_load_ms,
         NULLIF(result #>> '\''{timings,decode_ms}'\'', '\'''\'')::numeric AS decode_ms,
         NULLIF(result #>> '\''{timings,detect_ms}'\'', '\'''\'')::numeric AS detect_ms,
         NULLIF(result #>> '\''{timings,embed_ms}'\'', '\'''\'')::numeric AS embed_ms
  FROM attempts WHERE terminal_at IS NOT NULL
), first_claims AS (
  SELECT j.id AS job_id,
         EXTRACT(EPOCH FROM (MIN(a.claimed_at) - j.created_at)) * 1000 AS creation_to_claim_ms
  FROM jobs j LEFT JOIN attempts a ON a.job_id = j.id
  GROUP BY j.id, j.created_at
), size_distribution AS (
  SELECT CASE
      WHEN (input_fingerprint->>'\''original_size'\'')::bigint < 1000000 THEN '\''<1MB'\''
      WHEN (input_fingerprint->>'\''original_size'\'')::bigint < 5000000 THEN '\''1-5MB'\''
      WHEN (input_fingerprint->>'\''original_size'\'')::bigint < 10000000 THEN '\''5-10MB'\''
      ELSE '\''>=10MB'\'' END AS bucket,
    COUNT(*) AS count
  FROM jobs GROUP BY bucket
), retry_counts AS (
  SELECT job_id, COUNT(*) AS attempt_count FROM attempts GROUP BY job_id
)
SELECT
  (SELECT COUNT(*) FROM processing_processingjob j JOIN selected r ON r.id = j.run_id) AS cohort_size,
  (SELECT COALESCE(jsonb_object_agg(status, count), '\''{}'\''::jsonb)
     FROM (SELECT status, COUNT(*) FROM terminal_jobs GROUP BY status) outcomes) AS terminal_outcomes,
  (SELECT jsonb_build_object(
     '\''jobs'\'', (SELECT COUNT(*) FROM jobs),
     '\''terminal_attempts'\'', (SELECT COUNT(*) FROM attempts WHERE terminal_at IS NOT NULL),
     '\''creation_to_claim_ms'\'', (SELECT COUNT(creation_to_claim_ms) FROM first_claims),
     '\''download_ms'\'', (SELECT COUNT(download_duration_ms) FROM measurements),
     '\''compute_ms'\'', (SELECT COUNT(compute_duration_ms) FROM measurements),
     '\''total_ms'\'', (SELECT COUNT(total_duration_ms) FROM measurements),
     '\''model_load_ms'\'', (SELECT COUNT(model_load_ms) FROM measurements),
     '\''decode_ms'\'', (SELECT COUNT(decode_ms) FROM measurements),
     '\''detect_ms'\'', (SELECT COUNT(detect_ms) FROM measurements),
     '\''embed_ms'\'', (SELECT COUNT(embed_ms) FROM measurements)
   )) AS sample_counts,
  (SELECT COUNT(*) FROM retry_counts WHERE attempt_count > 1) AS retried_job_count,
  (SELECT COUNT(*) FROM attempts WHERE status = '\''expired'\'') AS expired_attempt_count,
  (SELECT COUNT(*) FROM attempts WHERE status = '\''stale'\'') AS stale_attempt_count,
  (SELECT COUNT(*) FROM attempts WHERE error_code = '\''lease_not_current'\'') AS lease_loss_count,
  (SELECT COALESCE(jsonb_object_agg(error_code, count), '\''{}'\''::jsonb)
     FROM (SELECT error_code, COUNT(*) FROM attempts
           WHERE terminal_at IS NOT NULL AND error_code <> '\'''\'' GROUP BY error_code) errors) AS terminal_error_code_counts,
  (SELECT COALESCE(jsonb_object_agg(bucket, count), '\''{}'\''::jsonb) FROM size_distribution) AS representative_input_size_distribution,
  '\''not_collected_by_benchmark_contract'\'' AS representative_dimension_distribution,
  EXTRACT(EPOCH FROM (r.closed_at - r.created_at)) * 1000 AS wall_clock_ms,
  (SELECT COUNT(*) FROM terminal_jobs) / NULLIF(EXTRACT(EPOCH FROM (r.closed_at - r.created_at)) / 60, 0) AS photos_per_minute,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY creation_to_claim_ms) FROM first_claims) AS creation_to_claim_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY creation_to_claim_ms) FROM first_claims) AS creation_to_claim_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY download_duration_ms) FROM measurements) AS download_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY download_duration_ms) FROM measurements) AS download_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY compute_duration_ms) FROM measurements) AS compute_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY compute_duration_ms) FROM measurements) AS compute_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_duration_ms) FROM measurements) AS total_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY total_duration_ms) FROM measurements) AS total_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY model_load_ms) FROM measurements) AS model_load_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY model_load_ms) FROM measurements) AS model_load_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY decode_ms) FROM measurements) AS decode_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY decode_ms) FROM measurements) AS decode_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY detect_ms) FROM measurements) AS detect_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY detect_ms) FROM measurements) AS detect_p95_ms,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY embed_ms) FROM measurements) AS embed_p50_ms,
  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY embed_ms) FROM measurements) AS embed_p95_ms
FROM selected r
"""
with connection.cursor() as cursor:
    cursor.execute(sql, [os.environ["BENCHMARK_RUN_ID"]])
    print(dict(zip([column[0] for column in cursor.description], cursor.fetchone())))
'
```

Снимите host/container evidence рядом с каждым baseline/replay, не добавляя в отчёт логи,
идентификаторы или storage details. Подставьте имя worker container из первого вызова во второй:

```bash
docker compose --profile worker ps
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'
docker inspect --format 'restarts={{.RestartCount}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' <worker-container>
grep -E 'MemTotal|MemAvailable' /proc/meminfo
df -h
iostat -xz 1 3
```

`docker stats` records CPU and RSS; `docker inspect` records restarts/OOM; `/proc/meminfo`,
`df`, and `iostat` record available memory, disk space, and iowait. If `iostat` is unavailable,
record that fact rather than installing tools during a benchmark.

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
