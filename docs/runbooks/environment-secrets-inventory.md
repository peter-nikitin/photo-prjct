# Canonical deployment secrets inventory

[`deploy/environment-secrets.json`](../../deploy/environment-secrets.json) is the non-secret
schema authority for the single canonical application deployment. This inventory makes its
ownership and consumer boundaries reviewable; it never proves that a payload was populated or a
workflow ran. Этот manifest описывает только секреты application deployment. SSH-идентичности
Infrastructure хранятся отдельно и не входят в него.
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
| Application GitHub Environment | none | application workflow jobs have no `environment:` key |
| CI service account ID | `ajeaekiue94ogksguh0h` | `yc iam service-account get --id ajeaekiue94ogksguh0h --format json` |
| OIDC federation ID | `ajeula3gd46omgf9jiko` | `yc iam workload-identity oidc federation get --id ajeula3gd46omgf9jiko --format json` |

The resolver permits only these exact main-branch workflow identities:

- `peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/monitor-public-health.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/face-embedding-benchmark.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/deploy-image-origin.yml@refs/heads/main`
- `peter-nikitin/photo-prjct/.github/workflows/deploy-public-probe.yml@refs/heads/main`

It validates the full `workflow_ref` claim before exchange. A matching path from another repository
or ref is not authorized. For this application secret, every reader needs both exact-secret roles:
`lockbox.viewer` for metadata and access-binding view, and `lockbox.payloadViewer` for payload
retrieval. Do not replace those resource-level bindings with folder-wide access.

<a id="infrastructure-ssh-access"></a>
## SSH-идентичности Infrastructure

Для подготовки VM используются отдельные SSH-идентичности: технический пользователь `deploy` для
Infrastructure Actions и оператор `petrnikitin` для административного доступа. Эти секреты не
относятся к manifest приложения или к OIDC-идентичности main-branch Deploy.

| Секрет Lockbox | ID | Пользователь SSH | Доступ |
| --- | --- | --- | --- |
| `findme-photo-vm-deploy` | `e6q12ru8opfn2sl91so3` | `deploy` | CI Infrastructure |
| `findme-photo-vm-admin` | `e6qes463bl6r09r72580` | `petrnikitin` | Оператор; CI не имеет payload grant |

Каждый секрет содержит ровно три текстовых поля: `ssh_private_key`, `ssh_public_key` и
`ssh_user`. В GitHub Environment `infrastructure` хранятся только открытые ключи:
`INFRA_SSH_PUBLIC_KEY` и `INFRA_ADMIN_SSH_PUBLIC_KEY`. Приватные ключи не хранятся в Git,
GitHub Secrets, Terraform state/output, логах или артефактах. Приватный ключ `deploy` временно
материализуется в Actions в файл `0600` для SSH/Ansible; ключ оператора в Actions не передаётся.
Infrastructure Actions использует service account `ajema849qjho2lgjaqvo` с OIDC subject
`repo:peter-nikitin/photo-prjct:environment:infrastructure`. Ему выдаётся только ресурсная роль
`lockbox.payloadViewer` на `findme-photo-vm-deploy`; доступ к metadata Lockbox и управление IAM ему
не нужны. На операторском секрете CI payload grant не выдаётся.

