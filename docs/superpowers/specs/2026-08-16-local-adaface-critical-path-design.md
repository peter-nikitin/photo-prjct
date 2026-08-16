# Local AdaFace Critical-Path Comparison Design

- **Status:** Approved in conversation and written review on 2026-08-16
- **Date:** 2026-08-16
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), event-scoped selfie
  search, derived recognition data, Django/PostgreSQL product authority, and the private photo worker
- **Related product jobs:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md) and
  [`2026-08-05-gallery-face-selector-design.md`](2026-08-05-gallery-face-selector-design.md)
- **Related ADRs:**
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md)
- **ADR impact:** **None — reversible local experiment.** The experiment does not change the
  accepted production model, search contract, storage boundary, deployment, or public behavior.
  AdaFace state remains confined to an experimental branch and an isolated local staging clone.

## Outcome

A maintainer can open the normal `cyclingrace-vechernee-sadovoe` event on an isolated local server,
submit a selfie through the existing gallery interface, and inspect an AdaFace-backed result using
the same customer path as the SFace-backed main site. The maintainer compares the two sites in
separate browser windows; the experiment adds no comparison UI or model selector.

The local application uses a cloned staging database, the already saved local preview corpus, and a
complete AdaFace face-embedding cohort for that event. It never reads event photos from Object
Storage and never mutates staging, production, the main checkout, or another agent's local
containers, database, volumes, or ports.

## Success criteria

The critical path succeeds when:

- the experiment runs from `.worktrees/adaface-critical-path` on
  `codex/adaface-critical-path`, based on the current `origin/main`;
- Docker Compose uses a unique project name, unique named volumes, loopback-only host ports, and no
  host port already used by another checkout;
- the isolated PostgreSQL database contains a validated clone of staging and only the selected
  event receives experimental face reprocessing;
- every backfill input resolves through the complete local preview manifest under
  `/Users/petrnikitin/Documents/Projects/photo-prjct-private/event-corpora/` and no backfill request
  reads remote media;
- YuNet detection and the active face-quality eligibility contract remain unchanged while both
  gallery faces and selfie queries use one pinned AdaFace model artifact and identical alignment,
  preprocessing, output normalization, model identity, and dimensions;
- the selected event has a complete, internally compatible AdaFace cohort with no mixture of
  SFace and AdaFace vectors in one search;
- the ordinary event page accepts a selfie and publishes an ordinary ready result page backed by
  exact event-scoped cosine ranking over the AdaFace cohort;
- the query vector is never persisted or logged, and the temporary selfie is deleted before the
  result becomes ready, preserving ADR 0019;
- a provisional AdaFace threshold is recorded from a small local calibration and is never described
  as production-approved; and
- stopping the experiment leaves the neighboring Compose project running and requires no staging or
  production rollback.

## Scope

### Included

- A local-only AdaFace inference adapter for gallery face embeddings and selfie-query embeddings.
- The official `minchul/cvlface_adaface_ir18_webface4m` checkpoint as the selected challenger,
  transformed reproducibly to a pinned local inference artifact without committing model weights.
- Existing YuNet detections and the existing quality-gating inputs.
- A new incompatible experimental processor/model generation and 512-dimensional normalized
  embeddings.
- Exact cosine ranking and the current gallery/result templates.
- A bounded local backfill for `cyclingrace-vechernee-sadovoe` on a staging database clone.
- The existing immutable local `preview-small-v1` corpus for all 17,043 event photos as the sole
  backfill image source.
- A short local threshold calibration using explicitly supplied comparison queries.
- Isolated Docker Compose project naming, volumes, loopback host ports, database, web, and worker.
- Focused contract, adapter, ranking, enrollment, event-scope, privacy, and critical-path checks.

### Excluded

- Staging or production deployment, activation, database mutation, or media mutation.
- A model selector, side-by-side result UI, benchmark dashboard, or automated visual scoring.
- Dual-model production storage or fallback between SFace and AdaFace.
- Recomputing more than the selected event.
- Changing YuNet, face-quality thresholds, cluster-expansion behavior, or gallery presentation.
- Persisting ordinary selfie queries, query vectors, or new biometric history.
- Claiming that the provisional threshold or visual comparison proves a production quality gain.
- Packaging or distributing third-party model weights as a repository or application artifact.
- Downloading event photos again or using Yandex Object Storage credentials for the backfill.

## Selected design

### Branch-local model replacement

The experimental branch replaces the active SFace embedding contract with AdaFace rather than
adding runtime model selection. This is the smallest path that exercises the current product UI.
SFace remains the control on the separately opened main site.

The worker retains YuNet for detection and five-landmark geometry. The AdaFace adapter aligns each
accepted face to the selected current CVLFace model's required `112x112` RGB input, normalizes every
channel with mean `0.5` and standard deviation `0.5`, runs the pinned IR-18 WebFace4M inference
artifact, validates 512 finite values, and L2-normalizes the output. Gallery processing and
selfie-query processing call the same adapter.

The official AdaFace and CVLFace source repositories are MIT-licensed. The checkpoint is used only
for a private local quality experiment. Its origin, upstream revision, download URL, conversion
command, and SHA-256 of the resulting inference artifact are recorded before use. Absence of this
evidence blocks the experiment; the design grants no production or redistribution approval.

### Incompatible experimental generation

AdaFace vectors never enter an SFace generation. The experiment declares a new processor/model
identity with model `adaface-ir18-webface4m`, 512 dimensions, and a configuration hash covering the
model artifact, alignment, input normalization, detection contract, and quality contract.

