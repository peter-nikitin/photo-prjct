# Selfie Search Face-Cluster Expansion Design

- **Status:** Approved in conversation and written review on 2026-08-05
- **Date:** 2026-08-05
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), implemented public
  event-scoped selfie search, PostgreSQL product authority, derived recognition data, and bounded
  selfie-search observability
- **Related product jobs:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
  and
  [`PJ-013 — Customer — Report selfie-search quality`](../../product-jobs.md#pj-013--customer--report-selfie-search-quality)
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md),
  [`2026-07-26-all-people-face-clustering-design.md`](2026-07-26-all-people-face-clustering-design.md),
  [`2026-08-04-selfie-search-quality-feedback-design.md`](2026-08-04-selfie-search-quality-feedback-design.md),
  and
  [`2026-08-04-selfie-search-observability-design.md`](2026-08-04-selfie-search-observability-design.md)
- **Related ADRs:**
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md), and
  [ADR 0025](../../adr/0025-expand-selfie-search-with-face-clusters.md)
- **ADR impact:** **Conforms to ADR 0025.** ADR 0019 deliberately excluded cluster and graph expansion
  from the first public selfie-search decision and defines direct face matches as the only saved
  result source. This design adds a durable derived identity-cluster corpus and a second result
  membership source. It preserves ADR 0019's event isolation, probable-match language, transient
  query embedding, temporary-selfie cleanup, immutable bearer result, and worker authority
  boundaries, and it conforms to ADR 0023's rule that feedback never changes ranking automatically.

## Outcome

A successful public selfie search can return additional event photographs of the same person when
those photographs belong to a conservative face cluster reached from a strong direct face match.
The customer sees one result gallery: existing direct matches first, followed by new cluster-expanded
photos. A photograph appears at most once.

The system does not attempt to assign one global person identity or produce a participant count.
One person may remain split across several anonymous clusters, and singleton clusters remain valid.
False merges are more harmful than fragmentation because one incorrect cluster can add many
unrelated photographs to a result.

Each saved result retains immutable provenance. Product records and bounded structured events make
it possible to determine how many photos the new mechanism added, how often it helped, and how
customers labelled direct and expanded results. Feedback is evaluation evidence only; it never
changes a cluster, threshold, model, or saved result automatically.

## Success criteria

The feature succeeds when:

- a ready search preserves every direct result and can append unique photographs from a compatible
  published face-cluster corpus;
- only separately calibrated strong direct matches may seed expansion;
- direct results retain their current deterministic order and always precede expanded results;
- every result records whether it came from `direct` or `face_cluster_expansion`, while a photo
  supported by both remains one direct result with both evidence sources retained;
- an unavailable, failed, unpublished, or incompatible cluster corpus degrades to the unchanged direct
  search without delaying selfie cleanup or terminal publication;
- a published cluster corpus is event-scoped, immutable, reproducible, anonymous, and derived only
  from compatible accepted gallery face embeddings;
- one search records the exact cluster-corpus version and expansion configuration it used;
- structured events and the daily summary distinguish direct matches from photos added by cluster
  expansion without logging photo, face, cluster, selfie, vector, or bearer identity;
- durable PostgreSQL result provenance supports retrospective source and feedback reports after the
  bounded journal-retention window has expired; and
- a labelled closed benchmark demonstrates an accepted recall increase and an explicitly approved
  false-positive cost before activation.

## Scope

### Included

- Versioned offline face-clustering runs for an event's compatible accepted gallery embeddings.
- Deterministic conservative graph clustering with guarded component merges.
- Immutable published cluster versions and explicit activation for new searches.
- Singleton clusters and fragmentation of one person across several clusters.
- A separately calibrated strong-anchor policy for cluster expansion.
- One immutable result snapshot containing direct results followed by unique expanded results.
- Per-result direct and face-cluster provenance, including dual evidence for a direct photo also
  reached through expansion.
- Feedback labels attributable to the frozen result source and cluster-corpus version.
- Privacy-bounded ranking events, terminal counts, daily aggregates, and durable retrospective
  reporting fields for cluster expansion.
