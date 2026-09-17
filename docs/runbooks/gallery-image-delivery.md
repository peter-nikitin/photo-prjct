# Gallery image delivery rollout

This runbook prepares the accepted CDN/imgproxy design without combining application deployment,
cloud provisioning, access changes, or feature activation into one authority. The isolated origin
is replaceable and owns no database, durable transform cache, extra data disk, or application
service. Approval of the implementation plan is not approval for any Yandex Cloud, IAM, Object
Storage, Lockbox, DNS, certificate, CDN, or production mutation.

## Fixed order and stop conditions

1. Merge and deploy the application with `gallery-cdn-images=off`, the canonical non-secret
   `GALLERY_CDN_ORIGIN=https://img.findme-photo.ru`, no signing secrets required, and no image
   origin, DNS, certificate, or CDN resource. Complete the dark-deployment checks below and stop if
   any existing path changes.
2. **Provision the reviewed resources** only after refreshing the exact profile, cloud, folder,
   canonical VM, VPC/subnet, bucket policy, quotas, and official pricing. Present the machine plan,
   normalized policy diff, stable resource IDs, monthly price delta, exact command, validation, and
   rollback, then obtain fresh manual approval. A reviewed nonce is valid only for that exact plan.
3. **Validate the origin, credentials, and CDN gates** while the feature remains off. The dedicated
   credential must allow one accepted `derivatives/previews/*` GET and deny bucket listing,
   originals, staging, writes, multipart creation, ACL changes, and deletes. Valid and altered CDN
   tokens, fixed cache identity, authenticated origin access, TLS, DNS, rollback, and monitoring
   datapoints must also pass.
4. The exact native **S3 403** signal remains a **Task 6 staff-activation blocker**. It is not a
   configured alert and must not be described as one until a real bounded-cardinality provider or
   native imgproxy signal has been proven.
5. Enable `gallery-cdn-images=staff` only after every preceding gate is green. Validate one free and
   one paid gallery and rehearse rollback to `off` before restoring staff mode.
6. Run the accepted cold, warm, and saturation cohort. Enable `gallery-cdn-images=on` only after the
   performance, policy, credential, visual, application-capacity, and worker-throughput gates pass.

Any failed policy, credential, origin, CDN, visual, performance, or isolation gate is a no-go. Keep
the feature `staff` or `off`; do not resize the VM or enable paid CDN options without a reviewed
plan update and new cost approval.

## Dark application deployment

This boundary deploys application support only. It does not authorize provisioning, Lockbox
mutation, DNS or certificate changes, origin/CDN contact, or feature activation. The deployment
resolver may omit `GALLERY_CDN_TOKEN_SECRET`, `GALLERY_IMGPROXY_KEY`, and
`GALLERY_IMGPROXY_SALT`; the web container receives empty values and every worker, importer,
database, Nginx, and Certbot container remains outside that projection.

Before the application workflow starts, confirm there is no delivery resource and that the
code-owned `gallery-cdn-images` definition will reconcile to `off`. If an earlier partial rollout
already created the row in another state, change it to `off` through Django Admin before deploying;
the deployment itself must never activate a feature flag. Deploy the exact reviewed application
commit through the canonical workflow without any image-origin workflow or provisioning command.

After deployment, record one bounded, read-only evidence set:

1. the requested commit SHA equals `/opt/photo-prjct/deployed-image` and the running web image;
2. the canonical web, database, application Nginx, Certbot, bulk worker, selfie worker, Commerce
   worker when enabled, and import worker when enabled retain their expected health and topology;
3. the reconciled `gallery-cdn-images` row is exactly `off`;
4. a fresh normal gallery page on the original public HTTPS route contains only the existing direct
   accepted-preview capabilities and no `img.findme-photo.ru` URL;
5. the processing queue advances across two fresh observations, and PostgreSQL activity shows no
   new blocking or sustained gallery query;
