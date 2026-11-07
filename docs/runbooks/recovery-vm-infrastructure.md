# Подготовка recovery VM

Этот runbook описывает конфигурацию в коде. Его наличие не означает, что Terraform уже
создал VM или подготовил ОС. Тестовая VM `fv4lac9q76a3m7209gjp` остаётся отдельным ресурсом;
Terraform recovery её не импортирует и не изменяет.

## Доступ и ключи

Для будущего запуска используйте защищённое GitHub environment `infrastructure` и существующую
OIDC identity. В environment нужны публичные ключи `INFRA_SSH_PUBLIC_KEY` (технический `deploy`)
и `INFRA_ADMIN_SSH_PUBLIC_KEY` (оператор `petrnikitin`). Terraform передаёт оба публичных ключа
cloud-init; Ansible поддерживает их в `authorized_keys`. У обоих пользователей есть sudo, а SSH
разрешает только этих пользователей. Вход по паролю и root SSH выключены; OS Login выключен.

Приватные ключи и логины хранятся в отдельных Yandex Lockbox secrets. Техническая пара находится
в секрете `findme-photo-vm-deploy` (`e6q12ru8opfn2sl91so3`), операторская — в
`findme-photo-vm-admin` (`e6qes463bl6r09r72580`). Оба секрета содержат `ssh_private_key`,
`ssh_public_key` и `ssh_user`. GitHub Actions получает только техническую пару через OIDC;
операторская приватная пара в Actions не передаётся. Оператор получает её напрямую из Lockbox.

OIDC service account должен иметь роль `lockbox.payloadViewer` на техническом секрете. Это
отдельная bootstrap-настройка IAM: текущая CI identity не может читать или управлять IAM
привязками Lockbox. Не выдавайте ей доступ к операторскому секрету или folder-wide
`lockbox.payloadViewer`. Terraform отслеживает ID технического секрета, но не хранит приватные
значения и не управляет этим IAM grant.

После apply workflow проверяет username и совпадение приватного ключа с ожидаемым публичным
ключом, создаёт временный файл с режимом `0600` и удаляет его в завершающем шаге. При ошибке
проверки SSH/Ansible не запускается. Host key берётся только из стандартного блока
`-----BEGIN SSH HOST KEY KEYS-----` / `-----END SSH HOST KEY KEYS-----` в аутентифицированном
serial output Yandex Cloud.

Старый GitHub secret `VM_SSH_KEY` не используется infrastructure workflow. Его можно удалить
только после проверки нового Lockbox-пути. Отдельные устаревшие workflow и ручные инструменты
ещё ссылаются на старый Lockbox `VM_SSH_KEY`; до их отдельного отключения исходный Lockbox secret
нельзя удалять или ротировать через этот PR.

## Ресурсы и границы

Terraform создаёт новую VM `findme-photo-app` со штатными сетевыми ресурсами, SSD boot disk
30 GiB и пустым SSD PostgreSQL disk 10 GiB. Автоудаление обоих дисков вместе с VM отключено. Ansible готовит
ОС, Docker/Compose, PostgreSQL mount и swap 2 GiB. Он не запускает приложение, миграции, workers,
restore или backup agent.

Pinned Ubuntu 24.04 image ID `fd8p50cvjmb6fo0ik4hk` совпадает с образом работающей тестовой VM;
Terraform использует этот ID для новой VM и не меняет тестовый instance.

Перед будущим release нужна квота `compute.ssdDisks.size.ru-central1-d` не менее 40 GiB. Свежая
read-only проверка показала лимит 10 GiB и использование 0 GiB; увеличение квоты потребуется до
создания двух новых SSD дисков общим размером 40 GiB. Тестовая VM использует отдельные 20 GiB HDD,
они не входят в лимит SSD. Тестовую VM не изменять для освобождения квоты.

Стоимость проверяйте по свежей автоматической оценке Terraform plan в PR: переход на SSD меняет
стоимость дисков, поэтому предыдущую цифру не используйте. Оценка покрывает новые управляемые
Terraform ресурсы, но не включает существующую тестовую VM и её расходы. Два Lockbox secret
versions оцениваются отдельно; запросы чтения тарифицируются отдельно. Суммы не являются лимитом
счёта.

Этот PR подготавливает код, проверки и опубликованный plan. Создание новой VM, подготовка ОС,
проверка повторного Ansible запуска и восстановление данных относятся к последующим шагам.
