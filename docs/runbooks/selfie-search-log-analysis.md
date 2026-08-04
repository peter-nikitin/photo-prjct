# Runbook: анализ логов selfie-search

Практическая инструкция для оператора FindMe Photo, который разбирает жалобу на отсутствие
результатов поиска по селфи. Источник operational evidence — host `journald` с Compose-тегами и
root-owned daily summary. PostgreSQL остаётся источником истины для состояния продукта; лог-события
не являются backup, аналитикой посещаемости или доказательством личности.

## Правила безопасности

- Анализируйте закрытый календарный день Europe/Moscow. Текущий день ещё неполный: в incident
  report всегда ставьте operational `complete=false`, даже если parser payload пока вернул `true`.
- `journalctl` — источник истории после замены контейнеров. Не используйте `docker logs`.
- Не запускайте `docker compose logs`, не выгружайте сырые публичные access logs и не прикладывайте
  сырые строки журнала к тикету.
- Не печатайте и не копируйте `.env`, секреты, bearer-токены, signed URL, object key, selfie bytes,
  EXIF, filename, vector, photo/face IDs или raw exception. Не используйте `set -x` и не source-ьте
  `.env`.
- `selfie_observability_probe` — синтетическая проверка deployment, не запрос посетителя. Валидные
  probes исключаются из funnel.
- Для point-корреляции используйте только opaque `search_id`, а наружу выводите только bounded
  технические поля. Отклонённая submission до создания search индивидуального selector-а не имеет.

## 1. Подключение и транспорт

```bash
# Replace the placeholder with the approved staging SSH alias; do not record host/IP in the ticket.
ssh <configured-staging-ssh-alias>
cd /opt/photo-prjct
umask 077
ANALYSIS_DIR="$(mktemp -d /tmp/findme-selfie-analysis.XXXXXX)"
trap 'rm -rf "$ANALYSIS_DIR"' EXIT INT TERM
```

Проверьте host package, journal, retention и timer одной root-owned командой:

```bash
sudo -n /usr/local/sbin/findme-selfie-observability verify
```

Ожидаются `journal_disk_usage=...`, `oldest_selfie_event_realtime=...` (или `none`) и
`SELFIE_OBSERVABILITY_HOST_VERIFIED`. Helper проверяет timer как enabled и active, а summary service
является `Type=oneshot` и не обязан оставаться active между запусками. Контракт timer:
`OnCalendar=*-*-* 00:10:00 Europe/Moscow`, `RandomizedDelaySec=0`, `Persistent=true`. Контракт
journal: `Storage=persistent`, `MaxRetentionSec=14day`, `SystemMaxUse=1G`. Более глубокий root-level
осмотр в этот runbook не входит: при ошибке сохраняйте sanitized output verifier-а и эскалируйте
deployment/host finding.

Проверка Compose сверяет journald driver и стабильные tags, затем создаёт один synthetic probe и
читает его root helper-ом. Это не пользовательский поиск:

```bash
DEPLOY_ROOT=/opt/photo-prjct \
COMPOSE_PROJECT_NAME=photo-prjct-staging \
DEPLOYMENT_TARGET=staging \
  sh /opt/photo-prjct/deploy/verify-selfie-observability.sh
```

Для production используйте `photo-prjct-production` и `DEPLOYMENT_TARGET=production`. Не заменяйте
эту проверку `docker logs` или ручным просмотром контейнера.

## 2. Daily summary за закрытый Moscow-день

Явная дата запускает безопасную recomputation и не меняет приложение, базу или Object Storage:

```bash
REPORT_DATE=2026-08-03
sudo /usr/local/lib/findme-selfie-observability/run-daily-summary.sh "$REPORT_DATE" \
  > "$ANALYSIS_DIR/summary-$REPORT_DATE.json"
```

Выведите только bounded aggregate:

```bash
python3 - "$ANALYSIS_DIR/summary-$REPORT_DATE.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
allowed = (
    "report_date", "window_start", "window_end", "recomputed", "submissions", "terminals",
    "worker_attempts", "durations_ms", "cohort", "integrity",
)
safe = {key: payload[key] for key in allowed}
safe["parser_complete"] = payload["complete"]
print(json.dumps(safe, ensure_ascii=True, sort_keys=True))
PY
```

Интерпретация:

