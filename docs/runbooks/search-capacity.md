# Профили загрузки и поиска

Оба профиля рассчитаны на одну ВМ `standard-v3` с 8 vCPU (100%) и 16 GiB RAM.
Источник параметров — [production Compose](../../docker-compose.deployment.yml).
Профиль поиска введён в [PR #367](https://github.com/peter-nikitin/photo-prjct/pull/367)
и проверен на проде после [PR #380](https://github.com/peter-nikitin/photo-prjct/pull/380). Профиль загрузки восстанавливает параметры,
снятые с продакшена до #367, поверх текущей версии приложения.

## Какой профиль выбрать

| Компонент | Загрузка и распознавание | Публичный поиск |
| --- | --- | --- |
| Django `web` | 2 процесса Gunicorn × 3 потока | 4 процесса Gunicorn × 2 потока |
| `gallery-search-worker` | `--concurrency 1`, 0,5 vCPU, 256 MiB | `--concurrency 2`, 1 vCPU, 512 MiB |
| `photo-worker` | Один экземпляр, один слот, семь типов задач, без квоты CPU | Один экземпляр, только `selfie_query`, два слота по одному вычислительному потоку, квота 2 vCPU |
| `import-worker`, `archive-worker` | По 0,5 vCPU и 256 MiB | По 0,5 vCPU и 256 MiB |
| `db` | PostgreSQL 16 / pgvector, `pg_stat_statements`, без контейнерных квот CPU и RAM | Те же параметры |

В обоих профилях SQL-воркер проверяет пустую очередь через 500 мс и получает 10 секунд
на остановку. Быстрый начальный опрос статуса скачивания (#376) и поиска (#380) используется в обоих профилях.

**Загрузка и распознавание.** Используйте до публикации, пока идёт импорт или подготовка снимков.
`PHOTO_WORKER_PROCESSOR_IDENTITIES` включает:

```text
1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,3/face_embedding/5,1/bib_recognition/1,2/person_appearance/1,1/selfie_query/2
```

`PHOTO_WORKER_PROCESSOR_TYPES` — `bib_recognition,face_embedding,capture_metadata,generate_preview,generate_watermarked_preview,person_appearance,selfie_query`.
Публичный поиск работает, но распознавание селфи делит воркер с обработкой фото, а SQL-воркер
выполняет по одной задаче. Допуск воркера к типу задачи и её постановка управляются отдельно.
Проверьте `PHOTO_PROCESSING_ENABLED=True` и `PHOTO_PROCESSING_FACE_ENABLED=True`. Для автоматической постановки внешности после
принятия чистого превью нужен `person-appearance-processing=on`. На дату сверки стоял `staff`,
при котором оператор может поставить конкретное фото вручную. Подробнее в
[runbook внешности](appearance-search.md). Для номеров проверяйте настройку конкретного мероприятия.

**Публичный поиск.** Переключайтесь перед анонсом после завершения распознавания снимков.
`PHOTO_WORKER_PROCESSOR_IDENTITIES=1/selfie_query/2`, `PHOTO_WORKER_PROCESSOR_TYPES=selfie_query`.
Импорт может принять новые оригиналы, а задачи подготовки и распознавания будут ждать
профиля загрузки. Перед переключением проверьте готовность лиц и внешности у публикуемых фото
и дождитесь завершения принятых задач обработки. Два слота селфи включаются автоматически
в выделенном воркере с квотой 2 vCPU ([PR #378](https://github.com/peter-nikitin/photo-prjct/pull/378)).

## Переключение и приёмка

1. Для перехода на загрузку используйте [PR #368](https://github.com/peter-nikitin/photo-prjct/pull/368).
   Перед слиянием проверьте актуальность относительно `main`, зелёный CI и отсутствие другого
   активного выпуска. Проверьте текущие флаги и зафиксируйте незавершённую работу в очередях.
2. Слейте PR в `main`. Дождитесь Infrastructure → Deploy и успешной проверки метрик по
   [регламенту выпуска](deployment.md). При переключении используются текущий код и данные.
3. На ВМ проверьте состояние контейнеров и фактические значения из таблицы. Из SSH-сессии
   оператора выполните команды ниже. Они выводят только разрешённые параметры.
4. Проверьте публичный `/health/`, открытие галереи, поиск из неё и поиск по селфи.
   В профиле загрузки обработайте один новый JPEG на служебном мероприятии штатным путём.
   Проверьте приём оригинала, метаданные, оба превью и принятые результаты распознавания лица.
   Проверьте номера и внешность, если для этого мероприятия и пути включена их постановка.
   После smoke убедитесь, что очередь движется, попытки завершаются и возраст новых задач не растёт.
5. Для возврата к поиску откройте PR, восстанавливающий только разницу Compose из таблицы.
   После слияния повторите проверки фактической конфигурации и публичный поиск из пункта 4.
   При деградации после переключения верните предыдущие параметры отдельным PR на актуальном коде.

```sh
cd /opt/photo-prjct
sudo docker ps --filter label=com.docker.compose.project=photo-prjct \
  --format '{{.Names}} {{.Status}}'
for service in web db photo-worker gallery-search-worker import-worker archive-worker; do
  ids=$(sudo docker ps -q --filter label=com.docker.compose.project=photo-prjct \
    --filter "label=com.docker.compose.service=$service")
  [ -n "$ids" ] || { echo "$service: контейнер не запущен"; exit 1; }
  for id in $ids; do
    sudo docker inspect --format '{{.Name}} nano_cpus={{.HostConfig.NanoCpus}} bytes={{.HostConfig.Memory}} stop_timeout={{.Config.StopTimeout}}' "$id"
    case "$service" in
      web|photo-worker)
        sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id" |
          grep -E '^(GUNICORN_WORKERS|GUNICORN_THREADS|PHOTO_WORKER_PROCESSOR_IDENTITIES|PHOTO_WORKER_PROCESSOR_TYPES|PHOTO_PROCESSING_ENABLED|PHOTO_PROCESSING_FACE_ENABLED)='
        ;;
      gallery-search-worker)
        sudo docker inspect --format '{{json .Config.Cmd}}' "$id"
        ;;
    esac
  done
done
curl -fsS --max-time 10 https://findme-photo.ru/health/
```

## Измерение поиска

Контрольное мероприятие — «Велоспорт - Чемпионат России среди любителей 2026 (Раздельный
старт)», `cycling-russian-amateur-championship-290826`. В исходном снимке было 12 390
фотографий с подготовленными лицами и внешностью. Для публичного сравнения нужны
`face-search-anchor-expansion=on`, `apparel-search-expansion=on`, `selfie-search-passes=on`.
Сами профили эти флаги не переключают.

По [инструкции k6](../../tests/load/README.md) запустите smoke, затем согласованный темп
60 или 300 визитов в минуту. Для изолированного замера поиска используйте `PREVIEW_COUNT=0`.
Фиксируйте SHA релиза, хеши сценария, cases и контрольного изображения, флаги и интервал UTC.
Снимайте ошибки и пропущенные старты k6, p95 ожидания SQL и селфи, полное время поиска,
CPU PostgreSQL и очередь после прогона. Прервите нагрузку при двух последовательных сбоях
`/health/` и дождитесь обработки уже принятых задач.

Для одежды сопоставьте `apparel_stage_finished` по `search_id`. Сравните `elapsed_ms`,
`reason`, `budget_remaining_ms` и `statement_timeout_ms` по этапам. Общий бюджет проверок
опор и расширения — 2000 мс. Фактический таймаут SQL также ограничен остатком дедлайна задачи.

В замере после #373 профиль поиска при 300 визитах/мин без превью дал 306 завершённых визитов
и 124 готовых поиска без ошибок и пропущенных стартов. p95 ожидания SQL составил 1,83 с
для галереи и 2,36 с для селфи. Замер относится к одному мероприятию и сценарию без одновременной
массовой обработки снимков. Профиль загрузки предназначен для восстановления полного конвейера,
а его пропускную способность при новом массовом импорте нужно измерять отдельно.
