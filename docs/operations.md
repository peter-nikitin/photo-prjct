# Operations

This index links reviewed operator procedures. A runbook describes a boundary and its evidence; it
does not authorize a live cloud, credential, or deployment mutation.

## Canonical deployment secrets

- [Environment secrets operator runbook](runbooks/environment-secrets.md)
- [Environment secrets inventory](runbooks/environment-secrets-inventory.md)

The current scope is the canonical deployment. One manifest and Lockbox authority serve the
repository consumers; repository variables hold non-secret configuration, and neither the VM nor
the runtime reads Lockbox.

## Other runbooks

- [Minimal monitoring](runbooks/minimal-monitoring.md)
- [Canonical deployment](runbooks/deployment.md)
- [Selfie-search log analysis](runbooks/selfie-search-log-analysis.md)
