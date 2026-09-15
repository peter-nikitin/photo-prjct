# Gallery image delivery rollout

This runbook prepares the accepted CDN/imgproxy design without combining application deployment,
cloud provisioning, access changes, or feature activation into one authority. The isolated origin
is replaceable and owns no database, durable transform cache, extra data disk, or application
service. Approval of the implementation plan is not approval for any Yandex Cloud, IAM, Object
Storage, Lockbox, DNS, certificate, CDN, or production mutation.

## Fixed order and stop conditions

1. Deploy the application with `gallery-cdn-images=off` and no new resources. Confirm fresh gallery
   pages still contain direct accepted-preview capabilities and that the deployment did not require
   origin/CDN settings.
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

## Repository-only dry run

Before any resource creation, seed exactly `GALLERY_CDN_TOKEN_SECRET` and
`IMAGE_ORIGIN_HEADER_SECRET` into the current Lockbox secret version through the established
protected, separately approved secret-update procedure. Do not pass either value in shell
arguments, the non-secret contract file, or process environment. The CDN token must be 6-32
characters and the origin header 16-128 characters from `[A-Za-z0-9_-]`. Read-only metadata must
then show exactly these two gallery keys: zero, one, three-to-five, or any different partial set is
a stop. The provisioner adds only the remaining imgproxy key/salt and S3 credential pair; a complete
six-key set is initialized and must not be rotated by provisioning.

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
boot-image ID, then apply that second nonce. Reapplying initialized state does not rotate keys.
Policy and Lockbox payloads use mode-0600 temporary files or standard input, are removed on exit,
and are never rendered in the plan or command arguments.

Run the credential probe only as a separate Task 6 gate through the `image-origin` projection:

```sh
sh deploy/image-origin/provision.sh --probe
```

It fails closed unless the existing accepted preview returns 200 and every list, original/staging
read, write, multipart, ACL, and delete attempt returns the exact authorization-denial 403. Its
output is aggregate counts only; default dry-run and approved apply never execute the probe.

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
