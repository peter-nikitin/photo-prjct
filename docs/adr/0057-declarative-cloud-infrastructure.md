# 0057: Описывать инфраструктуру в IaC и применять проверенный план

- Status: Accepted
- Date: 2026-10-25
- Deciders: владелец проекта
- Supersedes: none

## Контекст

У системы не было проверяемого описания облачных ресурсов. Ручные изменения затрудняют
повторное создание VM и понимание влияния изменений. Первый этап должен создать одну VM и
подготовить ОС, не запуская приложение или восстановление данных.

## Мотивы

- Хранить желаемое состояние инфраструктуры в Git.
- Рассматривать действия и оценку стоимости до применения.
- Использовать стандартный учёт ресурсов и механизм plan/apply.

## Решение

Terraform — источник истины для желаемого состояния ресурсов и стандартный механизм сравнения
с облаком. Выбранный provider определяет API и типы ресурсов. Ansible подготавливает ОС.
Приложение и пользовательские данные остаются за отдельными границами и не запускаются этим
первым этапом.

### Состояние и разрешения

State хранится вне VM в закрытом versioned S3 bucket с блокировкой. Конфигурация, версии
инструментов и несекретные параметры находятся в Git; секреты, state и сохранённые планы — вне
Git. Backend service account ограничен объектами state и lock:

```text
core/single-vm/terraform.tfstate: GetObject, PutObject, GetObjectVersion
core/single-vm/terraform.tfstate.tflock: GetObject, PutObject, DeleteObject
```

CI не может удалить версию state. Удаление объекта разрешено только для lock-файла. Доступ к
bucket задан ACL и ограничительной policy; bootstrap identity не имеет folder role.
Существенная часть фактической policy (CanonicalUser — backend service account):

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {"CanonicalUser": "ajeonsj2akeimj92fokf"},
      "Action": ["s3:GetObject", "s3:PutObject", "s3:GetObjectVersion"],
      "Resource": "arn:aws:s3:::findme-photo-tfstate/core/single-vm/terraform.tfstate"
    },
    {
      "Effect": "Allow",
      "Principal": {"CanonicalUser": "ajeonsj2akeimj92fokf"},
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::findme-photo-tfstate/core/single-vm/terraform.tfstate.tflock"
    }
  ]
}
```

### Выпуск

CI проверяет Terraform без backend и cloud identity. При Terraform-изменениях PR создаёт
сохранённый plan, безопасный diff и оценку стоимости. Уполномоченный сотрудник подтверждает
конкретный run; слияние применяет только этот сохранённый plan. Новый commit требует нового
plan и подтверждения. PR без изменений Terraform получает успешный статус `infra/approved`
со значением «не требуется», чтобы обязательная проверка не оставалась ожидающей.

После применения Terraform Ansible настраивает ОС. Этот этап не строит образы, не размещает
runtime package, не запускает приложение и не восстанавливает данные. Изменения только Ansible
не запускают Terraform apply. Docs-only и app-only не запускают инфраструктурный процесс.

## Фактический bootstrap

Создано 2026-10-29; Terraform apply и VM не выполнялись:

- Cloud `findme-photo`, ID `b1gmcsmr51o5kvp86l55`; целевой folder `core`,
  ID `b1g2qttgfhb4gdunvlge`.
- State bucket `findme-photo-tfstate`, ID `e3efvlu4k2hg92fsg0pg`, Standard storage,
  versioning включён.
- Backend service account `ajeonsj2akeimj92fokf`; CI service account
  `ajema849qjho2lgjaqvo`.
- OIDC federation `aje2m3i6k60tdau0u9vk` и credential `ajeo1rvk92qc8s7vnjd6`
  ограничены repository environment `infrastructure`.
- CI роли: `compute.editor`, `vpc.privateAdmin`, `vpc.securityGroups.admin`,
  `vpc.publicAdmin`, `iam.serviceAccounts.admin`, `resource-manager.admin`.

Bucket policy разрешает backend identity только читать версионирование bucket, перечислять
префиксы `core/single-vm/*` и `core/lock-probe/*`, читать/записывать текущий state и читать его
версии. `DeleteObject` для state запрещён; он разрешён только для `.tflock` объектов. Доступ к
lock-probe предназначен для проверки реальной блокировки до первого apply.

## Последствия

Изменение ресурсов можно проверить до применения, а устаревший plan или занятый state lock
останавливают выпуск. Bootstrap backend и identity выполняется отдельно один раз. Успешная
проверка кода не доказывает создание VM, восстановление базы или запуск приложения; эти факты
подтверждаются отдельными операционными свидетельствами.

## Проверка

Terraform format, offline validation и mock tests проходят в PR. Для resource change plan и
оценка стоимости должны быть рассмотрены до подтверждения. После merge CI применяет сохранённый
plan и подготавливает ОС Ansible; workflow не содержит сборки или старта приложения.

## Связанные решения

- [Недостающее покрытие оценки стоимости](../future-work/2026-10-25-yc-cost-estimation-resource-coverage.md)
- [Terraform providers](https://developer.hashicorp.com/terraform/language/providers)
