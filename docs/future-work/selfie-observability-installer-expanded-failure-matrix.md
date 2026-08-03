# Selfie observability installer expanded failure matrix

## Observed gap

The repository contract covers the critical first-install, no-op, invalid-candidate, transactional
multi-file activation, signal/failure rollback, prior timer state, verification, and exact managed-
file rollback boundaries. It does not exhaust every localized `journalctl` disk-usage rendering or
every Docker/systemd diagnostic-output wording.

## Why this is non-blocking now

Diagnostics are deliberately sanitized to stable repository-owned messages and no operator flow
parses their prose. The omitted wording variants do not affect mutation, rollback, or acceptance.

## Trigger

Expand the harness when a supported host locale changes the machine-parsed disk-size token or a
Docker/systemd upgrade changes an inspected field rather than only human-readable wording.
