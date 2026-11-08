# Operations

This index links reviewed operator procedures. A runbook describes a boundary and its evidence; it
does not authorize a live cloud, credential, or deployment mutation.

## Canonical deployment secrets

- [Environment secrets operator runbook](runbooks/environment-secrets.md)

Устройство хранения секретов и разделение доступа приложения, CI и оператора описаны в
[архитектуре](architecture.md#secrets-and-ssh-access). Точная схема секретов приложения находится
в [manifest](../deploy/environment-secrets.json), а порядок получения SSH-ключа — в
[инструкции оператора](runbooks/environment-secrets.md#ssh-keys-infrastructure).

## Other runbooks

- [Исторический минимальный мониторинг](runbooks/minimal-monitoring.md)
- [Canonical deployment](runbooks/deployment.md)
- [Selfie-search log analysis](runbooks/selfie-search-log-analysis.md)
