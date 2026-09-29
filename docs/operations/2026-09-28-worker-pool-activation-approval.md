# Согласование запуска изолированных worker pools

Дата: 2026-09-28. Статус: **предложение для согласования, не готовый apply-пакет**.
В этой проверке выполнены только read-only запросы. Новые ресурсы, квоты, IAM,
секреты и production-конфигурация не изменялись.

Основание: [утверждённый план](../plans/2026-09-27-autoscaled-worker-isolation.md),
[runbook](../runbooks/worker-pools.md), ADR 0042/0043 и
[проверенные тарифы](../research/2026-09-28-worker-pool-activation-cost.md).
Это пакет операционного согласования существующего решения, не новая архитектура.

Дополнение 2026-09-29: утверждён [autoscaled этап с потолком один](../superpowers/specs/2026-09-29-capped-worker-pool-activation-design.md):
bulk 0..1, selfie 1..1, required `pool_max_size=1` в checksum-bound конфигурации.
Начальная warm-проверка создаёт 1+1; backlog не разрешает вторую VM. Последовательная
замена/rollback временно расширяет только текущий пул до двух, затем восстанавливает
его политику и доказывает удаление старого boot disk до расширения следующего пула.
Repository approval не разрешает paid creation, IAM/network изменения или cutover.
По инвентаризации 2026-09-28 этап укладывается в 200 GiB только без пересечения builder
и replacement и без посторонних retained SSD. Перед apply нужны свежие quota/inventory.
Потолок два остаётся отдельным будущим согласованием; повышение квоты не меняет policy.

## Что уже выложено и что ещё не запущено

PR #224 в main: `454e46a03634fd5ca55672c41f1fd17ddc052376`.
Canonical Deploy `36470658090` завершён успешно. В snapshot 2026-09-28 фото-воркеры локальные:
два bulk и один selfie. Тогдашний read-only отчёт показал нулевые claimable очереди
и active leases обоих пулов; исторические expired attempts не являются текущим спросом.
В том snapshot удалённых Instance Groups, worker OS image, NAT и route table нет.
Удалённый scrape и новые диагностические правила ещё не активированы.

Основную VM не уменьшаем в этом этапе. PostgreSQL, web, import и commerce остаются
на ней; pgvector не переносим, не откатываем и не запускаем backfill.

## Targets, проверенные 2026-09-28

CLI profile: `default`; cloud/folder в профиле не заданы, поэтому команды обязаны
указывать targets явно. Cloud `b1gmcsmr51o5kvp86l55`, folder `b1g2qttgfhb4gdunvlge`.

| Объект | Точное значение в датированном snapshot |
| --- | --- |
| Canonical VM | `epdr5g3p24tdns9890nr`, `dev-photo-prjct`, ru-central1-b |
| Canonical shape | standard-v3, 8 vCPU 100%, 16 GiB, regular |
| Canonical private/public IPv4 | `10.129.0.34` / `111.88.151.64` |
| Canonical boot disk | `epdrviptgj09r7ac478d`, 100 GiB network-ssd |
| Network | `enpevjgdgdavmrv9ahb8` |
| Canonical subnet | `e2l18k26thgtq8vobbq7`, `10.129.0.0/24` |
| Canonical attached SG | `enpclrep8uilre076c6q` |
| Canonical service account | `aje62dg0p7tpn6tqu67d` |
| Application secret/version | `e6q85jjl76r45maigtfb` / `e6q72kupvflgru2iu8r2` |
| Image-origin VM / SG | `epdf6696opq3ock91pih` / `enphgh6s669dv1647ggv` |

## Предлагаемый состав новых ресурсов

- Оба пула: standard-v3, 2 vCPU 100%, 8 GiB RAM, 32 GiB network-ssd,
  контейнер 2 CPU / 5 GiB, concurrency 1, без публичного IP и ingress.
- `findme-photo-worker-selfie`: regular, минимум 1, максимум 1.
  Backend сохраняет ограничение одного claim-capable selfie worker до принятия
  проверки двух независимых поисков. Вторая VM всё равно оплачивается, если существует.
- `findme-photo-worker-bulk`: preemptible, минимум 0, максимум 1.
  Начальное создание — по одной VM каждого пула; idle bulk затем возвращается к нулю.
  Одна bulk VM заменяет два локальных процесса: меньше concurrency и дольше очередь —
  принятый trade-off. При release/rollback максимум три worker boot disks суммарно.
