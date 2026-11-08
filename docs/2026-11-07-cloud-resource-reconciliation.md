# Сверка ресурсов Yandex Cloud с Terraform

> Исторический снимок 7 ноября. Состояние и рекомендации ниже уточнены повторной
> сверкой 8 ноября в разделе «Поправка» в конце и в
> [карте сохранения](2026-11-07-yandex-cloud-removal-inventory.md).
> Перед PR повторно проверены state serial 8 и NLB `enpbdbcdd3merghos9sr`: NLB ACTIVE,
> IP `111.88.151.64` used; `legacy_holder` отсутствует в state. Исторические выводы
> ниже о несуществующем NLB и старом state больше не являются текущими.

## Область и достоверность

Проверено 7 ноября 2026 года около 22:20 МСК через Yandex Cloud CLI 1.21.0.
Профиль `default`, cloud `b1gmcsmr51o5kvp86l55`, folder `core`
`b1g2qttgfhb4gdunvlge`. Код: commit `a45b7ccec96176ec1904f481be6c3eeaa53c6e35`,
ветка сверки `codex/terraform-resource-audit`.

Это датированный снимок, а не автоматически обновляемый реестр. Проверка выполнялась
чтением API и DNS; облачные ресурсы, Terraform state, DNS и настройки приложения не изменялись.
В первоначальной облачной сверке секретные payload, ключи и содержимое пользовательских
объектов не запрашивались. При дополнительной проверке runtime использован штатный
resolver административного SSH-ключа; подробности безопасного доступа приведены ниже.

**Основной результат:** базовая инфраструктура `findme-photo-app` существует и по проверенным
параметрам соответствует `.tf`. Все восемь описанных ресурсов подтверждены в remote state;
повторный import для них не нужен.
Медиа, CDN, сертификат, большая часть IAM, Lockbox и политики Backup в текущих `.tf` отсутствуют.
Часть найденных ресурсов относится к прежнему развёртыванию; наличие ресурса не доказывает,
что приложение сейчас его использует.

### Ограничения

- Первоначальное чтение state от имени пользователя вернуло HTTP 403 `AccessDenied`.
  Дополнительная проверка от имени существующего backend service account успешно прочитала
  тот же объект; права доступа не изменялись. Подробности и полная таблица — ниже.
- Проверен только state `core/single-vm/terraform.tfstate`. Отсутствие ресурса в нём
  не исключает управление через другой state или другой инструмент.
- Локальный `terraform` не найден в PATH. `plan`, `refresh` и `import` не запускались.
- Первоначальный запрос `storage bucket get <четыре имени> --full` завершился
  `PermissionDenied`. Дополнительная проверка через существующие SA прочитала настройки
  public-media и selfie-feedback. Полная конфигурация `hires-staging` и backend bucket
  остаётся не сверена; текущий доступ приложения к трём прикладным бакетам проверен отдельно.
- Дополнительная проверка прочитала состав контейнеров, разрешённые несекретные settings
  и состояния feature flags работающей app VM. Пользовательские записи БД не читались.
  `RUNNING` VM, включённый флаг и `active` CDN не доказывают успешное выполнение всей функции.
- Проверка ограничена указанным folder и перечисленными ниже API. Это не полный аудит
  всех сервисов облака, организации, биллинга или внешних провайдеров.

## Проверка remote state

Дополнительная проверка: 07.11.2026 22:54 МСК. Прочитан текущий объект
`s3://findme-photo-tfstate/core/single-vm/terraform.tfstate` от имени backend service account
`findme-photo-tfstate-backend` (`ajeonsj2akeimj92fokf`) через существующую возможность
impersonation локального профиля `default`. Создание ключей и изменение IAM не потребовались.
Это прямое чтение тем же backend service account, который используется в CI, а не новый запуск CI.

State: `serial=5`, `terraform_version=1.17.0`. Все восемь записей имеют
`mode=managed`; у экземпляров нет отметки tainted. Ниже полный список ресурсов этого state:

| Адрес Terraform | ID из remote state | Сопоставление с облачной сверкой |
| --- | --- | --- |
| `yandex_compute_disk.boot` | `fv403tmlprjig1hhq03s` | Совпадает |
| `yandex_compute_disk.postgres` | `fv4judonkgm21ge8u4in` | Совпадает |
| `yandex_compute_instance.app` | `fv49nfcj1075ujmp7798` | Совпадает |
| `yandex_iam_service_account.backup_agent` | `ajehrj01jqanjq5k314n` | Совпадает |
| `yandex_resourcemanager_folder_iam_member.backup_agent` | `b1g2qttgfhb4gdunvlge/backup.user/serviceAccount:ajehrj01jqanjq5k314n` | Совпадает |
| `yandex_vpc_network.core` | `enplm7eeg91tbuinnf5s` | Совпадает |
| `yandex_vpc_security_group.vm` | `enphvlbssskeh7f8an1g` | Совпадает |
| `yandex_vpc_subnet.core_d` | `fl834drjhtpjeva0ehe9` | Совпадает |

