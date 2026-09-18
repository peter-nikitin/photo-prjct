# CDN and imgproxy gallery delivery implementation plan

- Date: 2026-09-15
- Status: Approved
- Owner: project maintainer
- Related specification: [CDN and imgproxy delivery for gallery grid images](../superpowers/specs/2026-09-15-cdn-imgproxy-gallery-delivery-design.md)
- Related architecture: [Architecture](../architecture.md)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md), [ADR 0006](../adr/0006-yandex-object-storage-media.md), [ADR 0007](../adr/0007-nginx-certbot-https-edge.md), [ADR 0020](../adr/0020-use-signed-direct-object-storage-media-delivery.md), [ADR 0028](../adr/0028-operate-one-canonical-deployment.md), [ADR 0029](../adr/0029-use-watermarked-previews-for-paid-photos.md), [ADR 0036](../adr/0036-issue-direct-gallery-small-preview-capabilities.md), [ADR 0037](../adr/0037-deliver-gallery-grid-images-through-cdn-and-imgproxy.md)
- ADR impact: conforms to accepted ADR 0037; ADR 0036 remains authoritative only for rollback, legacy small presentations, and the explicitly unchanged media paths

## Goal

Implement the [approved specification's goals](../superpowers/specs/2026-09-15-cdn-imgproxy-gallery-delivery-design.md#goals) by delivering normal public gallery-grid images through Yandex Cloud CDN and an isolated imgproxy origin without adding a persistent derivative or returning image traffic to the application VM.

## Scope

None. This plan implements the approved specification without changing its scope.

The plan is intended for execution through `$execute-implementation-plan`; implementation and review should stay focused on the accepted critical path and the realistic policy, isolation, and rollback failures named below.

## Acceptance criteria

The implementation must satisfy every [functional, performance, visual, and security criterion in the approved specification](../superpowers/specs/2026-09-15-cdn-imgproxy-gallery-delivery-design.md#acceptance-criteria), plus these sequence-dependent delivery checks:

- The canonical application is deployed first with `gallery-cdn-images=off`; that deployment must not require the new origin or CDN to exist.
- Staff mode remains `off` until the origin, prefix-only credential probe, CDN token check, and cache-key check are green; it remains `staff` only if the immediate free/paid browser checks and rollback rehearsal are also green.
- Public activation does not start until the same 100-card cohort passes the cold/warm performance and saturation criteria while application HTTP and worker throughput probes run concurrently.
- The rollout gate and obsolete direct derivative-backed small-preview issuer are removed only after 72 continuous hours of public operation with no sustained image 5xx alert, no paid-source policy violation, and no rollback.

## Worker/state/artifact release safeguards

Not applicable. The change does not alter a worker contract, durable processing state, accepted derivative identity, or generated artifact. Existing immutable accepted previews remain the source of truth; CDN objects are reproducible transient cache entries and need no migration, backfill, requeue, or purge for correctness.

## Fixed implementation decisions

- Use `ghcr.io/imgproxy/imgproxy:v4.0.12` and record its immutable multi-architecture digest in Compose before merge. Do not enable Pro-only options.
- Use one server-owned preset: `gallery-v1=resizing_type:fit/width:960/height:960/enlarge:0/format:jpg/quality:78`. Also enable progressive JPEG output, automatic orientation, and metadata stripping globally. `IMGPROXY_ONLY_PRESETS=true` rejects arbitrary processing options.
- Encode the exact source URL `s3://${PRIVATE_MEDIA_S3_BUCKET}/${accepted_derivative_key}` using URL-safe Base64 without padding. The signed imgproxy path is `/${imgproxy_signature}/gallery-v1/${encoded_source}.jpg`; the signature is URL-safe Base64 without padding of HMAC-SHA256 over `salt + complete-path-after-signature`, including its leading slash.
- The public URL is `https://img.findme-photo.ru${signed_imgproxy_path}?md5=${cdn_token}&expires=${unix_seconds}`. Following the Yandex Cloud secure-token contract, `cdn_token` is URL-safe Base64 without padding of binary MD5 over `str(expires) + path + " " + secret`. Do not bind tokens to client IP.
- Use a six-hour capability and browser cache lifetime (`21600` seconds), a 30-day successful-response edge TTL (`2592000` seconds), ignored cookies, and `ignore-query-string=true`. The transform and source identity are entirely in the path, so ignoring `md5` and `expires` cannot merge different images.
- Do not enable paid origin shielding, CDN logs, a dedicated CDN IP, a local imgproxy result cache, or a second transform in the first release. Bound cold work to two concurrent imgproxy transformations; failure of the cold-load criteria blocks public activation.
- Provision one replaceable, non-preemptible `standard-v3` image-origin VM with 2 vCPU at 100 percent core fraction, 4 GiB RAM, a 20 GiB network HDD boot disk, and a reserved public IPv4 address. It runs only origin Nginx, Certbot, and imgproxy. This is the smallest production shape admitted by the pre-provisioning cold test; resize or replication requires a measured follow-up decision.
- Use `img-origin.findme-photo.ru` as the HTTPS origin name and `img.findme-photo.ru` as the CDN CNAME. Keep the authoritative zone at its discovered current provider; the runbook supplies the exact A, ACME-validation, and CNAME records after cloud resource IDs are known.
- The origin accepts public port 80 only for ACME and redirects other traffic; port 443 requires the unguessable CDN origin header before proxying. Its access log records request ID, status, bytes, and timings but never URI, query, source key, or authorization values.
- Publish aggregate CDN/origin/imgproxy/VM metrics through the existing Yandex Monitoring boundary. Alert on image 5xx above 2 percent for five minutes, origin CPU above 90 percent or memory above 85 percent for ten minutes, origin 429 above 1 percent for five minutes, warm-cohort CDN hit ratio below 70 percent for 30 minutes, any Object Storage 403 in each of five consecutive one-minute windows, and origin-auth rejection above ten requests per minute for five minutes.
- imgproxy receives S3 static credentials belonging to a dedicated service account. Its managed bucket-policy statement grants only `s3:GetObject` on `arn:aws:s3:::${PRIVATE_MEDIA_S3_BUCKET}/derivatives/previews/*`; it grants no bucket listing or mutation. A second managed statement preserves the canonical application static key's existing `s3:*` access to the bucket and its objects through `StringEquals` on `yc:access-key-id`. Reconciliation replaces both managed statements while preserving unrelated statements.
- Secrets are distinct: `GALLERY_CDN_TOKEN_SECRET`, `GALLERY_IMGPROXY_KEY`, `GALLERY_IMGPROXY_SALT`, `IMAGE_ORIGIN_HEADER_SECRET`, `IMAGE_ORIGIN_S3_ACCESS_KEY_ID`, and `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY`. The application receives only the first three; the origin receives only the latter five excluding the CDN token. Provisioning receives the CDN token, origin-header secret, and existing `PRIVATE_MEDIA_S3_ACCESS_KEY_ID`, but never `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY`.
- Secret rotation uses a parallel `img-next.findme-photo.ru` CDN resource and a second imgproxy verification slot on the same isolated VM. Django switches new page capabilities to the replacement hostname/key set while the old CDN resource and verifier remain for six hours; the old resource, verifier, and secrets are removed only after that drain. This is a runbook operation, not continuously provisioned duplicate capacity.

## Implementation

### Task 1: Build deterministic gallery capability signing

**Files:** create `src/backend/picflow/gallery_image_delivery.py` and `src/backend/picflow/tests/test_gallery_image_delivery.py`; modify `src/backend/config/settings.py` and `.env.example`.

- **Specification:** URL structure and trust boundaries; transform contract; capability lifetime.
- **Depends on:** None.
- **Produces:** a settings-backed signer compatible with the existing accepted-preview capability interface.

- [ ] Add failing unit tests with fixed golden vectors for URL-safe source encoding, imgproxy HMAC-SHA256, Yandex CDN MD5 token construction, six-hour expiry, and separation of path identity from authentication query parameters. Include rejection of a non-HTTPS CDN origin, invalid CDN secrets outside 6–32 characters, non-hex imgproxy key/salt, and keys outside the existing accepted-preview regex.
- [ ] Run `make test TESTS="src/backend/picflow/tests/test_gallery_image_delivery.py"` and confirm the new tests fail because the module is absent.
- [ ] Add `GalleryImageDeliverySettings.from_django_settings()` that reads `GALLERY_CDN_ORIGIN`, `GALLERY_CDN_TOKEN_SECRET`, `GALLERY_IMGPROXY_KEY`, `GALLERY_IMGPROXY_SALT`, `PRIVATE_MEDIA_S3_BUCKET`, and the fixed six-hour TTL. Fail closed with a configuration error when construction is requested and any value is missing or invalid.
- [ ] Implement `GalleryImageUrlSigner.sign_accepted_preview(key: str, expires_in: int) -> str`. Reuse the accepted-preview key validator instead of introducing a second policy regex; keep encoding and both signature functions pure and clock-injectable for deterministic tests.
- [ ] Set only non-secret defaults in `.env.example`: `GALLERY_CDN_ORIGIN=https://img.findme-photo.ru`; secret values remain blank.
- [ ] Run the targeted test again and record GREEN with every golden vector matching the published imgproxy and Yandex Cloud algorithms.

### Task 2: Switch one authorized gallery page through a runtime gate

**Files:** modify `src/backend/feature_flags/registry.py`, `src/backend/feature_flags/tests/test_registry.py`, `src/backend/config/views.py`, `src/backend/picflow/gallery_preview_grants.py`, `src/backend/picflow/tests/test_gallery_preview_grants.py`, and `src/backend/picflow/tests/test_views.py`.

- **Specification:** eligible presentation; failure behavior; runtime rollout and rollback contract.
- **Depends on:** Task 1.
- **Produces:** `gallery-cdn-images` in `off/staff/on` modes with page-level all-or-nothing URL selection.

- [ ] Add failing registry and view tests proving that `off` preserves the current direct accepted-preview URLs, `staff` changes only staff page renders, and `on` changes all eligible normal public grid renders.
- [ ] Add failing view tests proving that free pages sign only accepted `preview-small-v1` keys, paid pages sign only accepted `preview-watermarked-v1` keys, one card and its face crops reuse one URL, and a 100-card page emits no more than 100 distinct CDN URLs.
- [ ] Preserve explicit regression tests for legacy small presentation, lightbox/large preview, download, selfie results, private management, cart/order, and purchased media. These paths must not call the CDN signer.
- [ ] Assert query bounds: gate evaluation happens once per page, accepted-derivative selection remains one bounded query, and no Object Storage, CDN, or imgproxy call occurs while producing HTML.
- [ ] Register `GALLERY_CDN_IMAGES = FeatureDefinition("gallery-cdn-images", "Deliver gallery grid images through CDN")`. Evaluate it once in `event_detail`; when enabled, construct one `GalleryImageUrlSigner` and pass it to `issue_gallery_preview_urls`. Do not change per-photo policy selection.
- [ ] If enabled configuration or signing fails, return the existing explicit 503 path. Never mix direct and CDN URLs in one eligible derivative-backed page, fall back to a clean paid source, or weaken authorization. When disabled, do not validate or require CDN settings.
- [ ] Run `make test TESTS="src/backend/feature_flags/tests/test_registry.py src/backend/picflow/tests/test_gallery_image_delivery.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/picflow/tests/test_views.py"` and record GREEN.

### Task 3: Package and constrain the isolated image origin

**Files:** create `deploy/image-origin/compose.yml`, `deploy/image-origin/nginx.conf.template`, `deploy/image-origin/presets.txt`, `deploy/image-origin/apply.sh`, `deploy/image-origin/check.sh`, `deploy/image-origin/monitoring/unified-agent.yml.template`, `deploy/image-origin/monitoring/dashboard.json`, `deploy/image-origin/monitoring/alerts.md`, `deploy/image-origin/test/compose.yml`, `deploy/image-origin/test/fixtures/`, and `tests/deployment/test_image_origin_contract.py`; modify `Makefile` only if the operational selector lacks a target for the new integration check.

- **Specification:** infrastructure isolation; storage and credential boundary; failure behavior; observability and privacy.
- **Depends on:** Task 1 path contract.
- **Produces:** a digest-pinned, replaceable Nginx/imgproxy Compose package and a local MinIO-backed contract test.

- [ ] Add failing static and integration tests that inspect the effective Compose configuration and boot the package against MinIO with one accepted JPEG. The valid CDN-header/signed-path request must produce a progressive JPEG no larger than 960 pixels; altered header, signature, preset, source prefix, source URL scheme, output extension, oversized source, and redirect must fail before a successful transform.
- [ ] Pin imgproxy `v4.0.12` and Nginx/Certbot images by immutable digest. Run both services read-only with dropped capabilities, `no-new-privileges`, explicit health checks, tmpfs scratch space, memory/PID/CPU limits, and no host media volume.
- [ ] Configure only S3 source loading for the supplied `PRIVATE_MEDIA_S3_BUCKET` at `https://storage.yandexcloud.net`, presets-only mode, two concurrent transformations, 10 MiB maximum source bytes, 25 megapixels maximum decoded source, 960-pixel maximum result dimension, redirects disabled, progressive JPEG, automatic rotation, metadata stripping, and no internal result cache. Treat an unsupported or renamed imgproxy v4 option as a failing startup contract, not an ignored setting.
- [ ] Configure Nginx to terminate HTTPS, compare `X-FindMe-Origin-Auth` without logging it, strip authentication query parameters before `proxy_pass`, accept only the exact signed `gallery-v1` path shape, remove untrusted forwarding headers, limit connections and request rate, and return `Cache-Control: public, max-age=21600, s-maxage=2592000` only for successful images. Use a four-second origin response budget, below Yandex CDN's five-second origin limit.
- [ ] Use a URI-free access log and bounded aggregate status/timing metrics. Confirm tests cannot find query values, object keys, photo/event identifiers, or secrets in logs.
- [ ] Configure the existing Unified Agent pattern to export only aggregate Nginx/imgproxy/VM metrics, and define the fixed alert thresholds above. Dashboard panels must separate CDN hits/misses, CDN 4xx/5xx, origin 429/5xx, S3 failures, transform concurrency/duration, CPU, and memory.
- [ ] Run the representative cold 100-image fixture with Docker limited to 2 vCPU and 4 GiB. Require the complete transform batch, four-second per-image origin budget, and bounded memory to pass before the matching live VM may be provisioned.
- [ ] Make `apply.sh` idempotently install the reviewed package under `/opt/photo-prjct-image-origin`, preserve certificates, validate `docker compose config` and `nginx -t`, start the candidate, and require `check.sh` success before replacing the previous containers. Keep the old Compose package for one-command rollback.
- [ ] Run `make test-operational` and the new local origin integration target; record GREEN and the resulting image dimensions/content type.

### Task 4: Add least-privilege secrets and cloud provisioning contracts

**Files:** create `deploy/image-origin/provision.sh`, `deploy/image-origin/bucket-policy-statement.json`, `deploy/image-origin/cloud-contract.env.example`, `tests/deployment/test_image_origin_cloud_contract.py`, and `docs/runbooks/gallery-image-delivery.md`; modify `deploy/environment-secrets.json`, `docs/runbooks/environment-secrets-inventory.md`, and `docs/runbooks/environment-secrets.md`.

- **Specification:** infrastructure isolation; storage and credential boundary; observability and privacy.
- **Depends on:** Task 3.
- **Produces:** reviewed, dry-run-by-default provisioning and secret projection with no live mutation during implementation review.

- [ ] Add failing contract tests for stable cloud/folder discovery, exact resource names, VM shape, reserved address, security-group ports, dedicated service account, the prefix-only origin statement, the application access-key statement, secret consumer separation, masked command output, and refusal to mutate without `--apply` plus an exact approval nonce.
- [ ] Make `provision.sh` default to read-only discovery and a machine-readable plan. It must resolve and print cloud, folder, network, subnet, bucket, existing policy, service-account, address, VM, origin-group, CDN-resource, and certificate IDs before proposing any mutation; never infer them from display names alone after creation.
- [ ] Define resources named `findme-gallery-image-origin` for the service account, VM, security group, and reserved address. Reuse the discovered canonical VPC/subnet; create no database, disk beyond the 20 GiB boot disk, load balancer, NAT gateway, or durable cache.
- [ ] Generate a bucket-policy patch that replaces two managed statements: one principal-scoped `s3:GetObject` statement for `arn:aws:s3:::${PRIVATE_MEDIA_S3_BUCKET}/derivatives/previews/*`, and one `Principal: "*"`, `Action: "s3:*"` statement for the exact bucket and all objects, conditioned by `StringEquals` on the existing `PRIVATE_MEDIA_S3_ACCESS_KEY_ID`. Resolve the bucket from the canonical deployment's `PRIVATE_MEDIA_S3_BUCKET`, preserve every unrelated existing statement, and bind the normalized before/after diff and application access-key ID into the approval nonce. Immediately after a policy write, require a real application-runtime `GetObject`; on failure restore the exact previous policy, or remove the new policy if none existed, and stop before image-origin credential creation. Provisioning must never read the application secret. Add a separate image-origin credential probe that must allow one accepted preview read and deny `ListBucket`, original/staging reads, put, multipart, ACL, and delete.
- [ ] Add the six named secrets to the existing Lockbox inventory. Extend `local-web` and `deploy` only with the three Django signing secrets; add `image-origin` and `image-delivery-provision` consumers with the minimal sets fixed above. Secret creation writes values directly to protected temporary files/Lockbox and must never echo them.
- [ ] Make the dry-run report include the commands that Task 6 will use to refresh `yc config profile list`, `yc config get cloud-id`, `yc config get folder-id`, resource inventories, quotas, and the official pricing estimate for the exact VM, disk, reserved IP, CDN base resource, and expected egress. Acceptance of this plan is not mutation approval.
- [ ] Run `make test-operational` and record GREEN without creating or changing a live cloud, IAM, bucket-policy, DNS, certificate, CDN, or Lockbox resource.

### Task 5: Dark-deploy application support with the gate off

**Files:** modify `docker-compose.deployment.yml`, `.github/workflows/deploy.yml`, `deploy/apply-deployment.sh`, and their existing tests under `tests/deployment/`; update `docs/runbooks/gallery-image-delivery.md`.

- **Specification:** runtime rollout and rollback contract; required critical-path tests.
- **Depends on:** Tasks 2 and 4.
- **Produces:** canonical application support running in production with `gallery-cdn-images=off` before any new billed delivery resource is required.

- [ ] Add failing deployment tests proving that the three Django signing secrets are projected to `web` only, are absent from worker/importer containers and logs, and can be omitted while the gate is off.
- [ ] Pass `GALLERY_CDN_ORIGIN`, `GALLERY_CDN_TOKEN_SECRET`, `GALLERY_IMGPROXY_KEY`, and `GALLERY_IMGPROXY_SALT` through the canonical deployment without changing worker topology or restart semantics.
- [ ] Run the selected local suites and deploy the application commit with the synced runtime flag explicitly reconciled to `off`. The deploy must not contact or require the image origin or CDN.
- [ ] Verify running SHA, container health, the original public gallery route, direct accepted-preview URLs on a fresh page, queue advancement, PostgreSQL activity, application/worker VM resources, and a fresh error window. Stop before cloud provisioning if this dark deploy changes any existing production path.

### Task 6: Provision delivery resources, deploy the origin, and activate for staff

**Files:** create `.github/workflows/deploy-image-origin.yml`, `deploy/image-origin/run-remote.sh`, `deploy/image-origin/configure-cdn.sh`, and `tests/deployment/test_image_origin_workflow.py`; modify `deploy/environment-secrets.json` allowed workflows and `docs/runbooks/gallery-image-delivery.md`.

- **Specification:** delivery design; cache policy; infrastructure isolation; rotation and staff-rollout contracts.
- **Depends on:** Tasks 3–5, including the successful dark production deploy.
- **Produces:** an independently deployed image origin, active CDN resource, and bounded staff validation while public traffic remains on the direct path.

- [ ] Add failing workflow/command-contract tests for OIDC identity, commit-SHA pinning, host-key verification, Lockbox consumer scoping, no secret output, candidate health check, rollback package retention, and no dependency on the application deployment workflow.
- [ ] Add a manual `deploy-image-origin.yml` workflow that authenticates through the existing GitHub OIDC federation, materializes only the `image-origin` consumer, deploys the package to the recorded static IP, verifies the certificate and local signed image probe, configures Unified Agent, and reports the deployed repository SHA. It must not deploy or restart Django, PostgreSQL, application Nginx, or workers.
- [ ] Add `configure-cdn.sh` as dry-run by default. It creates or reconciles one HTTPS origin group for `img-origin.findme-photo.ru` and one initially inactive CDN resource for `img.findme-photo.ru`; configure the secure key, `ignore-query-string=true`, ignored cookies, 30-day edge cache, six-hour browser cache, and static `X-FindMe-Origin-Auth`. Do not enable shielding, logs, dedicated IP, slicing, compression transforms, or cache warming.
- [ ] Refresh live inventory, quotas, and official pricing immediately before mutation. Present the exact cloud/folder, policy diff, service account, VM shape, disk, reserved IP, security group, origin group, CDN resource, certificate, DNS records, secrets, and monthly estimate; obtain fresh manual approval, then run `provision.sh --apply`. Obtain a second fresh approval immediately before the access-changing bucket-policy/credential step if it is not in the same displayed mutation batch.
- [ ] Record every created live ID without secret values. Run the prefix-only credential probe and stop before origin deployment unless the one allowed read and every required denial pass.
- [ ] Run the independent image-origin workflow. Add `img-origin.findme-photo.ru A ${IMAGE_ORIGIN_RESERVED_IP}`, complete origin ACME issuance, add `img.findme-photo.ru CNAME ${CDN_PROVIDER_CNAME}`, attach the managed CDN certificate, and wait for DNS/CDN propagation. Reject ANAME and verify the expected certificate chain and CNAME.
- [ ] While the application flag remains `off`, prove the secured origin directly, obtain fresh approval for the CDN create/update if it was not covered by the displayed batch, then activate the CDN resource. Validate that a valid CDN token reaches origin and an expired or altered token is rejected by CDN even when the object is warm. Repeat one URL and prove the second request is a CDN hit with no origin or Object Storage request.
- [ ] Set `gallery-cdn-images=staff`. Validate one representative free event and one paid event as staff: source policy, at most one URL per card, card/face reuse, landscape/portrait, smallest face crops, high-density rendering, watermark visibility, eager first four/lazy remainder, placeholders on image failure, and unchanged lightbox/download behavior.
- [ ] Rehearse rollback by setting the flag `off`, render fresh free and paid pages, and prove direct accepted-preview URLs return immediately without CDN/origin dependency. Restore `staff` only after the rehearsal passes.
- [ ] Measure gallery HTML query count and latency against the pre-change page. Require no external media calls and regression within the larger of 200 ms or 10 percent.
- [ ] Run `make test-operational`; record GREEN local contract, deployed origin SHA, resource IDs, origin health, CDN configuration, DNS, TLS, token rejection, credential denials, warm-hit evidence, monitoring datapoints, and alert IDs.

### Task 7: Run the production-like performance and isolation gate

**Files:** create `scripts/measure_gallery_image_delivery.py` and `docs/verification/gallery-image-delivery-baseline.md`; update `docs/runbooks/gallery-image-delivery.md`.

- **Specification:** performance and isolation acceptance criteria; observability and privacy.
- **Depends on:** Task 6 in staff mode.
- **Produces:** reproducible cold/warm evidence and a go/no-go record for public activation.

- [ ] Make the measurement script accept only an explicitly supplied event URL and bounded page number, record status/timing/bytes/cache headers for at most 100 unique grid URLs, and redact query strings and source paths. It must not generate new searches, uploads, processing jobs, or media.
- [ ] Capture the current direct-delivery baseline and the staff CDN result for the same `cyclingrace-moscow-2026` page, browser profile, network location, and sample count. Purge only the exact test paths when a cold run is required; obtain explicit approval before any production CDN purge.
- [ ] Require median transferred grid-image bytes at least 60 percent lower, complete cold unique-image transfer no more than 15 MiB, and first 12 unique images at least 50 percent faster than the recorded direct baseline.
- [ ] Repeat the same page warm and prove cache hits with zero matching origin/imgproxy/Object Storage fetches. Separate CDN 4xx, origin 5xx, source failures, and client cancellations in the evidence.
- [ ] Saturate only the image origin with a bounded two-concurrent-transform cold cohort while independently sampling the public application route, PostgreSQL activity, worker queue throughput, and application VM resources. Require no new application 5xx and no more than 10 percent regression in public-route p95 latency or processing-worker throughput versus the immediately preceding equal-duration baseline; stop immediately on any breach.
- [ ] Treat any failed performance, visual, policy, credential, or isolation criterion as no-go. Keep the feature in `staff` or `off`; do not resize, add premium CDN features, or expand transforms without a reviewed plan update and fresh cost approval.

### Task 8: Public activation, observation, and permanent-path cleanup

**Files:** modify `src/backend/config/views.py`, `src/backend/picflow/gallery_preview_grants.py`, their tests, `src/backend/feature_flags/registry.py`, `docs/architecture.md`, `docs/product-jobs.md`, `docs/engineering-jobs.md`, `docs/future-work/2026-07-31-direct-media-performance-thresholds.md`, and `docs/runbooks/gallery-image-delivery.md`.

- **Specification:** public rollout, removal of obsolete architecture, documentation reconciliation.
- **Depends on:** Task 7 is fully green.
- **Produces:** public CDN delivery followed by one permanent normal-gallery small-image path.

- [ ] Set `gallery-cdn-images=on` and verify a newly rendered anonymous free page and paid page use CDN URLs while legacy/out-of-scope presentations remain unchanged. Recheck running SHA, public HTTPS, CDN hit/miss split, origin saturation, Object Storage failures, application errors, DB activity, and worker throughput.
- [ ] Observe for 72 continuous hours. Alert and roll back to `off` on sustained image 5xx, abnormal cold-miss growth, origin resource exhaustion, prefix-policy denial, paid clean-preview exposure, application capacity regression, or worker throughput regression. A CDN/origin incident without application regression still rolls back image URL emission but does not restart app workers.
- [ ] After the observation gate passes, remove `GALLERY_CDN_IMAGES` and the derivative-backed direct Object Storage signing branch. Keep the legacy-photo application route and every out-of-scope media path. Update tests so normal eligible galleries have exactly one permanent CDN implementation.
- [ ] Remove the three Django signing values from any component that no longer needs them only if construction is moved behind a narrower configuration boundary; do not combine this cleanup with secret rotation.
- [ ] Mark the direct-media performance future-work trigger resolved with links to the measured evidence. Update product/engineering job evidence, architecture topology/status, and the runbook's steady-state operation and A/B rotation procedure.

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification, ADR 0037, the still-applicable portions of ADRs 0003/0006/0007/0020/0028/0029/0036, and `docs/architecture.md`.
- [ ] Confirm the final state has one normal eligible gallery-grid image path, no new durable derivative or processing state, and an origin isolated from the canonical application VM.
- [ ] Update implemented architecture facts, exact cloud resource IDs, operational ownership, and verification links.
- [ ] Stop for a decision instead of contradicting an accepted ADR; supersede rather than edit an accepted decision.
- [ ] Record the reconciliation outcome in the pull request.

## Verification

For each task, preserve the exact command, exit status, summary, and final package fingerprint required by [Testing](../testing.md). The final branch must run:

1. `.venv/bin/pre-commit run --files src/backend/picflow/gallery_image_delivery.py src/backend/picflow/gallery_preview_grants.py src/backend/config/views.py src/backend/config/settings.py src/backend/feature_flags/registry.py src/backend/picflow/tests/test_gallery_image_delivery.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/picflow/tests/test_views.py src/backend/feature_flags/tests/test_registry.py`
   - Expected: Ruff formatting/lint and full-project mypy are GREEN; rerun if hooks modify files.
2. `make test TESTS="src/backend/feature_flags/tests/test_registry.py src/backend/picflow/tests/test_gallery_image_delivery.py src/backend/picflow/tests/test_gallery_preview_grants.py src/backend/picflow/tests/test_views.py"`
   - Expected: signer, policy, page-level gate, query-bound, legacy, and unchanged-presentation tests are GREEN.
3. `make test-operational`
   - Expected: image-origin, Lockbox, workflow, cloud-contract, deployment, privacy, and rollback contract tests are GREEN.
4. `docker compose -f deploy/image-origin/test/compose.yml up --build --abort-on-container-exit --exit-code-from acceptance`
   - Expected: valid transform and every required negative origin/security probe are GREEN; logs contain no prohibited identifiers or secrets.
5. `.venv/bin/python scripts/select_test_suites.py select --base origin/main --head HEAD --format json`
   - Expected: record the final selector result; run every selected expensive suite below for the exact same package fingerprint.
6. `.venv/bin/python scripts/select_test_suites.py fingerprint --base origin/main`
   - Expected: record the final package fingerprint used by all reusable evidence.
7. `make check`
   - Expected: the complete Python quality suite is GREEN on the final package.
8. `make test-migrations` when selected.
   - Expected: migration compatibility checks are GREEN; no schema migration is introduced by this plan.
9. `npm run test:visual` when selected.
   - Expected: gallery visual baselines are GREEN without snapshot changes unless an actual rendering difference is reviewed.

Production acceptance additionally records, without secrets or signed query strings:

- deployed application and image-origin SHAs;
- CDN resource, origin group, certificate, VM, service-account, address, network, subnet, security-group, bucket, and Lockbox IDs;
- free/paid/legacy browser results and rollback rehearsal;
- prefix-only credential denials;
- cold/warm bytes, timings, cache hits, and origin/Object Storage request counts;
- application HTTP, PostgreSQL, queue throughput, worker state, and both VM resource samples during origin saturation;
- the 72-hour public observation result.

## Operational impact and rollout

This release adds one billed CDN resource, one non-preemptible image-origin VM and boot disk, one reserved public IP, one dedicated service account/static access key, two DNS names, certificates, Lockbox entries, alerts, and an independent deployment workflow. It does not move or restart Django, PostgreSQL, application Nginx, processing workers, or import workers as part of image-origin changes.

The order is strict:

1. Merge and deploy application support with the runtime flag `off`.
2. With fresh manual approval, create credentials/cloud resources, deploy origin, configure CDN inactive, and establish DNS/TLS.
3. Validate token, cache, origin, and credential boundaries; activate CDN resource.
4. Enable `staff`, run functional/visual/HTML/rollback checks.
5. Run cold, warm, and saturation acceptance.
6. Enable `on`, observe 72 hours, then remove the rollout gate and obsolete direct eligible-gallery path in a separate cleanup pull request.

Cloud and access mutations are never authorized by approving this document. Immediately before each mutation batch, refresh live inventory and pricing, name exact targets and policy diff, and obtain explicit user approval. Optional CDN features remain disabled unless a later measured decision approves their cost.

## Rollback

Before permanent-path cleanup, set `gallery-cdn-images=off`. Newly rendered eligible pages immediately return to the accepted direct Object Storage capabilities from ADR 0036. Do not purge CDN, delete credentials, restart application workers, requeue photos, or modify derivatives. Already rendered CDN URLs and browser copies expire under their bounded six-hour contracts.

If the origin deployment itself is faulty, roll back `/opt/photo-prjct-image-origin` to the retained previous Compose package and run its health check; this affects only uncached image misses. If CDN configuration is faulty, deactivate the CDN resource after the application flag is off. Preserve the old CDN/origin verifier for six hours during any key rotation or hostname replacement.

After permanent-path cleanup, rollback is a normal code revert plus redeployment of the removed gate/direct issuer commit; the CDN/origin can remain running during that code rollback. There is no database, worker-state, derivative, or cache migration to reverse. Delete billed cloud resources or revoke the dedicated credential only as a separately approved, exact-target cleanup after all six-hour capabilities have drained.

## Open questions

None.

## References

- [Yandex Cloud secure-token algorithm](https://yandex.cloud/en/docs/cdn/concepts/secure-tokens)
- [Yandex Cloud CDN cache-key and TTL configuration](https://yandex.cloud/en/docs/cdn/operations/resources/configure-caching)
- [imgproxy configuration options](https://docs.imgproxy.net/configuration/options)
- [imgproxy S3 source configuration](https://docs.imgproxy.net/latest/image_sources/amazon_s3)
