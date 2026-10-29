# Первое создание инфраструктуры одной VM

Этот документ описывает код этапа 1 и порядок bootstrap. 29 октября 2026 года после
отдельного разрешения владельца созданы backend и CI identity в `core`; проверены versioning,
изоляция доступа и настоящая конкуренция за S3 lock. Облачный plan PR #299 выполнен в
[Infrastructure run 40641805973, attempt 2](https://github.com/peter-nikitin/photo-prjct/actions/runs/40641805973/attempts/2)
для коммита `385a341d0fa100998e9577aa494b2b86eb48c2d2`: восемь действий create, без apply.
VM, диски и Cloud Backup agent ещё не создавались. Оценщик стоимости в этом запуске выдал
ошибку при успешном exit code; этот запуск не подтверждает успешную оценку расходов.
Проверки Terraform с `-backend=false` и mock provider также не являются облачным plan.
После согласования ресурсов один merge запускает `Infrastructure`: публикацию immutable образов,
apply одобренного plan, Ansible и размещение runtime-пакета. БД, web и обработчики остаются
остановленными до ручного восстановления по [порядку восстановления](single-vm-recovery.md).

## Граница ресурсов и измерений

Terraform root `infra/terraform/` управляет новой VPC `findme-photo-core`, подсетью
`10.240.0.0/24` в `ru-central1-d`, security group с публичным 80 и рассмотренными SSH CIDR,
одной VM `standard-v3` (2 vCPU, 20%, 1 ГБ), отдельными boot и PostgreSQL network-hdd дисками
и service account с `backup.user`. Закреплён Ubuntu 24.04 x86-64 image
`fd8a30ptg5tdapq35dlb` (публичная проверка 28 октября: READY, минимум boot 10 ГиБ).
Boot и PG диски имеют `auto_delete=false`; PG также защищён `prevent_destroy`.
Служебный public IPv4 выделяется интерфейсу VM. NLB, NAT, мониторинг, другие VM, media buckets,
Lockbox payload и backup policy не создаются. Старые ресурсы не удаляются.

`project.tfvars` явно задаёт начальные boot **30 ГиБ** и PostgreSQL **10 ГиБ**. Это
предварительные значения для пустой VM, а не измеренный минимум или вместимость восстановленной
БД. Boot учитывает тяжёлые текущие образы, временное место для их pull, 2 ГиБ swap и установку
агента поверх минимума Ubuntu image 10 ГиБ; локально собранные ARM64 images занимают:
web 1 908 016 180, photo worker 8 388 917 406, import worker 234 018 402 байта;
worker base 8 386 450 891 байт входит в слои photo worker и может разделяться. Три runtime
image дают около 10,53 ГБ decimal / 9,81 ГиБ до дедупликации слоёв. Образы для AMD64
runner/VM и пик распаковки могут отличаться; суммарное место ОС/агента ещё не подтверждено.
30 ГиБ — рассмотренный начальный бюджет текущей
нагрузки, а не гарантия вместимости или резерв трафика. До платного plan владелец сверяет
измерения, стоимость и при необходимости меняет размеры отдельным reviewable PR. Для restore
отдельно измерьте data, WAL, tablespaces и scratch из Cloud Backup и до копирования сделайте
новый Terraform PR на увеличение PG диска, если 10 ГиБ недостаточно. Архив около 52 ГиБ не
равен размеру восстановленной БД. Диски не увеличиваются скрыто через CI vars.

## Предварительная конфигурация, отдельная от PR

Пока её нет, облачный plan и merge-release не могут успешно выполниться. Ниже приведены команды
**для предъявления владельцу перед исполнением**, они здесь не исполнялись. Оператор с правами
администрирования `core` создаёт отдельный приватный state bucket; versioning хранит прошлые
версии state и увеличивает оплачиваемое хранение. Проверить уникальность имени и тариф по
официальному калькулятору до создания. Никакого публичного доступа и lifecycle удаления версий.

```sh
yc storage bucket create --name findme-photo-tfstate --folder-id b1g2qttgfhb4gdunvlge
yc storage bucket update --name findme-photo-tfstate --versioning versioning-enabled
yc storage bucket get --name findme-photo-tfstate
```

Отдельная CI identity нужна только для root `core`. Следующие команды фиксируют создаваемые
объекты; значения `SA_ID` и `FEDERATION_ID` берутся из возвращённых ID и не угадываются:

```sh
yc iam service-account create --name findme-photo-infrastructure --folder-id b1g2qttgfhb4gdunvlge
SA_ID=<returned-service-account-id>
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role compute.editor --subject "serviceAccount:$SA_ID"
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role vpc.privateAdmin --subject "serviceAccount:$SA_ID"
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role vpc.securityGroups.admin --subject "serviceAccount:$SA_ID"
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role vpc.publicAdmin --subject "serviceAccount:$SA_ID"
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role iam.serviceAccounts.admin --subject "serviceAccount:$SA_ID"
yc resource-manager folder add-access-binding --id b1g2qttgfhb4gdunvlge --role resource-manager.admin --subject "serviceAccount:$SA_ID"
yc iam workload-identity oidc federation create --name findme-photo-infrastructure --folder-id b1g2qttgfhb4gdunvlge --issuer https://token.actions.githubusercontent.com --audiences https://github.com/peter-nikitin --jwks-url https://token.actions.githubusercontent.com/.well-known/jwks
FEDERATION_ID=<returned-federation-id>
yc iam workload-identity federated-credential create --service-account-id "$SA_ID" --federation-id "$FEDERATION_ID" --external-subject-id 'repo:peter-nikitin/photo-prjct:environment:infrastructure'
```

`resource-manager.admin` требуется для назначения `backup.user` новому VM account; это
широкое право в пределах `core`, поэтому перед bootstrap отдельно просмотрите полный список
прав и возможность ограничить их после создания. `compute.editor` и `vpc.publicAdmin` нужны
созданию VM с публичным IP; `vpc.privateAdmin` — VPC и subnet,
`vpc.securityGroups.admin` — SG. Роли `vpc.editor` в ЯО нет. Эти роли **не** дают доступа к
прежним media buckets вне `core` по намерению конфигурации, но фактические inherited bindings
нужно проверить. Для чтения serial output у CI достаточно включённого в `compute.editor`
`compute.viewer`. [Compute roles](https://yandex.cloud/en/docs/compute/security/),
[IAM roles](https://yandex.cloud/en/docs/iam/security/),
[Resource Manager roles](https://yandex.cloud/en/docs/resource-manager/security/).

Для S3 backend создайте **второй**, отдельный service account и static access key. Готовая
[bucket policy](../../infra/bootstrap/state-bucket-policy.json.template) разрешает только этому
`CanonicalUser`: `GetBucketVersioning`, список объектов/версий в `core/single-vm/*` и
`core/lock-probe/*`, чтение/запись конкретного state key, удаление только `.tflock` и probe
state. Основной state нельзя удалить этим ключом; предыдущие версии можно читать для recovery.
Не выдавайте этому account `storage.editor` на весь `core` с media buckets. Оператор с
правами создания bucket/policy и service account применяет пакет после просмотра шаблона:

```sh
yc iam service-account create --name findme-photo-tfstate-backend --folder-id b1g2qttgfhb4gdunvlge
BACKEND_SA_ID=<returned-service-account-id>
umask 077
private_dir=$(mktemp -d /tmp/findme-tfstate-bootstrap.XXXXXX)
python3 -c 'import pathlib,sys; p=pathlib.Path("infra/bootstrap/state-bucket-policy.json.template"); print(p.read_text().replace("BACKEND_SA_ID",sys.argv[1]))' "$BACKEND_SA_ID" > "$private_dir/policy.json"
yc storage bucket update --name findme-photo-tfstate \
  --grants "grant-type=grant-type-account,grantee-id=$BACKEND_SA_ID,permission=permission-read" \
  --grants "grant-type=grant-type-account,grantee-id=$BACKEND_SA_ID,permission=permission-write"
yc storage bucket update --name findme-photo-tfstate --policy-from-file "$private_dir/policy.json"
yc iam access-key create --service-account-id "$BACKEND_SA_ID" --format json > "$private_dir/access-key.json"
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["access_key"]["key_id"])' "$private_dir/access-key.json" > "$private_dir/key-id"
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["secret"])' "$private_dir/access-key.json" > "$private_dir/secret"
gh secret set INFRA_S3_ACCESS_KEY_ID -R peter-nikitin/photo-prjct --env infrastructure < "$private_dir/key-id"
gh secret set INFRA_S3_SECRET_ACCESS_KEY -R peter-nikitin/photo-prjct --env infrastructure < "$private_dir/secret"
```

Команды сохраняют одноразово выдаваемый secret только в приватных файлах и передают его в
`gh secret set` через stdin. После проверки GitHub secrets удалите приватный каталог безопасным
способом на машине оператора; потеря secret до загрузки требует выпуска нового static key.
В ЯО bucket policy проверяется после базового IAM/ACL разрешения, поэтому сначала
назначаются bucket ACL READ/WRITE и только затем ограничивающая policy. Если policy уже
установлена, не удаляйте её вслепую: она может запрещать оператору изменение ACL; требуется
рассмотренное узкое разрешение управления ACL для конкретного оператора.
[Порядок проверок доступа](https://yandex.cloud/ru/docs/storage/security/overview).

Policy применяется **целиком** и заменит прежнюю policy, поэтому этот пакет рассчитан только
на новый отдельный bucket. Перед первым настоящим plan проверьте доступ и lock на probe key
ниже. [Bucket policy](https://yandex.cloud/en/docs/storage/concepts/policy),
[S3 actions](https://yandex.cloud/en/docs/storage/s3/api-ref/policy/actions),
[static key](https://yandex.cloud/en/docs/iam/operations/authentication/manage-access-keys),
[S3 backend YC](https://yandex.cloud/en/docs/tutorials/infrastructure-management/terraform-state-storage).

GitHub environment `infrastructure` требует `YC_INFRA_SERVICE_ACCOUNT_ID` как var и
упомянутые S3 secrets. Задайте var `INFRA_SSH_PUBLIC_KEY` (пара существующего
`VM_SSH_KEY`). `ssh_cidrs = ["0.0.0.0/0"]` уже указан в отслеживаемом `project.tfvars`:
текущий GitHub-hosted runner не имеет стабильного egress IP. SSH допускает только ключ,
непривилегированного пользователя и проверку host key из Yandex serial output; диапазон нужно
пересматривать в PR при появлении стабильного egress. Существующий `VM_SSH_KEY`
передаётся runner как secret. Проверьте, что policy environment допускает PR plan для
доверенной ветки и push main. `infra/ci/identity.py` проверяет issuer/audience/repository,
environment subject, ref и workflow_ref, затем получает короткоживущий токен ЯО. Федерация
привязана к точному subject, но право запустить план дополнительно проверяется через
write/maintain/admin permission автора PR. Fork plan с credentials не запускается.

## Проверка backend lock до первого apply

Сначала используйте отдельный **тестовый key** в том же bucket и те же credentials. Команды
ниже создают только тестовое состояние/lock-объекты; выполните их после разрешения bucket и
identity, не в PR без credentials:

```sh
export AWS_ACCESS_KEY_ID=<backend-access-key-id>
export AWS_SECRET_ACCESS_KEY=<backend-secret-key>
PROBE_ROOT=$(mktemp -d /tmp/findme-tf-lock-probe.XXXXXX)
cp infra/terraform/backend.tf "$PROBE_ROOT/backend.tf"
printf 'variable "lock_probe" { type = string }\n' > "$PROBE_ROOT/probe.tf"
terraform -chdir="$PROBE_ROOT" init -reconfigure -backend-config='key=core/lock-probe/terraform.tfstate'
terraform -chdir="$PROBE_ROOT" plan
```

Первый `plan` должен удерживать lock, пока ожидает значение `lock_probe`. Из второго процесса
с тем же probe root запустите `terraform -chdir="$PROBE_ROOT" plan -input=false
-lock-timeout=0s -var=lock_probe=complete`. Он обязан получить ошибку блокировки.
После ввода значения в первый процесс и его завершения повторный plan должен пройти.
На реальной проверке 29 октября `terraform console` 1.17.0 не удержал lock; для этой проверки
используется `plan`. Не выполняйте apply ни в probe root, ни в основном root.
Удаляйте только тестовый key и его версии после проверки, что процессы закрыты; основной
state/lock не трогайте. Перед первым основным plan инициализируйте canonical backend
через `terraform -chdir=infra/terraform init -reconfigure`. Не используйте `-lock=false`.

## PR → сохранённый plan → merge

Первый PR содержит новый workflow: `pull_request` job из самого PR способен выполнить
независимые offline `fmt`, `init -backend=false`, `validate`, mock Terraform test и Ansible
syntax. Облачный plan стартует только после настройки backend/identity/размеров. Он сохраняет
бинарный `plan.tfplan` как закрытый Actions artifact на 7 дней, публикует в PR только список
ресурсов/действий и оценку через локальный
[`terraform-yc-cost-estimation`](https://github.com/terraform-yc-modules/terraform-yc-cost-estimation/tree/7a74a71c72d7f49cae58ebc626658f5c2c816df1).
Для совместимости с форматом этого estimator CI передаёт ему временную копию `planned_values`:
внешний диск, указанный в `boot_disk.disk_id`, переносится в формат `initialize_params` и
исключается из отдельного подсчёта, чтобы учесть boot disk ровно один раз. Исходный plan не
меняется. Ошибка адаптера или estimator, в том числе напечатанная upstream CLI при exit code 0,
завершает шаг оценки ошибкой; неполный отчёт не публикуется как успешная оценка. Месячный итог
считает 31 день (744 часа), как закреплённый upstream estimator. Полный plan JSON не пишется в
комментарий. Непокрытые компоненты — трафик, backup-agent, Object Storage state/versioning и
пространство восстановления — оцениваются вручную по официальным тарифам; число `0` для них не
означает бесплатность. Public IPv4 при `nat = true` считается estimator-ом.

После просмотра точного run, resource diff и стоимости уполномоченный участник с правом write,
maintain или admin оставляет комментарий `/infra approve RUN_ID`. Новый push требует нового
plan run и нового комментария. После merge job заново проверяет автора, run `head_sha`,
workflow, разрешение комментатора и unexpired artifact, скачивает этот сохранённый plan и
выполняет **его** apply. Terraform проверяет state/lock; устаревший plan завершается ошибкой,
а не пересчитывается скрыто. Затем Ansible подключается с host key, извлечённым из
авторизованного Yandex serial output: cloud-init пишет туда только публичный ED25519 key;
`StrictHostKeyChecking=yes` обязателен. CI публикует web/photo/import images на runner,
использует pinned worker base digest, размещает пакет в `/opt/photo-prjct` и вытягивает images
на VM без `docker compose up`, `migrate` или restore.

На 28 октября `main` **не защищена** required checks. Для первого PR `issue_comment`
handler ещё не установлен в default branch, поэтому комментарий согласования не переключит
`infra/approved` в success до merge; самостоятельная проверка approval в push job обязательна
уже для первого merge. После первой установки workflow настройте branch protection/ruleset:
`infra/approved` и `Infrastructure validation` — обязательные, минимум один review,
запрет прямого push и stale approvals. Эта настройка GitHub не выполняется кодом PR и
должна быть проверена отдельно; сейчас нельзя утверждать, что она блокирует merge.

## Временный Cloud Backup agent и завершение

Первый Ansible `site.yml` не устанавливает агент и не запускает контейнеры. После проверки
свободного места (официальное условие: минимум 2 ГиБ свободно на Linux-диске) и отсутствия
других app workloads оператор отдельно запускает `infra/ansible/install-backup-agent.yml`
с тем же доверенным known_hosts. Он скачивает официальный installer и не печатает token из VM
metadata. Ошибка установки не меняет размер VM автоматически и не включает backup policy.
[Инструкция ЯО](https://yandex.cloud/en/docs/backup/operations/connect-vm-linux),
[условия подключения](https://yandex.cloud/en/docs/backup/concepts/vm-connection/compute).

После проверки извлечённых файлов и успешного restore отвяжите новую VM от регулярной backup
policy, если такая была назначена вручную, затем удалите временного агента на **новой** VM:

```sh
sudo /usr/lib/Acronis/BackupAndRecovery/uninstall/uninstall -a --no-purge
yc backup vm delete <new-vm-id> --folder-id b1g2qttgfhb4gdunvlge
```

Перед командами сверяйте `<new-vm-id>` с Terraform output, а исходную VM
`epdr5g3p24tdns9890nr` и её архив `c45fbb19-3a7a-5452-0e6d-257c29f0952e` не удаляйте.
`yc backup vm delete` удаляет регистрацию **новой** VM в Cloud Backup, но не Compute VM;
удаление архивов — отдельное действие. [Удаление регистрации](https://yandex.cloud/en/docs/backup/operations/delete-vm),
[деинсталлятор агента](https://www.acronis.com/en/support/documentation/CyberProtectionService/unattended-installation-or-uninstallation-in-linux.html).

## Приёмка после разрешённого apply

Запишите из CI run/TF outputs фактические VM/disk/subnet IDs, IP, затраты, версию agent и
Ansible результат. Повторный plan должен быть без необъяснённых изменений, повторный Ansible
не должен форматировать или перезапускать данные. Проверьте mount UUID,
`RequiresMountsFor=/var/lib/findme-photo` у Docker, права `postgres` 0700/UID 999,
отсутствие запущенных `db`, web, workers, отсутствие NLB и доступность SSH только по
проверенному ключу. Только затем переходите к [этапу БД и HTTP](single-vm-recovery.md).