Оператору нужен `lockbox.payloadViewer` на `findme-photo-vm-admin`. Инструкции по безопасному
получению ключа находятся в [процедуре оператора](environment-secrets.md#ssh-keys-infrastructure).

Существующая запись `VM_SSH_KEY` и её потребители приложения остаются отдельными; это не fallback
для Infrastructure.

## Manifest key schema and consumer projections

`local` permits the supported local launcher to materialize a key; it does not make a value
local-only. `VM_SSH_KEY` is binary and becomes only `VM_SSH_KEY_FILE`.

| Manifest key | Target | Type | Local | Consumers |
| --- | --- | --- | --- | --- |
| `SECRET_KEY` | `SECRET_KEY` | text | yes | `local-web`, `deploy` |
| `DB_PASSWORD` | `DB_PASSWORD` | text | no | `deploy` |
| `LETSENCRYPT_EMAIL` | `LETSENCRYPT_EMAIL` | text | no | `deploy` |
| `COMMERCE_ORDER_ACCESS_SIGNING_SECRET` | `COMMERCE_ORDER_ACCESS_SIGNING_SECRET` | text | no | `deploy` |
| `COMMERCE_POSTBOX_API_KEY_ID` | `COMMERCE_POSTBOX_API_KEY_ID` | text | no | `deploy` |
| `COMMERCE_POSTBOX_API_KEY_SECRET` | `COMMERCE_POSTBOX_API_KEY_SECRET` | text | no | `deploy` |
| `MEDIA_S3_ACCESS_KEY_ID` | `MEDIA_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy` |
| `MEDIA_S3_SECRET_ACCESS_KEY` | `MEDIA_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | `PRIVATE_MEDIA_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy`, `image-delivery-provision` |
| `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `PHOTO_PROCESSING_WORKER_TOKEN` | `PHOTO_PROCESSING_WORKER_TOKEN` | text | yes | `local-web`, `deploy` |
| `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID` | text | yes | `local-web`, `deploy` |
| `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY` | text | yes | `local-web`, `deploy` |
| `VM_SSH_KEY` | `VM_SSH_KEY_FILE` | binary | no | `deploy`, `remote-check`, `image-origin`, `public-probe-deploy` (transport only) |
| `GHCR_READ_TOKEN` | `GHCR_READ_TOKEN` | text | no | `deploy` |
| `YANDEX_MONITORING_API_KEY` | `YANDEX_MONITORING_API_KEY` | text | no | `public-monitor` |
| `GALLERY_CDN_TOKEN_SECRET` | `GALLERY_CDN_TOKEN_SECRET` | text | no | `local-web`, `deploy`, `image-delivery-provision` |
| `GALLERY_IMGPROXY_KEY` | `GALLERY_IMGPROXY_KEY` | text | no | `local-web`, `deploy`, `image-origin` |
| `GALLERY_IMGPROXY_SALT` | `GALLERY_IMGPROXY_SALT` | text | no | `local-web`, `deploy`, `image-origin` |
| `IMAGE_ORIGIN_HEADER_SECRET` | `IMAGE_ORIGIN_HEADER_SECRET` | text | no | `image-origin`, `image-delivery-provision` |
| `IMAGE_ORIGIN_S3_ACCESS_KEY_ID` | `IMAGE_ORIGIN_S3_ACCESS_KEY_ID` | text | no | `image-origin` |
| `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY` | `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY` | text | no | `image-origin` |

The fixed resolver boundary is:

```text
scripts/run-with-environment-secrets.py \
  --consumer <local-web|deploy|remote-check|public-monitor|image-origin|image-delivery-provision|public-probe-deploy> \
  --identity <yc|github-oidc> -- <child command>
```

It reads the active metadata and one matching payload version, validates every required key plus
any reviewed optional Commerce key that is present, and passes only the selected projection through
a mode-0600 file named by `FINDME_ENV_FILE`. Callers cannot select a secret, version, or
projection. The six gallery delivery entries are optional at generic projection time so the
application can dark-deploy with `gallery-cdn-images=off`; the image-origin runtime and provisioning
action reject a missing required subset before their own mutation. The Commerce signing and Postbox
keys may be absent only while the deployed Commerce worker remains disabled; an enabled deploy
rejects their absence before mutation.

## Application repository variables

Для application deployment несекретная конфигурация хранится в repository variables, а не в GitHub
Environment. Просматривать имена можно командой
`gh variable list --repo peter-nikitin/photo-prjct --json name,updatedAt`. Application workflow
читает переменные `VM_HOST`, `VM_USER`, `VM_SSH_KNOWN_HOSTS`, `DB_NAME`, `DB_USER`, `GHCR_USERNAME`
и публичную конфигурацию. Изменение схемы требует согласованно обновить manifest, workflow и
инвентарь; не создавайте альтернативный источник секретов.

## Lockbox entry ownership and rotation

This ledger assigns the current Lockbox entries only. It does not describe retired GitHub source
scopes or destinations. An owner coordinates the approved rotation and incident response; they are
not necessarily the only permitted reader.

| Lockbox entry | Owner | Rotation trigger |
| --- | --- | --- |
| `SECRET_KEY` | Application maintainer | Django signing-key incident or approved coordinated application rotation |
| `DB_PASSWORD` | Database maintainer | Database role rotation, suspected disclosure, or access removal |
| `LETSENCRYPT_EMAIL` | Edge maintainer | Certificate-account contact change or account recovery |
| `COMMERCE_ORDER_ACCESS_SIGNING_SECRET` | Commerce maintainer | Order grant signing-key rotation, suspected disclosure, or access-boundary change |
| `COMMERCE_POSTBOX_API_KEY_ID` | Commerce email maintainer | Postbox API-key rotation, service-account scope change, or suspected disclosure |
| `COMMERCE_POSTBOX_API_KEY_SECRET` | Commerce email maintainer | Postbox API-key rotation, service-account scope change, or suspected disclosure |
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
| `GALLERY_CDN_TOKEN_SECRET` | Gallery delivery maintainer | CDN token-key rotation, CDN replacement, or suspected disclosure |
| `GALLERY_IMGPROXY_KEY` | Image-origin maintainer | imgproxy verifier rotation, origin replacement, or suspected disclosure |
| `GALLERY_IMGPROXY_SALT` | Image-origin maintainer | imgproxy verifier rotation, origin replacement, or suspected disclosure |
| `IMAGE_ORIGIN_HEADER_SECRET` | Image-origin maintainer | CDN-origin authentication rotation, origin replacement, or suspected disclosure |
| `IMAGE_ORIGIN_S3_ACCESS_KEY_ID` | Private media maintainer | Image-origin credential rotation, policy scope change, or suspected disclosure |
| `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY` | Private media maintainer | Image-origin credential rotation, policy scope change, or suspected disclosure |

## Payload-version evidence ledger

After an approved operation, protected evidence may contain only secret ID, non-secret version ID,
key names, timestamps, operator identity, consumer, and sanitized stage code. It must never contain
a payload value, token, key material, response body, or temporary-file path.

The manual public-probe deployment projects only `VM_SSH_KEY`. Its probe runtime uses the
existing VM metadata identity; no Lockbox payload or API key is copied to the host package.
