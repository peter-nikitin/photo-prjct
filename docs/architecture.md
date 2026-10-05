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
## Текущая схема

```text
Браузер → Nginx/HTTPS → Django/Gunicorn → PostgreSQL
                           │
                           ├→ закрытые оригиналы и производные в Object Storage
                           ├→ приватный API обработки → пулы photo-worker
                           └→ платёжный шлюз и доставка писем через Commerce worker

GitHub Actions → GHCR → каноническая VM и пулы worker
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

Публичный адрес — `https://findme-photo.ru/`. Один канонический сервис на VM выпускается через
Docker Compose и workflow **Deploy**; пулы обработчиков отделены от него, но используют его
очередь и полномочия. Образы web и photo-worker могут выпускаться независимо. Актуальный
порядок и состояние приведены в [runbook развёртывания](runbooks/deployment.md) и
[runbook пулов](runbooks/worker-pools.md). Границы закрепляют
[ADR 0028](adr/0028-operate-one-canonical-deployment.md),
[0042](adr/0042-isolate-autoscaled-photo-worker-pools.md) и
[0051](adr/0051-release-photo-worker-images-independently.md).

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
