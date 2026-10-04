# Retire Permanently Enabled Feature Switches

- **Status:** Approved on 2026-09-25
- **Date:** 2026-09-25
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), photo ingestion and
  indexing, public selfie search, private media delivery, canonical deployment, and security,
  privacy, and legal boundaries
- **Related ADRs:**
  [ADR 0012](../../adr/0012-use-django-photographer-permissions.md),
  [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0014](../../adr/0014-keep-stage-2-ingestion-request-driven.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md),
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md),
  [ADR 0034](../../adr/0034-stream-page-scoped-photo-archives-through-django.md), and
  [ADR 0035](../../adr/0035-use-django-polled-yandex-disk-import.md)
- **ADR impact:** Conforms to ADRs 0028 and 0032 by removing stable temporary release gates and
  their guarded branches together. It preserves the authorization, storage, worker, privacy, and
  media contracts of ADRs 0012, 0013, 0014, 0017, 0023, 0034, and 0035. No ADR is superseded.

## Outcome

The canonical application has one implementation of each already accepted and permanently
enabled capability. Eight switches that are currently `True` or `on` in the canonical deployment
cease to exist:

- database definitions `bulk-photo-download` and `yandex-disk-import`;
- Django settings `PHOTO_UPLOAD_ENABLED`, `PHOTO_IMPORT_ENABLED`,
  `PHOTO_PROCESSING_ENABLED`, `PHOTO_PROCESSING_FACE_ENABLED`,
  `PHOTO_PROCESSING_PREVIEW_ENABLED`, and `SELFIE_FEEDBACK_ENABLED`.

Their enabled branches become ordinary application behavior. Their disabled branches, flag-only
tests, deployment inputs, Compose projections, local/test bootstrap values, documentation, and
operator instructions are removed in the same release. No compatibility alias, replacement flag,
or hidden kill switch is introduced.

The change is one atomic cutover. A deployed image must not contain a mixture in which an
application branch is permanent but its obsolete deployment or database switch remains
actionable.

## Confirmed baseline

A read-only inspection of the canonical deployment on 2026-09-25 established:

- `bulk-photo-download=on` and `yandex-disk-import=on` in PostgreSQL;
- all six listed Django settings are `True` in the canonical web process;
- the upload, processing, preview, face, feedback, import, and archive paths are therefore already
  exposed subject to their independent authorization and eligibility rules; and
- the six corresponding GitHub repository variables are all `True`.

This specification retires controls around already active behavior. It does not activate a
currently dark capability.

## Scope

### Included

- Make photographer upload permanently available to callers with the existing Django permission.
- Make Yandex Disk import permanently available to eligible upload owners and keep the import
  worker as a normal canonical service.
- Permanently accept authenticated photo-processing worker API traffic when its independent bearer
  credential is valid.
- Permanently assign the current preview-first policy to newly confirmed photos where the existing
  policy contract applies.
- Permanently enroll current face-processing work according to each event's persisted generation.
- Permanently expose the accepted consented selfie-feedback experience and endpoint.
- Permanently expose page-scoped archive actions and endpoints wherever their existing free-result
  or paid-Order authorization succeeds.
- Remove obsolete database definitions, environment variables, GitHub variables, deployment
  branches, Compose projections, settings checks, local/test defaults, and flag-state tests.
- Update current-state architecture, product/engineering jobs, runbooks, and examples so they no
  longer describe these capabilities as disabled, optional, pending activation, or rollbackable by
  the retired switches.

### Explicitly excluded

- `COMMERCE_WORKER_ENABLED` and every Commerce worker enablement or credential boundary.
- Every `paid-*` database feature definition and guarded paid-event behavior.
- `gallery-cdn-images`, which remains in `staff`.
- `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED`, `ADAFACE_LOCAL_EXPERIMENT_ENABLED`, and every other
  currently false or staff-only switch.
- Changes to event publication, event access type, media authorization, photographer permissions,
  import ownership, worker credentials, Object Storage grants, result bearer capabilities, paid
  Order entitlement, or feedback consent.
- A new runtime operations service, percentage rollout, replacement feature flag, or generic
  configuration abstraction.
- New product behavior, data migrations, reprocessing, preview backfill, import replay, feedback
  data mutation, or archive persistence.

`bulk-photo-download` is the deliberate exception to the otherwise excluded paid area. Its single
definition currently guards both free ready-result archives and paid Order archives, so retiring
it removes that gate at both call sites. The paid archive remains inaccessible unless all existing
paid-event, paid-purchase, paid-Order, and customer-capability checks independently succeed. No
other paid or Commerce branch changes.

## Permanent behavior and preserved invariants

### Photographer upload

The upload entry point and event-management upload affordances no longer test
`PHOTO_UPLOAD_ENABLED`. They continue to require an authenticated active user with
`ingestion.upload_photos`, preserve batch ownership and event selection rules, and issue only the
existing constrained private Object Storage grants.

