# Architecture Decision Records

ADRs capture durable decisions that materially affect system structure, operations, security, data,
or development workflow. They explain why a choice was made; `architecture.md` describes the
resulting system.

## Lifecycle

- **Proposed**: under discussion; implementation must not rely on it as settled.
- **Accepted**: approved and authoritative.
- **Rejected**: considered but not selected.
- **Superseded**: replaced by a later ADR, linked in both records.

Accepted ADRs are immutable except for spelling, formatting, and link corrections. Change a decision
with a new ADR that supersedes the old one. Allocate the next four-digit number and use a lowercase
hyphenated filename: `NNNN-short-title.md`. Copy [the template](0000-template.md), never edit it in
place, and add the new record to this index.

## Index

| Number | Decision | Status |
| --- | --- | --- |
| 0001 | [Use a Django modular monolith](0001-django-modular-monolith.md) | Accepted |
| 0002 | [Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md) | Accepted |
| 0003 | [Deploy with Docker Compose to Yandex Cloud](0003-docker-compose-yandex-cloud.md) | Accepted |
| 0004 | [Keep engineering knowledge in the repository](0004-repository-engineering-knowledge.md) | Accepted |
| 0005 | [Promote immutable images through staging](0005-promote-images-through-staging.md) | Accepted |
| 0006 | [Use Yandex Object Storage for media](0006-yandex-object-storage-media.md) | Accepted |
| 0007 | [Use Nginx and Certbot for the HTTPS edge](0007-nginx-certbot-https-edge.md) | Accepted |
| 0008 | [Temporarily allow HTTP-only staging when public DNS is unroutable](0008-temporary-staging-http-fallback.md) | Superseded |
| 0009 | [Separate the staging HTTP edge from the production HTTPS edge](0009-separate-staging-http-edge.md) | Superseded |
| 0010 | [Share the HTTPS edge across public environments](0010-share-https-edge-across-environments.md) | Superseded |
| 0011 | [Use a minimal shared HTTPS rollout](0011-use-minimal-shared-https-rollout.md) | Accepted |
| 0012 | [Use Django photographer permissions](0012-use-django-photographer-permissions.md) | Accepted |
| 0013 | [Use direct private Object Storage ingestion](0013-use-direct-private-object-storage-ingestion.md) | Accepted |
| 0014 | [Keep Stage 2 ingestion request-driven](0014-keep-stage-2-ingestion-request-driven.md) | Accepted |
| 0015 | [Allow anonymous free-event original delivery](0015-allow-anonymous-free-event-original-delivery.md) | Superseded |
| 0016 | [Allow deterministic staging reference media](0016-allow-deterministic-staging-reference-media.md) | Proposed |
| 0017 | [Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md) | Accepted |
| 0018 | [Use managed Yandex Monitoring with independent public probes](0018-use-managed-yandex-monitoring.md) | Accepted |
| 0019 | [Use public event-scoped selfie search](0019-use-public-event-selfie-search.md) | Accepted |
| 0020 | [Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md) | Accepted |
| 0021 | [Allow original download for authorized photos](0021-allow-original-download-for-authorized-photos.md) | Accepted |
| 0022 | [Use numbered gallery pages](0022-use-numbered-gallery-pages.md) | Accepted |
| 0023 | [Store consented selfie-search quality feedback](0023-store-consented-selfie-search-feedback.md) | Accepted |
| 0024 | [Use a selected gallery face as a search query](0024-use-gallery-face-as-search-query.md) | Accepted |
| 0025 | [Expand selfie search with conservative face clusters](0025-expand-selfie-search-with-face-clusters.md) | Accepted |
| 0026 | [Use Lockbox for environment secrets](0026-use-lockbox-for-environment-secrets.md) | Accepted |
| 0027 | [Project current capture time onto Photo](0027-project-capture-time-onto-photo.md) | Accepted |

## Public selfie-search outcome

[ADR 0019](0019-use-public-event-selfie-search.md) supersedes
[ADR 0015](0015-allow-anonymous-free-event-original-delivery.md). The repository implementation
conforms to ADR 0019's Django/PostgreSQL authority, private worker, event isolation, transient query
embedding, cleanup-before-publication, stable bearer result, and paid-result-only media boundaries.
ADR 0020 supersedes only the inline-Django transport for already authorized gallery and result
media; its direct Object Storage delivery is accepted but not yet implementation evidence. Lifecycle
mutation, real-storage preflight, exact rollout-image model smoke, staging capacity evidence, and
feature activation remain rollout work rather than completed decision evidence. The existing worker
image already packages pinned public OpenCV Zoo YuNet/SFace files; it is not a new worker or a
future private-model delivery mechanism.

[ADR 0021](0021-allow-original-download-for-authorized-photos.md) supersedes only ADR 0019 and
ADR 0020's attachment-download exclusions. It accepts attachment delivery wherever those existing
gallery or ready-result contexts already authorize an original, without adding a free-versus-paid
decision or opening a normal paid gallery.

[ADR 0022](0022-use-numbered-gallery-pages.md) supersedes only ADR 0020's cursor-pagination
follow-up. Normal galleries and ready selfie-search results use bounded numbered pages; all media
delivery and authorization decisions in ADRs 0019, 0020, and 0021 remain unchanged.

[ADR 0023](0023-store-consented-selfie-search-feedback.md) preserves ADR 0019's search-selfie
cleanup gate while accepting a separate consented feedback copy: the browser retains the selected
file locally for at most seven days, PostgreSQL stores one immutable feedback/contact/consent record
per search, and a dedicated private KMS-encrypted bucket deletes feedback selfies through its
30-day lifecycle. Feedback media is unavailable to public media routes and the ML worker; sensitive
staff access is explicit and audited.

[ADR 0025](0025-expand-selfie-search-with-face-clusters.md) extends ADR 0019's direct-only result
membership with conservative event-scoped face-cluster expansion from calibrated strong direct
anchors. Direct results remain first; PostgreSQL stores immutable source provenance and counts;
feedback and bounded observability distinguish direct from expanded results without changing
clusters automatically. Named identity, cross-event matching, contextual evidence, and persistent
query vectors remain excluded.

[ADR 0024](0024-use-gallery-face-as-search-query.md) accepts one explicitly selected current
compatible face embedding of an existing gallery photo as a second event-scoped query source. A
single usable face submits directly; multiple usable faces require an explicit crop-based choice.
It preserves ADR 0019's ranking, immutable bearer result, and media-authorization boundaries
without creating a crop object, temporary image, or worker job.
