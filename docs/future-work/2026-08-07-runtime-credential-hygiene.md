# Runtime credential hygiene and recovery

## Observed gap

A sanitized read-only staging inspection on 2026-08-07 confirmed that the current deployment
persists credentials across several host and container surfaces:

- `/opt/photo-prjct/.env` is a `deploy:deploy` mode-0600 file containing populated Django,
  PostgreSQL, Object Storage, processing, and feedback credential fields;
- Docker stores service environment values in container configuration metadata: the web container
  receives the deployment environment, PostgreSQL receives its password, and workers receive their
  API token;
- membership of the root-equivalent `docker` group currently includes `petrnikitin`, `deploy`,
  `ubuntu`, and `codex`, so mode `0600` on the deployment environment is not the effective access
  boundary for those identities;
- `/home/deploy/.docker/config.json` is a mode-0600 persistent GHCR authentication file without a
  credential helper;
- `/home/petrnikitin/.bash_history` contains two secret-assignment command shapes involving
  `DB_PASSWORD` and `SECRET_KEY`; values were not displayed or copied during inspection;
- the Certbot volume contains the required root-owned mode-0600 TLS private key; and
- any system-disk snapshot or full backup would inherit these files and Docker metadata, but current
  snapshot/image/schedule state could not be verified because the local `yc` authentication had
  expired.

No stale `.env.previous`, `.env.requested`, or `.env.recovery` file was present. SSH private keys
were not found on the VM; inspected SSH files were public `authorized_keys`. The Unified Agent
configuration exposed no API-key, OAuth-token, IAM-token, or service-account authentication field
name. VM metadata exposed no application-secret field names. An attached service-account endpoint
was visible but did not return a usable identity through the sanitized probe, so its current IAM
boundary remains unverified.

The source path is explicit in the repository: `deploy/apply-deployment.sh` writes a protected
candidate file and promotes it to the canonical `.env`; `docker-compose.prod.yml` consumes that
file for the web service and expands selected values into database and worker environments. Moving
the upstream authority from GitHub Secrets to Yandex Lockbox does not by itself change this runtime
storage model.

## Why it is non-blocking

The current EJ-017 increment establishes one authoritative environment secret source, local access,
GitHub OIDC authentication, retrieval validation, and migration away from GitHub Secrets. It can
retain the existing VM/runtime delivery contract without making current exposure worse.

The observed canonical environment is restricted to `deploy`, Docker data and TLS keys are
root-owned, temporary deployment copies are cleaned during normal execution, and staging currently
has no separate production environment or accepted runtime-secret hardening requirement. Selecting
one runtime mechanism now would prematurely couple the Lockbox migration to restart, rollback,
Compose, Certbot, backup, and disaster-recovery decisions that need their own design.

This classification does not treat the shell-history observation as safe. Before any reuse of the
affected values outside disposable staging, verify whether the assignments contain current real
credentials and rotate them under a separately approved containment action. Deleting history alone
is not containment because other copies may exist.

## Revisit trigger

Prepare and approve a dedicated EJ-018 specification at the earliest of:

- completion of the EJ-017 Lockbox migration, before declaring the end-to-end secret lifecycle
  validated;
- preparation of the first production environment or promotion of the current VM to production;
- introduction of VM disk snapshots, images, backup automation, or restore drills;
- addition of another human or automation identity to `docker`, sudo, SSH, or cloud administration;
- evidence that a real credential value was retained in shell history, logs, process arguments,
  artifacts, or an unexpected persistent file; or
- a credential exposure, lost-device, lost-account, host-compromise, or recovery incident.

Production preparation is a hard trigger: the first production deployment must not silently inherit
the current staging credential topology without an accepted EJ-018 design and validation evidence.

## Likely scope

Start with a fresh read-only inventory across repository workflows, the live host, Docker metadata,
VM metadata/service-account IAM, volumes, snapshots/images/backups, operator accounts, and recovery
procedures. The specification must then select and reconcile:

- the minimum credential projection for web, database, each worker, Nginx, Certbot, monitoring, and
  deployment tooling;
- whether runtime values use persistent protected files, Docker secrets/configs, file-based
  application settings, tmpfs materialization, runtime Lockbox retrieval, or another supported
  mechanism;
- restart behavior when GitHub, Yandex IAM, or Lockbox is unavailable;
- registry login without an indefinitely retained reusable token;
- Docker/sudo/SSH membership and the effective root-equivalent trust boundary;
- TLS-key ownership, renewal, backup, and recovery;
- snapshot and backup inclusion, encryption, access, retention, restore, and credential rotation;
- secret-safe shell/operator workflows and containment of historical exposure;
- lost-device, lost-account, break-glass, rotation, revocation, rollback, and disaster-recovery
  drills; and
- automated non-disclosure, permissions, restart, rollback, restore, and live-environment evidence.

Treat this as an architecture and operations capability, not a cleanup script. Do not mutate or
delete host state merely to satisfy an inventory check; any history cleanup, access removal,
credential rotation, Docker reconfiguration, snapshot change, or runtime migration requires its own
approved plan and live safety gates.