The private-media bucket, credentials, endpoint, region, and allowed-origin checks become
unconditional configuration checks for a runnable web process. Storage probe commands validate the
storage contract directly rather than requiring an enablement setting.

### Yandex Disk import

The web UI, task creation, retry, worker claim, and publication paths no longer test either
`PHOTO_IMPORT_ENABLED` or `yandex-disk-import`. They continue to enforce the current upload
permission, owner identity, event/folder scope, source validation, retry and lease contracts,
content identity, and publication rules.

The import worker API still requires its dedicated non-empty bearer token with constant-time
comparison. Removing the capability switch must not make an absent or invalid token acceptable.
The worker still has no database credential, Django secret, or permanent Object Storage
credential.

Existing paused or queued imports are not rewritten. Once the permanent implementation is
deployed, their next valid claim or user retry is governed only by their durable state, current
owner permission, and ordinary eligibility. There is no longer a flag-based paused state.

### Photo processing, previews, and face embeddings

The private processing API no longer tests `PHOTO_PROCESSING_ENABLED`; the dedicated worker token,
request shape, processor identity, lease, current attempt, and typed-result checks remain
mandatory. An unconfigured or invalid token continues to receive the same unauthorized response.

New-photo policy selection no longer tests `PHOTO_PROCESSING_PREVIEW_ENABLED`: newly confirmed
eligible photos receive the currently accepted preview-first policy, while persisted legacy
photos retain their explicit legacy policy and are not backfilled. Preview publication remains the
prerequisite for preview-backed gallery eligibility and downstream face enrollment.

Enrollment and preview completion no longer test `PHOTO_PROCESSING_FACE_ENABLED`. They still use
the event's persisted face-embedding generation and the existing processor/configuration identity.
This retirement does not choose SFace versus AdaFace, change thresholds or vectors, or reinterpret
existing evidence.

Stopping a worker service may stop computation, but it is no longer represented as an application
feature state. Durable jobs may continue to queue while a worker is stopped and resume through the
existing lease/retry protocol when service returns.

### Consented selfie feedback

Terminal eligible selfie results always use the accepted feedback presentation and submission
path. Browser opt-out, seven-day browser-local retention, explicit consent, contact validation,
one immutable feedback per search, result-label membership, dedicated private storage, KMS,
30-day object lifecycle, restricted audited staff access, and log-redaction requirements remain
unchanged.

Feedback storage configuration is mandatory for a runnable canonical web process. Local and test
bootstrap may provide clearly test-only placeholder values, but production checks continue to
require the exact approved endpoint, region, limits, separate bucket identity, credentials, and KMS
key. No feedback storage operation may silently fall back to ordinary private media or filesystem
storage.

This cleanup does not broaden feedback data collection beyond the behavior already live on
2026-09-25 and does not claim legal review. The existing requirement to reconcile the published
personal-data policy with the feedback purpose and retention remains a separate governance item;
it is not weakened or represented as completed by retiring the switch.

### Page-scoped archives

Free ready-result and paid Order archive UI and endpoints no longer test
`bulk-photo-download`. Every archive still requires the exact existing surface authorization,
current page membership, original-delivery policy, safe member names, sequential private-object
streaming, and failure semantics.

On the paid path, removing this shared gate grants no Order or media authority. The paid Order must
still be reachable through its existing purchase-browser or access-grant capability, must be paid,
and remains subject to every retained paid release gate that governs the surrounding product path.
Per-item grant audits and missing-original attention remain unchanged.

## Registry and persisted state

`BULK_PHOTO_DOWNLOAD` and `YANDEX_DISK_IMPORT` are removed from the authoritative feature registry
in the same package as their final guarded branches. The startup reconciliation transaction then
deletes exactly those two stale `FeatureFlag` rows from the canonical database. It preserves every
remaining definition and operator-selected state.

No schema or application-data migration is required. Django Admin history remains durable even
after the two actionable rows disappear. A rollback to a prior image recreates definitions known
to that image in `off`; it does not restore their former `on` state automatically.

## Configuration and deployment contract

The six Django settings are removed from settings parsing and from every application check. The
Deploy workflow, deployment scripts, Compose files, environment manifest, `.env` example, local
overrides, test wrapper, and documentation stop accepting or projecting them. Their GitHub
repository variables are deleted after the candidate no longer consumes them.

The final canonical environment file contains none of the six names. Import and photo-processing
services are normal members of the accepted deployment topology rather than profiles selected by
these switches. Their images, credentials, resource limits, processor identities, health checks,
lease drain, and safe replacement behavior remain explicit deployment inputs.

