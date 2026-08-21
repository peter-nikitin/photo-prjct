# FindMe Photo Architecture

## Document purpose

This document is the architectural source of truth for FindMe Photo. It distinguishes the
software that exists today from accepted constraints, proposed MVP components, and deferred
ideas. Accepted decisions live in [Architecture Decision Records](adr/README.md); delivery work
is decomposed in [implementation plans](plans/README.md).

## Product goal

FindMe Photo is an event photo marketplace for running, cycling, obstacle, corporate, and similar
mass-participation events. A customer selects an event, finds likely photos by bib number, face,
time, or location, purchases selected photos, and receives protected downloads. Operators and
photographers upload batches, review processing failures, correct machine-generated metadata, and
manage publication and sales.

The defining relationship is:

`event -> location -> photographer -> photo -> recognition -> review -> search -> purchase -> download`

Search is event-scoped by default. Automated recognition produces candidates with confidence;
manual corrections take precedence and the product must not claim certain identity.

## Architectural status vocabulary

- **Implemented**: present in the repository and executable.
- **Accepted**: governed by an accepted ADR, even if delivery is incomplete.
- **Proposed**: target MVP direction that still needs an ADR, experiment, or implementation plan.
- **Deferred**: deliberately outside the MVP.

## Current architecture — implemented

The repository currently contains an early Django application:

Historical rollout evidence in this section retains its then-current `staging` and `production`
wording. It records dates, commits, and prior gates; it is not an operator instruction or a current
deployment topology. ADR 0028 and the accepted constraints below define the canonical deployment.

- Django 6 serves the canonical customer UI from server-rendered templates and local static CSS
  and SVG assets under `src/backend`.
- The `picflow` application owns the first target `Event` catalog model and a preliminary `Photo`
  model. Published events are managed through Django Admin and rendered by server-side templates.
- Published event detail pages apply an explicit persisted gallery-media policy. Free events retain
  the existing legacy and accepted-clean-preview rules. With the off-by-default paid-watermark gate
  enabled, a published paid event lists only `watermarked_preview_required` photos backed by an
  accepted watermark state, attempt, and `preview-watermarked-v1` derivative; older paid photos
  remain absent. The database-only factory converts rows to immutable `GalleryPhoto` presentation
  values, including stable `photo_id`, semantic small- and large-preview application URLs, and a
  nullable `download_url`, so templates neither inspect storage fields nor select media variants.
- `PublicMediaResolver` is the sole server-side selector of public bytes. It retains legacy and
  clean-preview behavior, selects `preview-watermarked-v1` for both presentation roles of the new
  paid policy, and rejects that policy's original download before storage signing. It never falls
  back to an original for missing required derivative evidence. Public routes recheck publication
  and eligibility on every request and expose no permanent key or credential.
