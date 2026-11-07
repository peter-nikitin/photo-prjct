# Operations

This index links reviewed operator procedures. A runbook describes a boundary and its evidence; it
does not authorize a live cloud, credential, or deployment mutation.

## Canonical deployment secrets

- [Environment secrets operator runbook](runbooks/environment-secrets.md)
- [Environment secrets inventory](runbooks/environment-secrets-inventory.md)

Общий manifest описывает источники и потребителей секретов приложения и SSH. Resolver читает
секреты приложения и общую техническую SSH-пару из разных Lockbox secrets; VM и runtime
не читают Lockbox. Административная пара доступна только оператору. Границы доступа описаны в
[инвентаре секретов](runbooks/environment-secrets-inventory.md#infrastructure-ssh-access).

## Other runbooks

- [Minimal monitoring](runbooks/minimal-monitoring.md)
- [Canonical deployment](runbooks/deployment.md)
- [Selfie-search log analysis](runbooks/selfie-search-log-analysis.md)
