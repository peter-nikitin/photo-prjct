# Аудит алертов и каналов после восстановления

Дата: 10 ноября 2026. Аудит только на чтение. Облачные настройки не менялись,
тестовые уведомления не отправлялись. Статусы ниже — снимок на момент проверки.

## Область проверки

Профиль YC `default`, облако `b1gmcsmr51o5kvp86l55` (`findme-photo`).

| Каталог | ID | Остатки мониторинга |
| --- | --- | --- |
| core | b1g2qttgfhb4gdunvlge | 11 нативных алертов, 2 канала, 1 Prometheus workspace |
| findme-photo-workers | b1gvs3prcd72lnivvdea | Нативных алертов, каналов и workspace нет |

В Monium проект для core ещё отображается как `default`; область проверена по ID каталога.
В core новая VM `fv49nfcj1075ujmp7798` (`findme-photo-app`) — RUNNING, ru-central1-d.
Старая `findme-gallery-image-origin` (`epdf6696opq3ock91pih`) — STOPPED, ru-central1-b.

## Что действительно работает

- Workspace `mon0c97qv2s5uju1ark8` существует и получает метрики новой VM.
- В облаке остался файл `findme-photo.yml`: 33 alerting rules в четырёх группах.
  Чтение файла и снимков вычислений через документированный extensions API успешно.
  Последние вычисления без ошибок; возраст отдельных снимков при первичном чтении — примерно
  1–5 минут. Это свидетельство работы evaluator, а не работоспособности всех источников.
- Linux, Django и PostgreSQL публикуют свежие исходные выборки: возраст последних конечных
  значений в проверенных сериях — примерно 39–103 секунды.
- `findme_db_usable=1`, `pg_up{job="findme-postgres"}=1`. Дополнительно подтверждены свежие
  серии соединений, блокировок, длительности транзакций, XID age, freeze limit, WAL,
  commit/rollback. Основные входные метрики семи PostgreSQL-правил сохранились.
- Сохранились HTTP-счётчики и Prometheus-правило `Application5xxDegradation`, которое
  использует текущие имена метрик. Искусственный отказ и срабатывание не проверялись.
- Alertmanager в UI имеет состояние Active. Его текущая конфигурация прочитана в интерфейсе:
  `project="findme-photo"` + `notification="actionable"` направляются в `findme-operator`,
  который использует оба существующих канала. `send_resolved: true`, группировка по `incident`,
  group_wait 30s, group_interval 5m, repeat_interval 4h. Остальные правила идут в `unmatched`
  без каналов. Поэтому отсутствие уведомлений от диагностических правил соответствует настройке.

## 33 правила Prometheus

| Группа | Количество | Состояние покрытия |
| --- | ---: | --- |
| findme-postgres | 7 | Все основные входные метрики свежие; правила в UI OK. Срабатывание/доставка при отказе не проверены |
| findme-photo | 11 | HTTP-правило использует текущие метрики; публичный probe, VM и Commerce требуют исправления источников/селекторов |
| findme-workers | 6 | Старая схема instance groups; необходимых наблюдений очередей и инфраструктуры нет |
| findme-image-origin | 9 | Старая отдельная VM остановлена; прежнее покрытие не подтверждает работу image-origin на новой общей VM |

Во вкладке Prometheus → Алерты фактически видны диагностические срабатывания:

- WorkerQueueObservationMissing — 2 FIRING;
- WorkerCloudObservationMissing — 2 FIRING;
- WorkerNativePublisherMissing — 2 FIRING;
- ImageOriginTelemetryMissing — 1 FIRING;
- PublicObservationsMissing, VMTelemetryMissing, CommerceObservationsMissing — по 1 PENDING.

Эти правила не имеют `notification="actionable"`, поэтому в email/Telegram не направляются.
Они указывают на отсутствие прежних источников, а не доказывают отказ текущих обработчиков.
Состояния могут меняться после снимка аудита.

Нельзя смешивать `state=OK` в snapshots (успешное вычисление) со статусом отдельного
срабатывания. Запрос `ALERTS` через HTTP query в этом аудите вернул пустой набор, а Remote Read
вернул метаданные серий без конечных числовых точек; при этом UI показывал перечисленные выше
FIRING/PENDING. Для текущего списка срабатываний использована непосредственно вкладка Алерты.
Пустой ответ API не трактуется как доказательство отсутствия срабатываний.

## Где покрытие потеряно

1. **Публичная доступность и TLS.** Свежих `findme_probe_success` и
   `findme_probe_tls_days_remaining` с `check="canonical-health"` нет. Правило недоступности
   не может обнаружить реальный отказ сайта без нового независимого источника наблюдений.
2. **Диски, память, CPU и uptime VM.** Prometheus-правила используют
   `instance="dev-photo-prjct"`; текущие серии имеют `instance="findme-photo-app"`.
   Старый селектор не возвращает свежих точек. Алерт диска смотрит только `/`, а у новой VM
   PostgreSQL расположен на отдельном `/var/lib/findme-photo`.
3. **Commerce.** Нет свежих `commerce_worker_alive` и `commerce_oldest_ready_age_seconds`
   с `check="canonical-commerce"`. Эти алерты сейчас не доказывают работу Commerce.