- Event galleries use locally packaged GLightbox 3.3.1 assets with normal anchor fallback.
  Task 6's browser run and inspected snapshots verified responsive populated and empty layouts,
  keyboard and pointer operation, mobile swipe, Escape/control close, focus restoration, and
  operation with JavaScript disabled. The later local implementation rerun was infrastructure-blocked:
  after a Docker LinuxKit/API wedge and restart, two pre-existing catalog cases rendered with
  HTTP-200 resources but timed out waiting for `networkidle`. PR #45 CI run
  [29693681091](https://github.com/peter-nikitin/photo-prjct/actions/runs/29693681091) then passed all
  44 visual tests for the CI-tested implementation commit `7d6a718` in 47.7 seconds; later docs-only
  evidence commits were not included. Neither automated result represents a canonical-deployment activation.
- PostgreSQL is configured entirely through environment variables.
- Local development uses Docker Compose for Django and PostgreSQL.
- Confirmed private JPEGs are transactionally enrolled in explicit processing states. Django and
  PostgreSQL own jobs, leases, retries, accepted results, immutable attempt evidence, and immutable
  event-scoped reports. The shipped preview-first path persists explicit legacy or preview-first
  policy; when the separate `PHOTO_PROCESSING_PREVIEW_ENABLED` gate is enabled it queues
  `2/generate_preview/1`, publishes a verified immutable preview, and only then queues preview-
  backed face work selected by the event's immutable search generation. Events that existed before
  the AdaFace rollout remain on `2/face_embedding/3` using SCRFD/SFace; newly created events default
  to `3/face_embedding/5` using SCRFD/AdaFace with 512-dimensional embeddings and provisional direct
  distance threshold `0.42`. No existing event is replayed or reinterpreted. The standalone worker polls the private Django API with one-at-a-
  time round-robin identity scheduling, has no Django/database or permanent Object Storage
  credentials, and receives only short-lived grants for exact input/output objects. Local targeted
  tests exercise real-JPEG preview generation, publication, gallery selection, preview-backed face
  enrollment, reporting, and the no-credential container contract. The feature is shipped and
  locally verified, but tracked defaults leave preview activation false. A seven-day temporary-preview
  lifecycle rule, representative original-versus-preview ML comparison, and concurrency-one capacity
  measurement remain canonical-deployment activation blockers. No preview worker is enabled.
- The repository now also implements the dark-deployable preview-quality candidate
  `3/face_embedding/4`. It accepts only the already verified `preview-small-v1` input, and its
  fixed event-scoped replay command is dry-run by default and requires an explicit apply option.
  Enrollment fails closed on the reviewed event, configuration and artifact identity, and current
  accepted-preview cohort before it creates or reuses a job. It recomputes the exact canonical
  accepted `PhotoDerivative` cohort over photo ID, accepted SHA-256, byte size, and geometry;
  activation recomputes the same hash while holding the event lock. Any change to those accepted
  derivative fields blocks enrollment and activation even when the photo count is unchanged.
  Candidate status exposes bounded aggregate job, attempt, state, terminal/nonterminal, failure,
  detection, and projection counts.
  Activation additionally requires every photo in the frozen eligible cohort to have one compatible
  accepted projection and no queued, processing, retryable, failed, stale, or technical-failure
  candidate state. It appends an event selection only after those checks; existing baseline,
  version-3, version-4, failed-attempt, projection, activation, and bearer-result evidence is not
  rewritten. The worker/deployment contract accepts the identity only when explicitly configured
  and forwards it unchanged through the canonical deployment; deployment itself neither
  enrolls nor activates the candidate. Version 4 leaves the `0.363` selfie-search ranking threshold
  and direct/cluster result evidence unchanged. The accepted local full-corpus quality selection
  covers 17,043 photos/jobs/attempts/projections, zero technical failures, 37,573 kept faces, and
  18,610 quality-rejected faces. Its local preview manifest hash is `62f071…`, local canonical
  projection hash is `a98b5d…`, accepted runtime cohort hash is `6701b743…`, and immutable reviewed
  crosswalk hash is `055d7c…` with `entries=17043` and `sha_mismatch=17043`. Local and accepted
  preview SHA-256 values therefore differ systematically; the crosswalk binds the two reviewed
  identities and does not claim byte equivalence. The full hashes, exact configuration,
  comparison-manifest, and historical YuNet/SFace SHA-256 values are recorded in the
  [approved rollout design](superpowers/specs/2026-08-10-preview-face-quality-v4-rollout-design.md#approval-evidence).
  Current-merge-candidate full `make check`/reconciliation, PR and CI, canonical deployment,
  event replay/activation, and customer-facing verification remain unevidenced.
- Events now carry an optional, explicitly entered IANA `timezone_name`; Django validates it with
  `ZoneInfo`, and publication rejects a missing or invalid value while draft events may remain
  unset. The capture-time migration assigns `Europe/Moscow` only to the existing event with ID 9;
  it does not derive a timezone from city text or populate other events.
- Capture-metadata processor version 2 keeps the existing Django-polled worker boundary and its
  short-lived exact-object grants. Its event-specific immutable configuration records the event
  timezone. Within existing byte and pixel bounds, the worker reads JPEG and MPO EXIF metadata,
  including standards-defined nested capture fields; explicit EXIF offsets take precedence, and
  offset-less wall times are resolved through the configured event timezone. Successful results
  retain canonical UTC capture times with bounded source/provenance fields and warnings; new
  version-2 results never use `inferred_none`.
- The repository includes strict event-9 capture-time reprocessing and read-only aggregate-report
  commands. The reprocessing command defaults to dry run, requires an explicit apply, validates
  the approved event identity/cohort/configuration, and enrolls immutable version-2 work without
  rewriting prior attempts. The report emits bounded completion, timezone-state, warning, UTC, and
  event-local-hour aggregates.
- Release A was the deployed projection writer and direct current-v2 evidence reader. Its
  then-designated staging operational gate at commit `41e3068` had final global reconciliation clean at 17,043
  exact event-9 source/value pairs, and a transaction-rollback lifecycle smoke cleared then
  republished the derived projection without rewriting immutable evidence. The source of truth is
  still the current accepted version-2 attempt; `Photo.capture_time` and
  `Photo.capture_time_source_attempt` are a synchronous, rebuildable PostgreSQL read projection.
  Release B replaces the filtered gallery's direct JSON join/cast with the indexed
  `Photo.capture_time` range and retains no direct-reader fallback. On the immutable accepted local
  clone (9 events, 17,310 photos; event 9 has 17,043), final global reconciliation was clean before
  and after the read-only candidate benchmark, and every first/midpoint/last database and rendered
  ratio passed the 2x gate; see the [sanitized aggregate report](performance/2026-08-08-event-gallery-time-filter-local-clone.json).
  That local Release B candidate evidence preceded the deployed Release B commit `d5b21e4`.
  Future deployment candidates must pass the live all-events reconciliation and event-9 benchmark
  before service switch; the exact Release A image precondition is retired.
- Developers can stream a validated deployed-VM PostgreSQL logical dump through SSH and restore it only
  into the current checkout's isolated local Compose database when preparing a migration. The
  workflow rejects non-local Docker engines, serializes each Compose project/database, stops the
  normal local web service before replacement, keeps a local safety dump, and validates migration
  readiness without starting Django's mutating entrypoint. The web service remains stopped for an
  explicit restart after successful validation. This is not the service backup, retention, or
  disaster-recovery strategy.
- The repository implements the consented selfie-search quality-feedback path governed by
  [ADR 0023](adr/0023-store-consented-selfie-search-feedback.md): browser-local seven-day selfie
  preservation, one immutable feedback record per terminal search, optional saved-result labels,
  explicit consent and contact validation, restricted audited staff inspection, and dedicated
  private feedback storage with guarded 30-day lifecycle and deployment preflight commands. The
  implementation is disabled by default (`SELFIE_FEEDBACK_ENABLED=False`); no canonical-deployment
  activation, published-policy gate, bucket/KMS preflight, or customer-outcome evidence is
  claimed yet.
- The canonical-deployment Docker image runs migrations, collects static files, and starts Gunicorn.
- The accepted topology uses the shared Nginx/Certbot HTTPS edge to terminate trusted TLS and proxy
  the internal Django service; the canonical apex and `www` names route to that edge, with HTTP and
  alias traffic redirected to canonical HTTPS. Current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded, and the later [public-monitor run 32461320506](https://github.com/peter-nikitin/photo-prjct/actions/runs/32461320506) succeeded. This is not a direct host, DNS, certificate, or customer-path observation.
- Retained old Compose resources, when present, are rollback artifacts only and are not routine
  operator targets. The **Deploy** workflow selects `docker-compose.deployment.yml` and
  `docker-compose.https.yml` with Compose project `photo-prjct`.
- The shared HTTPS overlay terminates TLS, serves ACME HTTP-01 challenges, and Certbot manages
  Let's Encrypt certificates in persistent volumes. This accepted boundary is governed by
  [ADR 0011](adr/0011-use-minimal-shared-https-rollout.md).
- HTTPS deployment ensures a certificate exists, validates canonical redirects and trusted health
  with `curl`, restores the prior application image in process on failure, and records the successful
  image only after all checks pass. DNS is an activation preflight, and hostname changes require an
  operator-controlled certificate reissue.
- A merge to `main` builds an immutable image in GHCR and deploys it with Docker Compose to the
  canonical Yandex Cloud VM through **Deploy**. There is no promotion workflow or GitHub Environment
  deployment boundary.
- Pull-request CI treats every numbered migration already present on the base revision as an
  immutable identity: modifications, deletions, and renames fail the identity check, while new
  leaves and explicit merge migrations remain allowed. The deployment workflow classifies changes
  to the privileged selfie-observability package before building; such a push ends in a named,
  successful controlled pause with image build and application deployment skipped until the
  existing operator bootstrap and a manual deployment dispatch. Ordinary pushes retain the
  automatic SHA-image path.
- Before `mutation_started=1`, the candidate image performs read-only migration-history validation
  and prints its migration plan. Deployment emits a bounded `DEPLOY_PHASE` marker sequence and one
  sanitized `DEPLOY_RESULT`; automatic push failures reconcile one exact-title GitHub issue using a
  non-blocking, least-privilege notification job. Issue reconciliation is operational metadata,
  not a monitoring or audit-log replacement, and its failure cannot change deployment or rollback
  authority. These checks leave the existing SHA-tagged image, GHCR, Docker Compose, single-VM
  topology, root-owned observability package, and rollback path unchanged, conforming to
  [ADR 0003](adr/0003-docker-compose-yandex-cloud.md) and
  ADR 0028. Repository tests cover this transition; current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703) and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) ended with `DEPLOY_RESULT=success`. This Actions evidence does not directly validate the VM, deployed image, runtime configuration, or notification drill.
  Before a service switch, `apply-deployment.sh` pulls only the candidate
  web image. If no successful `deployed-image` marker exists, it emits the sanitized
  `gallery-private-media-preflight-skipped:no-existing-deployment` marker and continues the normal
  first-deployment path without constructing an ORM preflight container; this is not `GetObject`
  validation. Once that marker exists, the candidate runs its read-only gallery preflight against a
  mode-0600 temporary environment file and the existing Compose network/database. An eligible row
  must yield one nonempty byte and a sanitized success marker; no eligible row yields a distinct
  skip marker that is also not storage-permission evidence; an unavailable established database
  fails closed before deployment. Automated tests validate these paths and failure cleanup, but no
  canonical deployment, IAM state, cloud policy, or private object was changed or validated by this
  delivery.
- Unfinished screen concepts live only in the test-only Django visual-reference gallery under
  `tests/visual`. Playwright renders it through isolated settings and `/__visual__/` routes; neither
  the production URLconf nor the production Docker image includes the gallery. Visual regression
  runs in a separate digest-pinned Python/Node/Chromium container with an ephemeral PostgreSQL
  service, keeping local and CI rendering environments identical.
- Event covers use a public Yandex Object Storage bucket in deployed environments.

```text
Browser -> Nginx HTTPS edge -> Django/Gunicorn -> PostgreSQL
                                   |
                                   +-> packaged templates and static assets
                                   `-> private originals in Object Storage

GitHub Actions -> GHCR -> Yandex Cloud VM -> Docker Compose
                                             |- Django
                                             `- PostgreSQL
```

## Accepted constraints

- Start as a Django modular monolith; extract services only after measured operational need.
- Use PostgreSQL as the transactional system of record.
- Deploy the initial product as containers through Docker Compose on a Yandex Cloud VM.
- Operate one unqualified canonical deployment on the current VM; use runtime release gates for
  incomplete customer-facing functionality, as defined by [ADR 0028](adr/0028-operate-one-canonical-deployment.md).
- Load environment-specific configuration from environment variables and never commit secrets.
- Use one complete, versioned Yandex Lockbox secret as the persistent secret authority for the
  canonical deployment. Authorized developers read it through interactive `yc`; approved main-branch
  GitHub workflows use workload identity federation and resource-level payload access. Runtime
  services do not read Lockbox, as defined by ADR 0026 as superseded by ADR 0028.
- Keep architecture, decisions, and delivery plans in this repository.
- Prefer simple, repeatable operations over premature distributed infrastructure.
- Use the shared Nginx and Certbot HTTPS edge on the canonical deployment as defined by
  [ADR 0007](adr/0007-nginx-certbot-https-edge.md) and
  [ADR 0011](adr/0011-use-minimal-shared-https-rollout.md).
- Use Django sessions and the additive `ingestion.upload_photos` permission for photographer
  uploads. Staff and photographer access remain independent; authorized photographers see all
  events but own only their batches, as defined by
  [ADR 0012](adr/0012-use-django-photographer-permissions.md).
- Upload originals directly to a private incoming Object Storage prefix with constrained 10-minute
  grants, then promote verified objects to browser-inaccessible final keys under the lifecycle and
  access boundaries in [ADR 0013](adr/0013-use-direct-private-object-storage-ingestion.md).
- Keep Stage 2 ingestion control and confirmation request-driven, with bounded browser transfer
  concurrency and no worker or broker, as defined by
  [ADR 0014](adr/0014-keep-stage-2-ingestion-request-driven.md).
- Treat `EventFolder` as event-scoped catalog metadata, never as an Object Storage prefix. Django
  Admin manages normalized names inline with the event; a nullable protected folder assignment
  travels from an `UploadItem` through confirmation to its `Photo`. One browser queue and one
  batch may contain several named folders and `Без папки`. Reassigning already uploaded photos,
  including a photographer-facing mass editor and its authorization boundary, is explicitly
  deferred.
- Run the first Stage 3 photo processor as a separately runnable worker that polls a private Django
  API backed by PostgreSQL jobs and leases. Give it no database or permanent Object Storage
  credentials; issue only short-lived exact-object media grants, as defined by
  [ADR 0017](adr/0017-use-django-polled-photo-processing-jobs.md).
- Use Yandex Monitoring and one unprivileged Unified Agent for basic VM and private
  low-cardinality Django HTTP metrics. Check the canonical public HTTPS health endpoint through a
  managed probe outside Yandex Cloud, as defined by
  [ADR 0018](adr/0018-use-managed-yandex-monitoring.md).
- The `selfie_search` Django app implements two public event-scoped face-query sources. An uploaded
  selfie immediately creates the queued bearer-link result page; the existing worker returns one
  transient query embedding, Django loads and ranks the compatible event cohort once without
  persisting per-face candidate rows, deletes the temporary selfie before terminal publication,
  and serves a stable immutable result. An eligible gallery photo with one or more current
  compatible accepted faces exposes a direct one-face action or an explicit multi-face choice;
  Django uses the selected existing embedding to create an immediately ready immutable result
  without a temporary image, persisted query vector, or worker job. Both sources retain event
  isolation, the existing bearer/result-media rules, and the direct path's
  bounded field-only cohort reads; searches created before the direct-ranking change remain
  compatible with their already-persisted candidate rows. The direct path selects only the six
  identity/vector fields needed for ranking and does not hydrate the full embedding, detection,
  attempt, and photo model graph.
  On 2026-07-31, the then-designated staging deployment activated the existing selfie-upload path after applying the one-day
  `selfie-search/` lifecycle rule, passing real-bucket preflight, and verifying a live published
  Unicode event search, original-size result media, and paid-result-only media access. The
  repository default remains disabled. The gallery-photo query path has local focused test evidence
  only; no canonical-deployment evidence is
  claimed for it.
- Allow public event-scoped selfie searches to use the existing worker for temporary query
  embedding and Django for exact search, then publish immutable non-expiring bearer-link results
  only after deleting the selfie. A valid result link may deliver its matched originals for a
  published free or paid event without opening the normal paid gallery, as defined by
  [ADR 0019](adr/0019-use-public-event-selfie-search.md), which supersedes ADR 0015. Verified
  signed direct Object Storage redirect transport is implemented for already authorized gallery and
  result media under [ADR 0020](adr/0020-use-signed-direct-object-storage-media-delivery.md).
- Allow a currently rendered gallery photo with one or more current compatible accepted faces to
  start the same event-scoped exact ranking from the one face directly or from an explicitly
  selected face in a compact chooser. This creates an immediately ready immutable bearer result
  without a temporary image, stored query vector, or worker job, as defined by [ADR 0024](adr/0024-use-gallery-face-as-search-query.md).
- The repository implements browser-local reopening of existing selfie-search results: a result
  page saves only its canonical path, event slug, and open timestamp in versioned `localStorage`,
  and the matching event page renders that browser's list after JavaScript reads it. This adds no
  account, server-side history, synchronization, or request carrying history data. Before an
  explicit local button navigation, the event page keeps a bearer path out of its DOM, analytics,
  and background or history requests; it sends no local history list to Django. ADR 0019's bearer
  authorization, retention, event isolation, selfie cleanup, and query-vector boundaries are
  unchanged.
- Keep one consented quality-feedback record per terminal selfie search without delaying ADR 0019's
  temporary-selfie cleanup. The repository implementation retains the selected file locally for
  seven days, stores immutable feedback/contact/consent/labels in PostgreSQL, and stores one selfie
  in a dedicated private KMS-encrypted bucket whose 30-day lifecycle is authoritative. Public media
  routes and the ML worker cannot access feedback media, and staff access is explicit and audited,
  as defined by [ADR 0023](adr/0023-store-consented-selfie-search-feedback.md). The feature remains
  disabled by default and has no canonical-deployment activation evidence until its policy, bucket,
  KMS, and preflight gates are satisfied.
- The browser source boundary accepts JPEG, PNG, HEIC, and HEIF; Django bounds and decodes the
  source, preserving JPEG/PNG or normalizing HEIC/HEIF to canonical JPEG bytes before temporary
  storage and worker input. Stored objects and worker configuration remain canonical JPEG/PNG only,
  with no source metadata propagated. This conforms to ADR 0019; its privacy, event-isolation,
  bearer, and cleanup boundaries are unchanged.
- Allow attachment delivery wherever an existing normal-gallery or ready-result context already
  authorizes an original, without adding a free-versus-paid branch or opening a normal paid gallery,
  as defined by [ADR 0021](adr/0021-allow-original-download-for-authorized-photos.md). Verified
  one-click original downloads use stable gallery and ready-result application routes, reusing
  those contexts' existing authorization before redirecting to a short-lived signed exact-object
  attachment URL. Rendered cards provide a subdued download action, and GLightbox provides the
  same action in its built-in bottom description area. ADR 0019's result-membership and ADR 0020's
  transport, signing, expiry, and storage boundaries remain unchanged; commerce entitlements remain
  future work.
- For a new explicit paid-watermarked photo generation, accept one private clean preview for ML and
  one public-presentation watermarked preview. The repository implements the new explicit pair,
  independent clean-preview downstream enrollment, immutable watermark publication, and gated
  paid-gallery and ready-result presentation. Both paid presentation roles select only the accepted
  watermark and original presentation/download are denied; existing rows receive no backfill, as
  defined by [ADR 0029](adr/0029-use-watermarked-previews-for-paid-photos.md). The focused local
  Django checks passed on 2026-08-20. Current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. Neither paid runtime gate nor real paid artwork was directly observed as active. The anonymous cart consumes only this presentation boundary and cannot authorize media bytes. Customer activation still requires necessary-cookie legal review, worker/staff smoke evidence, and explicit gate activation. Purchase, entitlement, and purchased-original delivery remain unimplemented.
- Implement optional event-scoped selfie expansion from an immutable conservative face-cluster
  corpus. The repository builds and publishes versioned anonymous corpora from compatible accepted
  gallery embeddings, evaluates them through the private closed-benchmark CLI, records immutable
  direct/cluster provenance in PostgreSQL, and exposes bounded source-separated feedback and
  observability aggregates. Direct results remain first; unavailable or incompatible optional data
  falls back to the unchanged direct snapshot. `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False` is
  the repository and worktree default, and no canonical-deployment or customer activation is evidenced in
  this branch. The accepted design adds no named identity, cross-event matching, contextual
  evidence, automatic feedback tuning, persistent query vector, worker credential/configuration
  expansion, or online vector service, as defined by
  [ADR 0025](adr/0025-expand-selfie-search-with-face-clusters.md).
- Present normal galleries and ready selfie-search results as server-rendered numbered pages of at
  most 100 photos. Normal galleries use original filename then photo ID order; ready results retain
  persisted rank then photo ID order. [ADR 0022](adr/0022-use-numbered-gallery-pages.md) supersedes
  only ADR 0020's cursor-pagination follow-up.
- The deployment repository implements a bounded selfie-search operations slice: exact structured
  application/worker events, privacy-redacted Nginx routing, persistent host journald policy capped
  at 14 days and 1 GiB, stable Compose journal tags, and a daily Moscow-time summary timer. The
  deployment entrypoint reconciles and verifies these managed files with exact rollback. This is
  repository verification only; canonical-deployment activation evidence is not yet recorded. Journald is
  operational evidence, not a product-data backup. Dashboards, alert delivery, central/cloud
  logging, and biometric-quality benchmarking remain proposed or excluded.

## Deployment domain assignment — accepted

- The canonical deployment serves `https://findme-photo.ru/`.
- The accepted topology routes public DNS for the canonical and `www` names to the deployment VM,
  with the apex serving canonical HTTPS and HTTP and `www` requests redirecting to it. The successful
  [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) and later successful [public-monitor run 32461320506](https://github.com/peter-nikitin/photo-prjct/actions/runs/32461320506) are dated delivery evidence, not a direct host, DNS, TLS, or customer-path check.
- A future isolated test environment, if an availability-isolation need is accepted, uses a distinct
  name and decision; it does not change this deployment's identity.

## Target MVP architecture — proposed

The MVP remains one product with modules that have explicit responsibilities:

| Module | Responsibility | Status |
| --- | --- | --- |
| Catalog | Events, free/paid type, publication state, public pages | Implemented |
| Ingestion | Photographer permissions, request-driven batch upload, object promotion, and resumable upload state | Implemented |
| Media | Private originals and activation-gated previews; thumbnails, watermarks, and purchased exports | Implemented for originals, preview-first, and the gated paid-watermark repository slice; real watermark activation and purchased exports remain unimplemented |
| Recognition | Face, bib-region, OCR, image embeddings, and anonymous event-scoped face clusters | Preview-backed worker input/persistence plus the disabled-default offline face-cluster corpus path are implemented locally; canonical-deployment activation and customer outcomes are not evidenced |
| Search | Event-scoped face/bib/time/location queries | Public direct face search and disabled-default direct-first face-cluster expansion are implemented locally; no canonical-deployment activation or customer-outcome validation is claimed, and remaining modes are proposed |
| Moderation | Manual corrections, hiding, complaints, audit history | Proposed |
| Commerce | Anonymous event carts, promotions, orders, payment state, download entitlement | Disabled-default anonymous server-side event cart is merged; current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703) and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. Neither paid gate nor real paid assets were directly observed as active; legal-cookie review, worker/staff smoke, explicit gate activation, and live verification remain blockers; promotions, packages, orders, payment state, and download entitlement remain proposed |
| Operations | Processing visibility, structured logs, health and backups | Selfie structured-event/journald/daily-summary plus aggregate face-cluster report slice implemented in repository; dashboards, alerts, central logging, and backups proposed |

Logical module boundaries do not imply separately deployed services. Django owns product rules and
transactional state. Background processing and specialized ML runtimes may use separate containers
after their interfaces and operational costs are validated.

### Proposed deployment topology

```text
Customer/operator
      |
  HTTPS edge
      |
 Django application ------ PostgreSQL
      |                         |
      |                         `- transactional and audit data
      +---- object storage (private originals and derivatives)
      +---- task queue ---- workers ---- ML runtimes
      `---- vector search capability
```

The diagram shows target capabilities, not one delivery stage. Yandex Object Storage is selected by
ADR 0006, and Stage 2 ingestion reaches it directly without the task queue or workers. The queue,
broker, vector engine, and ML implementations shown for later processing require separate ADRs.

## Core data flows — proposed

### Photo ingestion and indexing

1. An authorized photographer creates a batch for any event. The photographer may access only their
   own batches; superusers retain administrative visibility. PostgreSQL preserves unfinished batch
   and item state, so an open upload page can list the photographer's unfinished batches and, after
   explicit reselection of local files, reconstruct its browser queue while skipping server-confirmed
   items. Each item durably retains its event-scoped folder or `NULL` (`Без папки`) across
   registration, retry, and resume; confirmation copies that exact assignment to the photo.
   Closing the page still stops unfinished browser transfers; it does not retain local-file access
   or continue transfer in the background.
2. A browser-managed queue uploads files with bounded concurrency to generated keys in a private
   incoming prefix using constrained 10-minute presigned POST grants.
3. In a confirmation request, Django verifies the incoming object and binds validation and
   server-side promotion to its ETag. The browser is never authorized to write the immutable final
   key.
4. Django records the confirmed original and upload state in PostgreSQL. Confirmed originals have no
   automatic deletion in this stage; unconfirmed objects become stale after 24 hours without
   activity.
5. The Stage 3 worker extracts bounded JPEG EXIF capture metadata through a Django-polled private
   API with one-at-a-time local worker concurrency, explicit per-photo states, immutable attempts,
   and immutable event-run reports. The preview-first implementation adds the versioned
   `generate_preview` processor: after explicit activation it normalizes one JPEG through an
   attempt-scoped temporary upload (the processing term, not a deployment name), Django verifies and publishes an immutable derivative, and only
   then makes the photo tile-eligible and queues preview-backed face work. Its Docker profile is
   locally opt-in and the API-only/no-credential container contract is locally verified. Tracked
   defaults remain disabled; lifecycle, ML-comparison, and capacity gates prevent canonical-deployment
   activation. The worker-image and deployment validator package the optional exact
   `2/generate_watermarked_preview/1` identity, but all worker and deployment defaults and the
   required preview-processing identity set omit it. The `paid-watermarked-previews` feature-flag
   row is absent or off by default, and no migration creates or enables it. A code deploy can
   therefore package placeholder assets but cannot enqueue the new policy or expose its public
   gallery to anonymous users. Activation must first use approved non-placeholder assets with their
   declared checksums, explicitly enable the worker identity, pass one real staff-only smoke, and
   only then enable the public gate. A broker remains later-stage design.
6. Recognition stages detect people/faces and likely bib regions, perform OCR, and create candidate
   embeddings. The implemented preview-first contract records preview coordinate space and source
   dimensions for face results. The repository includes the approval-gated version-4 candidate for
   one exact event and preserves its immutable evidence, but no canonical-deployment processing or activation
   is claimed. Each result records model version, confidence, geometry, and processing status.
7. Search indexes are updated only within the photo's event scope.
8. Operators can correct or suppress candidates. Manual decisions outrank automated results.
9. Failures remain visible and retryable without re-uploading the original.

### Public event-gallery filtering

1. The public event gallery first builds its normal eligible queryset, then derives the visible
   folder choices from that base set. A named folder and `Без папки` appear only when they contain
   an eligible photo; active folder and capture-time filters never make an existing choice vanish.
2. Repeated `folder` GET values and the explicit `unfiled` choice are validated against the current
   event and narrow the base queryset with folder `OR`; the capture-time range combines with that
   predicate using `AND`. Unknown, malformed, deleted, and foreign folder values are ignored.
3. Numbered page links preserve valid folder and time parameters, while reset removes both filter
   kinds. Empty intersections retain the stable controls and provide an accessible reset path.
4. Folder values do not grant authority. Existing gallery eligibility and media authorization run
   unchanged before any folder predicate, so filtering cannot expose non-public, paid, unprocessed,
   or cross-event media.

### Search

1. The customer selects an event before searching.
2. A bib query matches confirmed numbers first and automated candidates second. A face query uses
   either an uploaded selfie, which creates a temporary query embedding through the existing
   worker, or one explicitly selected current compatible accepted embedding from an eligible
   gallery photo. Django performs exact comparison and publishes an immutable probable-match
   snapshot; the selfie path deletes its temporary image before publication, while the gallery path
   is immediately ready and creates no temporary object or worker job. When the optional cluster
   gate is enabled and an explicitly activated compatible corpus exists, either query source may
   append unique cluster-expanded photos after the unchanged direct results; direct-only fallback
   remains complete for every missing, failed, or incompatible corpus outcome.
3. Every query filters by `event_id`; time and location further narrow results.
4. Face results are ordered by ascending cosine distance with stable photo-ID tie breaking and are
   exposed through the non-expiring public bearer link accepted by ADR 0019. Results from other
   events never enter the snapshot.

An event with the version-4 candidate still resolves its frozen baseline until an explicit guarded
activation appends the candidate selection. That selection is event-scoped and cannot mutate older
search snapshots, projections, attempts, or activation records; a rollback appends the preceding
generation selection. The candidate does not alter the ordinary `0.363` ranking threshold or the
immutable direct and optional cluster-expansion evidence that ADRs 0019, 0024, and 0025 require.

The worker-backed selfie source is implemented in the repository and locally verified with real
SCRFD/SFace inference for the submitted selfie query. The existing selfie E2E's gallery side uses
deterministic accepted embedding fixtures for historical stored `1/face_embedding/1` and current
preview-backed `2/face_embedding/3`; its preview-first member is canonical-deployment-reachable through an
accepted, verified `2/generate_preview/1` derivative and the resulting enrollment into
`2/face_embedding/3`.
That evidence covers a published paid event, frozen event-only candidates, stable ranked results,
selfie deletion before `ready`, and ready-result media for both generations without opening the
normal paid gallery. The existing immutable worker image packages pinned official SCRFD and OpenCV
Zoo SFace models and runs a non-root build-time smoke through both `face_embedding` and
`selfie_query`; the exact rollout image must run that same smoke before activation.
Public selfie search is always available when its existing processing prerequisites are healthy;
the retired availability switch is no longer an active setting. No temporary-lifecycle mutation,
bucket preflight, exact rollout-image smoke, VM capacity smoke, cluster
corpus activation, canonical-deployment activation, or customer outcome is claimed. `SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False`
remains the independent repository default. Corpus build, private benchmark, aggregate report, and
guarded activation commands are repository interfaces only until the release gate and an explicit
later rollout approve them.

The gallery-photo source is locally verified by 145 focused Python tests, 70 JavaScript tests for
the production markup and chooser behavior, and 83 visual tests covering the zero-, one-, two-,
and four-face event-gallery fixture at desktop and 390px mobile widths. The root `make check`
also passes with 1,256 tests passed and 3 skipped, 83.28% coverage, and clean system/migration
checks. The gallery-photo source has no canonical-deployment evidence. Public selfie search is not
controlled by an availability flag; the independent
cluster-expansion flag remains disabled by default.

### Purchase and download

ADR 0030 accepts an anonymous, event-specific cart stored in PostgreSQL and addressed by one opaque
browser cookie. Cart totals use the event's current per-photo price, and carts expire 30 days after
their last actual mutation. The repository implements this selection boundary: one `HttpOnly`,
`Secure`, `SameSite=Lax` browser cookie addresses server-side, event-separated carts; current
eligible watermarked items and current event price determine every response; the automatic Deploy
success path executes the committed [`apply-deployment.sh` install path](../deploy/apply-deployment.sh#L907).
Current main `be22bdd` passed [CI run 32457775703](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775703), and its automatic [Deploy run 32457775668](https://github.com/peter-nikitin/photo-prjct/actions/runs/32457775668) succeeded. Requests fail closed on expiry while physical deletion is left to cleanup. The `paid-photo-cart` runtime gate is absent/off by default and was not directly observed as active.

Public activation remains blocked on approved real watermark assets and worker, staff smoke,
necessary-cookie legal review, an explicit gate mutation, and live verification. The selection state
grants no download entitlement. Packages, checkout, payment, orders, promotions, entitlement, and
purchased-original delivery remain unimplemented and require separately approved work.

ADR 0031 accepts immutable single-event RUB Orders, normalized PaymentAttempts behind a narrow
bank adapter, authoritative server evidence or trusted manual payment confirmation, and permanent
original entitlement derived from each paid OrderItem. Anonymous access uses a separate temporary
purchase-browser capability plus permanent revocable Order grants. Email is asynchronous
notification, and a separate PostgreSQL-polling Commerce worker owns delivery and payment
reconciliation with durable operator attention. This accepted purchase boundary remains
unimplemented and off by default; concrete bank/email protocols, fiscal and legal contracts,
public activation, refunds, and ZIP delivery remain later work.

## Security, privacy, and legal boundaries

- Originals remain private storage objects. The implemented preview-first slice creates an
  unwatermarked, metadata-stripped reduced JPEG for a newly confirmed photo only after explicit
  activation; the free-event tile route uses the published derivative while the large route retains
  controlled inline original delivery under the policy now governed by ADR 0019. Until activation,
  explicit legacy photos use the original for both variants. The normal paid gallery remains
  unavailable; ADR 0019 permits only a valid ready face-search-result bearer link to deliver a saved
  free- or paid-event member. The repository also implements ADR 0029's new paid generation: only
  accepted watermarked derivatives reach its normal gallery and ready results, and original/download
  routes deny those photos before storage signing. The cart consumes that presentation boundary and
  cannot authorize bytes. Both paid-watermarked-preview and paid-cart runtime gates are absent or
  off by default; no direct runtime activation or real-media smoke is claimed. Purchases and entitled
  exports remain unresolved. Neither current route exposes a permanent storage key, but original
  delivery still gives an eligible recipient complete unsanitized bytes that can be saved or
  redistributed.
- Stage 2 browsers receive only exact-key, short-lived incoming-write grants. Restricted CORS and
  least-privilege credentials deny browser read, list, copy, delete, and final-key write access.
- Event-folder identifiers are catalog selectors, not media authority. Upload registration and
  confirmation require the folder to belong to the batch event; public GET filtering can only
  narrow the already-authorized gallery, and media delivery authorization is unchanged.
- Photographer routes require the additive upload permission, and non-superuser batch access is
  restricted to the owning uploader.
- Secrets and credentials are environment-provided; `.env` files remain untracked.
- The cart token is a narrow anonymous bearer for selection only, never a customer identity,
  selfie-result bearer, or media authority. PostgreSQL stores its SHA-256 digest, not the raw token;
  token-bearing cart responses are private and no-store. Public activation requires the pending
  necessary-cookie disclosure review before the explicit runtime gate is enabled.
- Face images and embeddings may be biometric personal data. ADR 0019 accepts a narrow MVP in
  which the selfie is deleted before terminal publication, the query embedding is not persisted,
  and the immutable result is accessible through a non-expiring bearer link. Broader consent,
  revocation, suppression, moderation, and incident handling remain required before named
  identity, cross-event matching, or broader biometric reuse.
- Face-cluster corpora are anonymous, event-scoped derived recognition data under PostgreSQL
  authority. They are derived only from compatible accepted gallery face embeddings and store
  immutable member/provenance references without duplicating gallery vectors; they do not contain a
  selfie query, query vector, contact, bearer token, person name, participant identifier, or
  cross-event relationship. The worker boundary is unchanged: it still receives only the transient
  selfie-query request and no corpus, gallery embedding, database, or permanent Object Storage
  credential.
- ADR 0024 accepts public reuse of one explicitly selected existing gallery face embedding as an
  event-scoped query. A one-face card submits directly; a multi-face card requires an explicit
  choice. It adds no stored query vector, temporary image, named identity, cross-event matching,
  or new media authorization.
- ADR 0023 accepts the narrower feedback-specific consent and retention boundary: one immutable
  quality report may retain plaintext contact, consent evidence, search labels, and a lifecycle-
  bounded private feedback selfie. It does not authorize named identity, automated training,
  cross-event reuse, or a general biometric consent ledger.
- Face results are probable matches, not identity assertions. Users and operators need removal and
  suppression workflows.
- Payment callbacks must be authenticated and idempotent; download authorization is derived from
  persisted order state.
- Administrative corrections and sensitive access require an audit trail.
- Open-source code, model weights, datasets, and hosted APIs require a commercial-use license review.
  AGPL components are not adopted without an explicit legal and architectural decision.
- Backups must cover transactional data and private media metadata; restore procedures must be tested.

## Evolution stages

1. **Foundation (current):** stable development checks, documentation, PostgreSQL, containers, CI/CD,
   and operational baseline.
2. **Event catalog:** minimal free/paid event domain, Django Admin management, publication, and a
   public catalog.
3. **Photo-bank core:** Django photographer permission, request-driven direct upload to private
   originals, then derivatives, explicit photo publication, and event galleries. Free events permit
   controlled anonymous original downloads; paid events expose only watermarked previews until
   commerce grants entitlement. Stage 2 upload itself adds no worker or broker.
4. **Repeatable processing:** versioned jobs and immutable analysis runs executed by a worker or ML
   container while Django and PostgreSQL retain product and transactional ownership.
5. **Bib validation and search:** benchmark detected regions and OCR on representative uploaded
   photos before delivering corrections and event-scoped search.
6. **Face governance, validation, and search:** approve biometric policy and independently benchmark
   face models before delivering embeddings, event filtering, removal, and candidate UX.
7. **Commerce:** the accepted anonymous event-cart selection boundary is implemented under its
   disabled-default runtime gate; separately approved packages, promotions, payment integration,
   orders, entitlements, and protected export for paid events remain later work.
8. **Operational readiness:** monitoring, alerting, backup/restore evidence, capacity limits, and runbooks.

### Remaining checkout, payment, entitlement, and original-delivery seam

Checkout, payment, entitlement, and original delivery may consume the cart's selected photo IDs,
current event price, `GalleryPhoto.photo_id`, its nullable `download_url`, and the existing
gallery-card action container. `PublicMediaResolver` remains the sole owner of public-media
selection; this seam defines no checkout, payment, order, entitlement, or original-delivery behavior.

## Deferred beyond MVP

- Full online photo editing and AI enhancement.
- Native mobile applications.
- Natural-language semantic search and recommendation systems.
- Automatic best-shot selection and advanced sequence grouping.
- Public partner APIs and event-registration integrations.
- Kubernetes, service mesh, and independently deployed microservices without demonstrated need.

## Open decisions

Each item needs evidence and an ADR before implementation commits the architecture:

- Stage 3 processing SLA and the measured threshold for replacing ADR 0017 polling with a broker.
- `pgvector` versus a dedicated vector database and migration thresholds.
- Broader biometric governance beyond ADR 0019's event-scoped public bearer-link MVP.
- Bib-region detection/OCR implementation and model licensing.
- Payment provider, callback contract, refunds, and download entitlement policy.
- Purchase entitlement and protected original delivery beyond ADR 0029's watermarked
  presentation-only boundary.
- Monitoring retention; backup targets; retention; RPO/RTO; encryption-at-rest policy; media
  recovery; and disaster-recovery procedures.
- CDN/WAF and static/media delivery topology beyond the Nginx edge.

## Change rules

- Update this document when system boundaries, deployed topology, or architectural status changes.
- Create an ADR for durable choices with meaningful alternatives or cross-cutting consequences.
- Do not rewrite accepted decisions here; summarize them and link to their ADR.
- Create an implementation plan before substantial delivery work and link it to relevant ADRs.
- Before approving a specification, read the ADR index and applicable ADRs and record exact related
  architecture, related ADRs, and ADR impact.
- After specification approval and before planning, resolve required new or superseding ADRs with
  explicit decision authority; specification approval alone does not accept an ADR.
- Before completing delivery, reconcile implemented behavior with the approved specification,
  applicable ADRs, and this document. Update implemented facts, or supersede a changed decision
  instead of editing an accepted ADR.