The local selected event activates only the complete AdaFace generation after reconciliation proves
that every eligible photo has a terminal accepted projection or an explicitly classified terminal
quality rejection. Search rejects an incompatible model, dimension, processor generation, or
configuration instead of falling back to SFace.

No schema migration is required: existing immutable attempts, detections, embeddings, projections,
and event activation records carry the new generation identity. If implementation inspection shows
that these existing records cannot represent the AdaFace generation without ambiguous meaning,
execution stops for a scope decision rather than adding a migration implicitly.

### Existing search and privacy path

The ordinary event page, selfie submission, worker job, cleanup gate, bearer result, result gallery,
event isolation, best-face-per-photo deduplication, and deterministic ordering remain unchanged.
Only the embedding contract and provisional distance threshold differ on the experimental branch.

The threshold is explicit configuration for this local generation. It is selected from a small
local comparison set by inspecting true and false pairs, then frozen before the user-facing visual
comparison. The SFace value `0.363` is never reused as an AdaFace default. The result page must label
matches exactly as the existing probable-match product surface does; it must not claim identity.

### Isolated local runtime

The experiment uses a Compose project name unique to this worktree. Database and other named volumes
therefore cannot resolve to a neighboring checkout's volumes. Database and web ports bind only to
`127.0.0.1` and are selected after inspecting currently bound ports; the intended defaults are
`15433` for PostgreSQL and `18080` for Django, but a collision causes selection of another free pair.

The staging clone command targets only the isolated Compose database and retains its normal explicit
replacement confirmation. It does not stop, attach to, rename, or reuse another Compose project.
The experiment records the resolved Compose project and container names before clone, backfill, and
shutdown, and verifies the neighboring containers remain unchanged afterward.

The sole image source for backfill is
`/Users/petrnikitin/Documents/Projects/photo-prjct-private/event-corpora/cyclingrace-vechernee-sadovoe/previews/preview-small-v1`.
Its `manifest.json` declares event `9`, slug `cyclingrace-vechernee-sadovoe`, 17,043 photos, zero
unresolved items, the production-equivalent 1600px JPEG preview contract, and a complete corpus.
Before processing, the experiment validates the manifest's completeness, event identity, photo-ID
coverage against the cloned database, source/production contract hashes, and per-file size and
SHA-256. The corpus mounts read-only into a one-shot seeding service, which copies only validated
files to their exact preview object keys in the experiment's isolated local S3-compatible store.
The normal Django grant and worker-download path then reads only that local store. Arbitrary paths,
unmanifested files, missing files, checksum mismatches, and event mismatches fail before enqueue.

The worktree does not copy or link the main checkout's `.env`. Backfill needs no Yandex Object
Storage credential, remote download, or cloud IAM change. The isolated local S3-compatible store
also provides the existing temporary-selfie upload, worker grant, cleanup, and result-preview
interfaces without broadening access to the saved host corpus.

## Data flow

1. Validate the complete local 17,043-photo preview manifest without modifying its files.
2. Build or obtain the pinned local AdaFace inference artifact and pass its smoke check.
3. Start the isolated database, web, and worker services under the unique Compose project.
4. Clone staging PostgreSQL into that database through the existing guarded clone workflow.
5. Resolve `cyclingrace-vechernee-sadovoe`, join its eligible photos to the local manifest by
   `photo_id`, and freeze that preview-backed cohort.
6. Enqueue the new AdaFace generation only for that cohort and let the isolated worker read the
   manifest-approved local preview files.
7. Reconcile attempts, projections, compatibility, and coverage; activate the generation locally
   only after the selected event is complete.
8. Calibrate and freeze the provisional local AdaFace cosine-distance threshold.
9. Open the normal local event page, submit a selfie, and publish the ordinary result through the
   unchanged privacy and cleanup path.
10. Compare the local AdaFace result with the same query submitted to the main SFace site.

## Failure and rollback semantics

- Model download, checksum, conversion, load, dimensionality, or smoke failure stops before backfill.
- An incomplete, mismatched, missing, or checksum-invalid local event corpus stops before enqueue;
  the experiment never falls back to remote media.
- Clone validation failure leaves the prior isolated local database recoverable under the existing
  clone workflow and does not touch any other project.
- A partial or failed backfill is not activated. It remains isolated derived data and can resume by
  the immutable processing contract.
- An incompatible or incomplete cohort fails closed; search does not mix models or generations.
- Temporary-selfie deletion failure keeps the result non-terminal exactly as under ADR 0019.
- Rollback is stopping the uniquely named Compose project and retaining or deleting only its
  explicitly identified local volumes. Branch deletion and volume deletion are separate, explicit
  operations; neither is necessary to protect staging or production.

## Acceptance evidence

The handoff records:

- branch, worktree, source SHA, Compose project, host ports, and container names;
- upstream model identity, revision, local artifact SHA-256, and smoke output;
- staging-clone validation result without credentials or private media identifiers;
- local preview manifest identity, declared photo count, completeness, contract hashes, and validated
  database join count without enumerating private filenames;
- selected event identity and bounded backfill totals by accepted, quality-rejected, failed, and
  unresolved outcome;
- generation compatibility and activation reconciliation;
- provisional threshold and the limited calibration method used to choose it;
- focused automated checks and one successful local HTTP event-to-selfie-to-ready-result smoke; and
- before/after evidence that the neighboring Compose project remained running and unchanged.
