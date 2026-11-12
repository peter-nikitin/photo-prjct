# Сохранение и условный вывод ресурсов Yandex Cloud

Основа: снимок 8 ноября 2026 года; дополнение перед PR — повторное чтение NLB/VM/IP/state 8 ноября.
Это карта решений, а не команда на удаление. Никаких облачных изменений при её составлении
не выполнялось. Сверять её с актуальным API перед любым действием: состояние уже менялось
между снимками 7 и 8 ноября без участия этой задачи.

## Сохранить для продукта

| Группа | Подтверждённое основание | Владелец конфигурации и граница |
| --- | --- | --- |
| Основной web/DB и вход | VM `fv49nfcj1075ujmp7798`, диски `fv403tmlprjig1hhq03s`, `fv4judonkgm21ge8u4in`, core network/subnet/SG, reserved IP `e2lq0aldvqrp4uebr75l`, target group `enpn00nn07i2oi7no491` | Уже учтены в state serial 8 вместе с NLB `enpbdbcdd3merghos9sr` (ACTIVE по повторному API чтению). Адрес только `yandex_vpc_address.edge`, IP `111.88.151.64` используется NLB. Это не подтверждает end-to-end доступность приложения. |
| Object Storage и KMS | Четыре бакета, KMS `abjjca35o900fng2nk6v`, их подтверждённые permissions | Native Terraform определения подготовлены; backend bucket описан частично из-за запрета полного чтения. Данные бакетов не являются отдельными Terraform resources. |
| IAM/Lockbox/OIDC/Postbox | Используемые SA, роли, три Lockbox shells, federations и credentials; Postbox нужен транзакционной почте | Native определения; payload/keys и версии сохраняются вне state. Postbox domain identity взята из sender `orders@findme-photo.ru`, API вернул 403; существование/verification не подтверждены. DKIM/SPF/DMARC по решению владельца сейчас не проверяются. |
| Галерея/CDN | CDN resource `bc8ryczd5bpuzduhwubz` активен; origin group `2569228330826683012`, сертификат `fpqkhstccrsfl899lq4h` ISSUED | Origin group и сертификат подготовлены к native import. Сам CDN resource остаётся у существующего `deploy/image-origin/provision.py`/`configure-cdn.sh`: native Read переносит signing key и origin-auth headers в state, поэтому import запрещён. Публичная выдача ещё не проверена end-to-end. |
| Monitoring | Workspace `mon0c97qv2s5uju1ark8`; Dashboard SDK Get ранее подтвердил `fbeketud0mdaupj43of6`. Текущий `single_vm_dashboard.py` и `single-vm-dashboard.json` описывают только dashboard | Сохранить существующий dashboard controller единственным writer; SA/OIDC/IAM подготовлены к import. Ранее API прочитал legacy `findme-photo.yml` (4 группы, 33 правила), а старая конфигурация называла EMAIL/TELEGRAM каналы; текущий controller больше не управляет rules/routing/channels. Их живое состояние и доставка требуют отдельного аудита. Не импортировать dashboard вторым writer в Terraform. |

## Защитить до завершения восстановления и аудита

| Объект | Текущий факт | Условие для пересмотра |
| --- | --- | --- |
| Boot disk прежнего origin `epd07hfr77d4a3un6n9d`, IP `e2l0lvoheorael320u6u`, SG `enphgh6s669dv1647ggv`, subnet b `e2l18k26thgtq8vobbq7`, default network `enpevjgdgdavmrv9ahb8`, SA `ajef0cammpfg75gn7ntd` | Владелец решил вывести только VM зоны b `epdf6696opq3ock91pih` из целевой Terraform. Перечисленные ресурсы остаются защищёнными native определениями и import blocks; их удаление не разрешено. | Сохранять при последующем удалении VM. У boot disk сейчас `auto_delete=true`: до удаления отдельно обеспечить сохранение диска, исключив каскадное удаление. Восстановление image-origin и независимого public probe — отдельные будущие задачи. |
| Default SG `enpclrep8uilre076c6q`, peer SG `enp2dpjqa3i8cttdsnpp`, auto private DNS зоны default/core | Origin SG ссылается на оба peer SG; DNS и default SG — системные дети сохраняемых сетей. | До удаления любого peer проверить реальные SG rules. Дочерние объекты не считать мусором из-за отсутствия отдельного `.tf`. |
| Daily Backup policy `cdg3zbstfcpxlmyrxm3a` и binding к старой VM `epdr5g3p24tdns9890nr` | Binding ещё подтверждён API, хотя VM уже NotFound. Архив `c45fbb19-3a7a-5452-0e6d-257c29f0952e` содержит 15 recovery points; старая регистрация `505d0bdd-a624-4b6d-b9d2-1d1ef78b984e` имеет FAILED, новая `ce81900e-9714-450a-8361-e9c808dbe940` — `no_policies_applied`. | Сохранить архив и цепочку до независимого restore proof и решения о recovery. Не перепривязывать старую policy к новой VM без отдельного плана. Возможность импорта binding при исчезнувшем VM проверить отдельно. |
| `sa-dev-photo-monitoring` `aje62dg0p7tpn6tqu67d` | SA и `monitoring.editor` существуют; также обнаружены `vpc.viewer` и `backup.user`. Старой VM больше нет, но callers/API keys не проверены. | Аудит вызовов, ключей и чужих ссылок. Дополнительные роли помечены для проверки, не удаляются и не усваиваются как worker scope. |
| Cloud Logging и неизвестные права | Group list повторно вернул `Unavailable` 8 ноября около 00:20; отсутствие групп не доказано. | Повторить API inventory с доступной службой; до этого не считать ресурсы неиспользуемыми. |

