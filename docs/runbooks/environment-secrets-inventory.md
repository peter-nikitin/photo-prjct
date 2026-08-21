# Canonical deployment secrets inventory

[`deploy/environment-secrets.json`](../../deploy/environment-secrets.json) is the non-secret
schema authority for the single canonical deployment. This inventory makes its ownership and
consumer boundaries reviewable; it never proves that a payload was populated or a workflow ran.
Never record a payload, credential fragment, Lockbox version ID, or resolved environment file in
the repository, a ticket, command line, CI output, or shell history.

## Stable deployment identity ledger

| Item | Reviewed value | Operator check |
| --- | --- | --- |
| Deployment | Canonical customer-serving VM | [deployment runbook](deployment.md) |
| Lockbox secret ID | `e6q85jjl76r45maigtfb` | `yc lockbox secret get --id e6q85jjl76r45maigtfb --format json` |
| Folder ID | `b1g2qttgfhb4gdunvlge` | `yc resource-manager folder get --id b1g2qttgfhb4gdunvlge --format json` |
| GitHub OIDC issuer | `https://token.actions.githubusercontent.com` | Read-only federation and resolver check |
| GitHub OIDC audience | `https://github.com/peter-nikitin` | Read-only federation and resolver check |
| GitHub OIDC subject | `repo:peter-nikitin/photo-prjct:ref:refs/heads/main` | Read-only federated-credential check |
| GitHub repository/ref | `peter-nikitin/photo-prjct` / `refs/heads/main` | Workflow checkout and OIDC claim check |
| Workflow | `Deploy` / `.github/workflows/deploy.yml` | `gh workflow view deploy.yml` |
| Compose project | `photo-prjct` | deployed-VM Compose health command |
| GitHub Environment | none | workflow jobs have no `environment:` key |
| CI service account ID | `ajeaekiue94ogksguh0h` | `yc iam service-account get --id ajeaekiue94ogksguh0h --format json` |
| OIDC federation ID | `ajeula3gd46omgf9jiko` | `yc iam workload-identity oidc federation get --id ajeula3gd46omgf9jiko --format json` |

The resolver permits only these exact main-branch workflow identities:

- `peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/monitor-public-health.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/face-embedding-benchmark.yml@refs/heads/main`

It validates the full `workflow_ref` claim before exchange. A matching path from another repository
or ref is not authorized. Every reader needs both exact-secret roles: `lockbox.viewer` for metadata
and access-binding view, and `lockbox.payloadViewer` for payload retrieval. Do not replace those
resource-level bindings with folder-wide access.

## Manifest key schema and consumer projections

`local` permits the supported local launcher to materialize a key; it does not make a value
local-only. `VM_SSH_KEY` is binary and becomes only `VM_SSH_KEY_FILE`.

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

The fixed resolver boundary is:

```text
scripts/run-with-environment-secrets.py \
  --consumer <local-web|deploy|remote-check|public-monitor> \
  --identity <yc|github-oidc> -- <child command>
```

It reads the active metadata and one matching payload version, validates the complete key set, and
passes only the selected projection through a mode-0600 file named by `FINDME_ENV_FILE`. Callers
cannot select a secret, version, or projection.

## Repository variables

Non-secret configuration belongs to repository variables, not a GitHub Environment. Inspect names
only with `gh variable list --repo peter-nikitin/photo-prjct --json name,updatedAt`. The current
workflow reads repository variables such as `VM_HOST`, `VM_USER`, `VM_SSH_KNOWN_HOSTS`, `DB_NAME`,
`DB_USER`, `GHCR_USERNAME`, and public/processing configuration. Any schema change needs a reviewed
manifest, workflow, and inventory update; do not create an alternate secret authority.

## Lockbox entry ownership and rotation

This ledger assigns the current Lockbox entries only. It does not describe retired GitHub source
scopes or destinations. An owner coordinates the approved rotation and incident response; they are
not necessarily the only permitted reader.

| Lockbox entry | Owner | Rotation trigger |
| --- | --- | --- |
| `SECRET_KEY` | Application maintainer | Django signing-key incident or approved coordinated application rotation |
| `DB_PASSWORD` | Database maintainer | Database role rotation, suspected disclosure, or access removal |
| `LETSENCRYPT_EMAIL` | Edge maintainer | Certificate-account contact change or account recovery |
| `MEDIA_S3_ACCESS_KEY_ID` | Media storage maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `MEDIA_S3_SECRET_ACCESS_KEY` | Media storage maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | Private media maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | Private media maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `PHOTO_PROCESSING_WORKER_TOKEN` | Processing maintainer | Worker-token rotation, access removal, or suspected disclosure |
| `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | Selfie feedback maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | Selfie feedback maintainer | Object Storage key rotation, scope change, or suspected disclosure |
| `VM_SSH_KEY` | Deployment operations maintainer | SSH key rotation, lost runner access, VM replacement, or suspected disclosure |
| `GHCR_READ_TOKEN` | Registry maintainer | Token expiry, scope change, suspected disclosure, or registry access removal |
| `YANDEX_MONITORING_API_KEY` | Monitoring maintainer | Monitoring API-key rotation, scope change, or suspected disclosure |

## Payload-version evidence ledger

After an approved operation, protected evidence may contain only secret ID, non-secret version ID,
key names, timestamps, operator identity, consumer, and sanitized stage code. It must never contain
a payload value, token, key material, response body, or temporary-file path.
