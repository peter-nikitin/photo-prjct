# All-People Face Clustering Design

## Status

Approved in conversation on 2026-07-26, including the exact deterministic clustering and
comparison rules added during plan-boundary review.

- Related architecture: [`docs/architecture.md`](../../architecture.md), proposed Recognition
  module, recognition processing flow, face-data security constraints, and evolution stage 6
- Related ADRs: none
- ADR impact: None — reversible implementation detail
- Implementation plan:
  [`docs/plans/2026-07-26-all-people-face-clustering.md`](../../plans/2026-07-26-all-people-face-clustering.md)

## Goal

Extend the isolated YuNet/SFace experiment so one immutable run extracts every accepted face,
groups all extracted faces into anonymous people clusters, and saves every discovered person,
including people represented by only one photo. Evaluate that completed run against the separately
exported Peakshot person-to-photo mapping without using Peakshot data during detection, embedding,
or clustering.

The first run is an honest directional experiment. It does not aim for or claim 100% agreement with
Peakshot.

## Scope

The change remains inside `experiments/face_recognition_spike/`. It does not integrate with Django,
PostgreSQL, Object Storage, or customer-facing search.

This spike targets the maintainer's current macOS environment only. Existing portable code may
remain when it adds no maintenance burden, but cross-platform behavior and Docker execution are not
acceptance requirements. A production pipeline will be designed and implemented separately for
its container runtime.

The experiment will:

- extract an SFace embedding for every accepted YuNet detection rather than only the largest face;
- assign stable face-instance identifiers;
- cluster all successfully embedded face instances into anonymous people;
- save a reviewable directory for every cluster, including singleton clusters;
- allow one group photo to appear in multiple people directories;
- retain bounded-memory processing, immutable runs, atomic publication, and no embedding export;
- compare a completed run with a Peakshot export in a separate command; and
- produce machine-readable and visual comparison artifacts.

It will not:

- use Peakshot people, assignments, or identifiers as clustering input;
- preserve the previous labelled single-person workflow, CSV label model, primary-face retrieval,
  or its artifact schemas;
- infer names or confirmed identities;
- tune repeatedly until the run agrees with Peakshot;
- suppress singleton people;
- serialize raw face embeddings; or
- authorize production use.

## Inputs and Isolation Boundary

The clustering command consumes only:

- the event photo directory;
- the YuNet ONNX model;
- the SFace ONNX model; and
- explicit detection, minimum-face-size, clustering-distance, image-limit, and output parameters.

Peakshot data is not an input to this command. The completed run records all clustering parameters
and model hashes in its manifest.

Task 1 introduces a new internal unlabelled inventory and all-face analysis API in focused modules.
It does not adapt the previous labelled types or widen them with compatibility unions. The old
workflow may coexist temporarily while tasks are executed, but it is not a supported interface.
When the public `cluster` command and its immutable artifacts are complete, the previous labelled
`run`, label builder, image-level retrieval, old reports, and their dedicated tests are deleted.

The unlabelled inventory contains immediate regular `.jpg` and `.jpeg` files, matched
case-insensitively by extension and sorted by the original filename. Immediate regular files with
other extensions, including sidecars such as `.DS_Store`, are ignored. An empty image inventory,
case-fold-colliding image filenames, any symlink image entry, or any nested directory is rejected as
`invalid_photo_inventory`; the experiment never follows or silently searches nested paths.

The comparison command separately consumes:

- one already completed immutable clustering run; and
- one Peakshot reference export containing `peakshot-person-photo-map.csv` and its metadata.

The comparison command never modifies the clustering run. It publishes a separate immutable
comparison directory.

## Face-Instance Model

Every accepted YuNet detection becomes a face instance. A face instance contains:

- a stable `face_id`;
- its source filename;
- a deterministic per-image index;
- bounding box and landmarks;
- detection confidence;
- crop artifact path;
- processing status; and
- an in-memory normalized SFace embedding when extraction succeeds.

Within each image, face instances are ordered by
`(box.x, box.y, box.width, box.height, -confidence)` using the normalized finite YuNet values.
Identifiers use the source filename plus that one-based zero-padded order, such as
`0M0A0439.JPG#face-001`. There is no `PRIMARY` face in the new pipeline.

An image-level analysis contains zero or more face instances. An alignment or embedding failure is
recorded on the affected face instance without discarding successful faces from the same image.

