# Проверка конфигурации поиска

## Исходный режим загрузки

Снимок получен 2026-11-21 через API Yandex Cloud и `docker inspect` работающих контейнеров.
Код: `d50857910fe4d6afbdede0afb7b48afc76686491`. ВМ `fv49nfcj1075ujmp7798`, каталог
`b1g2qttgfhb4gdunvlge`: `standard-v3`, 8 vCPU, 100%, 16 GiB RAM. Размер ВМ при сравнении не менять.

| Компонент | Исходная конфигурация загрузки |
| --- | --- |
| `web` | `GUNICORN_WORKERS=2`, `GUNICORN_THREADS=3`, CPU/RAM без контейнерных лимитов |
| `gallery-search-worker` | Один процесс, CPU 0.5, RAM 256 MiB, `stop_grace_period: 0s` |
| `photo-worker` | Один процесс; все идентичности ниже; CPU/RAM без контейнерных лимитов |
| `import-worker` | CPU 0.5, RAM 256 MiB |
| `archive-worker` | CPU 0.5, RAM 256 MiB |
| `db` | pgvector 0.8.6 / PostgreSQL 16, `shared_preload_libraries=pg_stat_statements`, CPU/RAM без контейнерных лимитов |

Исходные идентичности photo-worker:

```text
1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,3/face_embedding/5,1/bib_recognition/1,2/person_appearance/1,1/selfie_query/2
```

Исходные `PHOTO_WORKER_PROCESSOR_TYPES`:

```text
bib_recognition,face_embedding,capture_metadata,generate_preview,generate_watermarked_preview,person_appearance,selfie_query
```

`PHOTO_WORKER_REPLICAS=1`. При отсутствии CPU quota native inference использует один поток.
Исходный SQL исполнитель после простоя увеличивал паузу 2→4→8→16→30 секунд;
пустая очередь селфи возвращала задержку 5 секунд. Эти задержки при возврате к загрузке не восстанавливать.

Ссылки на исходные образы (историческое доказательство; возвращаем параметры поверх актуального кода):

- web и SQL: `ghcr.io/peter-nikitin/photo-prjct@sha256:e16c61e4438ef09855e9b301ddbfc0c0cbfe84d42fc392afc5b4d42e0b525c2a`;
- photo-worker: `ghcr.io/peter-nikitin/photo-prjct-worker@sha256:8cd4907d8c4014f00e02175fd66284bbd05620c52fffe7f3aee40d3ca554d10d`.

## Набор данных и флаги

Мероприятие №13: «Велоспорт - Чемпионат России среди любителей 2026 (Раздельный старт)»,
`cycling-russian-amateur-championship-290826`. В снимке 12 390 фото; metadata, preview,
face и appearance завершены для всех 12 390, 19 799 обнаружений людей. SQL очередь перед подготовкой пуста.

До подготовки: `face-search-anchor-expansion=on`, `apparel-search-expansion=staff`,
`selfie-search-passes=staff`, `person-appearance-processing=staff`.
Для обоих сравниваемых публичных прогонов через Django Admin включены
`face-search-anchor-expansion=on`, `apparel-search-expansion=on`, `selfie-search-passes=on`.
Остальные флаги не менять. Состояние подготовки appearance не заменяет фактическое участие
алгоритма: после smoke проверить новые проходы и счётчики расширений в БД/телеметрии.

## Целевая конфигурация и возврат

Кандидат для поиска: web 4×2, SQL два процесса в одном контейнере (CPU1/RAM512MiB),
photo-worker только `1/selfie_query/2`, CPU2. PostgreSQL, размеры ВМ и алгоритмы сохраняются.
Оптимальность кандидата подтверждается только сравнением; увеличение параллельности может
повысить конкуренцию запросов к БД.

Возврат отдельным PR: вернуть web2×3, SQL concurrency1/CPU0.5/RAM256MiB,
все исходные идентичности photo-worker и убрать его CPU quota. Сохранить быстрый polling,
безопасную остановку SQL исполнителя и нагрузочные сценарии. Не откатывать приложение на старый
образ и не удалять данные поиска. Проверить effective config через `docker inspect`,
здоровье и обработку одной новой фотографии штатной очередью.

## Порядок сравнения

1. Открыть PR с кодом сценария и кандидата. До merge проверить флаги и smoke на исходной конфигурации.
2. Запустить 60, затем 300 визитов/мин при здоровом сайте и опустевшей очереди. Зафиксировать
   SHA кода, SHA256 сценария, cases и селфи, версии k6, флаги, effective config и интервалы UTC.
3. Прервать прогон при двух последовательных сбоях `/health/`; дождаться завершения принятых
   поисков. Падение health не считать успешной нагрузочной проверкой.
4. Сохранить k6 summary/JSON, CPU/RAM/throttling/диск ВМ, длительности и очередь каждого этапа,
   ошибки и число принятых/завершённых дополнительных проходов. Личные URL результатов,
   подписанные media URL и снимки хранить только локально в игнорируемых артефактах.
5. После зелёного CI слить основной PR, дождаться Deploy и проверить конфигурацию. Повторить
   тот же smoke и нагрузку с неизменными case/scenario hashes, флагами, долями и интервалами.
6. Открыть PR возврата параметров загрузки; оставить его неслитым до завершения сравнения.

Успех: нет ошибок/пропущенных стартов, очередь после нагрузки уходит; p95 ожидания SQL/селфи
целимся ≤1s, HTML≤2s. Полное время каждого действия сравнивать отдельно. Холодный и прогретый
поиск отмечать раздельно. Если однопоточное вычисление само занимает секунды, параллельность
не является гарантией выполнения этих целей при любом потоке.