- A disabled-by-default activation gate and rollback to direct-only search.
- Critical-path clustering, ranking, provenance, feedback, privacy, observability, failure, and
  regression coverage.

### Excluded

- Named identity, participant registration, or forcing the corpus to contain one cluster per event
  participant.
- Cross-event clustering or matching.
- Persisting a selfie query vector or extending temporary-selfie retention.
- Replacing direct embeddings with one centroid or representative per presumed person.
- Letting a weak or ordinary-threshold direct match expand a cluster.
- Clothing, shirt, helmet, bicycle, bib, time, location, or photographer-sequence recognition in
  this increment.
- A generic contextual-evidence graph implemented in advance of one measured additional factor.
- Automatically changing clusters, thresholds, result membership, or model weights from customer
  feedback.
- Recomputing existing bearer results after a new corpus version is published.
- A customer-visible distinction, badge, section, or explanation for direct versus expanded photos.
- A dashboard, alert, new cloud logging service, vector database, ANN service, broker, or separately
  deployed clustering service.
- Using cluster count as attendance, unique-person, or identity evidence.

## Selected design

### Conservative anonymous face clusters

At build time, clustering uses the same event-scoped eligibility and compatibility contract as
direct selfie search. Every input is one immutable face instance backed by one existing
`FaceEmbedding` and one event photo. The corpus contains no selfie, query vector, customer contact,
bearer token, person name, participant identifier, or Peakshot identifier.

The first production clustering algorithm is the experiment's established deterministic
thresholded graph with a medoid-radius guard against single-link chaining:

1. Compute cosine distances between normalized compatible face embeddings in bounded blocks and
   generate candidate edges only at or below the versioned strict cluster-edge threshold.
2. Process candidate edges in ascending `(distance, left_face_id, right_face_id)` order.
3. Represent each component by its medoid: the member with the lowest mean cosine distance to every
   other component member, with stable face identity breaking ties.
4. Join two components only when every member of the proposed component is at or below the
   versioned representative-distance threshold from the proposed component medoid.
5. Recompute the medoid after every accepted merge and retain the final representative and each
   member's distance to it.
6. Publish each final connected component as one anonymous face-cluster fragment.
7. Publish every unmerged eligible face as a singleton cluster.

The first increment uses the exact bounded-block edge calculation; it does not use approximate
candidate generation. Corpus build time and peak memory are activation evidence. A later approximate
builder requires a separate measured design because changing the candidate edge set can change
cluster membership. This design does not add an online vector service.

Cluster and guard thresholds are not copied from the direct-search threshold and are not selected
from an unlabelled production outcome. They are explicit immutable corpus configuration chosen from
a labelled closed benchmark. The corpus records the face processor generation, embedding model and
dimensions, input eligibility contract, algorithm version, thresholds, and configuration hash.

Clusters are fragments of evidence, not people. Fragmentation is acceptable. A single face cluster
may contain several photographs, several detections of the same person in a group photograph, or
only one face. A photo can belong to several different clusters when it contains several people.

### Versioned offline corpus lifecycle

Clustering runs outside customer requests and outside the ML query worker. Django and PostgreSQL
remain the control plane and product authority. Derived cluster membership is rebuildable recognition
data under ADR 0002, not an independent system of record.

One event may have multiple immutable corpus versions. A run records its frozen eligible input,
configuration, counts, status, and publication time. A version is either unpublished or completely
published; a partially written run can never be selected by search. Publishing a new version does
not mutate or delete an older version used by an existing result.

An operator explicitly activates one compatible published corpus for new searches of an event.
Adding photographs or accepting a new face-processing generation does not incrementally mutate the
active corpus. It requires a new offline run and an explicit activation. A search freezes the
selected corpus identity and expansion configuration before result publication. Existing searches
remain immutable.

The repository default keeps cluster expansion disabled. Corpus building and activation are
separate operations: successfully building a run never enables it automatically.

### Strong direct anchors

Direct ranking continues to compare the transient normalized selfie query vector with compatible
gallery face embeddings using the existing direct threshold. Its unique-photo results and order are
unchanged.

A direct matched face may seed expansion only when:

- it belongs to the selected compatible cluster corpus;
- its query cosine distance is at or below a separately calibrated anchor threshold that is
  strictly stronger than the ordinary direct-display threshold; and
