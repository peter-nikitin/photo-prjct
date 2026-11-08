# Конфигурация дашборда

Этот каталог нужен разработчику, который меняет графики или код их публикации в Yandex
Monitoring. Устройство сбора и хранения метрик описано в
[архитектуре](../../../docs/architecture.md#observability), подтверждённый статус — в
[инженерном реестре](../../../docs/engineering-jobs.md#ej-009--operator--detect-service-degradation).

## Текущие точки входа

- `single-vm-dashboard.json` — графики Linux, Django и PostgreSQL для одной VM.
- `single_vm_dashboard.py` — проверка конфигурации и обновление существующего дашборда
  независимо от свежести метрик. Совпадающий дашборд не перезаписывается.
- `verify_metrics.py` — проверка свежих исходных серий VM или приложения после их выпуска.
- `environment.json` — идентификаторы каталога, workspace и дашборда.
- `oidc.json` — настройки доступа workflow через GitHub OIDC.
- `control.py` — используемые скриптом функции доступа к API и проверки данных.
- `requirements.txt` — отдельные зависимости инструментов мониторинга; они не входят
  в зависимости Django и обработчиков.

## Как доставляются изменения

[Workflow Monitoring](../../../.github/workflows/monitoring.yml) проверяет изменения в PR:
структуру дашборда через SDK и синтаксис PromQL через `promtool`. После merge изменения
дашборда применяются автоматически независимо от выпуска источников. Infrastructure проверяет
метрики VM после настройки ОС, Deploy проверяет Django и PostgreSQL после выпуска приложения
или exporter. Сбой проверки виден в соответствующем выпуске. Резервная копия
изменяемого дашборда сохраняется в артефактах workflow.

Сборщики находятся отдельно: конфигурация Unified Agent — в
[роли Ansible](../../../infra/ansible/roles/monitoring/), PostgreSQL exporter — в
[Compose](../../../docker-compose.deployment.yml) и
[его каталоге](../../postgres-monitoring/).

Для повторной проверки выберите scope `vm` или `application` в Actions → **Verify metrics**.
Проверка читает сырые выборки через Prometheus Remote Read, без изменения облачных ресурсов.
