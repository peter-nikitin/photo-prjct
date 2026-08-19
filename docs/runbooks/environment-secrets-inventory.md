# Staging environment secrets inventory

This is the reviewed, non-secret inventory for the one logical `staging` environment. The
manifest at [`deploy/environment-secrets.json`](../../deploy/environment-secrets.json)
is the schema authority; this file makes its operational ownership visible. It is not evidence
that the payload has been populated or that any workflow has run.

No payload value, payload export, credential fragment, or Lockbox version ID belongs in this
repository, a ticket, command line, CI output, or shell history.

## Stable deployment identity ledger

| Item | Reviewed value | Operator check |
| --- | --- | --- |
| Logical environment | `staging` | `deploy/environment-secrets.json` |
| Lockbox secret ID | `e6q85jjl76r45maigtfb` | `yc lockbox secret get --id e6q85jjl76r45maigtfb --format json` |
| Folder ID | `b1g2qttgfhb4gdunvlge` | `yc resource-manager folder get --id b1g2qttgfhb4gdunvlge --format json` |
| GitHub OIDC issuer | `https://token.actions.githubusercontent.com` | Read-only federation and resolver check |
| GitHub OIDC audience | `https://github.com/peter-nikitin` | Read-only federation and resolver check |
| GitHub OIDC subject | `repo:peter-nikitin/photo-prjct:environment:staging` | Read-only federated-credential check |
| GitHub repository | `peter-nikitin/photo-prjct` | Workflow checkout and OIDC claim check |
| GitHub Environment | `staging` | GitHub job `environment: staging` |
| CI service account ID | `ajeaekiue94ogksguh0h` | `yc iam service-account get --id ajeaekiue94ogksguh0h --format json` |
| OIDC federation ID | `ajeula3gd46omgf9jiko` | `yc iam workload-identity oidc federation get --id ajeula3gd46omgf9jiko --format json` |

Allowed GitHub workflow identities are:

- `peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/monitor-public-health.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/face-embedding-benchmark.yml@refs/heads/main`

The resolver exactly enforces the GitHub OIDC `workflow_ref` claim against this full list before
exchanging the OIDC token. A matching path without the reviewed repository or `refs/heads/main`
is not an authorized workflow identity.

The resolver reads this exact secret's metadata before it reads the matching payload. Every reader
identity therefore needs both resource-level roles on this exact secret: `lockbox.viewer` and
`lockbox.payloadViewer`. `lockbox.viewer` provides metadata and access-binding view, but grants
neither payload access nor secret management. `lockbox.payloadViewer` permits the payload read.
Do not substitute folder-wide bindings.

The CI service account and approved human reader already have both exact-secret roles. This live
binding readback neither performs an IAM mutation nor declares the rollout complete. CI and human
readers receive no Lockbox-management, Compute, IAM, KMS, or Object Storage role. Human account IDs
and the federated-credential ID are operational evidence, not repository constants.

## Manifest key schema and consumer projections

`local` means a key may be materialized to the supported local-capable local launcher; it does
not mean that a value is local-only. `VM_SSH_KEY` is binary and materializes only as the private
path target `VM_SSH_KEY_FILE`.

| Manifest key | Target | Type | Local | Consumers |
| --- | --- | --- | --- | --- |
| `SECRET_KEY` | `SECRET_KEY` | text | yes | `local-web`, `deploy` |
| `DB_PASSWORD` | `DB_PASSWORD` | text | no | `deploy` |
| `LETSENCRYPT_EMAIL` | `LETSENCRYPT_EMAIL` | text | no | `deploy` |
| `MEDIA_S3_ACCESS_KEY_ID` | `MEDIA_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy` |
| `MEDIA_S3_SECRET_ACCESS_KEY` | `MEDIA_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy` |
| `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `PHOTO_PROCESSING_WORKER_TOKEN` | `PHOTO_PROCESSING_WORKER_TOKEN` | text | yes | `local-web`, `deploy` |
| `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy` |
| `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `VM_SSH_KEY` | `VM_SSH_KEY_FILE` | binary | no | `deploy`, `remote-check` |
| `GHCR_READ_TOKEN` | `GHCR_READ_TOKEN` | text | no | `deploy` |
| `YANDEX_MONITORING_API_KEY` | `YANDEX_MONITORING_API_KEY` | text | no | `public-monitor` |

The resolver command boundary is fixed:

```text
scripts/run-with-environment-secrets.py \
  --consumer <local-web|deploy|remote-check|public-monitor> \
  --identity <yc|github-oidc> -- <child command>
```

It reads the active metadata and exactly one matching version, validates the complete key set, and
passes only the selected projection through a mode-0600 file named by the non-secret
`FINDME_ENV_FILE` variable. Callers cannot supply a secret ID, version ID, or projection override.