Pixel arrays are released after diagnostic artifacts for the image have been written. Completed
in-memory analyses retain metadata and embeddings but no decoded RGB or BGR images.

## Clustering

The first implementation uses deterministic thresholded graph clustering with a guard against
single-link chaining.

1. Compute cosine distances between successful face embeddings in bounded blocks.
2. Create candidate edges only when the distance is at or below the configured strict clustering
   threshold.
3. Process candidate edges in ascending `(distance, left_face_id, right_face_id)` order.
4. Represent each component by its medoid: the member with the lowest mean cosine distance to all
   other component members, breaking ties by `face_id`.
5. Merge two components only when every member of the proposed merged component is at or below the
   configured representative threshold from the proposed component medoid. This bounded-radius
   guard reduces single-link chaining while keeping the approved graph approach.
6. Recompute the medoid after every accepted merge and record the final representative and member
   distances in the run artifacts.
7. Treat each final connected component as one anonymous person cluster.
8. Treat every unmerged successfully embedded face as a valid singleton cluster.

The representative-distance guard is intended to reduce obvious bridge merges while preserving the
approved graph approach. The clustering threshold remains an explicit run parameter rather than a
hidden constant. The initial real run uses cosine distance `0.363` for both the candidate-edge and
representative thresholds, a distance block size of `512`, YuNet detection threshold `0.75`, and
minimum face size `32` pixels. These are first-attempt experimental parameters, not calibrated
production thresholds. Later parameter changes require a new immutable run and are reported as a
new experiment, not as an overwrite.

Clusters receive stable identifiers `person-0001`, `person-0002`, and so on. Ordering is
deterministic from the sorted member face IDs. Peakshot identifiers never influence cluster IDs.

## Cluster Artifacts

Each completed clustering run contains:

- `manifest.json` with input basenames, model hashes, dependencies, parameters, timings, and counts;
- `faces.csv` and `faces.json` with face-instance metadata and status;
- `clusters.csv` and `clusters.json` with cluster membership;
- `annotated/` with image-level detection previews;
- `people/person-NNNN/faces/` with review crops;
- `people/person-NNNN/photos/` with source photos belonging to that cluster;
- `report.html` with cluster summaries, member faces, and source photos; and
- aggregate `metrics.json` describing detections, embedding success, cluster sizes, and singleton
  counts without making accuracy claims.

`faces.csv` uses these exact leading columns:

```text
face_id,filename,face_index,x,y,width,height,confidence,status,error_code,crop_path
```

`clusters.csv` uses these exact columns:

```text
cluster_id,representative_face_id,face_id,filename,face_index,distance_to_representative
```

The JSON artifacts expose the same fields grouped by image or cluster. Face status is one of `ok`,
`alignment_failed`, `embedding_failed`, or `invalid_embedding`. Image status is one of `ok`,
`no_detection`, `image_decode_failed`, `unsupported_image`, `image_too_large`, or
`detection_failed`; the new pipeline does not preserve the old label-related status vocabulary.

Within one cluster, an original filename is saved once even if multiple detected faces from that
photo enter the same cluster. A group photo may be saved into several cluster directories when its
different faces belong to different people.

The writer first attempts to hard-link source photos into cluster directories. If a hard link is
not supported across the relevant filesystems, it copies the source photo. The manifest records the
actual materialization counts by method. The run never changes source files.

Face crops are review artifacts, not biometric training data. Source photos, crops, labels,
embeddings, completed runs, and comparisons stay outside Git.

## Peakshot Comparison

The evaluator reads the Peakshot relationship `person_id -> filename` and the anonymous result
relationship `cluster_id -> filename`. It validates filenames case-sensitively and reports any
inventory mismatch rather than silently dropping rows.

Cluster-to-person alignment is evaluation-only:

1. For every result cluster, calculate its photo overlap with every Peakshot person.
2. Ignore zero-overlap pairs.
3. Assign the cluster to the Peakshot person with the greatest intersection count, then greatest
   Jaccard similarity, then lexicographically smallest `person_id`.
4. Match each Peakshot person to every cluster primarily assigned to it. The union of those
   clusters' filenames produces `our_photo_count` and the person's precision and recall row.
5. Treat a Peakshot person with multiple primary clusters as fragmentation.
6. Treat a result cluster with nonzero overlaps with multiple Peakshot people as merge evidence,
   while retaining its single deterministic primary assignment for aggregate relationship metrics.