- `report_date` — Moscow calendar day; `window_start`/`window_end` — его границы с `+03:00`.
- `recomputed=true` ожидаемо для явной даты. Повторный запуск идемпотентен по метрикам.
- День без событий может дать полный нулевой отчёт; это не доказывает отсутствие запросов вне
  окна или после journal eviction.
- Ненулевое любое поле `integrity` означает parser `complete=false`: `accepted_without_terminal`,
  `terminal_without_accepted`, `duplicate_logical_events`, `malformed_events`,
  `unknown_schema_or_event`, `late_events`.
- В отчёте инцидента называйте это `parser_complete`. Отдельно выставляйте
  `coverage_complete=true` только для закрытого дня, когда host verifier прошёл и
  `oldest_selfie_event_realtime` покрывает начало окна; `none`, journal eviction или неизвестный
  coverage делают его `false`/`unknown`.
- Текущий день не используйте как закрытую выборку: `parser_complete` может быть true, но
  `coverage_complete=false` и incident report всегда `complete=false`.

Последние агрегаты timer можно посмотреть без raw web access lines:

```bash
sudo journalctl -u selfie-search-summary.service --since '14 days ago' -o cat \
  | grep '"event":"selfie_search_daily_summary"' \
  | tail -n 14 \
  | python3 -c 'import json,sys; [print(json.dumps({"report_date":p["report_date"],"recomputed":p["recomputed"],"parser_complete":p["complete"],"integrity":p["integrity"]}, sort_keys=True)) for p in map(json.loads, sys.stdin)]'
```

## 3. Точечная корреляция по `search_id`

Summary и runner используют границы Europe/Moscow, но передают `journalctl` однозначный UTC
RFC3339. Для даты `2026-08-03` это `2026-08-02T21:00:00Z` — `2026-08-03T21:00:00Z`; для другой
даты вычисляйте границы так же:

```bash
REPORT_DATE=2026-08-03
WINDOWS="$(python3 - "$REPORT_DATE" <<'PY'
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import sys

day = date.fromisoformat(sys.argv[1])
moscow = ZoneInfo("Europe/Moscow")
print(" ".join(
    datetime.combine(day + timedelta(days=offset), time.min, tzinfo=moscow)
    .astimezone(timezone.utc)
    .strftime("%Y-%m-%dT%H:%M:%SZ")
    for offset in (0, 1)
))
PY
)"
WINDOW_START="${WINDOWS% *}"
WINDOW_END="${WINDOWS#* }"
SEARCH_ID='00000000-0000-0000-0000-000000000001'
DEPLOYMENT_TARGET=staging
sudo journalctl --since "$WINDOW_START" --until "$WINDOW_END" --output=cat \
  "CONTAINER_TAG=findme.service=web findme.environment=$DEPLOYMENT_TARGET" + \
  "CONTAINER_TAG=findme.service=worker findme.environment=$DEPLOYMENT_TARGET" |
  python3 -c '
import json
import sys

search_id = sys.argv[1]
if not search_id:
    raise SystemExit("search_id is required")
prefixes = ("DEBUG ", "INFO ", "WARNING ", "ERROR ", "CRITICAL ")
fields = (
    "event", "occurred_at", "service", "outcome", "status", "reason_code", "retryable",
    "attempt_count", "matched_photo_count", "eligible_photo_count", "eligible_face_count",
    "duration_ms", "download_ms", "compute_ms", "total_ms", "load_ms", "rank_ms", "elapsed_ms",
    "failure_code", "cleanup_confirmed", "configuration_hash",
)
for raw in sys.stdin:
    line = raw.strip()
    for prefix in prefixes:
        if line.startswith(prefix):
            line = line[len(prefix) :]
            break
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        continue
    if not isinstance(value, dict) or not str(value.get("event", "")).startswith("selfie_"):
        continue
    if value.get("search_id") != search_id:
        continue
    safe = {key: value[key] for key in fields if key in value}
    print(json.dumps(safe, ensure_ascii=True, sort_keys=True))
' "$SEARCH_ID"
```

Raw journal stream существует только в pipe и никогда не перенаправляется в файл. Parser понимает
web JSON и текущий worker формат `INFO {JSON}`; произвольные prefixes не удаляет. Основной режим —
`SEARCH_ID` для созданного поиска. Отклонённые submissions без `search_id` диагностируются только
через bounded daily aggregate (`rejection_reasons`, format и size buckets); индивидуальная
point-корреляция для них в текущем privacy contract недоступна. Не делайте event-wide dump.