## GitHub staging secret migration inventory

This table records every former GitHub Actions secret used by staging, with the exact GitHub source
scope that must be cleaned up. “Owner” is accountable for rotation and incident coordination, not
necessarily the only person permitted to retrieve the value. The six migrated configuration values
below are intentionally moved to visible `staging` GitHub Environment variables; they are not
Lockbox payload entries.

| Former GitHub Actions Secret | Source scope | Owner | Destination | Rotation trigger |
| --- | --- | --- | --- | --- |
| `ALLOWED_HOSTS` | repository | Application maintainer | `staging` GitHub Environment variable | Staging hostname or approved ingress policy changes |
| `DB_NAME` | repository | Database maintainer | `staging` GitHub Environment variable | Database topology or database-name migration |
| `DB_PASSWORD` | repository | Database maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `DB_PASSWORD` | Database role rotation, suspected disclosure, or access removal |
| `DB_USER` | repository | Database maintainer | `staging` GitHub Environment variable | Database role-name migration or access-policy change |
| `GHCR_READ_TOKEN` | repository | Registry maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `GHCR_READ_TOKEN` | Token expiry, scope change, suspected disclosure, or registry access removal |
| `GHCR_USERNAME` | repository | Registry maintainer | `staging` GitHub Environment variable | Registry principal rename or ownership transfer |
| `LETSENCRYPT_EMAIL` | repository | Edge maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `LETSENCRYPT_EMAIL` | Certificate-account contact change or account recovery |
| `MEDIA_S3_ACCESS_KEY_ID` | repository | Media storage maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `MEDIA_S3_ACCESS_KEY_ID` | Object Storage key rotation, scope change, or suspected disclosure |
| `MEDIA_S3_SECRET_ACCESS_KEY` | repository | Media storage maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `MEDIA_S3_SECRET_ACCESS_KEY` | Object Storage key rotation, scope change, or suspected disclosure |
| `PHOTO_PROCESSING_WORKER_TOKEN` | staging Environment | Processing maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `PHOTO_PROCESSING_WORKER_TOKEN` | Worker-token rotation, access removal, or suspected disclosure |
| `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | repository | Private media maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | Object Storage key rotation, scope change, or suspected disclosure |
| `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | repository | Private media maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | Object Storage key rotation, scope change, or suspected disclosure |
| `SECRET_KEY` | repository | Application maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `SECRET_KEY` | Django signing-key incident or an approved coordinated application rotation |
| `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | staging Environment | Selfie feedback maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | Object Storage key rotation, scope change, or suspected disclosure |
| `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | staging Environment | Selfie feedback maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | Object Storage key rotation, scope change, or suspected disclosure |
| `VM_HOST` | repository | Staging operations maintainer | `staging` GitHub Environment variable | Staging VM replacement, address change, or DNS cutover |
| `VM_SSH_KEY` | repository | Staging operations maintainer | Lockbox `e6q85jjl76r45maigtfb` binary entry `VM_SSH_KEY` | SSH key rotation, lost runner access, host replacement, or suspected disclosure |
| `VM_USER` | repository | Staging operations maintainer | `staging` GitHub Environment variable | Staging deploy-account rename or host-access policy change |
| `YANDEX_MONITORING_API_KEY` | staging Environment | Monitoring maintainer | Lockbox `e6q85jjl76r45maigtfb` entry `YANDEX_MONITORING_API_KEY` | Monitoring API-key rotation, scope change, or suspected disclosure |

`GITHUB_TOKEN` is not migrated: GitHub issues it job-scoped for GitHub-native GHCR publication. It
must not be stored in Lockbox or listed as a repository or GitHub Environment Secret to delete.

## Required non-secret staging Environment variables

The exact required non-secret `staging` GitHub Environment variable-name set is:

- `ALLOWED_HOSTS`
- `DB_NAME`
- `DB_USER`
- `GHCR_USERNAME`
- `VM_SSH_KNOWN_HOSTS`
- `VM_HOST`
- `VM_USER`

`VM_SSH_KNOWN_HOSTS` is a required non-secret `staging` GitHub Environment variable containing
the reviewed SSH host-key record. It is neither a Lockbox entry nor a migrated GitHub Secret.

## Payload-version evidence ledger

No Lockbox payload or version ID is recorded in this repository. After an approved live operation,
the protected incident/change record may contain only: secret ID, non-secret version ID, all key
names, creation and activation timestamps, operator identity, consumer name, and sanitized
success/failure stage code. It must never contain a payload value, response body, token, key
material, environment-file path after cleanup, or copied command output.

Use the operator procedure in
[the environment secrets runbook](environment-secrets.md) to create that evidence. The current
staging installation may later become production only through a separate decision. No production
secret, production IAM boundary, or production runtime Lockbox reader is defined here.
