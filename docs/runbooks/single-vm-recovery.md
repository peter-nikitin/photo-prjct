# Восстановление одной VM: подготовленный порядок

Этот runbook описывает подготовленный код этапа 1. Фактическое извлечение Cloud Backup,
восстановление БД, запуск сайта и проверка воркеров относятся к этапу 2; результаты этих
операций здесь пока не заявлены.

Создание backend, согласование plan, merge-release инфраструктуры, проверка host key и
временный Cloud Backup agent описаны в [bootstrap инфраструктуры](single-vm-infrastructure-bootstrap.md).

## Граница bootstrap

Terraform и Ansible подготавливают VM, диск, Docker, `/opt/photo-prjct` и временный доступ к
Cloud Backup. PostgreSQL, Django, edge, imgproxy и обработчики до извлечения файлов БД не
запускаются. Перед извлечением подтвердите, что `db` остановлен. Источник — точка
`a8a9af43-acff-4db2-9e2e-35ccb4be394a` архива
`c45fbb19-3a7a-5452-0e6d-257c29f0952e` исходной VM
`epdr5g3p24tdns9890nr` в каталоге `b1g2qttgfhb4gdunvlge`. Дата точки не доказывает
состояние схемы. Сентябрьский dump не используется.

Сначала ищите готовый логический dump. Если его нет, извлеките полный каталог PostgreSQL с
нужными WAL и tablespaces, проверьте согласованность и совместимость версии на отдельной копии,
затем создайте логический dump. ОС и старый Compose не восстанавливаются. Объём Cloud Backup
и свободное место для распаковки, временной репетиции и целевой БД измеряются до операции;
недостаток места требует отдельного ресурсного решения.

`/opt/photo-prjct/.env` — обычный root-private файл режима `0600`. Для запуска БД в нём
достаточно `DB_NAME`, `DB_USER`, `DB_PASSWORD`; Compose допускает инертные значения остальных
образов/секретов. До запуска Django заполните действующие значения `SECRET_KEY`, S3/Commerce
настроек и **независимые неизменяемые** `APP_IMAGE`, `PHOTO_WORKER_IMAGE`,
`IMPORT_WORKER_IMAGE`. `ALLOWED_HOSTS` перечисляет фактический IP VM, `web` и `localhost`
явно, без `*`. `PUBLIC_DOMAIN` остаётся штатным доменом для последующего HTTPS.
`GUNICORN_WORKERS=1`. `PHOTO_PROCESSING_WORKER_TOKEN` — отдельный служебный токен;
`PHOTO_PROCESSING_FLEET_TOKEN` не используется. `PHOTO_WORKER_BUILD` — SHA образа worker.

Перед первым запуском web задайте `DEBUG=False`, `MEDIA_STORAGE_BACKEND=s3`,
`PHOTO_PROCESSING_ENABLED=True`, `PHOTO_PROCESSING_FACE_ENABLED=True`, Gunicorn
параметры и действующие S3 buckets/credentials для публичных и приватных медиа. `web`
проверяет обязательные значения до `collectstatic` и Gunicorn; `imgproxy` отдельно проверяет
свои подписи, bucket и S3 credentials. Пустые интерполяции в Compose нужны только для фазы
`db` и не допускают запуск приложения с пустыми секретами. Собранные файлы `staticfiles`
пишутся web в общий Docker volume, а edge читает его только на чтение; при первом старте
web создаёт manifest до открытия `/`.

Для временного HTTP по IP задайте `GALLERY_CDN_ORIGIN=http://<IP_VM>` и действующие
`GALLERY_CDN_TOKEN_SECRET`, `GALLERY_IMGPROXY_KEY`, `GALLERY_IMGPROXY_SALT`,
`IMAGE_ORIGIN_S3_ACCESS_KEY_ID`, `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY`,
`PRIVATE_MEDIA_S3_BUCKET`, `MEDIA_S3_PUBLIC_BUCKET`. HTTP разрешён в signer только для
IPv4 literal. Django создаёт URL с существующими CDN token/expiry и imgproxy подписью;
edge запрашивает проверку token у внутреннего Django endpoint и передаёт подписанный путь
локальному imgproxy. Он читает прежние S3 buckets с прежними правами. Внешний `/internal/`
edge закрыт; `web`, PostgreSQL и imgproxy не публикуют порт на VM. Временный прямой HTTP
допускает только чтение GET/HEAD публичного каталога, галерей и signed images: `/admin/`,
`/photographer/` и небезопасные бизнес-запросы закрыты. Session/CSRF cookies остаются Secure;
авторизация, загрузка, корзина и другие изменения через браузер ждут HTTPS этапа 3.
Healthcheck edge обращается к `127.0.0.1:8080` с `Host: localhost`, который явно разрешён
в `ALLOWED_HOSTS`; публичный порт VM остаётся `80`.

## Восстановление