Набор наблюдаемых stages не задаёт полного chronological order: callback backend может записать
ranking/terminal до того, как worker attempt event станет виден, и worker event может появиться
после ranking/terminal. `occurred_at` — диагностический признак; не объявляйте отсутствие stage
только из-за порядка строк.

Для созданного поиска обычно встречаются: accepted submission; один или несколько worker attempts;
при успешном face embedding — ranking и затем terminal `ready`. `no_face`, `multiple_faces`,
`quality_rejected` и permanent worker failure могут terminalize search без ranking. Accepted search
может оставаться неполным, если callback/attempt не наблюдался.

## 4. Значение событий и decision tree

| Event | Основные поля | Граница |
| --- | --- | --- |
| `selfie_submission_finished` | `outcome`, `reason_code`, формат, size bucket, `duration_ms` | До создания search; `accepted` содержит opaque `search_id`. |
| `selfie_worker_attempt_finished` | `outcome`, bounded `reason_code`, `retryable`, download/compute/total ms | Один worker attempt; service `worker`. |
| `selfie_ranking_finished` | `eligible_photo_count`, `eligible_face_count`, matches, load/rank ms, `configuration_hash` | Cohort/index и ranking; service `web`. |
| `selfie_search_terminal` | `status`, matches, attempt count, elapsed, failure code, `cleanup_confirmed` | Финальное состояние после cleanup. |

Разбирайте «результатов нет» сверху вниз:

1. **Submission.** Сначала `submissions.outcomes` и `rejection_reasons`.
   `missing_or_empty`, `unsupported_format`, `corrupt_image`, `source_too_large`,
   `normalized_too_large`, `pixel_limit_exceeded` — входной файл или guidance. `storage_unavailable`
   — infrastructure boundary, а не качество лица. Эти события не создают `search_id`.
2. **Queue/worker.** При `accepted_without_terminal > 0` найдите `search_id`. Отсутствие attempt
   или повторяющиеся retryable failure (`network_interruption`, authorization expiry, storage,
   timeout) указывают на queue/lease/capacity/delivery путь. Сначала повторите transport verifier.
3. **Face/quality.** `status=no_face`, `multiple_faces`, `quality_rejected` — bounded объяснение
   входного селфи. Сопоставьте format/size bucket и worker reason; не меняйте модель или threshold
   по одной жалобе.
4. **Cohort/index.** Для `ready` с нулём matches:
   - `cohort.eligible_photo_min == 0` или `eligible_face_min == 0` — для части окна нет eligible
     index coverage; проверяйте processing/embedding read-only отдельно.
   - Положительный cohort при растущем `ready_zero` — ranking/coverage/threshold hypothesis.
     Сравните `configuration_hash`, eligible counts и `rank_ms`; logs не доказывают false negative
     и не разрешают tuning.
5. **Terminal failure.** `search_unavailable`/`failed` сопоставьте с worker reason, attempt count и
   cleanup. `terminal_without_accepted > 0` или `cleanup_confirmed != true` — integrity/contract
   finding, не пользовательский исход.
6. **Integrity first.** Любое ненулевое поле `integrity` делает funnel неполным. Сначала объясните
   наблюдаемость, потом сравнивайте периоды.

`ready_zero` и `ready_positive` нельзя смешивать с no-face, quality rejection или storage failure.
`ready` не является идентификацией человека; это только опубликованный результат поиска.

## 5. Cohort, index и latency

- `cohort.eligible_photo_min/max` и `eligible_face_min/max` — диапазон frozen cohort, не весь
  event inventory.
- `durations_ms.submission`, `worker_download`, `worker_compute`, `worker_total`, `cohort_load`,
  `ranking`, `search_lifetime` содержат `count`, `p50`, `p95`. `None` означает отсутствие sample,
  не нулевую задержку.
- `worker_attempts.total/succeeded/failed/retryable_failed` и `failure_reasons` отделяют transient
  retry от permanent input/model paths.
- Не задавайте SLA или threshold по одному отчёту: это indicators для сравнения, не benchmark
  biometric quality. Малый `count` отмечайте как низкую статистическую надёжность.

