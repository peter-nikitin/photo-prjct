# Очередь поиска по лицу галереи

`gallery-search-worker` работает на одной VM из того же `APP_IMAGE`, что web.
Gallery-origin `SelfieSearch(queued)` являются очередью, включая ранее созданные записи.
Селфи остаётся в прежнем ML-пайплайне. Несколько исполнителей и rolling overlap не поддерживаются.

## Выпуск

`deploy/apply-deployment.sh` останавливает старый gallery-search-worker с `--timeout 0`,
затем web перед `migrate`. После миграции и static web проходит обычный `--wait`, а worker
запускается отдельно без ожидания специального queue health. SIGTERM не ждёт текущий поиск;
`stop_grace_period: 0s`. Публикация результата атомарна: ready-снимок сохраняется целиком,
незавершённая транзакция не оставляет частичный результат.

При каждом старте до первого claim все gallery-origin processing
возвращаются в queued, получают attempt count 0 и available-at текущего времени. Сроков
lease или старой retry-задержки ждать не нужно, исчерпанный старый счётчик не мешает повтору.
Queued, ready, failed/search_unavailable, selfie jobs и сохранённые результаты не сбрасываются.
При недоступной БД исполнитель повторяет startup recovery с backoff; claim до неё не выполняется.

Новому коду нужна новая схема 0008. Live DB этой задачей не изменялась; выпуск и нагрузочная
проверка на VM не выполнялись. При ошибке остановите приём и worker, сохраните БД и выполните
forward fix. Очередь и результаты не очищайте; старый синхронный process endpoint не возвращайте.

## Управление

Используйте canonical Compose (`docker-compose.deployment.yml`, `.env`, project `photo-prjct`):

```sh
docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml ps gallery-search-worker
docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml logs --tail 100 gallery-search-worker
docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml stop --timeout 0 gallery-search-worker
docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml start gallery-search-worker
```

Локально используйте `.venv/bin/python src/backend/manage.py run_gallery_search_worker` в настроенном Django
runtime. Команда не требует lock-path, runtime-volume или отдельного health check. Docker
контролирует exit/restart; второй исполнитель вручную не запускается параллельно с основным.

Watchdog завершает процесс кодом 124 через 150 секунд. Docker restart немедленно восстанавливает
processing при новом старте. Ранжирование ограничено 120 секундами в своей read-транзакции;
после неё лимит SQL снова 30 секунд. Rank statement timeout даёт terminal failed без повторов.
Lock wait ограничен 1 секундой, connect — 5 секундами;
ресурсы контейнера: 0.5 CPU, 256 MiB, 32 PID. Пустая очередь опрашивается с backoff 2–30 секунд.
Обычная временная ошибка живого процесса допускает до 3 попыток с задержкой 5 секунд, затем failed.
Необратимая непригодность источника даёт search_unavailable, ошибка ранжирования — failed.
Если retry после сбоя БД сохранить нельзя, worker завершается, а новый startup восстанавливает
processing. Готовое состояние после неоднозначного commit не сбрасывается и не пересчитывается.

Перед production-приёмкой проверьте обычные страницы и status во время реального поиска на VM.
Локальные transaction tests проверяют HTTP и короткие блокировки, но не ресурсы production.

## Метрики времени

На существующем dashboard очередь показывает только gallery queued, включая отложенные повторы.
Составной график — среднее успешное время за 5 минут: ожидание от создания до первого claim и
обработка от первого claim до ready, включая повторы и восстановление. Верхняя граница слоёв
равна полному времени до результата; это прошедшее время, не CPU time. Момент ready фиксируется
после записи результатов. Старые результаты без измеренного первого старта не учитываются и
не получают backfill. Без завершений в окне точек времени нет. До первого измеренного
завершения накопительные счётчики равны 0; затем они сохраняются между перезапусками.
При ошибке запроса метрик серии поиска отсутствуют, вместо ложного нуля.
