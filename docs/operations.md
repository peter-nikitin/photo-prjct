# Operations

This index links reviewed operator procedures. A runbook describes a boundary and its evidence; it
does not authorize a live cloud, credential, or deployment mutation.

## Canonical deployment secrets

- [Environment secrets operator runbook](runbooks/environment-secrets.md)
- [Environment secrets inventory](runbooks/environment-secrets-inventory.md)

Эти документы описывают секреты развёртывания приложения: один manifest и один Lockbox secret
обслуживают потребителей приложения; VM и runtime не читают Lockbox. Для подготовки
инфраструктуры предназначены отдельные SSH-идентичности и отдельная граница Lockbox, описанные в
[инвентаре секретов](runbooks/environment-secrets-inventory.md#infrastructure-ssh-access).

## Other runbooks

- [Minimal monitoring](runbooks/minimal-monitoring.md)
- [Canonical deployment](runbooks/deployment.md)
- [Selfie-search log analysis](runbooks/selfie-search-log-analysis.md)
