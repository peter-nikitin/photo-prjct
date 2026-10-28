# Архитектура FindMe Photo

## Назначение документа

Это краткая схема текущей системы и её границ. **Реализовано** означает рабочий путь в коде,
**принято** — решение в [ADR](adr/README.md), **активировано** — подтверждённое состояние
конкретного развертывания. Статус функций и свидетельства работы находятся в
[продуктовом](product-jobs.md) и [инженерном](engineering-jobs.md) реестрах. Причины и условия
архитектурных решений раскрыты в ADR; процедуры — в [операционных документах](operations.md).

## Задача системы

FindMe Photo помогает участникам мероприятий находить снимки по номеру, лицу и данным события,
а фотографам и операторам — загружать и публиковать фотографии. Поиск ограничен мероприятием;
совпадение лица вероятностное, а ручная правка имеет приоритет. Доступ к закрытому оригиналу
зависит от правил галереи и подтверждённой покупки.

<a id="current-architecture--implemented"></a>
## Текущая схема кода

```text
GitHub Actions Infrastructure → Terraform (VPC, диски, одна VM)
                              → Ansible (пользователь, SSH, Docker, PG mount)
                              → GHCR images + runtime package без запуска приложения

После ручного восстановления БД:
Браузер → Nginx HTTP edge → Django/Gunicorn → PostgreSQL на той же VM
                            │
                            ├→ закрытые медиа в существующем Object Storage
                            ├→ приватный API → локальные photo/import worker
                            └→ Commerce worker и платёжный шлюз
```

Django — модульный монолит: он владеет HTTP-интерфейсом, полномочиями и правилами продукта.
PostgreSQL — источник истины для состояний и результатов. Object Storage хранит байты медиа.
Обработчики работают отдельно через закрытый API без прямого доступа к базе. Основу закрепляют
[ADR 0001](adr/0001-django-modular-monolith.md),
[0002](adr/0002-postgresql-system-of-record.md),
[0003](adr/0003-docker-compose-yandex-cloud.md) и
[0017](adr/0017-use-django-polled-photo-processing-jobs.md).

<a id="deployment-domain-assignment--accepted"></a>
## Развёртывание

В коде подготовлен один Docker Compose runtime на VM. `Infrastructure` после согласованного
PR plan применяет Terraform, повторяемо конфигурирует ОС через Ansible, публикует независимые
immutable web/photo/import images в GHCR и размещает runtime package. Он не запускает БД,
миграции или приложение до отдельного ручного восстановления. Старые автоматические пути
Deploy, image-origin, worker pools и monitoring для прежней топологии отключены.
Общий `deploy/apply-deployment.sh` выпускает выбранные образы только после появления схемы БД;
полное автоматическое приложение merge → release завершается на следующем этапе. Конкретный
порядок — в [bootstrap runbook](runbooks/single-vm-infrastructure-bootstrap.md) и
[runbook восстановления](runbooks/single-vm-recovery.md). Этот код ещё не применён к облаку:
live restore, запуск, публичный HTTP/HTTPS и стоимость не подтверждены.

<a id="accepted-recovery-target"></a>
## Принятые решения и следующие этапы

25 октября 2026 года владелец принял следующие решения для восстановления:

- [ADR 0056](adr/0056-minimum-viable-infrastructure.md): минимально жизнеспособная
  конфигурация без запаса под предполагаемую нагрузку; первоначально одна минимальная VM
  для приложения, БД, обработчиков и image origin, без второго прогретого экземпляра.
- [ADR 0057](adr/0057-declarative-cloud-infrastructure.md): IaC в Git — источник истины
  для желаемого состояния облачных ресурсов; Terraform и его выкатка через «merge → release».
  Перенос к другому поставщику требует адаптации provider-ресурсов и отдельного переноса данных.
- [ADR 0058](adr/0058-stable-public-entrypoint-nlb.md): постоянный публичный IP принадлежит
  NLB, backend размещается в `ru-central1-d`; TLS остаётся на Nginx.
- [ADR 0063](adr/0063-repeatable-host-configuration.md): конфигурация ОС воспроизводится
  из кода Ansible и выпускается через тот же пайплайн, включая изменения только ОС.

Код stage 1 реализует Terraform, Ansible и единый runtime без NLB. NLB, постоянный
публичный IP, HTTPS и полный app CI release относятся к следующим этапам; их наличие в ADR
не означает готовый код или live активацию. Постоянный мониторинг новой VM
отложен в [отдельную задачу](future-work/2026-10-25-restore-production-monitoring.md);
проверки готовности и приёмка после выпуска сохраняются. По уточнению владельца 26 октября восстановление использует
существующие media/feedback buckets с текущими именами; [их миграция](future-work/2026-10-26-canonical-media-bucket-migration.md)
отложена на следующий этап.

<a id="target-mvp-architecture--proposed"></a>
## Ответственность модулей

| Область | Ответственность |
| --- | --- |
| Каталог и загрузка | Мероприятия, публикация, права фотографа и приём снимков. |
| Медиа и обработка | Закрытые файлы, производные, задания и результаты worker. |
| Поиск | Номера, запросы по лицу и результаты в пределах мероприятия. |
| Commerce | Корзины, заказы, подтверждение оплаты и доступ к оригиналам. |
| Операции | Выпуск, секреты, наблюдение и восстановление. |

Это логические части монолита, а не отдельные сервисы.

<a id="photo-ingestion-and-indexing"></a>
## Загрузка и обработка

Фотограф загружает оригиналы в закрытое хранилище; Django принимает их и ставит долговременные
задания. Worker возвращает результат через закрытый API, а Django проверяет и публикует
производные. Решения: [ADR 0013](adr/0013-use-direct-private-object-storage-ingestion.md),
[0017](adr/0017-use-django-polled-photo-processing-jobs.md) и
[0035](adr/0035-use-django-polled-yandex-disk-import.md).

<a id="search"></a>
## Поиск

Номер и лицо ищутся только среди доступных фотографий одного мероприятия. Поиск по лицу
сравнивает совместимые векторы AdaFace в PostgreSQL; результат остаётся снимком вероятных
совпадений, а не подтверждением личности. Решения:
[ADR 0019](adr/0019-use-public-event-selfie-search.md),
[0040](adr/0040-use-pgvector-for-exact-face-search.md) и
[0054](adr/0054-retire-sface-and-fix-adaface-vector-dimension.md).

<a id="purchase-and-download"></a>
## Покупка и выдача

Корзина выбирает фотографии, заказ фиксирует состав и цену, а подтверждённая оплата создаёт
право на закрытый оригинал. Возврат браузера с платёжной формы оплату не подтверждает.
Решения: [ADR 0029](adr/0029-use-watermarked-previews-for-paid-photos.md),
[0031](adr/0031-use-orders-and-adapters-for-paid-original-delivery.md) и
[0047](adr/0047-separate-order-payment-from-new-cart-selection.md).

<a id="security-privacy-and-legal-boundaries"></a>
<a id="accepted-constraints"></a>
## Границы доступа и данных

Оригиналы остаются закрытыми; краткоживущая выдача следует после проверки полномочий. Секреты
хранятся в Yandex Lockbox, а не в Git. Наличие кода не означает публичную активацию функции:
для незавершённых путей используются runtime gates. Решения:
[ADR 0015](adr/0015-allow-anonymous-free-event-original-delivery.md),
[0020](adr/0020-use-signed-direct-object-storage-media-delivery.md) и
[0028](adr/0028-operate-one-canonical-deployment.md).