All configuration that protected an enabled path remains required. In particular, removing a
Boolean must not remove validation for private media credentials, allowed origins, import and
processing worker tokens, feedback bucket/KMS credentials, processor identities, bounded limits,
or storage preflight evidence.

## Tests and documentation

Tests whose only purpose is to prove `False`, `staff`, or `off` behavior for a retired switch are
deleted. Tests are not removed merely because they currently use an enabled override. Each such
test is rewritten without the override when it protects permanent product behavior,
authorization, privacy, storage, worker authentication, state transitions, retries, or failure
handling.

Registry and reconciliation tests prove that the two definitions are absent and that the rest of
the registry remains stable. Deployment tests prove that obsolete inputs are absent, required
services and credentials remain projected correctly, and the final environment contains no
retired names. Current-state documentation describes permanent behavior and retains honest
evidence boundaries; historical accepted specifications and ADRs are not rewritten to erase the
conditions under which features were introduced.

## Failure and rollback semantics

There is no runtime off switch for these capabilities after cutover. Failure remains bounded by
the preserved controls:

- invalid users, permissions, ownership, bearer capabilities, tokens, or object identities fail
  closed;
- missing mandatory canonical storage or credential configuration keeps the candidate unhealthy
  before it serves traffic;
- stopping import or photo-processing workers stops their external computation and publication
  progress without deleting durable work;
- archive source failure and disconnect retain ADR 0034's incomplete-stream behavior; and
- feedback storage failure remains isolated from ordinary selfie-search completion and creates no
  partial feedback row.

Operational rollback deploys the prior successful image. Because the retired GitHub variables no
longer exist and restored database definitions reconcile in `off`, that prior image returns these
capabilities to its safe disabled state. An operator must deliberately restore old environment
values and database states only if continued operation of the old image requires them. Existing
uploads, imports, jobs, photos, derivatives, feedback rows, archive audits, and media objects are
preserved; rollback never deletes or rewrites them.

The accepted trade-off is loss of immediate per-capability rollback without deployment. A
performance or correctness incident in permanent upload, import, processing, feedback, or archive
behavior therefore uses service stop where applicable and prior-image rollback for application
behavior. No replacement toggle is added.

## Acceptance criteria

1. The repository contains no production or deployment reference to the six retired Django
   settings and no registry or call-site reference to the two retired database definitions.
2. Authorized photographer upload remains available; anonymous, unauthorized, and cross-owner
   access remains denied, and private Object Storage checks remain mandatory.
3. Yandex Disk import creation, retry, claim, and publication work without either retired import
   gate while preserving current permission, ownership, source, lease, deduplication, and worker
   credential contracts.
4. Processing worker authentication depends on its dedicated token rather than an enablement
   Boolean, and invalid or absent credentials remain indistinguishable and unauthorized.
5. Newly confirmed eligible photos retain preview-first assignment, persisted legacy photos retain
   legacy policy without backfill, and face enrollment retains the event's exact generation and
   preview dependency.
6. Eligible terminal selfie results always expose the accepted feedback behavior while preserving
   opt-out, consent, storage, lifecycle, staff authorization, and privacy boundaries.
7. Free ready-result and authorized paid Order archives remain available without
   `bulk-photo-download`; direct endpoint denial still follows the underlying result or Order
   authorization.
8. Startup reconciliation removes exactly `bulk-photo-download` and `yandex-disk-import`, preserves
   all remaining rows and states, and a prior-image rollback recreates restored definitions in
   `off`.
9. Canonical deployment always includes the import and processing services with their current
   health, credential, lease, resource, and image boundaries, and its generated environment omits
   all six retired setting names.
10. GitHub contains none of the six retired repository variables after successful cutover.
11. Tests retain critical product, authorization, privacy, worker, storage, retry, and failure
   coverage while containing no state matrix for switches that no longer exist.
12. `COMMERCE_WORKER_ENABLED`, every `paid-*` definition and call site, `gallery-cdn-images`, cluster
   expansion, and the local AdaFace experiment retain their pre-change behavior.
13. Architecture, product/engineering status, runbooks, and examples describe the permanent
   current behavior without changing historical ADR or specification evidence.

## Rejected alternatives

### Retire the switches in several releases

Rejected because intermediate deployments would preserve misleading operator controls or combine
permanent application branches with optional deployment topology. The accepted cutover removes
the complete structure in one package.

### Keep the variables but ignore them

Rejected because an ignored variable is a false operational promise and leaves tests and runbooks
describing rollback behavior that no longer exists.

### Replace the switches with constants set to `True`

Rejected because it preserves dead branches, flag-only tests, and indirection without retaining a
real operational capability.

### Keep technical settings while removing only database gates

Rejected because the canonical deployment has already made upload, import, processing, preview,
face, and feedback permanent. Retaining dormant false branches would continue the same ambiguity
the cleanup is intended to remove.
