# Local Preview Quality-Profile Design

- **Status:** Approved in conversation and written review on 2026-08-09
- **Date:** 2026-08-09
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), preview-first photo
  processing, immutable processing attempts, and local private benchmark evidence
- **Related specification:**
  [`2026-08-07-gallery-face-quality-gate-design.md`](2026-08-07-gallery-face-quality-gate-design.md)
- **Related ADRs:** [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md)
- **ADR impact:** **None — reversible implementation detail.** This is private local experiment
  tooling. It does not change deployed storage, worker topology, search authorization, or durable
  media architecture.

## Outcome

The complete frozen event-9 original cache produces one deterministic local `preview-small-v1`
corpus using the production preview normalization code. Several face-detection and quality profiles
are then compared on the same preview bytes without writing processing results to PostgreSQL. After
human review selects one profile, exactly one new immutable face-processing generation is replayed
against the complete preview corpus in the isolated local database.

This supersedes the invalid local evaluation that processed full-resolution originals as quality
evidence. That attempt, its projections, and its existing local-only activation record remain
preserved; none may support approval of the preview-backed generation.

## Scope

### Included

- One-off generation of `preview-small-v1` files from the existing verified event-9 originals.
- Reuse of `photo_worker.preview.generate_preview` and the production preview output contract,
  including EXIF orientation, sRGB conversion, maximum long edge 1600, JPEG quality, byte limits,
  dimensions, and SHA-256 evidence.
- A separate private preview directory and immutable manifest outside every Git checkout and
  application media root.
- An analysis-only profile comparison over the seven reported problem photos and the existing
  deterministic 10% review sample.
- Detection-threshold candidates `0.75`, `0.70`, and `0.65`.
- The current recall-first quality rule plus bounded candidate rules that strengthen rejection of
  very small/background faces without introducing a global hard confidence threshold.
- Reports containing per-detection bbox, confidence, minimum side, relative area, sharpness,
  decision, reasons, and changed-decision review crops.
- A separate, versioned full-corpus replay only after one exact profile receives human approval.

### Excluded

- A general filesystem storage adapter or any deployed storage change.
- S3 reads after the already verified original cache is available.
- Mutation or deletion of previous jobs, attempts, detections, embeddings, projections,
  activations, benchmark attempts, or reports.
- Production, staging, downloader databases, ports 5432 or 55432, or any non-local activation.
- Changing SFace, vector normalization, search distance `0.363`, cluster thresholds, or search
  ranking.
- Selecting a final quality profile automatically from aggregate scores.

## Preview corpus

The source is the complete verified event-9 cache manifest and its `originals/` directory. The
preview output is a sibling `previews/preview-small-v1/` directory. Each preview uses a generated
photo-ID filename and is written through a partial file followed by atomic publication. Existing
complete files are reused only when manifest identity, byte size, dimensions, and SHA-256 match.

The preview manifest freezes:

- source manifest hash and event identity;
- production preview contract identity;
- sorted photo IDs and source SHA-256 values;
- preview filename, byte size, dimensions, oriented source dimensions, SHA-256, and warnings;
- unresolved failures; and
- a canonical manifest hash and completion marker.

Generation fails closed on an incomplete or changed source corpus, unexpected files, symlinks,
output-contract violations, or any unresolved photo. No preview bytes or private absolute paths are
committed to Git.

## Profile comparison

Every profile runs on exactly the same completed preview bytes. The first comparison set is the
seven problem photos identified in manual review. The second is the existing deterministic 10%
quality sample. Analysis is filesystem-only and does not create Django jobs, attempts, projections,
or activations.

The comparison holds YuNet and SFace model files constant. It varies detection threshold and one
quality decision profile at a time. The report separates:

- detector misses and newly recovered detections;
- current keeps and rejections;
- newly rejected faces;
- newly accepted faces;
- faces whose decision changed only because preview-scale evidence changed; and
- technical failures.

Candidate quality profiles may add a hard unusably-small floor or an area-sensitive blur rule.
They must not add a global `confidence >= 0.82` requirement: confidence remains supporting evidence
so clear helmeted or eyewear-obscured faces are not discarded solely by detector confidence.

## Human decision and full replay

No aggregate metric selects the winner. The operator reviews every changed decision on the seven
problem photos and the changed-decision bundle from the 10% sample. Approval freezes the exact
detection threshold, decision algorithm version, thresholds, preview manifest hash, and report
hash.

Only then may the local replay enroll a new processor generation against all 17,043 preview files.
The replay uses a new immutable configuration identity, input fingerprints tied to the preview
manifest, and the isolated PostgreSQL port 55433. It must reach exactly 17,043 accepted successful
terminal attempts and projections with zero queued, processing, retry, failed, stale, or technical
failures before a local-only activation can be considered.

## Acceptance criteria

- Exactly 17,043 deterministic production-contract previews exist with a complete verified
  manifest and zero unresolved items.
- Re-running preview materialization reuses all verified files and changes no bytes or hashes.
- Every comparison profile consumes the same preview manifest and produces immutable,
  content-addressed evidence.
- The seven problem photos explicitly show whether each reported foreground miss is recovered and
  whether each reported blurred/background keep is rejected.
- The 10% comparison exposes every changed decision for human review; no changed clear face is
  silently accepted as a loss.
- No comparison run writes to PostgreSQL or S3.
- The prior original-backed v3 attempt and activation record remain unchanged and are excluded from
  preview-profile approval evidence.
- A full replay cannot start until one exact profile is human-approved.
- A full replay cannot activate unless its exact 17,043-photo preview cohort is terminal and
complete in the isolated local database.

### Approved local replay selection — 2026-08-09

The operator approved `detection_threshold=0.75` with the unchanged `current-v3` quality decision
configuration after reviewing the preview-backed comparison. The selected evidence is frozen by:

- preview manifest SHA-256
  `62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879`;
- comparison manifest SHA-256
  `043ce5c02cd6df901f16096c2637c3a26b3b96171a9e9538b439cee12abca0a6`;
- YuNet SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`;
- SFace SHA-256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`.

The replay uses a new immutable processor version `4` even though the decision algorithm and
threshold values are unchanged. Reusing version `3` would collide with the prior original-backed
generation and mutate its projection pointers. Version `3` remains a valid historical activation
for rollback; version `4` is preview-backed only.

## Failure and rollback

An incomplete preview corpus or comparison report stops before profile approval. A profile that
loses a manually confirmed clear face is rejected rather than edited in place. Each changed profile
publishes a new immutable report.

Rollback before full replay is deletion of disposable local preview/report artifacts only after
their evidence is no longer needed. Rollback after replay selects the prior local generation for
future searches; no historical processing row is mutated or deleted.