- Новый private subnet `10.131.0.0/24` в ru-central1-b, в существующей сети;
  NAT gateway и default route только для нового subnet. Перед созданием повторно
  проверить пересечения адресов, включая маршруты/VPN; текущая инвентаризация не
  доказывает отсутствие внешних пересечений. Canonical subnet/routes не менять.
- Новые worker SG, canonical SG и временный builder SG.
  Worker egress: private API TCP 8443 к `10.129.0.34/32`, HTTPS 443 для
  GHCR/Lockbox/Object Storage, DNS TCP/UDP 53 к проверенному cloud resolver.
- Отдельные worker-runtime и group-manager service accounts. Runtime — только
  `lockbox.payloadViewer` на bootstrap secret; без cloud/folder authority и app-secret access.
  Manager — отдельная согласованная матрица прав, не worker identity.
- Новый Lockbox bootstrap secret с ровно двумя ключами:
  `PHOTO_PROCESSING_FLEET_TOKEN`, `IMAGE_PULL_AUTH` (read-only GHCR credential).
  Fleet token отдельный от локального. Совпадающий токен добавить в новую версию
  application secret, сохранив все остальные значения и старую версию для отката.
  Значения не хранить в Git, не передавать в argv и не выводить в отчёт.
- Один чистый OS image, собранный на временной private regular VM 2 CPU / 8 GiB /
  32 GiB SSD, без app secrets и application service account, срок до двух часов.
  Доступ через canonical bastion с существующим личным SSH-ключом.
  Никакого клонирования диска основной VM. Builder VM и диск удалить до запуска
  replacement. Доказать удаление диска отдельной полной inventory, а не только отсутствие VM.
  Новых buckets, DB, LB, YCR и Prometheus VM не добавляем.
  [Диагностика 20/24 GiB](2026-09-29-worker-disk-sizing.md) не согласует уменьшение:
  утверждённые worker и builder диски остаются 32 GiB.

## Блокеры до apply

1. **SSD inventory/quota и provider lifecycle.** Snapshot 2026-09-28: 200 GiB,
   занято 100 GiB. Начальные/steady 1+1 дают 164 GiB; один replacement — 196 GiB,
   запас лишь 4 GiB. Builder и replacement не пересекаются; остановленный retained
   диск продолжает занимать квоту. До activation доказать builder cleanup и свежие
   quota/VM/disk inventories. На реальных ресурсах отдельно проверить provider
   recovery, survivor selection при восстановлении maxSize/floor и удаление дисков:
   максимум три worker boot disks, включая stopped/transitional. Fixtures этого не
   доказывают. CPU/RAM/VM квоты также перепроверить. Исторический будущий 2+2 требует
   228 GiB (с builder 260 GiB); прежнее предложение 256 GiB не prerequisite cap-one
   и не разрешение поднять потолок.
2. **Canonical SG открыт полностью.** Его ingress ANY `0.0.0.0/0` разрешает и 8443.
   Добавление второй SG не ограничивает первую. Нужна замена списка SG canonical NIC:
   public TCP 22/80/443, private TCP 8443 только от worker SG, egress ANY сохраняем.
   Public SSH 22 сохраняет существующий доступ, а не вводит новую экспозицию;
   дальнейшее ограничение SSH не входит в этот этап. IP/NAT/subnet NIC не менять.
   Default SG не редактировать глобально. На image-origin существующее правило SSH
   с source default SG зависит от canonical identity: перед заменой добавить аналогичное
   узкое правило с source новой canonical SG, остальные правила сохранить.
3. **Чистый образ ещё не подготовлен.** Проверен официальный base image
   `fd84a0ma316h9ddtvdoi` (`ubuntu-24-04-lts-v20260928`), но это не worker image.
   Нужны review сборочного recipe, точные Docker/Compose версии, установка telemetry
   dependencies согласно ADR 0043, sanitization и проверки после нового boot.
   OS image ID, SSH public-key path и immutable GHCR worker digest пока не зафиксированы.
4. **IAM нельзя считать закрытым.** До выдачи прав проверить inherited cloud/org
   bindings и составить exact operation-to-role matrix для group manager и canonical
   observer/release/retirement. Не выдавать canonical folder-wide compute.editor/operator
   ради удобства: такие права затрагивают не только новые workers.
5. После создания prerequisites нужны реальные IDs, pinned versions/digests,
   чистый `--inspect`, reviewed checksum и durable receipt. Placeholder не является
   согласованным target. Публикация свежих demand metrics и диагностик требует live proof,
   а не только успешных локальных тестов или Deploy.
