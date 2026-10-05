# Архитектурные решения

ADR фиксирует долговременный выбор, причины, рассмотренные альтернативы и последствия.
[Архитектура](../architecture.md) описывает систему в целом, а код и операционные
свидетельства показывают фактическое состояние. Рабочие спецификации и планы удаляются
до слияния PR согласно [ADR 0055](0055-keep-accepted-decisions-not-working-documents.md).

<a id="lifecycle"></a>
## Статусы

- **Proposed** — решение обсуждается и пока не является обязательным.
- **Accepted** — решение принято и действует.
- **Rejected** — вариант рассмотрен и отклонён.
- **Superseded** — решение заменено более поздним ADR; оба документа ссылаются друг на друга.

Принятый ADR меняют только для исправления опечаток, форматирования и ссылок. Для изменения
самого решения создают новый ADR с очередным четырёхзначным номером и указывают взаимные
ссылки. Новые ADR пишутся по-русски; существующие английские тексты пока сохраняются.
Для нового решения используйте [шаблон](0000-template.md) как структуру, не редактируя его
вместо создания отдельного файла. Затем добавьте запись в указатель ниже.

<a id="index"></a>
## Указатель


| Number | Decision | Status |
| --- | --- | --- |
| 0001 | [Use a Django modular monolith](0001-django-modular-monolith.md) | Accepted |
| 0002 | [Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md) | Accepted |
| 0003 | [Deploy with Docker Compose to Yandex Cloud](0003-docker-compose-yandex-cloud.md) | Accepted |
| 0004 | [Keep engineering knowledge in the repository](0004-repository-engineering-knowledge.md) | Superseded |
| 0005 | [Promote immutable images through staging](0005-promote-images-through-staging.md) | Superseded |
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
| 0016 | [Allow deterministic staging reference media](0016-allow-deterministic-staging-reference-media.md) | Rejected |
| 0017 | [Use Django-polled photo-processing jobs](0017-use-django-polled-photo-processing-jobs.md) | Accepted |
| 0018 | [Use managed Yandex Monitoring with independent public probes](0018-use-managed-yandex-monitoring.md) | Accepted |
| 0019 | [Use public event-scoped selfie search](0019-use-public-event-selfie-search.md) | Accepted |
| 0020 | [Use signed direct Object Storage media delivery](0020-use-signed-direct-object-storage-media-delivery.md) | Accepted |
| 0021 | [Allow original download for authorized photos](0021-allow-original-download-for-authorized-photos.md) | Accepted |
| 0022 | [Use numbered gallery pages](0022-use-numbered-gallery-pages.md) | Accepted |
| 0023 | [Store consented selfie-search quality feedback](0023-store-consented-selfie-search-feedback.md) | Accepted |
| 0024 | [Use a selected gallery face as a search query](0024-use-gallery-face-as-search-query.md) | Accepted |
| 0025 | [Expand selfie search with conservative face clusters](0025-expand-selfie-search-with-face-clusters.md) | Accepted |
| 0026 | [Use Lockbox for environment secrets](0026-use-lockbox-for-environment-secrets.md) | Superseded |
| 0027 | [Project current capture time onto Photo](0027-project-capture-time-onto-photo.md) | Accepted |
| 0028 | [Operate one canonical deployment](0028-operate-one-canonical-deployment.md) | Accepted |
| 0029 | [Use watermarked previews for paid photo presentation](0029-use-watermarked-previews-for-paid-photos.md) | Accepted |
| 0030 | [Use anonymous server-side event carts](0030-use-anonymous-server-side-event-carts.md) | Accepted |
| 0031 | [Use orders and adapters for paid original delivery](0031-use-orders-and-adapters-for-paid-original-delivery.md) | Accepted |
| 0032 | [Reconcile code-owned feature flags at startup](0032-reconcile-code-owned-feature-flags-at-startup.md) | Accepted |
| 0033 | [Keep durable knowledge and test executable contracts](0033-keep-durable-knowledge-test-executable-contracts.md) | Superseded |
| 0034 | [Stream page-scoped photo archives through Django](0034-stream-page-scoped-photo-archives-through-django.md) | Accepted |
| 0035 | [Use Django-polled Yandex Disk import](0035-use-django-polled-yandex-disk-import.md) | Accepted |
| 0036 | [Issue direct gallery small-preview capabilities](0036-issue-direct-gallery-small-preview-capabilities.md) | Superseded |
| 0037 | [Use a gallery-media read projection](0037-use-gallery-media-read-projection.md) | Accepted |
| 0038 | [Deliver gallery grid images through CDN and imgproxy](0038-deliver-gallery-grid-images-through-cdn-and-imgproxy.md) | Accepted |
| 0039 | [Run the public health probe on the image-origin VM](0039-run-public-probe-on-image-origin-vm.md) | Accepted |
| 0040 | [Use pgvector for exact event-scoped face search](0040-use-pgvector-for-exact-face-search.md) | Accepted |
| 0041 | [Accept numerical boundary differences in exact pgvector search](0041-accept-pgvector-numerical-boundaries.md) | Accepted |
| 0042 | [Isolate autoscaled photo worker pools](0042-isolate-autoscaled-photo-worker-pools.md) | Accepted |
| 0043 | [Observe isolated workers with Git-managed alerts](0043-observe-isolated-workers-with-git-managed-alerts.md) | Accepted |
| 0044 | [Deliver public event covers through the image CDN](0044-deliver-public-event-covers-through-image-cdn.md) | Accepted |
| 0045 | [Deliver commerce thumbnails through the gallery CDN](0045-deliver-commerce-thumbnails-through-gallery-cdn.md) | Accepted |
| 0046 | [Isolate worker-pool management in a separate folder](0046-isolate-worker-pool-management-in-a-separate-folder.md) | Accepted |
| 0047 | [Separate Order payment from new cart selection](0047-separate-order-payment-from-new-cart-selection.md) | Accepted |
| 0048 | [Reuse Managed Prometheus for worker alerts](0048-reuse-managed-prometheus-for-worker-alerts.md) | Accepted |
| 0049 | [Retire local photo-worker recovery after remote acceptance](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md) | Accepted |
| 0050 | [Decouple processing jobs and attempts from worker builds](0050-decouple-processing-queue-from-worker-builds.md) | Accepted |
| 0051 | [Release photo-worker images independently](0051-release-photo-worker-images-independently.md) | Accepted |
| 0052 | [Notify only on actionable service degradation](0052-notify-only-on-actionable-service-degradation.md) | Accepted |
| 0053 | [Reconcile observability independently on main](0053-reconcile-observability-independently-on-main.md) | Accepted |
| 0054 | [Retire SFace and fix the AdaFace vector dimension](0054-retire-sface-and-fix-adaface-vector-dimension.md) | Accepted |
| 0055 | [Хранить принятые решения, а не рабочие спецификации и планы](0055-keep-accepted-decisions-not-working-documents.md) | Accepted |