В `infra/terraform/retained-dependencies.tf` эти внешние ресурсы описаны не секретным
`retained_cloud_dependencies` output с владельцем и причиной защиты. Это не cloud-managed
placeholder и не заменяет соответствующий controller или хранение данных. Перед будущим
native import передать управление origin SG/origin group/cert от legacy provisioning path;
его управление только CDN options/secrets оставить до отдельного решения. Два writer для
одного объекта недопустимы.

## Условные кандидаты на вывод

| Группа | Подтверждённое основание | Обязательное условие перед удалением |
| --- | --- | --- |
| Только origin VM зоны b `epdf6696opq3ock91pih` | Владелец прямо решил вывести эту VM из целевой конфигурации. В свежем снимке она STOPPED, но не удалена; прежние image-origin и public probe зависят от неё. | Будущий отдельный cloud план должен удалить только VM, сохранив boot disk `epd07hfr77d4a3un6n9d`, IP, SG, subnet, network и SA. `auto_delete=true` на boot disk требует до удаления устранить риск каскадной потери диска. Работоспособность доставки/probe после вывода не обещается; их восстановление планируется отдельно. Текущая задача не выполняет удаление. |
| Worker Instance Groups `cl13d5ffaml0f42s0s0s`, `cl1136g0efv0pl7u3d9a`, managed VM `epdja22rnci25shj6v5v` и диск `epdq58qmqik5f4pu6e2d` | ADR 0056 отменил отдельную worker fleet архитектуру; обе группы пока ACTIVE, одна VM STOPPED. | Отключить старый controller/CI и проверить очередь, данные, зависимости; удалять через группы, затем сверить каскадное удаление VM/диска. |
| Worker image `fd8kaqq1av9l38kt7kun`, Lockbox `e6qimj7tffu1d2ur06o4`, SA manager/runtime, worker subnet `e2lp2s94oh6cdl9uefj8`, SG `enphe9sidnbnrmfk49eu`, route table `enpu17uahu8el4k7jadk`, NAT gateway `enpkq1495sj7v645q2j8` | Относятся к отменённой fleet, но ещё существуют и могут иметь ссылки. | После удаления групп проверить image consumers, secrets, cross-folder IAM, SG peer refs, маршруты и NAT; Lockbox имеет deletion protection, снимать только отдельным решением. |
| `dev-network` `enplf0ljpuae7o5mu83q` и subnet `e9bt9lg5gv3d355ttsb4`, default subnets a/c | В повторном списке нет VM на них; default b и родитель default network остаются защищёнными по решению владельца, несмотря на план вывода одной VM. | Перед удалением повторить NIC, route, DNS и SG inventory. Не удалять default network или subnet b: они остаются в целевой конфигурации. |
| Weekly/monthly Backup policies без binding, standalone snapshot schedule | Связанных приложений не найдено в повторном policy list; само наличие не доказывает использование. | Сверить архивы, другие bindings, расписания и restore obligations отдельно. Не затрагивать daily policy, регистрацию и 15 точек восстановления. |

Старая `dev-photo-prjct` VM `epdr5g3p24tdns9890nr`, `findme-photo-app-3`
`fv4lac9q76a3m7209gjp` и их boot disks в свежем списке отсутствуют; API старой VM
вернул NotFound. Это исторические записи, **не кандидаты на повторное удаление и не
ресурсы для создания заново**. Причина исчезновения здесь не установлена. Legacy holder
отсутствует в свежем remote state serial 8; task не делал `state rm` и не изменял state.

Ни один пункт документа не разрешает `destroy` или расширение IAM. Сначала получить
актуальный снимок, проверить владельца и зависимости, согласовать план, затем отдельно
выполнять действия. Проверка сохранности recovery data обязательна. Доступность публичной доставки и probe после вывода VM не подтверждена; восстановление функций — отдельная задача.
