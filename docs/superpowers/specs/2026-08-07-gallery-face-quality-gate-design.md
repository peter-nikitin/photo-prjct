# Gallery Face Quality Gate Design

- **Status:** Approved in conversation and written review on 2026-08-07
- **Date:** 2026-08-07
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), implemented photo-processing
  control plane, versioned face-embedding generations, public event-scoped selfie search, derived
  recognition data, and immutable face-cluster corpora
- **Related product job:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
- **Related specifications:**
  [`2026-07-26-all-people-face-clustering-design.md`](2026-07-26-all-people-face-clustering-design.md),
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md), and
  [`2026-08-05-selfie-search-face-cluster-expansion-design.md`](2026-08-05-selfie-search-face-cluster-expansion-design.md)
- **Related ADRs:**
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0025](../../adr/0025-expand-selfie-search-with-face-clusters.md)
- **ADR impact:** **Conforms to ADR 0017, ADR 0019, and ADR 0025.** The design preserves the
  Django-owned immutable processing control plane, event-scoped probable-match search, transient
  query vectors, immutable bearer snapshots, and versioned anonymous cluster corpora. The quality
  rule and its calibrated thresholds are reversible processor implementation details, so no new
  ADR is required.

## Outcome

New selfie searches compare query faces only with gallery-face embeddings accepted by a calibrated
recall-first quality gate. Strongly blurred and unusably small background faces no longer receive
an embedding and therefore cannot enter direct ranking or a newly built face-cluster corpus.

The change must not clean the index by sacrificing useful clear faces. Ambiguous faces remain
eligible. A new processor generation is accepted only after a local comparison on the same photo
corpus as the latest published event shows no manually confirmed clear-face loss and no confirmed
relevant search-result loss.

Existing processing attempts, detections, embeddings, cluster corpora, and ready bearer results
remain immutable. The new generation is produced beside them and becomes eligible for new searches
only through a separate explicit activation after human review.

## Current gap

The current gallery `face_embedding` configuration declares `min_face_px`, but the gallery worker
does not apply that value before extracting an SFace embedding. The worker also has no gallery-face
sharpness or relative-area decision. Every selected YuNet detection that produces an embedding can
therefore be persisted as a kept detection and enter the compatible search cohort, including small
blurred people in the background.

The direct-search cosine threshold is not the source of this gap. It controls comparison against
already stored vectors and remains unchanged by this design.

## Success criteria

The quality-gated generation succeeds when:

- every selected YuNet detection receives a deterministic versioned quality decision before SFace
  embedding, while detections beyond the existing bounded per-photo limit remain explicit
  truncation evidence;
- clearly unusable small faces and severely blurred faces are rejected, while borderline cases
  remain accepted unless a second weak-quality signal corroborates the rejection;
- an accepted face has exactly one compatible normalized SFace embedding;
- a quality-rejected face has explicit bounded quality evidence and no embedding;
- the same quality configuration produces the same decision for the same decoded input and model
  outputs;
- old and new generations coexist without mutation, deletion, or implicit fallback;
- one search snapshot and one face-cluster corpus use only explicitly compatible approved
  generations and never mix old and new face populations;
- a complete local benchmark on the latest published event's frozen photo corpus reviews every new
  rejection and demonstrates zero manually confirmed clear-face losses;
- a closed search comparison demonstrates zero lost manually confirmed relevant photos;
- incomplete, failed, or unapproved benchmark evidence cannot activate the new generation; and
- rollback selects the previous generation for future searches without changing historical rows or
  existing bearer results.

## Scope

### Included

- A new immutable gallery face-embedding processor generation using the existing YuNet detector,
  SFace recognizer, and 128-dimensional normalized vector representation.
- A deterministic multi-signal recall-first gate using detection confidence, face size, relative
  face area, and normalized-crop sharpness.
- Explicit accepted and quality-rejected face records with bounded decision evidence.
- Computing and persisting an embedding only for an accepted detection.
- Exact processor-configuration identity, including quality algorithm and calibrated thresholds.
- Side-by-side old/new processing of the same frozen photo inputs from the latest published event.
- Immutable detection-level and search-level benchmark runs, manual labels, reports, and an
  explicit approval boundary.
- Explicit compatible-generation selection for new direct searches and new face-cluster corpora.
- Rollback to the previous generation for future searches.
- Critical-path quality-decision, persistence, compatibility, benchmark, activation, privacy, and
  regression contracts.

### Excluded