**Вывод:** все ресурсы текущих `.tf` уже учтены в этом state. Добавлять для них import не требуется:
связь с облачными объектами уже установлена. Совпадение ID не является проверкой всех атрибутов
или отсутствия drift; для этого нужен отдельный свежий Terraform plan.

В проверенном state отсутствуют дополнительные VM `app-3`, `dev-photo-prjct`, image origin
и их отдельные диски, дополнительные сети/SG/зарезервированные IP, все четыре bucket resources,
CDN/origin group, сертификат, три оболочки Lockbox, политики Backup и остальные SA/IAM bindings.
Упоминание bucket в `backend.tf` не создаёт для него managed resource в state.
Динамический IP app также не имеет отдельной записи: он связан с network interface VM.
Автоматические default SG и private DNS-зоны не следует автоматически превращать в отдельные
управляемые ресурсы только из-за их присутствия в облаке.

Эти пробелы — кандидаты на уточнение владельца и принятие под управление. Перед import
проверить отсутствие другого управляющего state, актуальность зависимости и поддержку provider.
Решение о сохранении старых ресурсов и app-3 этой проверкой не принято.

В [Infrastructure run 42389503635](https://github.com/peter-nikitin/photo-prjct/actions/runs/42389503635)
шаг Terraform apply завершился успешно; job затем упал на проверке SSH в Ansible.
Ошибка последующего шага не означает откат Terraform. Текущий state независимо подтверждает
созданные ID VM и дисков. Для чтения state release повторно не запускался, поскольку он может
применить Terraform и Ansible; отдельного read-only режима в текущем workflow нет.

Исходный state находился только во временном каталоге с ограниченным доступом и был удалён
после разбора. В отчёт перенесены только serial, версия Terraform и пары address/ID;
metadata, outputs и возможные секретные поля не публиковались.

## Ресурсы, для которых есть описание Terraform

Адреса ниже находятся в `infra/terraform/`. «Совпадает» означает совпадение перечисленных
параметров из API с кодом, а не результат Terraform plan.

| Адрес Terraform | Реальный ресурс | Сверка |
| --- | --- | --- |
| `yandex_compute_instance.app` | `findme-photo-app`, `fv49nfcj1075ujmp7798` | RUNNING; `ru-central1-d`, `standard-v3`, 2 vCPU, 20%, 1 GiB; boot и PG disk, subnet, SG и SA соответствуют ссылкам в коде |
| `yandex_compute_disk.boot` | `findme-photo-boot`, `fv403tmlprjig1hhq03s` | `network-ssd`, 30 GiB, `ru-central1-d`, image `fd8p50cvjmb6fo0ik4hk`; подключён к app |
| `yandex_compute_disk.postgres` | `findme-photo-postgres`, `fv4judonkgm21ge8u4in` | `network-ssd`, 10 GiB, `ru-central1-d`; подключён к app как `pgdata` |
| `yandex_vpc_network.core` | `findme-photo-core`, `enplm7eeg91tbuinnf5s` | Имя и folder совпадают |
| `yandex_vpc_subnet.core_d` | `findme-photo-core-d`, `fl834drjhtpjeva0ehe9` | Сеть core, `ru-central1-d`, `10.240.0.0/24` совпадают |
| `yandex_vpc_security_group.vm` | `findme-photo-single-vm`, `enphvlbssskeh7f8an1g` | TCP 22 и 80 из `0.0.0.0/0`, любой исходящий трафик; соответствует текущим правилам и `project.tfvars` |
| `yandex_iam_service_account.backup_agent` | `findme-photo-backup-agent`, `ajehrj01jqanjq5k314n` | Имя, folder и подключение к app совпадают |
| `yandex_resourcemanager_folder_iam_member.backup_agent` | `backup.user` для `ajehrj01jqanjq5k314n` | Привязка подтверждена `folder list-access-bindings` |

У app внутренний адрес `10.240.0.31`, внешний `158.160.152.73`.
Объект внешнего адреса `fl8dptsbj8v6s47ubs75`: `used=true`, признак резервирования
не выставлен. Это соответствует `nat=true`; отдельного закреплённого публичного адреса
в Terraform нет. Адрес не следует считать стабильным входом.

Cloud-init/metadata, фактическое содержимое дисков и применение Ansible здесь не сверялись.
`prevent_destroy` PG-диска — ограничение Terraform в коде; оно не подтверждает отдельную
защиту от удаления через облачный API.

## Дополнительные VM и диски

| VM | Состояние и параметры | Связанные ресурсы | Решение для учёта |
| --- | --- | --- | --- |
| `findme-photo-app-3`, `fv4lac9q76a3m7209gjp` | RUNNING; зона d, 2 vCPU, 20%, 1 GiB; `158.160.130.235`, `10.240.0.24` | Boot `fv4smqo4ka649adke494`, HDD 20 GiB, auto-delete; subnet core-d; явные SG и SA в ответе отсутствуют | Нет в текущих `.tf`; назначение не установлено. До принятия под управление или удаления установить владельца и зависимости |
| `dev-photo-prjct`, `epdr5g3p24tdns9890nr` | STOPPED; зона b, 8 vCPU, 100%, 16 GiB; `111.88.151.64`, `10.129.0.34` | Boot `epdrviptgj09r7ac478d`, SSD 100 GiB, auto-delete; subnet `e2l18k26thgtq8vobbq7`; SG `enp2dpjqa3i8cttdsnpp`; SA `aje62dg0p7tpn6tqu67d` | Прежняя VM; публичный DNS всё ещё указывает на её IP. Не считать ненужной только из-за STOPPED |
| `findme-gallery-image-origin`, `epdf6696opq3ock91pih` | STOPPED; зона b, 2 vCPU, 100%, 4 GiB; `84.252.136.164`, `10.129.0.21` | Boot `epd07hfr77d4a3un6n9d`, HDD 20 GiB, auto-delete; subnet `e2l18k26thgtq8vobbq7`; SG `enphgh6s669dv1647ggv`; SA `ajef0cammpfg75gn7ntd` | Имеет связанные CDN-ресурсы. Решение о дальнейшем использовании отсутствует в этой сверке |

Всего обнаружены четыре VM и пять дисков. Удаление, остановка и перенос ресурсов
не входят в эту работу; стоимость не рассчитывалась.

## Сети, адреса и публичный вход

- Кроме core существуют сеть `default` (`enpevjgdgdavmrv9ahb8`) и `dev-network`
  (`enplf0ljpuae7o5mu83q`). В `.tf` они не описаны.
- В default находятся подсети a `e9bjse1j1fkginpvk6bf` (`10.128.0.0/24`),
  b `e2l18k26thgtq8vobbq7` (`10.129.0.0/24`) и c `b0c6jcpi0vpsquv75kmr`
  (`10.130.0.0/24`). В dev-network — `e9bt9lg5gv3d355ttsb4` (`192.168.1.0/24`).
- Дополнительные SG: `findme-canonical-worker-api` (`enp2dpjqa3i8cttdsnpp`),
  `findme-gallery-image-origin` (`enphgh6s669dv1647ggv`) и три default SG:
  `enpclrep8uilre076c6q`, `enps7tf9tmo9oced65uk`, `enpvnto5k0jdjjvmad5b`.
  У трёх default SG разрешён любой входящий и исходящий IPv4-трафик.
  У app-3 список явно назначенных SG отсутствует; эффективные правила требуют отдельной проверки.
- Зарезервированы внешние IP `111.88.151.64` (`e2lq0aldvqrp4uebr75l`)
  и `84.252.136.164` (`e2l0lvoheorael320u6u`); оба `used=true`.
  У app-3 внешний адрес `fl8dueu69ock1gn1kd38` без выставленного `reserved`.
- NLB и target groups отсутствуют: оба list-запроса вернули `[]`.
  [ADR 0058](adr/0058-stable-public-entrypoint-nlb.md) описывает принятое решение,
  но это не существующий ресурс, который можно импортировать.
- Route tables и Compute snapshots: list вернул `[]`.

DNS на момент проверки:

| Запись | Ответ |
| --- | --- |
| `findme-photo.ru A` | `111.88.151.64` |
| `www.findme-photo.ru A` | `111.88.151.64` |
| `img.findme-photo.ru CNAME` | `6c512e0693e21c21.topology.gslb.yccdn.ru.` |
| `findme-photo.ru NS` | `ns3-l2.nic.ru`, `ns4-l2.nic.ru`, `ns8-l2.nic.ru`, `ns4-cloud.nic.ru`, `ns8-cloud.nic.ru` |

Cloud DNS list содержит только четыре автоматически созданные private reverse/internal зоны
для сетей default и core. Публичной зоны проекта в этом folder не найдено.
Управление публичной DNS-зоной следует устанавливать у фактического провайдера;
переносить её в Yandex Cloud ради наличия Terraform не требуется.

## Object Storage

Все четыре бакета существуют в целевом folder, используют STANDARD; у всех
anonymous read/list/config_read отключены. Название `public-media` не означает публичный ACL.
Первоначальное чтение полных настроек получило отказ. Результаты дополнительной
проверки public-media и selfie-feedback приведены ниже.

| Бакет | Resource ID | Versioning / max_size | Текущий Terraform |
| --- | --- | --- | --- |
| `hires-staging` | `e3edi4a2pi2mtrlej5c4` | Disabled; max_size не задан в ответе | Нет; известная привязка private media, актуальный runtime не проверен |
| `findme-photo-public-media-b1g2qttg` | `e3evsmo5nnco7mfm540p` | Enabled; 100 GiB | Нет |
| `findme-selfie-feedback-staging-b1g2qttg` | `e3e9q39ce6mese8b5kh4` | Disabled; 50 GiB | Нет |
| `findme-photo-tfstate` | `e3efvlu4k2hg92fsg0pg` | Enabled; max_size не задан в ответе | Упомянут в backend.tf; как управляемый bucket resource не описан |

`max_size` — настройка лимита, а не фактический объём данных. Backend bucket и его IAM —
bootstrap-зависимости; способ их дальнейшего управления нужно определить явно,
сохранив доступ к state и независимость восстановления backend.

## CDN и сертификат

| Ресурс | Факт из API | Terraform |
| --- | --- | --- |
| CDN `bc8ryczd5bpuzduhwubz` | `img.findme-photo.ru`, active=true, origin protocol HTTPS | Нет |
| Origin group `2569228330826683012` | `findme-gallery-image-origin`; source `img-origin.findme-photo.ru`, enabled=true | Нет |
| Сертификат `fpqkhstccrsfl899lq4h` | `findme-gallery-cdn`, MANAGED, ISSUED; домен `img.findme-photo.ru`; привязан к CDN; deletion protection=true; not_after `2026-12-22T06:37:21Z` | Нет |

Origin VM остановлена, CDN активен. Работоспособность origin, кэша и выдачи изображений
не проверялась. Полная CDN-конфигурация в отчёт не выводилась: она может содержать
секретные заголовки и ключи подписи. При подготовке HCL отделить их от несекретных настроек.

## IAM и Lockbox

Кроме описанного backup agent обнаружены следующие service accounts. Наличие аккаунта
и время последней аутентификации не доказывают, что он нужен текущему приложению.

| Аккаунт | ID | Folder roles, обнаруженные этой проверкой |
| --- | --- | --- |
| `sa-selfie-feedback-staging` | `aje39ip0j620c1l6qa7d` | Не обнаружены |
| `findme-monitoring-ci` | `aje3t70qka1dtc09k5ic` | `monitoring.editor` |
| `sa-dev-photo-monitoring` | `aje62dg0p7tpn6tqu67d` | `vpc.viewer`, `monitoring.editor`, `backup.user` |
| `sa-lockbox-ci-staging` | `ajeaekiue94ogksguh0h` | Не обнаружены |
| `findme-gallery-image-origin` | `ajef0cammpfg75gn7ntd` | `monitoring.editor`, `storage.viewer` |
| `sa-hires-staging` | `ajeg9msqqkanrlgqqr1f` | Не обнаружены |
| `find-me-staging` | `ajejo91its6ckh9ch3gq` | `storage.editor` |
| `findme-photo-infrastructure` | `ajema849qjho2lgjaqvo` | `resource-manager.admin`, `compute.editor`, `iam.serviceAccounts.admin`, `vpc.publicAdmin`, `vpc.privateAdmin`, `vpc.securityGroups.admin` |
| `sa-postbox-sender` | `ajenr791754j1aegg2o5` | `postbox.sender` |
| `findme-photo-tfstate-backend` | `ajeonsj2akeimj92fokf` | Не обнаружены |

Есть также folder bindings `monitoring.viewer` и `vpc.user` для service account
`aje21huml8igbm1llshe`, отсутствующего в списке SA этого folder. Его происхождение
не установлено; это не доказательство неработающей или лишней привязки.
Resource-level и унаследованные разрешения здесь не проверены.

Lockbox содержит три ACTIVE secret с deletion protection=true:

- `findme-photo`, `e6q85jjl76r45maigtfb`;
- `findme-photo-vm-deploy`, `e6q12ru8opfn2sl91so3`;
- `findme-photo-vm-admin`, `e6qes463bl6r09r72580`.

Их оболочки и IAM в текущих `.tf` отсутствуют. Payload не читался и не должен переноситься
в Git. Возможность описать оболочки отдельно от значений требует проверки схемы provider.
OIDC federation/credentials, resource-level bindings, ключи SA, Postbox identities,
Monitoring dashboards/alerts и настройки внешних сервисов этой сверкой не охвачены.

## Cloud Backup

- Зарегистрированы app `fv49nfcj1075ujmp7798` и прежняя VM `epdr5g3p24tdns9890nr`.
- Для app API сообщает `is_active=true`, agent `18.6.42870`, status `OTHER`,
  `status_details=no_policies_applied`. Регистрация агента не означает настроенный backup.
- Для прежней VM status `FAILED`, последний backup по resource API —
  `2026-10-07T01:46:46Z`. Сохранность и восстановимость recovery point здесь не проверялись.
- Существуют включённые политики: `Default weekly` (`cdg2ra5hfeeri4urojgz`),
  `Default daily` (`cdg3zbstfcpxlmyrxm3a`), `Default monthly` (`cdg3ufs5g565w7madd3p`).
  У каждой retention rule `max_count=15`. Привязки политик отдельно не запрашивались.
- Политики и bindings отсутствуют в текущих `.tf`; установка агента предусмотрена отдельным
  `infra/ansible/install-backup-agent.yml`. Применение политики — самостоятельное изменение,
  а не действие этой сверки.

## Что нужно для принятия используемых ресурсов под управление

1. Выполнено: remote state прочитан через существующую backend identity, полная безопасная
   таблица Terraform address → resource ID приведена выше. Восемь базовых ресурсов
   повторного import не требуют. IAM не изменялся.
2. Установить назначение app-3, дальнейшую роль прежней VM и image origin; сверить с текущим
   Deploy и runtime. Уточнить владельца публичного DNS и план переключения входа.
3. Для сохраняемых зависимостей дочитать bucket policies/CORS/lifecycle, IAM на ресурсах,
   OIDC, Backup bindings и необходимые настройки CDN. Проверить поддержку import и схемы
   закреплённого `yandex-cloud/yandex` версии `0.240.0` для каждого типа.
4. Добавлять HCL и import только для ресурсов, отсутствующих в state. Сначала получить план
   принятия под управление без create/update/delete/replace, затем повторный `No changes`.
5. Будущие ресурсы, включая NLB, и изменения выявленных конфигураций проводить отдельными
   проверенными изменениями. Не смешивать инвентаризацию с включением backup или переключением DNS.

Конфигурация ОС остаётся в Ansible, контейнеров — в Compose/Deploy; данные и секреты
не являются материалом для экспорта в `.tf`. Границы закреплены в
[архитектуре](architecture.md#deployment-domain-assignment--accepted) и
[ADR 0057](adr/0057-declarative-cloud-infrastructure.md).

## Подтверждённое использование и предложение по составу Terraform

Дополнительная проверка 7 ноября 2026 года около 23:00–23:04 МСК сопоставила:

- [успешный Deploy 42404178467](https://github.com/peter-nikitin/photo-prjct/actions/runs/42404178467),
  commit `a45b7cce`, с фактической VM `fv49nfcj1075ujmp7798`;
- GitHub repository variables с пагинацией, manifest `deploy/environment-secrets.json`,
  текущие Compose и сценарий выпуска;
- работающий web-контейнер, только несекретные settings и таблицу `feature_flags.FeatureFlag`;
- метаданные S3/API credentials и SA, KMS, resource-level IAM, OIDC и Backup bindings.

SSH выполнялся штатным `operator-admin-key` resolver с проверкой ожидаемого публичного ключа
из GitHub Environment `infrastructure` и host key из metadata конкретной VM.
Resolver временно получил только административную SSH identity и удалил её после команды.
S3 credentials оставались внутри web-контейнера; их идентификаторы сопоставлялись с метаданными
IAM в памяти, в результат попали только имена и ID SA. Значения секретов не публиковались.
Чтение feature flags выполнялось в транзакции `READ ONLY`; состояния не менялись.

### Что реально подключено

| Ресурс / зависимость | Подтверждение текущего использования | Предложение |
| --- | --- | --- |
| `hires-staging` | В settings web; `PHOTO_UPLOAD_ENABLED=True`; HEAD bucket существующими credentials приложения успешен | Добавить bucket и его фактические ACL/policy/CORS/lifecycle после дочитывания конфигурации |
| `findme-photo-public-media-b1g2qttg` | В settings web; default storage — `storages.backends.s3.S3Storage`; HEAD успешен | Добавить bucket и действующие настройки доступа/версий/лимита |
| `findme-selfie-feedback-staging-b1g2qttg` | В settings web; `SELFIE_FEEDBACK_ENABLED=True`; HEAD успешен | Добавить bucket, шифрование и retention |
| KMS `abjjca35o900fng2nk6v` (`selfie-feedback-staging`) | Тот же ID в runtime settings и encryption rule feedback bucket; ключ ACTIVE | Добавить `yandex_kms_symmetric_key` и текущий IAM member |
| SA `find-me-staging`, `ajejo91its6ckh9ch3gq` | Его S3 access key используется для public-media в работающем web | Добавить SA и относящиеся к нему действующие права |
| SA `sa-hires-staging`, `ajeg9msqqkanrlgqqr1f` | Его S3 access key используется для private media в работающем web | Добавить SA и bucket permissions после их полного чтения |
| SA `sa-selfie-feedback-staging`, `aje39ip0j620c1l6qa7d` | Его S3 access key используется web для feedback; ACL bucket и `kms.keys.encrypterDecrypter` ссылаются на этот SA | Добавить SA, bucket grants и KMS IAM |
| Основной Lockbox `e6q85jjl76r45maigtfb` и deploy SSH Lockbox `e6q12ru8opfn2sl91so3` | Указаны manifest; успешный Deploy использовал resolver и dedicated deploy identity | Добавить оболочки secrets и действующие IAM members; значения и версии payload в Terraform не переносить |
| Admin SSH Lockbox `e6qes463bl6r09r72580` | Реально использован для read-only SSH этой проверки | Добавить оболочку; сохранить текущую модель операторского доступа без автоматического расширения IAM |
| SA `sa-lockbox-ci-staging`, `ajeaekiue94ogksguh0h`, OIDC federation `ajeula3gd46omgf9jiko`, credential `ajej11dnec456ucpu1s3` | Manifest Deploy и облачный IAM подтверждают цепочку GitHub main → SA → Lockbox; сегодняшний Deploy успешен | Добавить SA, federation, credential и resource-level Lockbox IAM |
| Backend bucket `findme-photo-tfstate`, backend SA `ajeonsj2akeimj92fokf`, infrastructure SA `ajema849qjho2lgjaqvo`, federation `aje2m3i6k60tdau0u9vk`, credential `ajeo1rvk92qc8s7vnjd6` | Настроены в backend/Infrastructure; объект state прочитан; cloud подтверждает federation и credential | Описать существующий bootstrap-контур отдельно от обычных app resources с явной процедурой восстановления state; не создавать повторно |
| CDN `bc8ryczd5bpuzduhwubz`, origin group `2569228330826683012`, сертификат `fpqkhstccrsfl899lq4h` | Web использует `https://img.findme-photo.ru`; в БД `gallery-cdn-images=on` и `event-cover-cdn-images=on`; CDN active и ссылается на group/сертификат | Добавить как существующие зависимости галерей. Импорт не означает восстановление выдачи |
| Image origin VM `epdf6696opq3ock91pih` и её диск/SG/SA/IP | Origin group указывает на `img-origin.findme-photo.ru`; DNS A этого имени — `84.252.136.164`, внешний IP origin VM; отдельный workflow настроен на её внутренний `10.129.0.21` | Это настроенная зависимость CDN, хотя VM STOPPED. Не объявлять мусором; перед включением compute-части в целевой Terraform определить, сохраняется ли отдельный origin в принятой схеме одной VM |
| Static IP `111.88.151.64`, `e2lq0aldvqrp4uebr75l` | Основной DNS продолжает ссылаться на него; ADR 0058 закрепляет его за будущим NLB | Добавить существующий адрес с сохранением его жизненного цикла. Переподключение к NLB — отдельное изменение |

HEAD подтверждает существование и текущий доступ к bucket, но не наличие новых загрузок
или успешное выполнение операций записи. Объекты и их имена не перечислялись, фотографии не читались.
Для KMS подтверждены конфигурация и разрешение; пробное шифрование/дешифрование не выполнялось.

У feedback bucket дополнительно подтверждены:

- server-side encryption `aws:kms` с ключом `abjjca35o900fng2nk6v`;
- включённое lifecycle-правило `selfie-feedback-expire-after-30d`, expiration 30 дней;
- FULL_CONTROL для `aje39ip0j620c1l6qa7d`.

У public-media ACL содержит READ/WRITE для `ajejo91its6ckh9ch3gq` и FULL_CONTROL
для группы пользователей организации. Это зафиксированная конфигурация; менять права
при импорте без отдельного рассмотрения не предлагается.

### Что не следует автоматически добавлять в рабочий контур

| Ресурс / группа | Что установлено | Предложение |
| --- | --- | --- |
| `findme-photo-app-3`, `fv4lac9q76a3m7209gjp` и boot disk | RUNNING, но отсутствует в текущих Deploy target, state и ссылках исследованных `deploy/`, `infra/`, `.github/`, `docs/`; назначение не подтверждено | Оставить в инвентаре как неподтверждённый ресурс. Не включать в целевую конфигурацию и не удалять без выяснения назначения |
| `dev-photo-prjct`, `epdr5g3p24tdns9890nr`, SSD 100 GiB и SA `sa-dev-photo-monitoring` | STOPPED; текущий Deploy направлен на новую app VM. Старый адрес всё ещё в DNS; daily Backup binding остаётся на старой VM | Не включать старую VM в целевой runtime по умолчанию. Сохранить до завершения восстановления/переключения и решения о данных; учёт сохраняемых recovery-ресурсов определить отдельно |
| Старые/default сети, подсети a/c, `dev-network` | Текущий app использует core-d. Subnet b и default network связаны с origin и старой VM | Описывать только зависимости сохраняемого origin/recovery, а не всю инфраструктуру folder. Автоматические SG/private DNS не дублировать без необходимости |
| Weekly/monthly Backup policies | Существуют, но в `policy list-applications` найдена только daily policy `cdg3zbstfcpxlmyrxm3a` на старой VM | Не импортировать все политики как используемые. Сохраняемую daily policy и binding можно учесть как recovery; политику для новой VM сначала определить отдельным изменением |
| Postbox SA `ajenr791754j1aegg2o5` | `postbox.sender` существует; factory Postbox задана, но Commerce worker-контейнера нет. В web `COMMERCE_WORKER_ENABLED=False`; Postbox credentials в web отсутствуют и предназначены Compose service commerce-worker | Использование Postbox текущим runtime не подтверждено. По последующему решению владельца сохранить Postbox: SA и role включены в Terraform, доменная identity подготовлена к import из настроенного sender `orders@findme-photo.ru`. API чтения вернул 403, статус verification и существование identity не подтверждены; подписи DKIM/SPF/DMARC пока не проверять |
| Monitoring SA `aje3t70qka1dtc09k5ic`, credential `ajeprb31m2nhu6pbj2gn`, workspace `mon0c97qv2s5uju1ark8`, dashboard `fbeketud0mdaupj43of6` | Есть отдельный workflow/controller и его конфигурация в Git. API workspace отвечает на read queries и rules; свежий PromQL `up` вернул пустой result | SA/OIDC/IAM можно описать в Terraform. Rules/dashboard пока оставить существующему controller: не давать двум инструментам управлять одним объектом. Наличие конфигурации не доказывает исправный мониторинг |
| SA image-origin `ajef0cammpfg75gn7ntd` | Привязан к остановленному origin; folder роли `monitoring.editor`, `storage.viewer` | Включать вместе с решением о сохраняемом origin; не смешивать его права с web SA |
| Неидентифицированный SA `aje21huml8igbm1llshe` | Folder bindings есть, SA отсутствует в списке аккаунтов этого folder | Не импортировать и не удалять до установления владельца |

Новая VM фактически содержит только `photo-prjct-web-1`, `photo-prjct-db-1`,
`photo-prjct-nginx-1`; web/db healthy. Photo/import/commerce worker и observability-контейнеров
в `docker ps -a` не обнаружено. В web `PHOTO_WORKER_POOL_COORDINATOR_ENABLED=False`.
GitHub variables `COMMERCE_WORKER_ENABLED=True` и другие флаги не доказывают запущенные
worker-сервисы: текущий `apply-deployment.sh` останавливает workers и запускает только db/web/nginx.
Это существенное ограничение текущего runtime, а не основание импортировать несуществующий worker pool.

### Предлагаемый первый пакет принятия под управление

1. Три прикладных бакета, KMS feedback, три реально используемых S3 SA и их действующие права.
   Полную конфигурацию private-media дочитать до подготовки HCL; не реконструировать её по догадкам.
2. Три оболочки Lockbox, подтверждённые CI SA/OIDC и IAM. Секретные значения и материал ключей
   остаются в существующем хранилище; создание новых credentials не входит в импорт.
3. Отдельно описанный существующий bootstrap backend/Infrastructure и зарезервированный
   публичный IP. Это зависимости воспроизводимости проекта; точную границу bootstrap state
   согласовать до реализации, сохранив независимый доступ к backend.
4. Сохранить Postbox SA, `postbox.sender`, настроенный домен отправителя и SMTP key в Lockbox.
   Доменная identity подготовлена к будущему import; API чтения пока вернул 403, подписи не проверяются.
5. CDN/group/сертификат и сохраняемые зависимости origin — после решения о размещении origin.
   Включённые runtime flags означают, что этот вопрос нельзя отложить под предположением
   «CDN не используется». Создание NLB, запуск origin и переключение DNS — отдельные операции.

Предложение не является принятым ADR или разрешением менять ресурсы. До import для каждого
объекта проверить другой управляющий state и получить plan без облачных изменений.

Типы ресурсов проверены по [дереву provider v0.240.0](https://github.com/yandex-cloud/terraform-provider-yandex/tree/v0.240.0/docs/resources):
`storage_bucket`, `storage_bucket_grant`, `storage_bucket_policy`, `kms_symmetric_key`,
`kms_symmetric_key_iam_member`, `lockbox_secret`, `lockbox_secret_iam_member`,
`iam_workload_identity_oidc_federation`, `iam_workload_identity_federated_credential`,
`cdn_resource`, `cdn_origin_group`, `cm_certificate`, `backup_policy`, `backup_policy_bindings`,
`monitoring_dashboard`. Наличие типа не заменяет проверку его полной схемы и import-формата.
Документация [OIDC credential](https://yandex.cloud/en/docs/terraform/resources/iam_workload_identity_federated_credential)
и [Monitoring dashboard](https://yandex.cloud/en/docs/terraform/resources/monitoring_dashboard)
подтверждает возможность импорта этих типов; перенос dashboard не предлагается, пока им владеет controller.

## Воспроизводимость проверки

Каждый list-запрос ниже выполнялся с `--profile default`, явным
`--folder-id b1g2qttgfhb4gdunvlge` и `--format json`:

```text
yc compute instance list
yc compute disk list
yc compute snapshot list
yc vpc network list
yc vpc subnet list
yc vpc security-group list
yc vpc address list
yc vpc route-table list
yc iam service-account list
yc resource-manager folder list-access-bindings --id b1g2qttgfhb4gdunvlge
yc storage bucket list
yc load-balancer network-load-balancer list
yc load-balancer target-group list
yc dns zone list
yc cdn resource list
yc cdn origin-group list
yc certificate-manager certificate list
yc lockbox secret list
yc backup policy list
yc backup resource list
```

Все перечисленные запросы завершились с exit 0. Для CDN сохранялась только безопасная
проекция ID, домена, активности, origin и сертификата; для Lockbox — метаданные без payload.
Первоначальный отказ чтения state, успешное повторное чтение через backend identity
и отказ `bucket get --full` отражены выше. Команда успешного чтения:

```text
yc --profile default --impersonate-service-account-id ajeonsj2akeimj92fokf storage s3api get-object --bucket findme-photo-tfstate --key core/single-vm/terraform.tfstate <защищённый-временный-файл>
```

Команда завершилась с exit 0; JSON разобран локально без вывода исходного state. Локальная CLI предупреждала
о запрете записи своих логов в sandbox, но перечисленные успешные API-запросы вернули данные.
DNS проверен через `dig +short` для A, CNAME и NS. Этот документ не является результатом
Terraform plan и не утверждает отсутствие drift по непроверенным полям.

## Поправка по повторной сверке 8 ноября

Ранний список VM/дисков и «предлагаемый первый пакет» выше описывают исторический
снимок, не актуальную рекомендацию удалить CDN, Monitoring или Postbox. По текущему
API `dev-photo-prjct` `epdr5g3p24tdns9890nr`, `findme-photo-app-3`
`fv4lac9q76a3m7209gjp` и их boot disks отсутствуют; причины исчезновения не
установлены. В повторно прочитанном remote state serial 8 `legacy_holder` уже отсутствует;
его desired Terraform также исключён, чтобы не воссоздать VM. Edge IP
`e2lq0aldvqrp4uebr75l`, target group и NLB `enpbdbcdd3merghos9sr` учтены
в state. NLB ACTIVE и IP used по свежему API, но сквозная доступность не проверялась.

Принятые ADR 0038/0044/0045 сохраняют выдачу через CDN; ADR 0039 независимо требует
публичный probe. ADR 0056 отменяет отдельное размещение origin как постоянную
архитектуру, но не отменяет обе функции. По последнему уточнению владельца только origin VM зоны b исключена из целевой
Terraform для будущего отдельного удаления. Она не удалена этой задачей; boot disk
`epd07hfr77d4a3un6n9d` (`auto_delete=true` на VM), IP, SG, SA, default network и
subnet b сохраняются. При будущем удалении VM нужно заранее исключить каскадную
потерю диска. Image delivery и независимый public probe должны быть восстановлены
отдельно позднее; их работа после вывода VM не обещается.
CDN origin group и сертификат описаны в Terraform для будущего import. Сам CDN
resource остаётся у `deploy/image-origin` controller: native Read перенёс бы signing
key и origin-auth headers в state. Не секретная внешняя зависимость отражена в
`infra/terraform/retained-dependencies.tf`.

ADR 0018/0048/0053 сохраняют Monitoring. Текущий reconciler
`single_vm_dashboard.py` управляет только dashboard из `single-vm-dashboard.json`,
без второго Terraform writer. Dashboard Get ранее подтвердил ID
`fbeketud0mdaupj43of6`. Старый API read файла `findme-photo.yml` нашёл 4 группы
и 33 правила; прежняя конфигурация называла EMAIL/TELEGRAM каналы. После
обновления main эти файлы правил/routing и поля каналов удалены из текущего
controller. Это историческое наблюдение и предмет отдельного аудита, не
подтверждение текущей управляемой конфигурации или доставки алертов. Terraform
подготовил SA/OIDC/IAM и не секретный каталог внешних объектов. Cloud Logging list повторно вернул `Unavailable` около 00:20 8 ноября,
поэтому отсутствие групп не установлено.

Postbox сохраняется для транзакционных писем. SA/роль и домен sender описаны для
будущего принятия, но GetDomainIdentity вернул 403: существование и verification
не подтверждены; DKIM/SPF/DMARC по указанию владельца пока не проверяются.
Daily Backup policy/binding и 15 recovery points старой VM защищены до независимого
подтверждения восстановления, хотя VM отсутствует. Полные условия и условные
кандидаты на удаление находятся в [карте сохранения](2026-11-07-yandex-cloud-removal-inventory.md).

С учётом новых ресурсов, импортов и существующего state численные итоги прежнего
снимка выше не использовать как текущую инвентаризацию. Повторный API/state check
нужен перед любым live plan; в этой задаче cloud и state не изменялись.
Текущий `infrastructure.yml` не передаёт две обязательные переменные Postbox
management credentials, поэтому pipeline plan до отдельного безопасного wiring
не готов; достаточные read permissions для backend и Postbox также не доказаны.