- its cluster is eligible under the published corpus configuration.

The first increment uses this strict distance rule only. Ordinary-threshold matches do not become
stronger merely because several weak results appear similar. Multi-anchor corroboration can be
evaluated later as a separately specified policy after the strict-anchor baseline is measured.

Several strong anchors in the same cluster select that cluster once. A strong anchor in a singleton
cluster is valid but adds no photograph. A direct match whose detection is absent from the active
corpus remains a normal direct result and cannot expand.

### Expanded membership and deterministic ordering

For each selected cluster, Django considers every currently product-eligible event photo represented
by a member face. It removes photos already present in the direct result and deduplicates photos
reached through several anchors or clusters.

The saved result order is:

1. every direct result in the existing ascending direct-distance and stable-photo-ID order;
2. each selected cluster in the order of its best direct anchor rank; and
3. new photos within that cluster by ascending member distance to the cluster representative, then
   stable photo ID.

When a new photo is reachable through more than one selected cluster, its earliest tuple under this
ordering owns its expanded rank while every accepted source remains durable evidence. Cluster
distance is never presented as directly comparable to selfie cosine distance.

The customer receives one ordinary numbered result gallery. The UI does not expose a second block,
source badge, or cluster identifier. Existing current event/photo visibility checks still apply at
read time and do not mutate saved membership or relative order.

### Immutable result provenance

Each result has one primary source:

- `direct`; or
- `face_cluster_expansion`.

A direct result always keeps primary source `direct`, even when an activated cluster also reaches
the photo. Durable evidence records both sources. Direct evidence retains the matched detection and
query cosine distance already saved today. Cluster-expansion evidence retains only product-internal
references needed to reproduce why the photo entered the immutable snapshot: corpus version,
selected cluster, strong anchor result/detection, expanded member detection, and the bounded
ordering evidence.

The search snapshot stores direct result count, cluster-expanded result count, final result count,
strong-anchor count, selected-cluster count, corpus version, and expansion configuration hash.
These fields are authoritative for retrospective counts after operational logs expire. They contain
no persisted query vector.

The data model uses explicit direct and face-cluster evidence rather than a generic arbitrary-factor
schema. A later contextual-expansion specification may add typed `same_bib`, `same_shirt`,
`same_helmet`, `same_bicycle`, time, or sequence evidence without redefining the meaning of the two
sources accepted here.

## Data flow

1. An operator builds an immutable event corpus from a frozen set of compatible accepted gallery
   face embeddings and reviews its benchmark evidence.
2. The complete corpus is published and explicitly activated for new searches while cluster
   expansion remains environment-gated.
3. A customer submits a selfie through the existing event-scoped flow. The existing worker returns
   one transient query embedding.
4. Django loads the compatible direct cohort and performs the unchanged direct ranking.
5. Django selects strong direct anchors using the frozen anchor configuration and selected corpus.
6. Django expands selected clusters, deduplicates photos, orders direct results before expanded
   results, and prepares immutable result and evidence rows.
7. Django records bounded direct/expanded counts and emits the versioned ranking event.
8. Django deletes the temporary selfie and only then publishes the terminal bearer result, exactly
   as required by ADR 0019. The query embedding is never persisted.
9. If the customer later submits consented feedback, each explicit result label remains attached to
   the immutable result and its frozen provenance. No feedback mutation is applied to search or
   clustering state.

## Feedback and quality interpretation

The existing feedback experience remains one gallery and one optional set of `Я есть` / `Меня нет`
marks. The customer is not asked to classify the source. The server derives it from immutable result
evidence.

Restricted retrospective reports distinguish explicit labels for:

- direct-primary results;
- face-cluster-primary results; and
- direct-primary results that also had cluster evidence.

Reports always show labelled counts and coverage. An unmarked photo is unknown, not a positive or
negative label. Precision calculated from partial feedback is labelled-sample precision and must not
be presented as full-result precision. `Я есть` / `Меня нет` remains customer-provided evidence,
not verified identity.

