# Подготовка сохранения ресурсов в Terraform

Основа: снимок 8 ноября 2026 года, дополненный свежей сверкой NLB/state перед PR. В едином root `infra/terraform/` подготовлены определения
подтверждённых бакетов, KMS, IAM/OIDC, оболочек Lockbox, Postbox, CDN origin group,
сертификата, сохраняемых диска/IP/сети/SG/SA прежнего origin и старой daily Backup policy с binding.
`imports.tf` содержит только новые адреса. В этой задаче не было живого plan, import,
apply, изменений cloud, DNS или state. Код не доказывает, что объект уже принят под управление.

## Текущий state и ограничения

- Remote state serial 8 уже учитывает `yandex_vpc_address.edge`, app VM/диски,
  core network/subnet/SG, target group, NLB `enpbdbcdd3merghos9sr` и IAM.
  NLB ACTIVE, IP `111.88.151.64` used по повторному API чтению; это не доказывает
  работоспособность публичного пути. `legacy_holder` отсутствует в новом state;
  desired resource/import уже убраны. `state rm` этой задачей не выполнялся.
- По последнему решению владельца только origin VM зоны b `epdf6696opq3ock91pih`
  исключена из desired Terraform и import. Она STOPPED в последнем API снимке, но
  **не удалена** этой задачей. Диск `epd07hfr77d4a3un6n9d`, IP, SG, subnet, network
  и SA остаются. Boot disk у VM сейчас `auto_delete=true`; будущему плану удаления
  VM необходимо заранее исключить каскадное удаление диска. Image-origin и
  независимый публичный probe будут восстановлены отдельно; сейчас их работа
  после вывода VM не подтверждена.
- Backend bucket `findme-photo-tfstate` описан частично: имя/folder/STANDARD, versioning
  и private anonymous flags подтверждены, но policy/ACL/CORS/lifecycle/encryption/tags/
  website/logging/object lock полностью прочитать не удалось. Узкий `ignore_changes`
  не обходит ошибки чтения provider и не разрешает import/apply. Сохранить независимую
  identity для восстановления backend и его прежний state key; не помещать ключи в Git/state.
- Private-media policy содержит только существующий `yc:access-key-id` как ID условия,
  без создания access key. Grants/CORS/lifecycle/encryption для прикладных бакетов
  воспроизведены из подтверждённой конфигурации. `prevent_destroy` не предотвращает
  in-place изменение policy или прямые API изменения.

## Границы владельцев

- CDN resource `bc8ryczd5bpuzduhwubz` **не импортировать** в `yandex_cdn_resource`:
  его native Read переносит signing key и origin-auth headers в state. Публичные ID,
  origin group, сертификат и source paths находятся в не секретном output
  `retained_cloud_dependencies`. Существующие `deploy/image-origin/provision.py` и
  `configure-cdn.sh` сохраняют управление CDN options/secrets. Перед будущим native
  import origin SG/group/cert нужно передать их ownership от legacy provisioning
  path и отключить его запись в те же native объекты; нельзя оставить двух writer.
- Monitoring workspace и dashboard остаются у `deploy/monitoring/prometheus/`
  controller. Текущие источники — `single_vm_dashboard.py`,
  `single-vm-dashboard.json`, `environment.json` и `.github/workflows/monitoring.yml`.
  Terraform описывает SA, IAM и OIDC, а не dashboard второго writer.
  Старое API чтение `findme-photo.yml` (4 группы/33 правила) и прежние EMAIL/TELEGRAM
  имена каналов записаны в output отдельно как `pending-review`: текущий
  dashboard-only controller не управляет rules, routing или channels. Их живое
  состояние и доставка не подтверждены этим пакетом; не удалять до аудита.
- Оболочки Lockbox описаны без versions/payload. Не создавать SA static/API keys.
  Архив Cloud Backup и recovery points остаются данными с отдельным жизненным циклом,
  не фиктивными `terraform_data` resources. Daily policy и binding к исчезнувшей VM
  описаны ровно по текущему API, без переноса binding на новую VM. Возможность
  импорта binding при отсутствующем instance не подтверждена; policy provider не
  выражает все API поля, включая task-failure scheduling — проверять живой diff.
- Postbox использует стандартный AWS SESv2 provider только на endpoint Yandex.
  Management credentials обязательны, sensitive, внешние, не равны по умолчанию
  backend credentials или SMTP API key. Identity домена взята из sender
  `orders@findme-photo.ru`, но GetDomainIdentity вернул 403; существование и
  verification не подтверждены. DKIM/SPF/DMARC по решению владельца пока не проверяются.

## Условия будущих действий

1. Повторить API inventory и получить достаточные конфигурационные read права для S3,
   Postbox, IAM и Backup. Без полного чтения backend и Postbox import блокирован.
   Если identity Postbox отсутствует, прекратить import и отдельно решать создание.
2. Сверить текущий state: уже учтённые адреса не импортировать повторно. Для bucket
   ID используется имя, для IAM member — `resource-id,role,subject`, для Backup binding
   — `policy-id:instance-id`. `legacy_holder` уже отсутствует в state serial 8;
   не делать ручной `state rm` этим изменением.
3. Подготовить saved plan и разделить действия: каждый import должен показывать только
   ожидаемый `to import` без add/change/destroy своего ресурса. Отдельно оценить
   любые реальные изменения, в том числе drift Backup policy или сертификата. Не применять всё одним неразобранным планом.
4. Только после отдельной авторизации и передачи ownership выполнить одобренные
   операции. Проверить последующий plan и реальную доставку/восстановление. Offline
   validate/mock tests не заменяют этот приёмочный этап.

Оценка стоимости в CI не включает полный storage/KMS объём. Наличие resource в коде не
означает import, работающую галерею, Monitoring или восстановленную БД.

`infrastructure.yml` передаёт `TF_VAR_postbox_access_key` и
`TF_VAR_postbox_secret_key` из отдельных environment secrets
`INFRA_POSTBOX_ACCESS_KEY_ID` и `INFRA_POSTBOX_SECRET_ACCESS_KEY` в живые plan и
release. Сами secrets пока отсутствуют: их необходимо предоставить отдельно,
без подстановки backend ключей и без записи в Git или Terraform state. При
изменении Terraform draft PR выполняет обязательную offline validation, оставляет
`infra/approved` в `pending` и откладывает живой plan. Переход в Ready for review
запускает живой plan; пустые Postbox credentials останавливают его до обращения к
облаку. Приёмка по-прежнему требует достаточных read permissions для backend и
Postbox и передачи ownership существующих native объектов от старого provisioning.