4. **Обработка и поиск.** Нет свежих проверенных `worker_pool_queue_observation_timestamp_seconds`
   и `worker_pool_oldest_claimable_age_seconds`. Старая схема не покрывает текущие Compose
   обработчики и добавленный в #315 gallery-search-worker.
5. **Управление из Git.** Текущий workflow Monitoring применяет дашборд, а оставшийся в облаке
   `findme-photo.yml` и маршрутизация не управляются текущим кодом reconciler.

На момент замера свободно около 9,42 GiB на `/` и 4,65 GiB на `/var/lib/findme-photo`.
Это не авария по результатам аудита. Старый порог 5 GiB нельзя автоматически перенести
на data disk: сначала требуется выбрать порог под его фактический размер и рост данных.

## 11 нативных алертов

Все используют прежние native/custom источники; актуальные источники новой VM пишутся в
Prometheus. Статус OK ниже не является доказательством получения актуальных данных:
в UI таких алертов явно написано `No metrics found by selectors`, а no-data policy переводит
отсутствие данных в OK.

| Имя | ID | Состояние при проверке |
| --- | --- | --- |
| findme-photo-commerce-ready-work-overdue | mona9791ci9le4s6lmsi | OK без метрик |
| findme-photo-commerce-worker-unavailable | monj3d8mpbg5g3042fh2 | No data |
| findme-photo-deployment-application-5xx-degradation | monh5ptatl00ut817pr6 | OK без метрик |
| findme-photo-deployment-cpu-pressure | monmbj0um2km08b9ouki | OK без метрик |
| findme-photo-deployment-disk-free-bytes-critical | mon18prhdmiv677sce9m | No data |
| findme-photo-deployment-disk-space-critical | mon84ga0jeb2q1fqfph7 | No data |
| findme-photo-deployment-memory-pressure | mon37jg0fjicabt6nd8a | OK без метрик |
| findme-photo-deployment-public-service-unavailable | mong8d0bi05ohlo9hkq6 | No data |
| findme-photo-deployment-tls-certificate-expiring | mon1q74f33gtcfqj1adr | OK без метрик |
| findme-photo-deployment-vm-telemetry-missing | monf9ifhn3co4c7aipua | No data |
| findme-photo-notification-test | monu5gdllqhbc574uiib | No data; оставшийся тестовый ресурс |

У VM-правил встречается `host='dev-photo-prjct'`. Нативный HTTP-алерт читает
`app.findme_http_requests_total{service='custom'}`, тогда как текущий источник —
Prometheus `findme_http_requests_total`. Нативные правила не стоит просто переименовывать.

## Каналы и границы подтверждения доставки

| Канал | ID | Метод и получатель |
| --- | --- | --- |
| findme-photo-operator-email | fbefs2ubu6sq0k0jvlch | Cloud email, Пётр Никитин |
| findme-photo-operator-telegram | folder__b1g2qttgfhb4gdunvlge_findme-photo-operator-telegram | Cloud telegram, Пётр Никитин |

Email связан со старыми нативными алертами; у ряда правил отображается последний статус
отправки Success. Для некоторых нативных правил включены Alarm/Error/No data/Ok/Warning,
повторная отправка — «Никогда». Свежесть этих последних отправок аудит не установил.
Telegram показывает «Нет связанных алертов» в нативном списке связей, но **включён** в
фактическую конфигурацию получателя Alertmanager; эти два наблюдения не противоречат друг другу.

Лента уведомлений Monium ответила «Данные об эскалациях временно недоступны».
Наличие канала, Active у Alertmanager и прошлый Success не доказывают актуальную доставку
и прочтение уведомления получателем. Для обоих каналов остаётся отдельная контролируемая
проверка firing → получение → recovery. В этом аудите она намеренно не выполнялась.

## Что делать дальше

Сохранить workspace, оба канала и полезную политику actionable/diagnostic. Вернуть правила
и маршрутизацию под текущий Git workflow. Сначала восстановить независимую проверку HTTPS,
перенастроить правила VM на новый источник и оба диска; использовать сохранившиеся PostgreSQL
и HTTP-сигналы. Покрытие очередей добавить по текущей схеме обработчиков.
После доказанной доставки нового набора удалить 11 устаревших нативных алертов (включая
notification-test) и заменить правила старых instance groups/image-origin.

## Точки проверки

- [Нативные алерты core](https://monium.yandex.cloud/projects/folder__b1g2qttgfhb4gdunvlge/alerts)
- [Каналы core](https://monium.yandex.cloud/projects/folder__b1g2qttgfhb4gdunvlge/notification-methods)
- [Prometheus: состояния правил](https://monium.yandex.cloud/projects/folder__b1g2qttgfhb4gdunvlge/prometheus/mon0c97qv2s5uju1ark8/alerts)
- [Текущий Alertmanager](https://monium.yandex.cloud/projects/folder__b1g2qttgfhb4gdunvlge/prometheus/mon0c97qv2s5uju1ark8/alert-manager)
- [Документированный API файлов и snapshots](https://yandex.cloud/en/docs/monium/operations/prometheus/recording-rules)

Локальные снимки API и метрик сохранены в /private/tmp/findme-alerts-audit-*.json;
токены и полезные нагрузки секретов в отчёт и эти снимки не записывались.