- Mutating or deleting old processing attempts, detections, embeddings, or cluster corpora.
- Recomputing, rewriting, or invalidating existing ready bearer results.
- Changing the direct-search cosine threshold or cluster thresholds as part of quality filtering.
- Changing YuNet, SFace, embedding dimensions, or vector normalization.
- A learned face-quality model or a new ML runtime.
- Named identity, cross-event recognition, participant counting, or automatic identity claims.
- Automatically changing thresholds from customer feedback or benchmark results.
- Activating a new search generation merely because processing or benchmark execution completed.
- Committing event photos, face crops, query images, embeddings, labels, or benchmark artifacts to
  Git.
- A general-purpose experiment registry, vector database, ANN service, broker, dashboard, or new
  cloud service.

## Selected design

### New immutable processor generation

The quality rule changes which detected faces receive embeddings, so it is a new processor
generation rather than an in-place configuration adjustment. Its identity freezes the processing
contract, processor version, YuNet and SFace identities, vector dimensions and normalization,
input-media identity, quality algorithm version, threshold values, and normalized configuration
hash.

The generation does not replace historical data. A photo processed by both versions has separate
immutable attempts and generation-bound detections. A new attempt consumes the same immutable
source used by the baseline attempt: the original for an original-backed generation or the exact
published preview derivative for a preview-backed generation. Comparing results produced from
different media is not valid benchmark evidence.

Processor completion never activates search compatibility. Search cohort selection explicitly
lists approved generations. Until human approval, the new generation is queryable only by the
private benchmark path and cannot contribute to a customer result.

After activation, new searches use the approved new generation. Historical embeddings remain in
PostgreSQL but are excluded from the new cohort. Existing ready bearer results retain their saved
membership, order, score evidence, media authority, and generation evidence.

An active face-cluster corpus is also generation-bound. The existing corpus is not mutated or
silently combined with new embeddings. Cluster expansion for the new generation requires a new
complete immutable corpus and a separate explicit corpus activation under ADR 0025.

### Quality measurements

For every selected YuNet detection, the worker produces a bounded quality record before attempting
SFace embedding:

- detector confidence;
- minimum bounding-box side in pixels;
- bounding-box area divided by decoded input area;
- sharpness measured on a deterministic grayscale face crop normalized to the quality algorithm's
  fixed dimensions;
- decision `accepted` or `quality_rejected`; and
- a stable ordered set of rejection reasons.

The fixed-size sharpness crop makes scores comparable across face sizes and avoids treating raw
Laplacian variance from differently sized crops as the same measurement. Crop construction,
resampling, grayscale conversion, border handling, and sharpness formula are part of the quality
algorithm version. Diagnostic crops are benchmark artifacts only and are not production database
or Object Storage records.

Every numeric input and derived value must be finite and bounded. Invalid geometry or an invalid
quality calculation is a processor error, not evidence that the face is poor.

The existing bounded maximum detections per photo remains an independent resource limit. A
detection omitted because that limit was reached is reported as truncated; it is not represented as
quality-rejected and does not influence threshold calibration.

### Recall-first decision rule

The gate favors retention because the current search already loses valid appearances. It uses two
levels of rejection:

1. **Hard rejection:** the face is below the calibrated physically usable minimum size, or its
   sharpness is below the calibrated severe-blur threshold.
2. **Corroborated rejection:** sharpness is below a less severe borderline threshold and at least
   one supporting signal is also weak: relative face area or detector confidence.

All other detections are accepted. In particular, one borderline metric does not reject a face,
and a small relative area does not reject an otherwise sufficiently large, sharp, confident face.
The severe-blur threshold must be strictly more selective than the borderline threshold.

Rejection reasons distinguish at least `too_small`, `severe_blur`, and the contributing reasons for
a corroborated low-quality decision. Stable reasons are evidence, not customer-facing identity or
quality claims.

The previous experiment's values—detection confidence `0.82`, minimum side `32 px`, relative area
`0.0009`, and sharpness `50`—are candidate calibration points only. They are not production defaults
accepted by this specification because the production media path and normalized sharpness
measurement differ. Approved values come only from the benchmark defined below.

### Persistence contract

Every valid selected detection is represented in immutable attempt evidence:

- an accepted detection has status `kept`, its quality record, and one SFace embedding when
  embedding succeeds;
- a quality-rejected detection has a distinct rejected status, its quality record and reasons, and
  no `FaceEmbedding` row; and
