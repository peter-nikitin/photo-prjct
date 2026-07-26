# All-People Face Clustering Design

## Status

Approved in conversation on 2026-07-26.

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

Face instances are ordered deterministically by bounding-box coordinates and stable numeric
tie-breakers. Identifiers use the source filename plus a zero-padded index, such as
`0M0A0439.JPG#face-001`. The existing `PRIMARY` marker may remain as a diagnostic indication of the
largest face, but it has no role in clustering or evaluation.

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
4. Merge two components only when the candidate edge passes a representative-distance guard for
   both components. The representative and guard rule are deterministic and recorded in the run
   manifest.
5. Treat each final connected component as one anonymous person cluster.
6. Treat every unmerged successfully embedded face as a valid singleton cluster.

The representative-distance guard is intended to reduce obvious bridge merges while preserving the
approved graph approach. The clustering threshold remains an explicit run parameter rather than a
hidden constant. The initial real run uses one recorded parameter set; later parameter changes
require a new immutable run and are reported as a new experiment, not as an overwrite.

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

Cluster-to-person alignment is evaluation-only. It maximizes photo-set overlap while allowing:

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

## Testing and Verification

Model-independent tests will cover:

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

Verification after implementation will include:

1. the isolated fast test suite;
2. Ruff formatting and lint for the changed scope;
3. the opt-in real-model smoke test with more than one detected face where available;
4. one immutable full clustering run over the supplied event photos;
5. one immutable comparison against the supplied Peakshot export; and
6. manual inspection of representative clean clusters, singleton clusters, split people, merged
   clusters, group photos, and unmatched clusters.

The final report records commands, parameter values, run paths, runtime counts, measured comparison
metrics, representative failure modes, and limitations. It does not describe the result as
production-ready or as a 100% identity match.
