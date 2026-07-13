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
- PostgreSQL is configured entirely through environment variables.
- Local development uses Docker Compose for Django and PostgreSQL.
- A production Docker image runs migrations, collects static files, and starts Gunicorn.
- Staging runs a minimal HTTP Nginx edge that proxies to the internal Django service. Its public DNS
  record is currently unroutable, so the workflow verifies the VM-local edge only.
- A future production deployment uses an explicit HTTPS overlay where Nginx terminates TLS, serves
  ACME HTTP-01 challenges, and Certbot manages Let's Encrypt certificates in persistent volumes.
  This environment split is governed by [ADR 0009](adr/0009-separate-staging-http-edge.md).
- A merge to `main` builds an immutable image in GHCR and deploys it with Docker Compose to the
  staging Yandex Cloud VM. A separate manual workflow promotes that verified image to production
  after GitHub Environment approval; production infrastructure is not provisioned yet.
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
- Use the Nginx and Certbot HTTPS edge in production as defined by
  [ADR 0007](adr/0007-nginx-certbot-https-edge.md) and [ADR 0009](adr/0009-separate-staging-http-edge.md).

## Deployment domain assignment — accepted

- The current single active environment is assigned the canonical public URL
  `https://findme-photo.ru/`.
- When staging and production become separate live environments, `https://findme-photo.ru/` remains
  the production URL and staging moves to `https://staging.findme-photo.ru/`.
- This assignment records the intended routing contract; it does not claim that DNS or TLS rollout
  is already complete. Until that work is performed, the current preemptible VM retains the staging
  lifecycle and HTTP topology defined by ADR 0009, and the production readiness gate still applies.

## Target MVP architecture — proposed

The MVP remains one product with modules that have explicit responsibilities:

| Module | Responsibility | Status |
| --- | --- | --- |
| Catalog | Events, free/paid type, publication state, public pages | Implemented |
| Ingestion | Batch upload, file metadata, processing state, retries | Proposed |
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

The diagram shows required capabilities, not selected products. Object storage provider, queue and
broker, vector engine, and ML implementations require separate ADRs.

## Core data flows — proposed

### Photo ingestion and indexing

1. An operator creates an event and uploads a batch associated with an event, photographer, and
   optional location.
2. The application stores the original privately and records an immutable storage key.
3. Background work extracts EXIF metadata and generates thumbnail, preview, and watermarked assets.
4. Recognition stages detect people/faces and likely bib regions, perform OCR, and create candidate
   embeddings. Each result records model version, confidence, geometry, and processing status.
5. Search indexes are updated only within the photo's event scope.
6. Operators can correct or suppress candidates. Manual decisions outrank automated results.
7. Failures remain visible and retryable without re-uploading the original.

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

- Originals remain private storage objects. Public pages receive derived media: watermarked previews
  for paid events and unwatermarked reduced copies for free events. A free-event original may be
  delivered anonymously only through a controlled application response or short-lived access that
  does not expose its permanent storage key.
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
3. **Photo-bank core:** photographer access, private originals, batch ingestion, derivatives,
   explicit photo publication, and event galleries. Free events permit controlled anonymous original
   downloads; paid events expose only watermarked previews until commerce grants entitlement.
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

- Private media lifecycle and retention policy.
- Background task framework, broker, retry semantics, and processing SLA.
- `pgvector` versus a dedicated vector database and migration thresholds.
- Face detection/embedding implementation and biometric governance.
- Bib-region detection/OCR implementation and model licensing.
- Payment provider, callback contract, refunds, and download entitlement policy.
- Authentication model and photographer/operator permissions.
- Free/paid event media access and anonymous original-download policy.
- Observability stack, backup targets, retention, and recovery objectives.
- CDN/WAF and static/media delivery topology beyond the Nginx edge.

## Change rules

- Update this document when system boundaries, deployed topology, or architectural status changes.
- Create an ADR for durable choices with meaningful alternatives or cross-cutting consequences.
- Do not rewrite accepted decisions here; summarize them and link to their ADR.
- Create an implementation plan before substantial delivery work and link it to relevant ADRs.