- an alignment, inference, invalid-vector, or quality-computation failure is represented as a
  technical failure, not as `quality_rejected`.

The attempt artifact reports detection count, accepted count, quality-rejected count, embedding
success count, and bounded counts by rejection or technical-failure reason. Aggregate reports keep
these categories separate. A successful attempt may contain both accepted and quality-rejected
faces, but it cannot persist a partial record for a face whose quality decision failed validation.

Quality evaluation is fail-open only at the face-selection policy boundary: an explicitly
supported, finite borderline measurement retains the face. Unexpected exceptions, invalid numbers,
invalid geometry, or missing required fields fail the attempt or face record explicitly; they do
not silently accept or reject it.

### Search and corpus compatibility

Direct search loads embeddings only from the generation set frozen in the search configuration.
New searches after activation use only the approved quality-gated set. No search compares the same
query against a union of baseline and candidate embeddings.

The immutable result keeps generation evidence sufficient to reproduce cohort eligibility. Search
ordering and cosine comparison remain unchanged. Filtering happens before vector persistence, not
inside ranking.

A face-cluster corpus declares one compatible approved face population. New quality-gated
embeddings can enter only a new corpus version. A corpus build, corpus publication, corpus
activation, search-generation activation, and customer search remain separate state transitions.

## Local benchmark and calibration

### Frozen comparison cohort

The benchmark targets the exact photo corpus of the latest published event selected for evaluation.
Its manifest freezes the event identity, sorted photo identities, accepted input object or
derivative identities, baseline generation, candidate generation, model identities, algorithm
configuration, and inventory hash.

Baseline and candidate runs must cover the same inventory and the same immutable bytes. Missing,
changed, or differently sourced inputs are reported as cohort mismatches rather than silently
dropped. The benchmark is complete only when every declared photo has a terminal comparable state
or appears in an explicit unresolved list. A run with any unresolved item may be inspected but
cannot support activation.

Each candidate threshold configuration publishes a new immutable run. Calibration never overwrites
an earlier run. If evidence requires a change, one threshold changes at a time so its effect remains
attributable.

### Detection-level comparison

Baseline and candidate faces are matched within the same photo using deterministic bounding-box
overlap and stable tie-breaking. The report includes:

- photos processed and unresolved;
- detected, accepted, rejected, and embedded face counts;
- old-only, new-only, matched, retained, and newly rejected faces;
- distributions and threshold bands for every quality metric;
- counts by rejection and technical-failure reason;
- a review bundle containing every face embedded by the baseline and rejected by the candidate;
  and
- a stratified sample of retained faces near each threshold to expose blur that remains accepted.

Every newly rejected face receives one manual label: `clear`, `blurred`, `unusably_small`, or
`uncertain`. `Uncertain` is not evidence that deletion is safe. Candidate acceptance requires zero
`clear` labels among new rejections. The report separately shows how many labelled blurred and
unusably small embeddings the gate removed; it does not present unlabelled faces as confirmed
quality improvements.

### Search-level comparison

The same closed query set runs independently against the frozen baseline and candidate cohorts.
Queries may be consented temporary selfies under their approved retention boundary or gallery-face
proxies. A gallery proxy excludes every face from its source photo from the candidate cohort, not
only the selected face, so the query cannot match its own photo.

The comparison records unique-photo top-1, top-5, and top-10 results, total unique results, manually
confirmed relevant results, and every result that disappears only because its supporting face was
quality-rejected. Search uses the unchanged direct cosine threshold and deterministic ordering.

The candidate requires zero lost manually confirmed relevant photos. A gain or loss from any other
model, search-threshold, cluster-threshold, or corpus change invalidates attribution to the quality
gate.

Temporary query images, query vectors, review crops, manual labels, and detailed benchmark
artifacts remain in a private local benchmark directory outside Git. Query images and vectors are
deleted at the approved benchmark boundary. The repository may retain only bounded non-biometric
aggregate evidence and the accepted configuration needed to reproduce the processor generation.

### Approval record

A completed benchmark records its review status independently from execution status. Approval
identifies the immutable run, reviewer, review time, accepted threshold configuration, clear-loss
count, relevant-result-loss count, unresolved count, and a bounded aggregate summary.

Execution success never implies review approval. Only a complete explicitly approved run with zero
clear-face loss, zero confirmed relevant-result loss, and zero unresolved corpus items may authorize
the candidate generation for a later activation action.

## Data flow

1. An operator freezes the latest published event's exact photo and input-media inventory for a
   local comparison.
