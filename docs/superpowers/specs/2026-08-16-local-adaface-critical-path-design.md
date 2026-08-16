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
  [`2026-08-05-gallery-face-selector-design.md`](2026-08-05-gallery-face-selector-design.md), with
  [`2026-08-16-scrfd-production-detector-design.md`](2026-08-16-scrfd-production-detector-design.md)
  as an approved prerequisite
- **Related ADRs:**
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md)
- **ADR impact:** **None — reversible local experiment.** The experiment does not change the
  accepted production model, search contract, storage boundary, deployment, or public behavior.
  AdaFace state remains confined to an experimental branch and an isolated local staging clone.

## Outcome

A maintainer can open the normal `cyclingrace-vechernee-sadovoe` event on an isolated local server,
submit a selfie through the existing gallery interface, and inspect an SCRFD-10G_KPS plus
AdaFace-backed result using the same customer path as the production SCRFD-10G_KPS plus SFace site.
The maintainer compares the two sites in separate browser windows; the experiment adds no
comparison UI or model selector.

The local application uses a staging database freshly cloned after the SCRFD prerequisite, the
already saved local preview corpus, and a complete SCRFD plus AdaFace face-embedding cohort for that
event. It never reads event photos from Object Storage and never mutates staging, production, the
main checkout, or another agent's local containers, database, volumes, or ports.

## Success criteria

The critical path succeeds when:

- the experiment runs from `.worktrees/adaface-critical-path` on
  `codex/adaface-critical-path` with the approved SCRFD production-detector prerequisite merged;
- Docker Compose uses a unique project name, unique named volumes, loopback-only host ports, and no
  host port already used by another checkout;
- the isolated PostgreSQL database is freshly recloned from staging after the SCRFD prerequisite is
  present; no database containing old YuNet experimental jobs, results, projections, or activation
  evidence is reused, and only the selected event receives experimental face reprocessing;
- every backfill input resolves through the complete local preview manifest under
  `/Users/petrnikitin/Documents/Projects/photo-prjct-private/event-corpora/` and no backfill request
  reads remote media;
- gallery faces and selfie queries use the same pinned SCRFD-10G_KPS detector at `640×640`,
  confidence `0.5`, NMS `0.4`, and the same five landmarks for
  `scrfd-five-landmark-112x112` AdaFace alignment; both paths use one pinned AdaFace model artifact
  and identical preprocessing, output normalization, model identity, and dimensions;
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
- The approved SCRFD-10G_KPS prerequisite as the only detector for both experimental gallery and
  selfie inference, fixed at `640×640`, confidence `0.5`, NMS `0.4`, with five landmarks and
  artifact SHA-256
  `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91`.
- The official `minchul/cvlface_adaface_ir18_webface4m` checkpoint as the selected challenger,
  transformed reproducibly to a pinned local inference artifact without committing model weights.
- The existing quality-evidence contract applied to newly computed SCRFD detections; no old YuNet
  quality decision is reused as evidence for this generation.
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
- Changing SCRFD input size, confidence or NMS thresholds, face-quality thresholds,
  cluster-expansion behavior, or gallery presentation.
- Persisting ordinary selfie queries, query vectors, or new biometric history.
- Claiming that the provisional threshold or visual comparison proves a production quality gain.
- Packaging or distributing third-party model weights as a repository or application artifact.
- Downloading event photos again or using Yandex Object Storage credentials for the backfill.

## Selected design

### SCRFD prerequisite and branch-local recognizer replacement

The approved production-detector work is a prerequisite. Production uses SCRFD-10G_KPS plus SFace;
the experimental branch keeps that detector and replaces only the recognizer contract with AdaFace
rather than adding a fallback or customer-facing model selection. This is the smallest path that
exercises the current product UI. SCRFD plus SFace remains the control on the separately opened
production site.