6. application and worker VM CPU, memory, disk, and container restart counts remain within their
   pre-deploy bounds; and
7. a fresh post-deploy error window contains no new gallery, signing, feature-flag, worker,
   importer, database, or storage error.

If any check differs from the pre-deploy baseline, set or keep the flag `off`, use the established
prior-image rollback, and repeat the same evidence set. Do not provision around a failed dark
deployment. Only after every check is green may the operator refresh discovery, inventory, quotas,
and pricing, present the exact resource plan, and seek fresh approval for the separately bounded
provisioning operation.

## Repository-only dry run

The first read-only plan accepts only three complete Lockbox states: none of the six gallery keys,
exactly the two bootstrap keys, or all six. One key or any three-to-five-key partial state is a
stop. For an empty gallery-key set, the plan contains only a `secret-bootstrap` phase. After fresh
approval, that phase generates and patches exactly `GALLERY_CDN_TOKEN_SECRET` and
`IMAGE_ORIGIN_HEADER_SECRET` into a new version. It refetches secret metadata immediately before
the mutation and stops if the current version differs from the reviewed version, then submits only
those two payload-entry changes with the reviewed base-version ID. Lockbox inherits unchanged text
and binary entries; the provisioner never reads or rewrites their values. This metadata recheck is
a best-effort drift guard, not a provider compare-and-swap guarantee. Stop and obtain a new dry run
and approval nonce before resource identities. The later initialization adds only the imgproxy
key/salt and S3 credential pair. Reapplying an initialized state never rotates keys.

The CDN token is 6-32 URL-safe characters and the origin header is 16-128 characters from
`[A-Za-z0-9_-]`. Neither belongs in the non-secret contract file, dry-run plan, command output, or
GitHub logs. The current CDN CLI accepts these settings only as create/update flags, so approved
CDN apply must run on the isolated trusted operator host through the protected projection; never
copy the rendered command or inspect another process's arguments during that bounded operation.

Run every approved apply through the real protected projection, never by creating a private dotenv
file manually:

```sh
.venv/bin/python scripts/run-with-environment-secrets.py \
  --consumer image-delivery-provision --identity yc -- \
  sh deploy/image-origin/provision.sh --apply --approval-nonce '<reviewed-plan-nonce>'
```

Populate only the non-secret values described by
`deploy/image-origin/cloud-contract.env.example`. The provisioner obtains the current bucket policy
from Object Storage, binds its normalized before/after forms to the plan nonce, and refetches it
immediately before mutation. Secret inputs come from the narrowly scoped resolver projections.

Run:

```sh
sh deploy/image-origin/provision.sh > image-origin-plan.json
```

The JSON resolves the cloud, folder, canonical network/subnet/bucket, existing named resource IDs,
policy before/after diff, credential probe matrix, exact proposed commands, Task 6 inventory/quota
refresh, and pricing items. The `approval_nonce` is the SHA-256 digest of the canonical plan without
the nonce field. Dry-run performs only read-only discovery.

Quota refresh is cloud-scoped and service-specific. Use `resource-manager.cloud` with the resolved
cloud ID and run one `quota-limit list` for each of `compute`, `vpc`, and `cdn`; the older
folder-scoped command without `--service` is invalid in the current CLI. The dedicated service
account has exactly one folder role, `monitoring.editor`, so Unified Agent can publish aggregate
custom metrics. Any broader existing folder role is drift and blocks the plan.

Immediately before an approved apply, repeat the dry run and compare it to the reviewed plan. The
operator must approve the exact cloud/folder, returned IDs, VM shape (non-preemptible `standard-v3`,
2 vCPU, 4 GiB, 20 GiB network HDD), reserved address, security rules, bucket-policy diff, Lockbox
keys, price delta, command, validation, and rollback. Then, and only then:

```sh
sh deploy/image-origin/provision.sh --apply --approval-nonce '<reviewed-plan-nonce>'
```

