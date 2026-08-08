# Ten-percent face-quality review design

**Status:** Approved design

**Date:** 2026-08-08

## Outcome

Use a reproducible ten-percent manual sample of the latest event's quality-rejected faces as
sufficient local evidence for the operator to accept or reject the experimental quality
configuration. The review reports evidence and uncertainty; it does not apply an automatic pass or
fail threshold and does not activate a processor generation.

This design deliberately replaces the earlier Task 6 requirement to label every rejected face
before making the current experimental decision. Existing immutable full-corpus runs, comparison
artifacts, indexes, and the complete 15,052-face review bundle remain unchanged.

## Frozen inputs

- Quality comparison bundle SHA-256:
  `f1028cf1e581645dd0cf108e356394dc5ada838b92c9f662c1356cd52e657b48`.
- Rejected population: 15,052 matched baseline-accepted and candidate-rejected faces.
- Retained threshold controls: the existing 100 deterministic samples, 20 around each of the five
  configured thresholds.
- All private crops, labels, absolute paths, face identifiers, and query material remain outside
  Git.

The sampling tool must strictly reload and validate the complete comparison bundle before selecting
anything. It must reject a changed manifest, source hash, population, crop, or existing output.

## Sample

Select exactly 1,506 rejected faces. The sample is deterministic and reproducible: its seed is
derived from the frozen comparison-bundle SHA-256, not from runtime randomness.

Allocation is stratified by the exact rejection-reason tuple. Each non-empty rare stratum receives
a minimum allocation when the total sample permits it; remaining slots are allocated
proportionally using deterministic largest-remainder allocation. Selection inside each stratum uses
a stable hash of the bundle identity and face identifier. The artifact records population count,
sample count, and inclusion weight for every stratum so aggregate estimates remain population
weighted even when rare strata are deliberately oversampled.

The 100 retained threshold controls are shown as a separate audit set and do not replace any of the
1,506 rejected samples.

## Review workflow

One reviewer labels sampled rejected faces as exactly one of:

- `clear`: a usable, sufficiently clear face that the candidate should have retained;
- `blurred`: a real face whose blur makes it unsuitable for a stored embedding;
- `unusably_small`: a real face too small to provide useful identity evidence;
- `uncertain`: the crop does not support a confident decision.

The local, file-only review is split into pages of at most 250 rejected faces. It provides keyboard
shortcuts `1` through `4`, visible definitions, progress by page and total, browser-local draft
storage scoped to the immutable sample identity, validated import, and one complete CSV export.
Reloading a page must preserve the local draft. Export must fail until every one of the 1,506 sample
rows has exactly one valid label. No label is sent to a server or written into a database.

The retained controls are reviewable separately. They are evidence about what the candidate keeps,
not part of the rejected-population rate estimate.

## Analysis and decision

Finalization strictly validates sample identity, exact row coverage, uniqueness, labels, stratum
metadata, and source hashes. It produces an immutable private report containing:

- raw and population-weighted counts and proportions for all four labels;
- a 95% confidence interval for the population-weighted `clear` proportion;
- results by rejection-reason stratum and configured threshold vicinity;
- a review gallery containing every sampled `clear` and `uncertain` item;
- bounded source identities, reviewer identifier, review timestamp, and artifact hashes.

The tool does not produce an automatic pass/fail result. The operator reviews the report and records
the experimental decision. A ten-percent decision is explicitly labelled as sampled evidence; it
must not be represented as proof of zero clear-face loss or as full-population manual coverage.

If the operator accepts the test, that acceptance is sufficient for the current local experiment.
Any later production activation remains a separate explicit action and must state that its quality
evidence came from the ten-percent sample.

## Search-level review

Search relevance remains independent of face-quality labels. Review the 30 primary queries in the
existing immutable benchmark proposal first, using `relevant`, `different`, or `uncertain` for each
candidate. Open deterministic replacement queries only when a primary query cannot provide three
relevant held-out photos or conflicts with an already selected manual identity.

After 30 valid, person-disjoint queries are finalized, run the same closed query set against the
baseline and candidate indexes with full-photo holdout and direct threshold `0.363`. Do not infer
relevance from cluster membership and do not tune the direct threshold.

## Privacy, isolation, and immutability

- All sample crops, labels, reports, query material, and identifiers remain in the private benchmark
  root outside Git.
- The sampler and finalizer are filesystem-only and do not use Django, PostgreSQL, Object Storage,
  the downloader application, or port 55432.
- New sample and finalization outputs use non-existing directories and atomic publication. Failed
  attempts never overwrite prior evidence.
- No processor generation, production threshold, event activation, or historical result changes as
  a side effect of sampling, review, or report generation.

## Verification

Tests cover deterministic selection, exact sample size, rare-stratum allocation, weighting,
changed-input rejection, paging, browser-local resume/import/export contracts, incomplete and
duplicate label rejection, confidence-interval calculation, privacy-safe bounded output, and
atomic no-overwrite publication.

A real smoke check must build the sampled review from the frozen 15,052-face comparison, confirm
1,506 unique sampled rejections plus 100 retained controls, open representative pages locally, and
round-trip a fixture label export through the finalizer without changing the source bundle.