The worker uses the prerequisite's SCRFD-10G_KPS artifact SHA-256
`5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` and fixed detector behavior:
`640×640` input, confidence `0.5`, NMS `0.4`, and five source-space landmarks. The AdaFace adapter
consumes those landmarks through `scrfd-five-landmark-112x112`, aligns each accepted face to the
selected CVLFace model's required `112x112` RGB input, normalizes every channel with mean `0.5` and
standard deviation `0.5`, runs the pinned IR-18 WebFace4M inference artifact, validates 512 finite
values, and L2-normalizes the output. Gallery processing and selfie-query processing call the same
detector and recognizer adapters.

The official AdaFace and CVLFace source repositories are MIT-licensed. The checkpoint is used only
for a private local quality experiment. Its origin, upstream revision, download URL, conversion
command, and SHA-256 of the resulting inference artifact are recorded before use. Absence of this
evidence blocks the experiment; the design grants no production or redistribution approval.

### Incompatible experimental generation

AdaFace vectors never enter an SFace generation. The experiment declares a new processor/model
identity with model `adaface-ir18-webface4m`, 512 dimensions, and a configuration hash covering the
AdaFace artifact and revision, SCRFD artifact, `640×640` detector input, confidence `0.5`, NMS
`0.4`, `scrfd-five-landmark-112x112` alignment, input normalization, and quality contract.

The local selected event activates only the complete AdaFace generation after reconciliation proves
that every eligible photo has a terminal accepted projection or an explicitly classified terminal
quality rejection. Search rejects an incompatible model, dimension, processor generation, or
configuration instead of falling back to SFace.

No schema migration is required: existing immutable attempts, detections, embeddings, projections,
and event activation records carry the new generation identity. If implementation inspection shows
that these existing records cannot represent the AdaFace generation without ambiguous meaning,
execution stops for a scope decision rather than adding a migration implicitly.

### Invalidated YuNet experiment evidence

Any previous local canary, job, attempt, detection, embedding, projection, reconciliation total, or
activation produced with YuNet is incompatible with this SCRFD plus AdaFace generation. It is not a
baseline, resumable partial run, or acceptance evidence and must never be mixed with the new v5
cohort. Before either the 100-photo canary or the full backfill, the isolated PostgreSQL database
must be replaced through the guarded staging-clone workflow after the SCRFD prerequisite is present.
Reusing or incrementally repairing an isolated clone that contains the old YuNet experiment is
forbidden; the saved immutable preview corpus may be reused after its existing manifest validation.

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
shutdown, and verifies the neighboring containers remain unchanged afterward. A fresh guarded clone
after the SCRFD prerequisite is mandatory even if an earlier isolated AdaFace database exists,
because its YuNet-derived experimental rows are invalid for this generation.

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

The local Compose override enables the experiment on `web` only with
`MONITORING_ENVIRONMENT=local`, `ADAFACE_LOCAL_EXPERIMENT_ENABLED=True`, and an explicitly supplied
`ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD`. Compose interpolation fails when the threshold is absent;
there is no `0.363` or other silent default. A finite non-`0.363` placeholder may be supplied only
because Django settings must parse while the isolated stack performs backfill. It is not calibrated
evidence and must be replaced by the frozen `local-provisional` value before the first UI request.

The isolated worker overrides the production selectors with exactly
`3/face_embedding/5,1/selfie_query/2`, removes the plural and singular legacy type selectors, and
retains the narrow insecure-HTTP exception only for signed local MinIO downloads. Base and
production Compose keep the approved SCRFD plus SFace identities and settings unchanged.

## Data flow

1. Verify the approved SCRFD production-detector prerequisite is present, including its pinned
   detector identity and processor contracts.
2. Validate the complete local 17,043-photo preview manifest without modifying its files.
3. Build or obtain the pinned local AdaFace inference artifact and pass the combined SCRFD plus
   AdaFace smoke check.
4. Supply an explicit finite non-`0.363` backfill-only threshold placeholder so Compose can parse;
   do not describe or use it as calibrated search acceptance evidence.