6. **Cap-one saturation alert до customer cutover.** Подготовленное правило с `R - 1.5`
   требует две running VM и не покрывает утверждённый потолок один. Закрыть
   [обязательный cap-one prerequisite](../future-work/2026-09-29-cap-one-worker-saturation-alert.md)
   до любого ceiling-one customer cutover: reviewed cap-one-specific predicate, native
   Alarm/NoData/recovery и доказанная доставка в approved channel для actual pool/zone.
   Сохранить freshness/unknown handling при stale/missing cloud и queue observations,
   включая total publisher outage. Manual inspection и fixture GREEN не заменяют эту
   приёмку; её нельзя отложить до включения optional diagnostics.

## Датированная оценка дополнительной стоимости

Тарифы проверены 2026-09-28, не обновлены этим пакетом; перед paid approval обновить.
RUB с НДС; сравнение по 730 часам. Основная VM продолжает оплачиваться как сейчас.
Фиксированный subtotal включает compute, worker SSD, NAT и одну bootstrap version.
Расширенный ориентир добавляет image, условно оплачиваемый как 32 GiB, одну дополнительно
сохранённую app-secret version и около 0.50 RUB native demand writes.

| Непрерывно существующие workers | Фиксированный subtotal / месяц | Расширенный ориентир / месяц |
| --- | ---: | ---: |
| Idle: 1 selfie, 0 bulk | 4 511 RUB | около 4 651 RUB |
| Начальные и steady максимум 1 selfie, 1 bulk | 5 957 RUB | около 6 097 RUB |
| Исторический сценарий 1 selfie, 2 bulk; вне steady cap-one | 7 403 RUB | около 7 543 RUB |
| Исторический будущий потолок 2 selfie, 2 bulk | 11 605 RUB | около 11 745 RUB |