2. The existing generation remains the active customer-search generation and supplies immutable
   baseline evidence.
3. The candidate generation processes the same input bytes into separate immutable attempts.
4. For each selected detection, the worker calculates quality evidence and decides acceptance
   before SFace embedding.
5. Django validates and persists accepted or quality-rejected face evidence; only accepted faces
   can receive embeddings.
6. A private immutable benchmark run compares detections, produces review artifacts, and records
   manual labels.
7. The same closed query set runs against baseline and candidate cohorts without changing active
   search configuration.
8. A human explicitly approves or rejects the complete immutable benchmark evidence.
9. A separate activation selects the approved candidate generation for new searches. If cluster
   expansion is used, a separately built and approved compatible corpus is activated independently.
10. Existing bearer results and historical processing evidence remain unchanged.

## Failure and rollback semantics

- A technical failure for one photo follows the existing bounded retry and immutable-attempt
  semantics. It does not publish partial candidate evidence as accepted.
- A recoverable embedding failure for one accepted face remains distinct from a quality rejection
  and does not discard other valid faces from the photo.
- A benchmark inventory mismatch, malformed manual label, invalid threshold configuration,
  unresolved photo, missing review artifact, or inconsistent aggregate fails closed for approval.
- Benchmark publication is atomic. An existing output identity is never overwritten.
- Search and corpus activation fail closed when the approved configuration or generation identity
  does not match the candidate artifacts exactly.
- Rollback selects the previous approved generation for future searches. It does not delete the
  candidate, rebuild existing corpora, or recompute existing results.
- Existing bearer snapshots remain readable under their original authority and membership rules
  before, during, and after activation or rollback.

## Privacy and authorization

The worker authority does not expand. It receives only the exact short-lived input grant governed
by ADR 0017 and receives no database access, permanent Object Storage credential, gallery cohort,
query vector, cluster corpus, or bearer token.

Production quality records contain geometry and bounded numeric decision evidence already tied to
an event photo; they add no name, participant identifier, cross-event link, or identity assertion.
They remain derived biometric processing evidence under the same access boundary as existing face
detections and embeddings.

Detailed local review artifacts are private temporary benchmark data. They are not application
media, customer-visible resources, analytics payloads, or training data. No photo, crop, face ID,
query artifact, vector, bearer value, or absolute local path enters Git, public HTML, or ordinary
operational logs.

## Acceptance criteria

- The current search generation and all existing bearer results remain unchanged until explicit
  activation.
- The candidate has a distinct immutable processor identity and processes exactly the frozen
  baseline media inventory.
- Every valid detection has complete bounded quality evidence and one explicit decision.
- No quality-rejected detection has an embedding.
- No accepted detection receives more than one embedding for its attempt.
- Borderline quality remains accepted unless the configured corroboration rule is satisfied.
- Old and new generations cannot be mixed in one direct-search cohort or one cluster corpus.
- The complete detection review contains every newly rejected baseline face and records zero
  manually labelled clear-face losses.
- The closed search comparison records zero lost manually confirmed relevant photos.
- The approved run has no unresolved corpus items and identifies the exact frozen configuration.
- Build completion, benchmark approval, search activation, and cluster-corpus activation remain
  separate explicit actions.
- Rollback restores the previous generation for new searches without mutating historical data.
- Automated contracts cover the decision table, finite and bounded metrics, rejected-without-vector
  persistence, immutable generation identity, cohort compatibility, complete benchmark evidence,
  activation refusal, rollback, and historical-result regression behavior.

## Rejected alternatives

### Sharpness-only filtering

Rejected because one Laplacian threshold is sensitive to crop scale, contrast, noise, and JPEG
compression. It can reject a usable soft portrait and retain a noisy background face. Normalized
measurement plus corroborating signals better protects recall.

### Learned face-quality model

Rejected for this increment because it adds another model, packaging and resource requirements,
and an independent labelled calibration problem. The multi-signal gate is the smallest design that
can be evaluated end to end on the current corpus.

### Filtering inside search

Rejected because poor embeddings would remain persisted, enter other derived recognition uses,
and require every consumer to reproduce the same rule. Quality belongs before embedding
persistence, with the decision frozen in the processor generation.

### Deleting or rewriting old embeddings

Rejected because it would violate immutable processing and result evidence and make comparison or
rollback unreliable. Explicit generation compatibility provides a reversible transition without a
backward-compatibility layer in active search.