Для сравнения двух закрытых дней сначала получите два файла тем же root-owned runner, затем
выведите только bounded deltas:

```bash
python3 - "$ANALYSIS_DIR/summary-2026-08-02.json" "$ANALYSIS_DIR/summary-2026-08-03.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    before = json.load(source)
with open(sys.argv[2], encoding="utf-8") as source:
    after = json.load(source)

paths = {
    "accepted": ("submissions", "accepted"),
    "ready_zero": ("terminals", "ready_zero"),
    "ready_positive": ("terminals", "ready_positive"),
    "worker_failed": ("worker_attempts", "failed"),
    "worker_retryable_failed": ("worker_attempts", "retryable_failed"),
    "submission_p95_ms": ("durations_ms", "submission", "p95"),
    "worker_total_p95_ms": ("durations_ms", "worker_total", "p95"),
    "search_lifetime_p95_ms": ("durations_ms", "search_lifetime", "p95"),
}

def get(payload, path):
    for key in path:
        payload = payload[key]
    return payload

print(f"before={before['report_date']} parser_complete={before['complete']}")
print(f"after={after['report_date']} parser_complete={after['complete']}")
for label, path in paths.items():
    old, new = get(before, path), get(after, path)
    delta = None if old is None or new is None else new - old
    print(f"{label}: {old} -> {new} delta={delta}")
PY
```

Сравнивайте только `parser_complete=true`, `coverage_complete=true` и одинаковый environment. Если
любой день неполный, после eviction или с неизвестным oldest-event coverage, переносите сравнение в
hypothesis-only section и сначала разбирайте integrity/retention.

## 6. Incident report template

Используйте в закрытом incident ticket; raw journal stream не сохраняйте и не вставляйте.

```markdown
## Selfie-search incident — <short title>

- Environment: <staging|production>
- Moscow window: <YYYY-MM-DD 00:00> — <next date 00:00>
- Observed at (UTC): <time>
- Summary: report_date=<>, recomputed=<>, parser_complete=<>, coverage_complete=<>
- Symptom: <no results|upload rejection|processing stuck|other>

### Bounded evidence

- submissions: total=<>, accepted=<>, outcomes=<...>, rejection_reasons=<...>
- terminals: statuses=<...>, ready_zero=<>, ready_positive=<>
- worker attempts: total=<>, succeeded=<>, failed=<>, retryable_failed=<>, reasons=<...>
- cohort: eligible_photo_min/max=<>, eligible_face_min/max=<>
- latency p50/p95 ms: submission=<>, worker_total=<>, cohort_load=<>, ranking=<>, search_lifetime=<>
- integrity: accepted_without_terminal=<>, terminal_without_accepted=<>, duplicate_logical_events=<>, malformed_events=<>, unknown_schema_or_event=<>, late_events=<>

### Correlation and assessment

- Opaque search_id selector: <restricted ticket only; omit for rejected-before-search aggregate>
- Observed stages (no assumed total order): <submission|attempt|ranking|terminal>
- Boundary: <submission|queue/worker|face/quality|cohort/index|transport|integrity>
- Evidence and confidence: <...>
- Not established by logs: <...>

### Actions and privacy check

- Immediate action / owner / due time: <...>
- No raw access log, token, IP, URI, referrer, user-agent, image, vector, object key or `.env`: yes
- Probes excluded from user funnel: yes
```

После анализа удалите временный каталог явной командой; `trap` также удалит его при выходе:

```bash
rm -rf "$ANALYSIS_DIR"
```

## Ссылки на контракты

- [Selfie Search Observability Design](../superpowers/specs/2026-08-04-selfie-search-observability-design.md)
- [`run-daily-summary.sh`](../../deploy/selfie-observability/run-daily-summary.sh)
- [`summarize.py`](../../deploy/selfie-observability/summarize.py)
- [`root-helper.sh`](../../deploy/selfie-observability/root-helper.sh)
- [`docker-compose.prod.yml`](../../docker-compose.prod.yml) и [`docker-compose.https.yml`](../../docker-compose.https.yml)
- [`https.conf.template`](../../deploy/nginx/https.conf.template)
- [Краткий раздел эксплуатации в README](../../README.md#operate-selfie-search-observability)