5. Start only the isolated PostgreSQL and local MinIO services under the unique Compose project.
   Do not start the long-running web or worker services.
6. Freshly clone staging PostgreSQL into that database through the existing guarded replacement
   workflow; do not reuse a clone that contains old YuNet experimental state.
7. Query the fresh clone and require zero face-embedding v5 jobs, attempts, projections, or event
   activations. This stronger zero-v5 check includes every row under the invalid old YuNet
   configuration hash.
8. Seed the validated preview corpus into isolated MinIO while web and worker remain stopped.
9. Start web and worker only after clone verification and seeding. Require the worker's exact
   identities `3/face_embedding/5,1/selfie_query/2` and no legacy processor-type selectors.
10. Resolve `cyclingrace-vechernee-sadovoe`, join its eligible photos to the local manifest by
   `photo_id`, and freeze that preview-backed cohort.
11. Enqueue the 100-photo canary and then the remaining new SCRFD plus AdaFace generation only for
   that cohort; let the isolated worker read the manifest-approved local preview files.
12. Reconcile attempts, projections, compatibility, and coverage; activate the generation locally
   only after the selected event is complete.
13. Calibrate and freeze the provisional local AdaFace cosine-distance threshold. Replace the
    backfill-only placeholder and recreate web before any UI request.
14. Open the normal local event page, submit a selfie, and publish the ordinary result through the
   unchanged privacy and cleanup path.
15. Compare the local SCRFD plus AdaFace result with the same query submitted to the production
    SCRFD plus SFace site.

## Failure and rollback semantics

- Model download, checksum, conversion, load, dimensionality, or smoke failure stops before backfill.
- Missing explicit local distance threshold stops at Compose interpolation. The backfill-only
  placeholder never authorizes a UI comparison; web must be recreated with the frozen
  `local-provisional` value first.
- An incomplete, mismatched, missing, or checksum-invalid local event corpus stops before enqueue;
  the experiment never falls back to remote media.
- Clone validation failure leaves the prior isolated local database recoverable under the existing
  clone workflow and does not touch any other project.
- Discovery of any old YuNet experimental job, result, projection, or activation blocks the canary
  and full backfill; replace the isolated database with a fresh guarded clone instead of resuming it.
- Starting the long-running web or worker service before the guarded clone, zero-v5 verification,
  and corpus seed invalidates the run ordering; stop those services and restart from a fresh clone.
- A partial or failed backfill produced by this exact SCRFD plus AdaFace generation is not activated.
  It remains isolated derived data and can resume by the immutable processing contract.
- An incompatible or incomplete cohort fails closed; search does not mix models or generations.
- Temporary-selfie deletion failure keeps the result non-terminal exactly as under ADR 0019.
- Rollback is stopping the uniquely named Compose project and retaining or deleting only its
  explicitly identified local volumes. Branch deletion and volume deletion are separate, explicit
  operations; neither is necessary to protect staging or production.

## Acceptance evidence

The handoff records:

- branch, worktree, source SHA, Compose project, host ports, and container names;
- prerequisite SCRFD source identity, fresh staging-clone verification, and confirmation that no
  old YuNet experimental state was reused;
- upstream model identity, revision, local artifact SHA-256, and smoke output;
- staging-clone validation result without credentials or private media identifiers;
- local preview manifest identity, declared photo count, completeness, contract hashes, and validated
  database join count without enumerating private filenames;
- selected event identity and separate 100-photo canary/full-backfill totals for the exact SCRFD
  plus AdaFace generation by accepted, quality-rejected, failed, and unresolved outcome;
- generation compatibility and activation reconciliation;
- provisional threshold and the limited calibration method used to choose it;
- focused automated checks and one successful local HTTP event-to-selfie-to-ready-result smoke; and
- before/after evidence that the neighboring Compose project remained running and unchanged.