Feedback never suppresses an individual result, removes a cluster member, deactivates a corpus,
changes a threshold, or trains a model automatically. Any later calibration uses an explicitly
reviewed offline workflow and publishes a new version rather than mutating an existing corpus.

## Observability and retrospective reporting

### Per-search structured event

The existing `selfie_ranking_finished` contract advances to a new explicit schema version. A
successful ranking event contains these bounded cluster-expansion fields in addition to existing
event/search correlation, cohort, configuration, and duration fields:

| Field | Contract |
| --- | --- |
| `direct_matched_photo_count` | Unique photos accepted by direct ranking |
| `cluster_expanded_photo_count` | New unique photos added only through clusters |
| `final_matched_photo_count` | Saved unique-photo membership after expansion |
| `strong_anchor_count` | Direct face matches allowed to seed expansion |
| `expanded_cluster_count` | Unique selected non-singleton clusters |
| `cluster_corpus_version` | Bounded opaque version or null when unavailable/disabled |
| `cluster_configuration_hash` | Hash-only reviewed expansion-policy identity (corpus identity plus direct and anchor thresholds) or null |
| `cluster_expansion_ms` | Non-negative bounded expansion duration or null |
| `cluster_expansion_outcome` | One allowed bounded outcome |

Allowed outcomes are `expanded`, `no_strong_anchor`, `no_new_photos`, `corpus_unavailable`,
`corpus_incompatible`, and `disabled`. `expanded` requires
`cluster_expanded_photo_count > 0`; the other outcomes require zero added photos.

The event never contains a photo ID, face/detection ID, cluster ID, edge, vector, per-result
distance, selfie field, bearer token, object identity, or raw exception. Existing point correlation
continues to use only opaque `search_id` and bounded technical fields.

### Daily operational summary

The root-owned Moscow-day summary parses the new schema explicitly and reports:

- eligible searches by expansion outcome;
- searches with one or more new cluster photos;
- total direct, cluster-expanded, and final photos;
- p50/p95 added photos per eligible search;
- p50/p95 cluster-expansion duration;
- strong-anchor and selected-cluster totals;
- bounded outcome counts; and
- observed corpus-version/configuration-hash counts.

For this report, an `eligible_search` is a successful direct ranking for which cluster expansion was
enabled and a compatible corpus was selected. It includes `expanded`, `no_strong_anchor`, and
`no_new_photos`, and excludes `disabled`, `corpus_unavailable`, and `corpus_incompatible`.

It derives and reports, with their integer numerators and denominators:

```text
searches_helped_rate = searches_with_cluster_photos / eligible_searches
incremental_photo_rate = cluster_expanded_photos / final_matched_photos
```

Unknown schema versions, invalid count identities, duplicates, or missing accepted/terminal
counterparts make parser completeness false under the existing observability contract. A successful
event must satisfy:

```text
final_matched_photo_count
  = direct_matched_photo_count + cluster_expanded_photo_count
```

The daily journal is bounded operational evidence, currently subject to the accepted 14-day/1-GiB
retention contract. It is not the long-term product analytics store.

### Terminal event

The new schema of `selfie_search_terminal` retains `matched_photo_count` as the final published
count and adds `direct_matched_photo_count`, `cluster_expanded_photo_count`,
`cluster_corpus_version`, and the policy-valued `cluster_configuration_hash`. The terminal event is emitted only after
selfie cleanup and therefore confirms the source counts that became publicly visible. Its count
identity must match the successful ranking event and durable search snapshot. Non-ready terminal
states have zero direct and expanded counts.

The daily funnel uses the terminal event for published result volume and the ranking event for
anchor, selected-cluster, expansion-outcome, and phase-duration analysis. A mismatch makes the
summary incomplete rather than choosing one value silently.

### Durable retrospective report

PostgreSQL stores the immutable source counts, corpus/configuration identity, result provenance, and
feedback labels. A restricted date-bounded operator report can therefore reproduce direct versus
expanded volume and labelled outcomes after journal eviction. It must output aggregates only and
must not include search IDs, bearer tokens, event-photo/face/cluster identities, contacts, selfies,
vectors, filenames, object keys, or per-person histories.

Historical searches created before this feature have no cluster-expansion contract. Reports mark
their source metrics `not_available`; they must not convert missing provenance to zero or fabricate
backfilled events. This is reporting truth, not a runtime compatibility path.