7. Leave zero-overlap people and clusters unmatched.

This alignment allows:

- one Peakshot person to map to multiple result clusters, exposing fragmentation;
- one result cluster to overlap multiple Peakshot people, exposing merges; and
- unmatched Peakshot people and unmatched result clusters.

The evaluator reports at least:

- precision, recall, and F1 over person-photo relationships after the recorded alignment;
- cluster purity;
- counts and details of split Peakshot people;
- counts and details of merged result clusters;
- matched, missed, and unmatched people or clusters;
- singleton cluster statistics; and
- separate group-photo statistics for photos assigned to more than one Peakshot person.

Peakshot is an algorithmic silver-label reference, not ground truth. The comparison report describes
disagreement rather than calling every disagreement a recognition error.

## Required People Comparison Table

Every comparison publishes `people-comparison.csv` and the same table in
`people-comparison.html`. Its leading columns are:

| Column | Meaning |
| --- | --- |
| `peakshot_person_id` | Person identifier from the Peakshot export |
| `peakshot_photo_count` | Unique photos assigned to that Peakshot person |
| `matched_cluster_ids` | Semicolon-separated anonymous cluster IDs aligned to that person |
| `our_photo_count` | Unique photos across those clusters, without double-counting filenames |

The table also includes:

- `intersection_count`;
- `missing_count`;
- `extra_count`;
- `precision`;
- `recall`; and
- links in the HTML version to each matched cluster's review section.

Multiple cluster IDs make fragmentation visible. Result clusters that cannot be aligned to any
Peakshot person appear in a separate `unmatched clusters` section with cluster ID, photo count, and
review link. This preserves every found cluster in the evaluation.

## Failure Handling

Fatal input, model initialization, clustering configuration, artifact publication, or malformed
Peakshot reference errors exit nonzero and do not publish a completed directory.

Recoverable image decode, detection, alignment, and embedding failures remain explicit evidence.
Failures for one face do not discard other successful faces in the same photo. A completed run may
contain recoverable failures and must report their exact counts.

Both clustering and comparison outputs are immutable. A requested output path that already exists
is rejected. Writers use hidden staging directories, atomically publish on success, and remove
staging data on failure.

## Acceptance Criteria

The implementation is acceptable when automated evidence demonstrates:

- extraction and persistence of multiple face instances from one image;
- deterministic face IDs and cluster IDs;
- per-face recoverable embedding failures;
- singleton clusters;
- deterministic edge ordering and threshold boundaries;
- the representative guard preventing an obvious chaining merge;
- one source photo saved once within one cluster;
- one group photo saved into multiple people directories;
- hard-link fallback to copying;
- atomic publication and staging cleanup;
- comparison without feeding reference data into clustering;
- one-to-many fragmentation and many-to-one merge reporting;
- unique-photo counting in `our_photo_count`;
- unmatched Peakshot people and unmatched clusters; and
- deterministic CSV, JSON, and HTML output.

One immutable full-event run and one immutable Peakshot comparison must expose parameter values,
model hashes, runtime counts, measured comparison metrics, representative failure modes, and
limitations. Manual review must be possible for clean clusters, singletons, split people, merged
clusters, group photos, and unmatched clusters. No artifact may describe the result as
production-ready or as a 100% identity match.

## Scalable Cluster Review and Fragmentation Decisions

The all-clusters review surface must remain usable for a full event without asking a browser to
decode every crop and source photo at once.

For future cluster runs, `report.html` is a lightweight index. It shows aggregate counts and one
representative crop, cluster identifier, face count, and unique-photo count per cluster. Every
cluster links to `people/<cluster_id>/index.html`, which shows that cluster's member crops and
source photos. Images use lazy loading and bounded display dimensions. The index must not embed all
member crops or source photos.

Completed immutable runs are never rewritten to retrofit the new layout. A review builder consumes
a completed run and its completed Peakshot comparison and atomically publishes a separate immutable
review bundle. The bundle contains:

- a lightweight cluster index;
- one detail page per cluster;
- `fragmentation-review.html`; and
- the static data required by that page without copying embeddings or source photos.

The fragmentation page includes every Peakshot person aligned to two or more result clusters. It
presents deterministic cluster pairs with representative crops, cluster sizes, a bounded sample of
member crops, and links to complete cluster detail pages. Photo-level overlap is supporting context,
not proof that two face clusters are the same identity.

The reviewer assigns exactly one decision to a pair:

- `same`;
- `different`; or
- `uncertain`.

Decisions are keyed by the Peakshot person ID and the numerically ordered pair of cluster IDs.
They are saved in browser `localStorage` under a versioned key scoped to the review bundle's run
and comparison identities. The page supports filtering by decision state, displays reviewed and
total counts, exports deterministic CSV, and imports CSV produced by the same schema. Import
rejects malformed rows, unknown pair keys, invalid decisions, duplicate keys, and bundle identity
mismatches without partially replacing the stored review.

The review UI is entirely local. It does not transmit images, identifiers, or decisions and does
not modify the run, comparison, source photos, or Peakshot export.

### Review Acceptance Criteria

Automated evidence demonstrates that:

- the full-event index contains no eager full-size source-photo gallery;
- cluster detail pages contain only one cluster and resolve all local media links;
- every fragmented Peakshot person and deterministic cluster pair is represented;
- `same`, `different`, and `uncertain` decisions survive reload through `localStorage`;
- CSV export is deterministic and round-trips through strict import;
- malformed import leaves existing decisions unchanged;
- review publication is atomic and rejects an existing output path; and
- the derived review bundle can be built from the existing completed `run-001` and comparison
  without rerunning face detection, embedding, or clustering.

## Manual Quality Labels and Provisional Filtered Metrics

The review bundle distinguishes detector quality, identity-pair judgment, and ambiguous
photo-level evidence. These annotations are evaluation-only and never mutate a completed run,
comparison, or original metric.

Every result cluster has exactly one manual quality state:

- `unreviewed`;
- `usable`;
- `not_face`;
- `low_quality`; or
- `mixed`.

`not_face` covers false detections such as hands, shoes, and other non-face crops.
`low_quality` covers real faces that are too blurred, too small, too occluded, or too far in the
background to support identity review. `mixed` covers a cluster containing both usable and
unusable members.

Every fragmentation pair retains its identity decision (`same`, `different`, or `uncertain`) and
adds one evidence-quality state:

- `direct`; or
- `group_photo_ambiguous`.

When either cluster is `not_face`, `low_quality`, or `mixed`, the UI displays the pair as
`not_applicable` for identity analysis without deleting a previously recorded identity decision.
Group-photo ambiguity is explanatory and does not automatically change identity or quality states.

The page always shows the immutable original comparison metrics. Beside them it calculates
provisional filtered metrics after excluding clusters explicitly marked `not_face`, `low_quality`,
or `mixed`. `usable` and `unreviewed` clusters remain included, and the UI prominently reports
review coverage so partially reviewed values cannot be mistaken for final corrected metrics.
Precision, recall, F1, purity, unmatched-cluster count, singleton statistics, and fragmentation are
recomputed from the remaining cluster-photo relationships using the existing deterministic primary
assignments. Original published files and metrics remain unchanged.

For each Peakshot person, `same` decisions form connected components among its remaining clusters.
The UI reports:

- original algorithmic cluster count;
- confirmed distinct components after virtual `same` unions; and
- unresolved clusters or pairs.

Virtual unions affect only the manual fragmentation summary. They do not rewrite cluster IDs or
silently change relationship precision, recall, F1, or purity.

Cluster quality and pair decisions use versioned, bundle-scoped `localStorage`. The combined
deterministic CSV contains:

```text
peakshot_person_id,left_cluster_id,right_cluster_id,identity_decision,evidence_quality,left_cluster_quality,right_cluster_quality
```

Repeated cluster-quality values must agree across all rows. A separate deterministic cluster CSV
contains one row per cluster for convenient audit. Import validates bundle identity, headers,
states, pair keys, cluster IDs, duplicate pair keys, and repeated-quality consistency before one
atomic replacement of both stored maps. Any invalid row leaves all existing annotations unchanged.

### Manual Quality Acceptance Criteria

Automated evidence demonstrates that:

- all five cluster-quality states and both evidence-quality states persist across reload;
- unusable cluster states make affected pairs visibly `not_applicable` without erasing decisions;
- original metrics remain byte-for-byte identical to the completed comparison;
- provisional filtered metrics exclude only explicitly unusable clusters and report review
  coverage;
- virtual `same` unions deterministically reduce only the manual fragmentation summary;
- combined and cluster-only CSV exports are deterministic;
- valid combined CSV round-trips both maps; and
- malformed or inconsistent CSV import changes neither map.
