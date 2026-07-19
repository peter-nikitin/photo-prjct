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

- Django 6 serves the canonical production UI from server-rendered templates and local static CSS
  and SVG assets under `src/backend`.
- The `picflow` application owns the first target `Event` catalog model and a preliminary `Photo`
  model. Published events are managed through Django Admin and rendered by server-side templates.
- Published free-event detail pages select only completed uploaded `Photo` rows with a private
  original key and no legacy `src`, in stable ID order. A database-only factory converts them to
  immutable `GalleryPhoto` presentation values, so templates consume separate small- and
  large-preview application URLs without inspecting storage fields or selecting media variants.
- The current small- and large-preview resolver deliberately opens the same private original for
  both variants. The event-scoped GET endpoint rechecks publication, free access, and photo
  eligibility on every request, then streams the object inline with `private, no-store` caching and
  sanitized 404/503 outcomes. It exposes no permanent key, credential, S3 redirect, ETag, Range, or
  attachment behavior, and its owning iterator closes the storage body before iteration, at EOF,
  or after a read failure.
- Event galleries use locally packaged GLightbox 3.3.1 assets with normal anchor fallback.
  Task 6's browser run and inspected snapshots verified responsive populated and empty layouts,
  keyboard and pointer operation, mobile swipe, Escape/control close, focus restoration, and
  operation with JavaScript disabled. The first local current-HEAD rerun was infrastructure-blocked:
  after a Docker LinuxKit/API wedge and restart, two pre-existing catalog cases rendered with
  HTTP-200 resources but timed out waiting for `networkidle`. PR #45 CI run
  [29693681091](https://github.com/peter-nikitin/photo-prjct/actions/runs/29693681091) then passed all
  44 visual tests for current HEAD `7d6a718` in 47.7 seconds. Neither automated result represents a
  live staging activation.
- PostgreSQL is configured entirely through environment variables.
- Local development uses Docker Compose for Django and PostgreSQL.
- A production Docker image runs migrations, collects static files, and starts Gunicorn.
- Staging's normal deployment uses the shared Nginx/Certbot HTTPS edge to terminate trusted TLS and
  proxy the internal Django service. The canonical apex and `www` names route to that edge, with
  HTTP and alias traffic redirected to canonical HTTPS.
- `docker-compose.staging.yml` and `deploy/nginx/staging.conf` remain available only as a temporary
  manual HTTP recovery fallback while browser, internal-state, and renewal validation remain. The
  normal staging deploy workflow does not select them, and cleanup after validation remains required
  by [ADR 0011](adr/0011-use-minimal-shared-https-rollout.md).
- Every public environment will use one shared HTTPS overlay where Nginx terminates TLS, serves ACME
  HTTP-01 challenges, and Certbot manages Let's Encrypt certificates in environment-specific
  persistent volumes. This accepted transition is governed by
  [ADR 0011](adr/0011-use-minimal-shared-https-rollout.md).
- HTTPS deployment ensures a certificate exists, validates canonical redirects and trusted health
  with `curl`, restores the prior application image in process on failure, and records the successful
  image only after all checks pass. DNS is an activation preflight, and hostname changes require an
  operator-controlled certificate reissue.
- A merge to `main` builds an immutable image in GHCR and deploys it with Docker Compose to the
  staging Yandex Cloud VM. A separate manual workflow promotes that verified image to production
  after GitHub Environment approval; production infrastructure is not provisioned yet.
- Both deployment workflows propagate the existing private-media bucket and credential settings.
  Before environment promotion or any service switch, `apply-deployment.sh` pulls only the candidate
  web image. If no successful `deployed-image` marker exists, it emits the sanitized
  `gallery-private-media-preflight-skipped:no-existing-deployment` marker and continues the normal
  first-deployment path without constructing an ORM preflight container; this is not `GetObject`
  validation. Once that marker exists, the candidate runs its read-only gallery preflight against a
  mode-0600 temporary environment file and the existing Compose network/database. An eligible row
  must yield one nonempty byte and a sanitized success marker; no eligible row yields a distinct
  skip marker that is also not storage-permission evidence; an unavailable established database
  fails closed before promotion. Automated tests validate these paths and failure cleanup, but no
  staging deployment, IAM state, cloud policy, or private object was changed or validated by this
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
- Use the current preemptible VM for staging and a separate non-preemptible VM for production.
- Promote the same staging-verified image to production only after manual approval.
- Load environment-specific configuration from environment variables and never commit secrets.
- Keep architecture, decisions, and delivery plans in this repository.
- Prefer simple, repeatable operations over premature distributed infrastructure.
- Use the shared Nginx and Certbot HTTPS edge in every public environment as defined by
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
- Allow anonymous inline delivery of a complete private original only for an eligible completed
  upload in a currently published free event, within the narrow boundary of
  [ADR 0015](adr/0015-allow-anonymous-free-event-original-delivery.md). Paid-event media and
  broader attachment or download policy remain unresolved.

## Deployment domain assignment — accepted

- The current single active environment is assigned the canonical public URL
  `https://findme-photo.ru/`.
- When staging and production become separate live environments, `https://findme-photo.ru/` remains
  the production URL and staging moves to `https://staging.findme-photo.ru/`.
- Public DNS routes the canonical and `www` names to the current VM, where trusted HTTPS is active.
  The apex serves canonical HTTPS, and HTTP and `www` requests redirect to it. HTTPS activation does
  not change the current VM's preemptible staging classification or relax the production readiness
  gate.

## Target MVP architecture — proposed

The MVP remains one product with modules that have explicit responsibilities:

| Module | Responsibility | Status |
| --- | --- | --- |
| Catalog | Events, free/paid type, publication state, public pages | Implemented |
| Ingestion | Photographer permissions, request-driven batch upload, object promotion, upload state | Proposed |
| Media | Originals, thumbnails, previews, watermarks, purchased exports | Proposed |
| Recognition | Face, bib-region, OCR, and image embedding candidates | Proposed |
| Search | Event-scoped face/bib/time/location queries | Proposed |
| Moderation | Manual corrections, hiding, complaints, audit history | Proposed |
| Commerce | Cart, promotions, orders, payment state, download entitlement | Proposed |
| Operations | Processing visibility, structured logs, health and backups | Proposed |

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
   own batches; superusers retain administrative visibility.
2. A browser-managed queue uploads files with bounded concurrency to generated keys in a private
   incoming prefix using constrained 10-minute presigned POST grants.
3. In a confirmation request, Django verifies the incoming object and binds validation and
   server-side promotion to its ETag. The browser is never authorized to write the immutable final
   key.
4. Django records the confirmed original and upload state in PostgreSQL. Confirmed originals have no
   automatic deletion in this stage; unconfirmed objects become stale after 24 hours without
   activity.
5. Later background work extracts EXIF metadata and generates thumbnail, preview, and watermarked
   assets. Worker and broker technology remains undecided for that later stage.
6. Recognition stages detect people/faces and likely bib regions, perform OCR, and create candidate
   embeddings. Each result records model version, confidence, geometry, and processing status.
7. Search indexes are updated only within the photo's event scope.
8. Operators can correct or suppress candidates. Manual decisions outrank automated results.
9. Failures remain visible and retryable without re-uploading the original.

### Search

1. The customer selects an event before searching.
2. A bib query matches confirmed numbers first and automated candidates second. A face query creates
   a temporary query embedding and returns probable matches.
3. Every query filters by `event_id`; time and location further narrow results.
4. Results are ordered primarily by capture time. Results from other events, if offered, appear in a
   separate explicitly labelled section.

### Purchase and download

1. The cart contains event photos, prices, and any validated promotion.
2. A payment transition creates or updates an order idempotently.
3. For paid events, successful payment grants entitlement to generated exports; it never makes
   originals public. Free events use a separate controlled anonymous original-download policy.
4. Downloads use short-lived signed access or an authenticated application response and are audited.

## Security, privacy, and legal boundaries

- Originals remain private storage objects. The target policy is for public pages to receive derived
  media: watermarked previews for paid events and unwatermarked reduced copies for free events.
  During the current ADR 0015 transition, only eligible completed uploads for published free events
  use a controlled inline Django response backed by the original; paid-event media remains
  unavailable. The response does not expose the permanent storage key, but it delivers the complete
  unsanitized original and therefore cannot prevent recipients from saving or redistributing it.
- Stage 2 browsers receive only exact-key, short-lived incoming-write grants. Restricted CORS and
  least-privilege credentials deny browser read, list, copy, delete, and final-key write access.
- Photographer routes require the additive upload permission, and non-superuser batch access is
  restricted to the owning uploader.
- Secrets and credentials are environment-provided; `.env` files remain untracked.
- Face images and embeddings may be biometric personal data. Collection basis, consent, retention,
  deletion, access control, and incident handling must be approved before face search is released.
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
7. **Commerce:** pricing, promotions, payment integration, orders, entitlements, and protected export
   for paid events.
8. **Operational readiness:** monitoring, alerting, backup/restore evidence, capacity limits, and runbooks.

## Deferred beyond MVP

- Full online photo editing and AI enhancement.
- Native mobile applications.
- Natural-language semantic search and recommendation systems.
- Automatic best-shot selection and advanced sequence grouping.
- Public partner APIs and event-registration integrations.
- Kubernetes, service mesh, and independently deployed microservices without demonstrated need.

## Open decisions

Each item needs evidence and an ADR before implementation commits the architecture:

- Stage 3 background-processing worker, broker, retry contract, and processing SLA.
- `pgvector` versus a dedicated vector database and migration thresholds.
- Face detection/embedding implementation and biometric governance.
- Bib-region detection/OCR implementation and model licensing.
- Payment provider, callback contract, refunds, and download entitlement policy.
- Paid-event media access and the broader attachment/download policy beyond ADR 0015's narrow
  anonymous inline delivery for eligible free-event originals.
- Observability stack, backup targets, retention, and recovery objectives.
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