Idle 0+1 — нижняя граница после реального удаления bulk VM/disk, не непрерывные 1+1.
Временная serial replacement добавляет running compute и allocated SSD по фактическому
времени: по датированным ставкам около 1.9808 RUB/час для bulk или 5.7568 RUB/час
для regular selfie с 32 GiB диском. Builder — отдельное временное начисление.
Это не all-inclusive счёт и не прогноз нагрузки: фактический billed image size,
internet egress, bootstrap reads/churn, diagnostic Prometheus writes и доступный
account-wide free tier неизвестны. Bulk-zero экономит только после удаления VM
и её диска; остановленные/оставленные диски оплачиваются. NAT остаётся при idle.
Источники и формулы: [Compute](https://yandex.cloud/ru/docs/compute/pricing),
[VPC](https://yandex.cloud/ru/docs/vpc/pricing),
[Lockbox](https://yandex.cloud/ru/docs/lockbox/pricing),
[подробный расчёт](../research/2026-09-28-worker-pool-activation-cost.md).

Предлагаемое начальное окно acceptance — **24 часа**, builder — **до двух часов**.
По датированной оценке steady максимум 1+1 с условным image 32 GiB и дополнительной
app-secret version — около 200 RUB за 24 часа, builder до двух часов — около 12 RUB;
serial replacement добавляется по фактическому времени. Исторические 398 RUB относятся
к 2+2, не к текущему этапу. **500 RUB** — прежний предлагаемый
резерв фиксированной части, не автоматический billing cap и не ограничение переменных
расходов. Продление окна и оставление ресурсов после него требуют отдельного решения.

## Порядок и точки согласования

1. Repository policy уже согласован: `pool_max_size=1`, bulk0..1/selfie1..1.
   Пользователь отдельно согласует paid shapes, свежие квоты/стоимость, замену SG с сохранением
   доступа, временный builder и окно/бюджет. Это ещё не разрешение на неизвестные IAM grants.
2. Агент готовит reviewable build recipe и IAM matrix, сохраняет сетевой rollback snapshot,
   фиксирует все точные команды. Пользователь подтверждает consequential IAM/network/paid
   пакет непосредственно перед выполнением. GHCR read-only credential предоставляется
   через защищённый канал, если его ещё нет; ничего не накликиваем в cloud UI.
3. Агент создаёт только согласованные prerequisites, читает ресурсы обратно, собирает
   и проверяет OS image, удаляет builder VM/disk с полным read-back доказательством,
   материализует private provisioning config с integer `"pool_max_size": 1`.
4. Агент выполняет read-only inspect, показывает checksum и полный diff ресурсов.
   После отдельного подтверждения создаёт 1+1 группы с durable receipt; при timeout
   не повторяет mutation вслепую, сверяет exact-name status и receipt.
5. Canonical private TLS, secret/config и remote placement включаются через canonical
   Deploy с reviewed release manifest/checksum. Cutover только после warm/paused readiness,
   cloud observation и fencing: drain локальных leases, остановка только photo workers,
   затем remote claims. DB, import, commerce, gates и pgvector не трогаем.
6. Live acceptance: private TLS/auth и public запрет 8443, heartbeat/results обоих pools,
   выдача selfie результата, bulk обработка, отсутствие двойных claims, fresh native
   demand/capacity, bulk-zero/wakeup и selfie floor one, cap-one serial release/rollback
   с доказательством disk absence и survivor safety. Обязательна отдельная native приёмка
   cap-one saturation predicate, Alarm/NoData/recovery и доставки до customer cutover
   согласно [prerequisite](../future-work/2026-09-29-cap-one-worker-saturation-alert.md).
   Проверка диагностической доставки
   и правил — отдельные доказательства ADR 0043, не вывод из здоровья exporter.
   Не запускаем benchmark или старый backfill ради приёмки.
7. В конце окна явно решить: сохранить принятый steady state или откатить и убрать
   временные ресурсы. Проверить реальные VM/disks/image/secret versions и счётчики расхода.

## Проверенные интерфейсы команд — не выполнять с placeholders

```bash
yc --profile default --folder-id b1g2qttgfhb4gdunvlge \
  compute instance update-network-interface --id epdr5g3p24tdns9890nr \
  --network-interface-index 0 --security-group-id <REVIEWED_CANONICAL_SG_ID>

.venv/bin/python deploy/worker-pools/provision.py \
  --config <PRIVATE_CONFIG_PATH> --profile default --inspect

.venv/bin/python deploy/worker-pools/provision.py \
  --config <PRIVATE_CONFIG_PATH> --profile default \
  --apply <REVIEWED_SHA256> --receipt <FRESH_PRIVATE_RECEIPT_PATH>

.venv/bin/python deploy/worker-pools/provision.py \
  --config <PRIVATE_CONFIG_PATH> --profile default --status

.venv/bin/python deploy/worker-pools/acceptance.py --pool-max-size 1
```

До полного build/IAM пакета эти примеры намеренно не превращены в исполняемый скрипт.
Network/secret/SA создания должны иметь точные reviewed rules/bindings и read-back,
а не подразумеваемые широкие права. Config включает IDs из creation outputs,
`pool_max_size` равный integer 1, `worker_build` равный принятому deployed SHA
и immutable `worker_image` digest. Inspect/dry-run связывают policy с reviewed checksum;
fresh private receipt записывает checksum, folder и по каждому пулу state/group/operation IDs
до/после submission. Status должен отдельно доказать actual `scale_policy` bulk0..1/selfie1..1.
При ambiguous submission сохранить receipt; новый receipt и blind retry запрещены.
Release receipt `worker-pools-release.json` сохраняет `expanded_pool` и `worker_disks`:
resume/rollback сначала разрешает pending operation и текущий expanded pool.
Mode-aware checklist не выдаёт live GREEN; ceiling-two demand/второй selfie — отдельная приёмка.

## Откат

При откате первого cutover сначала fence remote claims, drain/дождаться принятого lease boundary и вернуть local
placement через совместимый canonical Deploy. Проверить локальную обработку и отсутствие
двойной выдачи. После этого отключить private listener 8443, и только затем при необходимости
вернуть canonical NIC на `enpclrep8uilre076c6q`: обратный порядок открыл бы 8443 публично.
Совместимый remote rollback выполняется тем же release controller и journal, последовательно:
только один временно расширенный пул, restored maxSize/floor и полная disk-absence inventory
до следующего expansion. Mixed-ceiling manifests отклоняются. Retained/transitional disks
или uncertain receipt оставляют фазу заблокированной для read-back; произвольного DELETE нет.
Удалять только exact-ID новые workers/groups и их worker disks после подтверждения
безопасного прекращения работы; это не application data disks. Удаление/retention image,
bootstrap secret и NAT/routes согласовать отдельно по использованию и rollback window.
Основной диск, PostgreSQL/pgvector и фото не удалять, DB restore не требуется.