Either argument alone or a stale nonce fails before mutation. Creation responses are captured and
their IDs feed dependent commands and the root-private state file; later runs use ID-based reads.
Provisioning is intentionally staged when provider-returned identifiers are dependencies: apply the
reviewed identity phase, generate and review a new plan containing the returned IPv4 and immutable
boot-image ID, then stop again at the separate `origin-access` phase. That phase may add only one
TCP/22 ingress rule sourced from the canonical VM's single attached security group. It preserves
the operator TCP/22 `/32`, accepts only the exact pre-bastion or post-bastion rule set, persists no
secret, returns `next_review_required=true`, and requires a fresh dry run and approval nonce before
the `origin-and-policy` phase may touch VM, folder IAM, bucket policy, or credentials. Reapplying
initialized state does not rotate keys.
Policy and Lockbox payloads use mode-0600 temporary files or standard input, are removed on exit,
and are never rendered in the plan or command arguments.

There is no operator-supplied SSH public-key path. VM authorization always uses the reviewed public
half of the existing workflow key at `deploy/image-origin/workflow-ssh-key.pub` (SHA-256
`5cb142386c744da7cc7783a90b499bd79ab5c65ef8ddb10b2e3a70f04f4650de`). VM creation also binds
`deploy/image-origin/cloud-init.sh` through `--metadata-from-file user-data=...`; its reviewed
SHA-256 is `0e34f70db848ceb9afa577d9cc75f845cb5786851835fb46d4546930a6876aeb` and both hashes are part of
the desired state and approval nonce. Existing VMs are accepted only when returned user-data
exactly matches the reviewed artifact; reports expose its hash, never the metadata body.
At apply time provisioning writes a mode-0600 temporary copy with every `$` doubled so the Yandex
Cloud CLI passes shell variables through instead of substituting its local environment. The provider
stores the original reviewed bytes, and the temporary copy is removed after VM creation.

The secret-free Ubuntu 24.04 bootstrap replaces the image's active package sources with the
reviewed Ubuntu archive and security repositories over HTTPS before its first APT request. It
runs update in fail-on-any-error mode, installs Ubuntu `docker.io` and `docker-compose-v2`, enables
Docker, downloads official Unified Agent `26.09.01`, verifies SHA-256
`08a79e7ce2a06d5b51e368025fd1efb2ccd3e7e91de550730063256b162ddc00`, installs and enables the
supported `unified-agent` service, and requires Docker Compose 2.24.4 or newer. Its root-owned
readiness marker is written only after every download, checksum, package, version, and service
check succeeds.

Run the credential probe only as a separate Task 6 gate through the `image-origin` projection:

```sh
sh deploy/image-origin/provision.sh --probe
```

It fails closed unless the existing accepted preview returns 200 and every list, original/staging
read, write, multipart, ACL, and delete attempt returns the exact authorization-denial 403. Its
output is aggregate counts only; default dry-run and approved apply never execute the probe.

## Origin workflow and inactive CDN resource

`deploy-image-origin.yml` is manual-only and accepts one exact lowercase 40-character repository
SHA. It checks out that commit, authenticates with the existing GitHub OIDC federation, and
materializes only the `image-origin` projection. That projection includes `VM_SSH_KEY` solely as
the transport file `VM_SSH_KEY_FILE`; `run-remote.sh` removes it before building the remote runtime
environment, and Compose/apply never receive it. Configure `VM_HOST`, `VM_USER`, and
`VM_SSH_KNOWN_HOSTS` from the canonical deployment as the bastion contract. Configure
`IMAGE_ORIGIN_VM_HOST` as the origin's private IPv4, plus `IMAGE_ORIGIN_VM_USER` and
`IMAGE_ORIGIN_SSH_KNOWN_HOSTS`, from the reviewed origin record. Also configure
`PRIVATE_MEDIA_S3_BUCKET`, `IMAGE_ORIGIN_PROBE_PATH`, and `YANDEX_CLOUD_FOLDER_ID`.