## Failure semantics

- No active corpus, a disabled gate, or a corpus not compatible with the direct cohort produces
  direct-only results and the corresponding bounded expansion outcome.
- A corpus read or integrity failure fails the optional expansion closed and preserves the complete
  direct result. It must not publish partial expanded membership.
- A direct-ranking failure retains the existing search failure semantics; cluster expansion cannot
  turn it into success.
- Cluster expansion never runs before direct ranking has produced its deterministic result and
  strong-anchor evidence.
- A partially built or unpublished corpus is ineligible for search.
- Corpus publication or activation never mutates existing search result membership.
- Result persistence, provenance, count snapshots, and intended terminal state are one transactional
  preparation. Cleanup retry remains idempotent and cannot duplicate evidence or change ranks.
- Structured logging failure never changes customer behavior, saved membership, or selfie cleanup.
- Daily-summary failure never mutates product state and can be recomputed inside retained coverage.
- Feedback-storage or feedback-submission failure remains isolated from search and clustering.

## Privacy and authorization boundaries

- Clustering is event-scoped and uses only already accepted gallery embeddings. It creates no
  cross-event identity or reusable customer profile.
- Cluster identifiers are opaque internal derived-data identities and never appear in public pages,
  bearer APIs, media URLs, structured events, or aggregate reports.
- The worker continues to receive no gallery embeddings, cluster corpus, database access, permanent
  Object Storage credentials, feedback media, or public token.
- The query embedding exists only inside the accepted Django completion operation and is never
  stored in a cluster, result, event, log, or report.
- The temporary selfie is deleted before terminal publication. Cluster failure does not extend its
  lifetime.
- Result pages continue to describe probable matches and never claim identification or attendance.
- Current bearer authorization and event/photo publication checks remain unchanged.
- Feedback contact and feedback selfie retain ADR 0023's separate consent, access, audit, and
  lifecycle boundaries and do not enter clustering automatically.

## Evaluation and activation gate

Evaluation compares two immutable configurations on the same labelled closed benchmark:

1. the current direct-search baseline; and
2. direct search plus conservative face-cluster expansion.

The benchmark uses person-separated calibration and evaluation subsets, full-photo holdout, and
explicit face/photo relevance labels. It reports at least:

- direct and final photo-level recall;
- direct-primary and face-cluster-primary labelled precision;
- incremental correct photos and incorrect photos added by expansion;
- searches helped and searches harmed;
- false cluster merges and fragmented identities;
- singleton and cluster-size distributions;
- corpus build time and peak resource use; and
- direct load/rank time plus cluster-expansion p50/p95.

The benchmark and review artifacts remain private and contain no production selfie/query retention
unless separately consented and approved with an explicit deletion boundary. Peakshot or another
algorithmic export may be a separate silver evaluator but never supplies production cluster
membership or ground truth automatically.

Activation requires:

- an explicitly approved anchor threshold, cluster-edge threshold, and component guard;
- a documented minimum recall gain and maximum acceptable precision regression on the held-out
  evaluation set;
- review of every observed false merge in the evaluation set;
- measured corpus build and search resource use within the supported environment;
- passing privacy, provenance, observability, and direct-only fallback checks; and
- explicit environment activation after one corpus version is published.

This specification intentionally does not invent numeric quality gates before labelled evidence is
available. Implementation completion is not activation approval.

## Contextual evidence extension boundary

Future recognition may evaluate shirt, helmet, bicycle, bib, time, location, or photographer
sequence as typed contextual evidence. Such factors must not be inserted into the face-identity
cluster or reinterpret a face cluster as a person record.

A later specification must introduce each factor separately, measure it against the direct plus
face-cluster baseline, preserve its source and confidence, prevent one weak factor from expanding a
result alone, bound graph path length, and retain the rule that confidence decays along indirect
paths. That future work may add `contextual_expansion` provenance without changing the accepted
meaning of `direct` or `face_cluster_expansion`.

## Acceptance criteria

1. A deterministic fixture produces identical anonymous cluster membership, representatives,
   singleton clusters, and configuration hash across repeated runs.