После извлечения и проверки источника на новой VM войдите в root shell (`sudo -i`) и
выполните только БД. Каталог `/opt/photo-prjct` имеет права `root:root 0700`, а `.env` — `0600`;
команды ниже предполагают root shell:

```sh
sudo -i
cd /opt/photo-prjct
docker compose --env-file .env -f docker-compose.deployment.yml up -d --wait db
DEPLOY_ROOT=/opt/photo-prjct scripts/rehearse-recovery.sh --dump /absolute/path/current.dump
DEPLOY_ROOT=/opt/photo-prjct scripts/restore-recovery-database.sh --dump /absolute/path/current.dump
```

`rehearse-recovery.sh` создаёт временную БД в том же PostgreSQL, выполняет полный
`pg_restore`, миграции и проверки, затем удаляет только временную БД. Для неё требуется
дополнительное место. Скрипты печатают SHA-256 dump; сохраните его вместе с инвентаризацией
`django_migrations`, количества событий/фотографий/заказов/прав, физической схемы AdaFace
512D и объёма. `restore-recovery-database.sh` принимает только явный `--dump`, восстанавливает
в свежую БД под владельцем `app` без `DROP` и `--clean`, затем запускает актуальные миграции,
`verify_pgvector_face_embeddings` и `verify_gallery_media_projection --all-events --require-clean`.
При ошибке остановитесь и диагностируйте её; обычный Deploy не запускает restore.

После успешных проверок БД и конфигурации запустите HTTP:

```sh
docker compose --env-file .env -f docker-compose.deployment.yml up -d --wait web imgproxy edge
curl --fail http://<IP_VM>/health/
curl --fail http://<IP_VM>/ready/
```

Проверьте каталог, доступную галерею, обложку и превью; отдельно убедитесь, что публичный
`/internal/` закрыт и PostgreSQL не имеет внешнего listener. `/health/` проверяет процесс,
`/ready/` выполняет `SELECT 1` к БД. Наличие healthy контейнеров не доказывает цикл обработки.

## Ограниченная проверка обработчиков

Постоянные pollers до инвентаризации восстановленной очереди выключены. Значения
`PHOTO_WORKER_ENABLED=True`, `PHOTO_IMPORT_WORKER_ENABLED=True` и
`COMMERCE_WORKER_ENABLED=True` включают соответствующие процессы только отдельным осознанным
шагом. Для photo и import одновременно требуются `PHOTO_PROCESSING_ENABLED=True` и
`PHOTO_IMPORT_ENABLED=True` соответственно, а также отдельные служебные токены. Пока флаги
отсутствуют или равны `False`, обычный Deploy обновляет выбранный образ, но не запускает poller.

Один конкретный photo либо selfie job по UUID и один import batch по UUID запускаются
без обхода остальных ready записей:

```sh
docker compose --env-file .env -f docker-compose.deployment.yml --profile processing run --rm --no-deps photo-worker python -m photo_worker --job-id UUID
docker compose --env-file .env -f docker-compose.deployment.yml --profile import run --rm --no-deps import-worker python -m import_worker --batch-id UUID
```

Для Commerce по одному `PaymentAttempt.id` допускается только статусная сверка без запуска
email/reminder poller. Команда обращается к настроенному gateway и может обновить статус
платежа/права при авторитетном ответе; выберите безопасную тестовую операцию, без реального
списания, до исполнения:

```sh
docker compose --env-file .env -f docker-compose.deployment.yml --profile commerce run --rm --no-deps --entrypoint python commerce-worker manage.py run_commerce_worker --once --payment-attempt-id ID
```

Для каждого задания зафиксируйте выдачу через закрытый API, выполнение, принятый Django
результат и видимый продуктовый эффект. После этого отдельно решайте, когда включать
постоянные pollers. Обычный повторный Deploy не извлекает Cloud Backup, не восстанавливает БД,
не сбрасывает очередь и не включает выключенные обработчики.

## Выпуск выбранных образов

`deploy/apply-deployment.sh` принимает `DEPLOY_ROOT=/opt/photo-prjct` и флаги
`DEPLOY_WEB`, `DEPLOY_PHOTO`, `DEPLOY_IMPORT`, `DEPLOY_COMMERCE` строго `true` или `false`.
Выбранному `DEPLOY_WEB`/`DEPLOY_COMMERCE` соответствует `APP_IMAGE`, `DEPLOY_PHOTO` —
`PHOTO_WORKER_IMAGE` и `PHOTO_WORKER_BUILD`, `DEPLOY_IMPORT` — `IMPORT_WORKER_IMAGE`.
Образы — независимые immutable GHCR refs с SHA tag или digest; совпадение всех SHA не
требуется. Перед миграцией web Deploy проверяет наличие `django_migrations` и отказывает
на пустой БД. Для одноразовых management commands используется форма
`docker compose run --rm --no-deps --entrypoint python web manage.py COMMAND`.