The workflow uses the one projected `VM_SSH_KEY_FILE` for both SSH hops. It requires `BatchMode`,
`IdentitiesOnly`, strict host-key checking, and the reviewed known-host line independently for the
canonical bastion and private origin, then reaches the origin through SSH `ProxyJump`. The
canonical VM is a deploy-time dependency only: image requests, CDN origin traffic, health checks,
and the running origin do not traverse or depend on it.

Before transporting either archive or environment, the workflow waits for cloud-init, requires the
reviewed readiness marker, Docker Compose 2.24.4 or newer, and an active supported `unified-agent`
service. Failures emit only stable sanitized codes; raw SSH output and secret values are never
relayed. The workflow then configures and restarts that agent and fails closed before reporting
green when the runtime contract changes.

Create the `img-origin.findme-photo.ru` A record and complete initial ACME issuance before invoking
the workflow: deployment is intentionally fail-closed unless the trusted certificate and signed
local image probe already pass. The workflow transports only `deploy/image-origin`, applies the
candidate, retains the previous package for rollback, rechecks the active signed JPEG, installs the
origin-specific Unified Agent template, and reports the deployed SHA. It never invokes the
application deployment, Django, PostgreSQL, application Nginx, or any worker operation.

CDN reconciliation is separately dry-run-first:

```sh
.venv/bin/python scripts/run-with-environment-secrets.py \
  --consumer image-delivery-provision --identity yc -- \
  sh deploy/image-origin/configure-cdn.sh > image-origin-cdn-plan.json
```

The plan contains one HTTPS origin group for `img-origin.findme-photo.ru` and one initially inactive
resource for `img.findme-photo.ru`. It fixes `ignore-query-string`, ignored cookies, a 30-day edge
TTL, six-hour browser TTL, secure-key validation, and `X-FindMe-Origin-Auth`; shielding, CDN logs,
dedicated IP, slicing, compression transforms, and cache warming remain absent. After reviewing the
fresh plan and cost boundary, apply only its exact nonce through the same protected projection:

```sh
.venv/bin/python scripts/run-with-environment-secrets.py \
  --consumer image-delivery-provision --identity yc -- \
  sh deploy/image-origin/configure-cdn.sh \
    --apply --approval-nonce '<reviewed-cdn-plan-nonce>'
```

An existing named origin group is accepted only when it contains exactly one enabled, non-backup
`img-origin.findme-photo.ru` source; its sanitized configuration is part of the reviewed plan and
nonce. An adopted group ID is persisted before any resource mutation so recovery dry-runs use the
same identity. `--dont-use-ssl-cert` is used only for initial inactive resource creation. Later
reconciliation records and preserves an attached Certificate Manager certificate; unsupported
certificate state fails closed instead of detaching it.

This step keeps the resource inactive and does not change DNS, attach or remove a certificate,
activate CDN, or change the application feature flag. Record returned IDs without secret values.
Activation, token/warm-cache proof, staff mode, and rollback rehearsal remain separate reviewed
live steps. The unresolved exact native S3 403 signal remains a hard staff-activation blocker.

## Validation and rollback

Before feature activation, retain the prior application path and the previous origin Compose
package. Verify the exact prefix-only credential probe, origin health, CDN token rejection on warm
and cold paths, a repeat cache hit without origin/Object Storage traffic, URI-free logs, and the
alert IDs that Task 6 actually creates. Record stable IDs, statuses, and timestamps only—never
secrets, signed URLs, query strings, source keys, payloads, or temporary paths.

Rollback image URL emission by setting the feature to `off`. Do not purge caches, restart
application workers, modify derivatives, revoke credentials, or delete billed resources as part of
that application rollback. Origin-package rollback uses the retained package described in
`deploy/image-origin/README.md`. Any CDN deactivation, credential revocation, policy restoration, or
resource deletion is a separate exact-target operation requiring a fresh inventory and approval.