2. Event, processor-generation, model, dimension, eligibility, or configuration mismatch prevents
   corpus selection and leaves direct search available.
3. Candidate edges cannot join components when the configured consistency guard rejects the
   resulting component, including a short bridge between otherwise distinct identities.
4. Corpus publication is atomic; incomplete, failed, and unpublished runs cannot be activated.
5. Activating a newer corpus affects only later searches and never mutates an existing bearer
   result or its evidence.
6. Direct ranking produces the same photos, distances, and relative order with expansion enabled or
   disabled.
7. Only direct matches satisfying the frozen strict-anchor rule select clusters; ordinary-threshold
   matches and missing-corpus detections do not expand.
8. The final snapshot places every direct result first, appends expanded photos deterministically,
   and contains each photo once.
9. A direct photo also reached through a cluster remains direct-primary and retains both evidence
   sources.
10. A corpus with singleton clusters, several anchors in one cluster, group photos in several
    clusters, and one photo reached through several clusters produces the exact expected unique
    ordering and provenance.
11. Missing, disabled, unreadable, unpublished, or incompatible cluster data produces a complete
    direct-only result with the exact bounded expansion outcome and no partial evidence.
12. The search stores direct, expanded, final, anchor, and cluster counts plus corpus/configuration
    identity without storing the query embedding.
13. Selfie deletion still precedes terminal publication; duplicate callbacks and cleanup retries
    cannot duplicate result/evidence rows or change their order.
14. Feedback labels remain bound to immutable result membership and can be aggregated separately
    for direct-primary, cluster-primary, and dual-evidence results without customer-supplied source
    fields.
15. Feedback never changes clusters, thresholds, models, result membership, or activation state.
16. The new ranking event rejects unknown fields and invalid count/outcome combinations and contains
    none of the prohibited identity, media, vector, token, contact, or exception sentinels.
17. A deterministic daily-summary fixture reports expansion volume, helped-search rate, incremental
    photo rate, latency, outcomes, and integrity exactly; old/missing schemas are not interpreted as
    zero expansion.
18. A restricted durable report reproduces direct/expanded result and labelled-feedback aggregates
    after journal coverage is absent without exposing individual or sensitive fields.
19. Existing result presentation, pagination, media authorization, ordinary direct search, worker
    no-credential boundary, feedback consent/storage, and direct-only observability remain passing.
20. The labelled held-out evaluation reports recall gain, source-separated precision, false merges,
    searches helped/harmed, and resource measurements, and activation remains disabled until its
    numeric gates receive explicit approval.

## Rejected alternatives

### Replace every cluster with one centroid or representative

This reduces comparison volume but can erase a useful front, profile, helmet, lighting, or
occlusion-specific embedding. It trades away the recall objective and makes a false merge affect one
canonical identity representation. Original embeddings remain the direct-search source.

### Force approximately one cluster per registered participant

Registration is neither complete gallery ground truth nor proof that a detected face belongs to a
participant. Spectators, staff, photographers, repeated appearances, missed faces, and cluster
fragmentation make the target invalid. Cluster count is not attendance.

### Expand from every direct result

An ordinary-threshold false positive could add an entire unrelated component. Only a separately
calibrated stronger direct anchor may seed the first increment.

### Mix direct and expanded photos by one synthetic score

Direct query distance and intra-cluster evidence have different semantics. A combined score would
hide that distinction and could move weaker expanded photos ahead of direct matches. Direct results
remain first.

### Put clothing, helmet, bicycle, or bib evidence into face clusters now

Those signals describe event context and appearance rather than stable facial identity. They can be
shared, changed, occluded, or misread. Adding them now would prevent isolated measurement of the
face-cluster contribution and amplify false merges.

### Use feedback to mutate production automatically

One partial customer label is not verified identity and may describe a detection, cluster, or
presentation problem. Automatic suppression or tuning would make behavior non-reproducible and
violate the accepted feedback boundary.

### Rely only on structured logs for retrospective impact

The journal is intentionally bounded by retention time and disk use. Durable immutable result
counts and provenance are required for longer-term aggregate reporting; logs remain operational
evidence rather than the product system of record.
