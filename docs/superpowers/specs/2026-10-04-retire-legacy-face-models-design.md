# Retire legacy face-recognition models

- **Status:** Draft for maintainer review
- **Date:** 2026-10-04
- **Owner:** FindMe Photo
- **Related architecture:** [Photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing),
  [Search](../../architecture.md#search), [Accepted constraints](../../architecture.md#accepted-constraints)
- **Related ADRs:** [0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [0019](../../adr/0019-use-public-event-selfie-search.md),
  [0024](../../adr/0024-use-gallery-face-as-search-query.md),
  [0025](../../adr/0025-expand-selfie-search-with-face-clusters.md),
  [0040](../../adr/0040-use-pgvector-for-exact-face-search.md),
  [0041](../../adr/0041-accept-pgvector-numerical-boundaries.md),
  [0049](../../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md),
  [0051](../../adr/0051-release-photo-worker-images-independently.md)
- **Related work:** [Historical AdaFace backfill](2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md),
  [pgvector reader retirement](../../plans/2026-10-04-complete-pgvector-face-read-cutover.md)
- **ADR impact:** Requires new ADR for the durable AdaFace-only recognition and legacy-vector
  retirement boundary. Conforms to ADRs 0017, 0019, 0024, 0025, 0040, 0041, 0049 and 0051.
  ADR 0040 required SFace and AdaFace to coexist **during the storage transition** and explicitly
  deferred recognition-model retirement. This design completes that later phase without changing
  exact pgvector search, the AdaFace threshold, worker authority, or saved-result semantics. No
  accepted ADR is superseded.

## Outcome

New photo processing, selfie inference, gallery-face queries, direct search and optional cluster
expansion use one pinned recognition contract: SCRFD-10G_KPS detection plus AdaFace IR18
WebFace4M, 512-dimensional normalized embeddings, and the existing `0.42` cosine-distance
threshold. PostgreSQL `FaceEmbeddingVector` remains the sole embedding store, and direct search
remains an exact event-scoped pgvector scan. A customer cannot choose a model. No production
code, worker image, setting, fallback or special photo filter supports SFace or an earlier face
recognizer after retirement.

The completed historical backfill is a source of **AdaFace processing outcomes**, not a promise
that every former SFace face has an AdaFace counterpart. A photo with a terminal AdaFace no-face
or quality-rejected outcome contributes no new face. The 636 formerly searchable photos in the
published historical event that have no suitable AdaFace face are accepted as recognition-quality
differences; the old SFace result is not copied, synthesized or used to fill the gap.

## Current state to reconcile

The exact pgvector reader, native-only writer and physical removal of the JSON embedding table
were deployed on 2026-10-04. A read-only production audit on that date found one published
`sface_v3` event and eight unavailable `sface_v3` events. The published event has 17,043 photos:
16,162 accepted AdaFace photo outcomes, 225 no-face outcomes, 20 quality rejections and 636
photos with no suitable AdaFace replacement for a former SFace match. It has no unfinished
AdaFace jobs or active leases. Several unavailable test events have no source objects and were
not enrolled; they are retained as events, with empty current recognition rather than invented
AdaFace evidence. These figures are a dated observation, not a hard-coded migration list or a
substitute for a fresh release inventory.

The code still has an event-level SFace/AdaFace selector, SFace worker inference and image
artifact, multiple backend/worker contract branches, SFace-capable vector constraints, local
experiment and backfill interfaces, and historical SFace vectors. Saved ready search results
live in shared result tables; opening or downloading a saved result reads its stored photo
membership and current media authorization without rerunning recognition.

## Selected design

### One active recognition contract

- All newly created face-processing jobs and selfie-query jobs carry the pinned AdaFace
  configuration. The worker accepts only that current contract and returns only its validated
  512D embedding. Remove SFace inference, model loading, result validation, defaults, environment
  variables, build-time smoke, packaged ONNX artifact and production callback branches. Keep
  SCRFD, AdaFace and their dependencies; a library such as OpenCV remains where the current
  detector or image processing uses it. Remove obsolete model names from active code comments,
  type names and build metadata as well as executable branches.
- Remove the event-level model selector once every published event has an accepted AdaFace
  disposition and old work is terminal. Ordinary event creation, enrollment, search submission,
  ranking, gallery-face selection, reporting and offline cluster construction use the same current
  AdaFace contract. Retain generic processor version, configuration hash and immutable attempt
  identity where they record processing provenance; they do not select an old model at runtime.
- Eliminate old SFace generation builders, approval/activation branches, SFace dimensions and
  thresholds, local AdaFace experiment mode, temporary backfill interfaces and completed
  migration-only commands from the shipped runtime. Delete the dormant YuNet/SFace experiment
  implementation under `experiments/face_recognition_spike`, its executable tests, container and
  dependency extras; retain only research documents needed as historical context. Remove
  old-model-only production tests and fixtures; current tests exercise AdaFace processing,
  selfie and gallery queries, failed/missing native evidence, saved results, authorization and
  cluster behavior. Historical Django migrations and accepted ADRs remain immutable records and
  may still name SFace.

### Existing events and data

- For a published event, the current AdaFace attempt, detection, projection and vector evidence
  must be reconciled before its active SFace selection is removed. Terminal no-face and quality
  rejection are valid AdaFace outcomes. The former-SFace replacement count is reported for
  review, but it cannot veto acceptance of those terminal outcomes or cause a fallback to SFace.
  No event is switched by changing its generation field alone. Both selfie and gallery-face
  queries must work from the accepted AdaFace cohort after the switch.
- Unavailable test events stay unavailable and are not deleted. Those without viable original
  media receive no fabricated recognition result and expose an empty current AdaFace cohort.
  Their SFace selection is removed by the same one-time data transition; publication must still
  require the ordinary current processing and media checks. Draft events already on AdaFace are
  unaffected.
- Existing unfinished SFace searches and live SFace processing attempts must be terminal before
  old worker/code removal. New submissions never freeze an SFace configuration. Existing ready
  bearer-linked results keep their photo membership, order, provenance, feedback and media
  authorization; they are not reranked or rewritten. Historical search configurations and
  append-only processing records, detections and event-activation receipts remain as inert audit
  data, but no live ranking, job claim, gallery source or result-page renderer interprets them as
  a request to run SFace. The activation receipt may retain a generic read-only model for audit;
  it is not an input to the current cohort selector.
- Delete historical SFace vector rows from the shared pgvector table after active references and
  old processes are gone. Also redact any raw SFace embedding arrays retained inside historical
  `ProcessingAttempt.result`, `ProcessingLateReceipt.payload` or other persisted legacy JSON.
  Preserve non-vector outcome, timing, geometry, quality, status, attempt identity and the
  original `result_hash` as an audit receipt of the submitted payload; the hash is no longer a
  checksum of the redacted JSON. This bounded historical redaction must not erase an attempt,
  detection, projection or saved search result. The pgvector table's live integrity contract
  then permits only finite normalized 512D AdaFace vectors. Deactivate SFace cluster corpora;
  retain an immutable corpus only where saved search provenance still references it. New corpus
  builds and activations accept only the current model. Retained historical records are not
  alternate active recognition sources.

### Search, privacy and failure behavior

Both query sources use the same accepted AdaFace cohort and exact pgvector result rules:
event isolation, inclusive `0.42` threshold, best face per photo, deterministic ordering,
direct-first cluster expansion and immutable saved results. A missing, divergent or invalid
current vector fails closed. It never triggers an old-model retry, lookup or compatibility
adapter. A gallery photo without an accepted AdaFace face offers no face-search action; this
follows the ordinary cohort rule, not a special SFace filter.

The uploaded selfie and its transient query vector retain the existing deletion and
non-persistence guarantees. Model-retirement checks and receipts expose only aggregate counts,
generation names and timing; no vectors, photo/search/user IDs, object keys or bearer URLs.
Deleting SFace vectors reduces retained biometric material. Saved-result media still obeys
current event publication, photo visibility and paid-access rules.

## Alternatives rejected

1. Keep SFace inference or a conditional reader for old events or old result links. Ready links
   are stored snapshots and need no recognizer; active compatibility would perpetuate two
   contracts and their worker artifacts.
2. Copy a former SFace match into an AdaFace cohort or fabricate a 512D replacement when AdaFace
   found no suitable face. The vector spaces and recognition decisions differ; such a row would
   falsely claim current-model evidence.
3. Delete unpublished events or saved ready searches to simplify retirement. Their visibility
   and saved-result contracts are independent of active recognition. Minimal empty recognition
   handles unavailable test events.
4. Add a permanent per-event exception or UI filter for the remaining SFace data. The final
   runtime has one cohort rule and no SFace-specific product branch.

## Release and recovery contract

The release is complete only when a fresh inventory shows no published event selecting SFace,
no nonterminal SFace search or processing lease, no active SFace cluster corpus, and no runtime
path that can create or consume SFace evidence. The candidate web and independently released
remote worker must both support the AdaFace-only contract before old images/processes can leave
service. After SFace vectors are removed, recovery is forward with compatible images and the
verified database backup; an old image requiring SFace vectors is not a rollback target. A
partial transition stops without silently treating missing AdaFace evidence as zero matches for
an accessible published event. The one-time data operation is bounded, restartable, and reports
exact before/after aggregates without listing biometric identities.

## Acceptance criteria

1. Every published event uses the accepted AdaFace cohort. Unavailable and draft events remain
   nonpublic; unavailable events without sources have an empty current cohort. No SFace-active
   event, job, lease or cluster activation remains.
2. New photo and selfie worker contracts, web callbacks, event creation, search and offline
   corpus paths accept only the pinned AdaFace model. The worker image contains no SFace model
   file or environment path. Active source, configuration and build manifests have no SFace or
   YuNet implementation, branch or model artifact. The obsolete experiment code and its tests
   are removed; historical migrations, research documents and ADRs remain historical.
3. `FaceEmbeddingVector` contains no SFace rows and rejects a new 128D or SFace row. Historical
   attempt JSON and other live database payloads contain no raw SFace embedding arrays, while
   their non-vector history and original result hashes remain available. Every current AdaFace
   vector still has the correct accepted detection/projection identity; missing or invalid
   evidence fails closed. No second vector store or query-vector persistence appears.
4. Customer-equivalent uploaded-selfie and gallery-face searches on published events return
   exact AdaFace results under existing threshold, ordering, privacy and media rules. The 636
   accepted recognition differences are not filled with old faces. Old ready bearer links remain
   readable with unchanged saved membership and authorized media; old failed links remain
   terminal.
5. Database, web and worker read-back prove the active model, zero old-model writes/claims,
   absence of old vectors, healthy search/processing and no material latency or failure-rate
   regression. The release can be recovered forward from a documented compatible image and
   verified backup without SFace fallback.
