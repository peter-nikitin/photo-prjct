# SCRFD production follow-ups

## Why these items do not block the first release

The approved release has one immediate goal: replace YuNet with the already benchmarked
SCRFD-10G_KPS detector for new preview-backed gallery jobs and new selfie queries before the next
event. The critical worker, model, versioning, normalization, and local acceptance paths are covered
in the implementation specification. The items below require broader evidence or a new product
decision and are not needed for that critical path.

## Commercial model licence

**Observed gap:** official InsightFace pretrained weights are restricted to non-commercial
research.

**Why non-blocking now:** the maintainer has confirmed that the current project is non-commercial.

**Trigger:** before charging customers, selling event-photo services, accepting commercial
sponsorship tied to the service, or otherwise operating the model commercially, obtain written
commercial rights or replace the weights with a commercially cleared detector.

## Broader detector validation and multi-face policy

**Observed gap:** the 36-case feedback cohort comes from one event. SCRFD recovered 17/17 intended
foreground faces but retained only 1/3 multiple-face controls as multiple.

**Why non-blocking now:** foreground recovery is the accepted release priority and the maintainer
explicitly accepted direct SCRFD behavior without a YuNet veto.

**Trigger:** after feedback from the next event, or when an incorrect-person selfie result is
reported, build a multi-event labelled cohort and decide whether to keep foreground-only behavior,
add a conservative multi-face policy, or recalibrate detector size/thresholds.

## Quality-v4 migration

**Observed gap:** the separately activated quality-v4 cohort and its confidence gate were calibrated
for YuNet and remain on their historical generation.

**Why non-blocking now:** normal new uploads use preview-backed face-embedding v3; the approved task
does not reprocess the historical quality cohort.

**Trigger:** before reprocessing that event, activating quality processing for a new event, or using
quality-v4 as the default upload path, benchmark SCRFD confidence/quality interactions and introduce
a new immutable quality generation.

## Worker capacity

**Observed gap:** the local detector benchmark recorded higher CPU latency for SCRFD than YuNet and
did not measure full event throughput in the production worker image.

**Why non-blocking now:** the observed per-image latency remains small relative to the existing
single-job worker lifecycle, and the first release retains concurrency one.

**Trigger:** if the local acceptance run fails its practical wait-time expectation, or event queue
age approaches the existing processing SLA, run the standard worker throughput benchmark before
changing replicas, providers, or concurrency.

## Historical gallery backfill

**Observed gap:** existing photos keep their stored YuNet/SFace results and do not benefit from
SCRFD recall.

**Why non-blocking now:** the maintainer explicitly limited this release to new uploads and selfies.

**Trigger:** an explicit event-scoped request to improve discovery on already processed photos must
define the target event, immutable processor generation, capacity window, rollback, and activation
evidence before enqueueing any backfill.
