# Preview Face Quality V4 Rollout Design

- **Status:** Approved in conversation on 2026-08-10; pending written review
- **Date:** 2026-08-10
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), preview-first photo
  processing, immutable processing attempts, event-scoped face search, and immutable-image
  promotion
- **Related specifications:**
  [`2026-08-09-local-preview-quality-profile-design.md`](2026-08-09-local-preview-quality-profile-design.md)
  and
  [`2026-08-07-gallery-face-quality-gate-design.md`](2026-08-07-gallery-face-quality-gate-design.md)
- **Related ADRs:** [ADR 0005](../../adr/0005-promote-images-through-staging.md),
  [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md),
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md),
  [ADR 0020](../../adr/0020-use-signed-direct-object-storage-media-delivery.md),
  [ADR 0024](../../adr/0024-use-gallery-face-as-search-query.md), and
  [ADR 0025](../../adr/0025-expand-selfie-search-with-face-clusters.md)
- **ADR impact:** **Conforms to ADRs 0005, 0017, 0019, 0020, 0024, and 0025.** The rollout
  reuses the accepted immutable image, Django/PostgreSQL processing authority, private worker,
  accepted 1600-pixel preview input, event isolation, immutable result evidence, and explicit
  event-scoped activation. It adds no service, identity system, cross-event matching, query-vector
  retention, or media authorization.

## Outcome

Deploy preview-backed face-embedding processor version `4`, replay it only for the published event
`cyclingrace-vechernee-sadovoe`, and make new gallery-face presentation and event-scoped searches
use the version-4 projections after complete processing and an explicit reviewed activation.

Version `4` uses the already approved local profile: YuNet detection threshold `0.75`, the existing
recall-first quality decision, SFace embeddings, and the accepted `preview-small-v1` input with a
maximum long edge of 1600 pixels. The local full-corpus run over 17,043 photos is the quality
selection evidence. It completed without a technical failure and was accepted by the maintainer
through direct inspection of the ordinary site. No additional manual sample or stricter numerical
quality gate is required for this rollout.

## Scope

### Included

- Production-capable processor identity `3/face_embedding/4` in the existing worker image and
  deployment configuration.
- One bounded, dry-run-by-default, idempotent Django command that enrolls version `4` for one exact
  event after validating the event, accepted preview cohort, configuration, and approval evidence.
- One bounded status surface for exact version-4 job, attempt, terminal, projection, and failure
  counts before activation.
- Content-addressed approval evidence for the exact event, configuration, preview corpus,
  comparison report, and model files; approval records the maintainer's explicit review without
  fabricating unobserved loss counts.
- Dark deployment to staging, staging processing and verification, promotion of the same immutable
  image to an already provisioned production environment, event-only production processing, and
  explicit append-only activation.
- Immutable preservation of every baseline, version-3, version-4, failed-attempt, projection, and
  activation row.
- Rollback by appending an activation that selects the preceding baseline generation; no processing
  evidence is rewritten or deleted.

### Excluded

- Any new UI, filesystem storage adapter, preview format, model, search threshold, cluster rule,
  ranking rule, or cross-event behavior.
- Reprocessing another event, automatically enrolling future photos, or globally selecting version
  `4` without an event activation.
- Reusing the ignored local replay helper, local activation bypass, private filesystem paths, or
  local database in a deployed environment.
- Deleting or mutating historical biometric evidence to save space or simplify rollback.
- Provisioning a production VM, changing VM size, adding worker replicas, or making another
  pricing-affecting cloud change. If production is not provisioned, rollout stops after verified
  staging until that separate billable action is explicitly approved.

## Approval evidence

The tracked approval binds activation to:

- event slug `cyclingrace-vechernee-sadovoe`;
- the exact version-4 configuration hash;
- the complete local preview manifest hash
  `62f071941cd8281745256ed6906f37cbfdac29996f20fd6a992c7f486783d879`;
- the reviewed comparison manifest hash
  `043ce5c02cd6df901f16096c2637c3a26b3b96171a9e9538b439cee12abca0a6`;
- YuNet SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`;
- SFace SHA-256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`;
- local replay counts `photos=jobs=attempts=projections=17043`, technical failures `0`, kept faces
  `37573`, and quality-rejected faces `18610`; and
- a reviewed/approved boolean representing the maintainer's explicit acceptance.

The approval does not claim that a person-labelled recall benchmark was completed and does not
invent `clear_loss`, `relevant_result_loss`, or `unresolved` values. Activation safety comes from
exact artifact identity, complete event coverage, terminal processing integrity, and explicit
human acceptance at the accuracy level chosen for this rollout.

## Processing and activation contract

The ordinary worker obtains the already accepted event preview through the existing short-lived
exact-object grant. Version `4` rejects an original-backed claim. The worker has no PostgreSQL or
permanent Object Storage credentials.

Enrollment is event-scoped, dry-run by default, and requires an explicit apply option. It validates
the event slug, approval identity, version-4 configuration, and the current accepted-preview cohort
inside a transaction before creating or reusing jobs. Repeated application is idempotent and never
rotates a terminal version-4 job into a different identity.

Candidate activation is allowed only when every photo in the frozen eligible cohort has exactly one
compatible accepted version-4 projection and there are no queued, processing, retryable, failed,
stale, or technical-failure states for the candidate cohort. New or changed event photos require a
new reviewed generation rather than silently joining the active one.

Activation appends an `EventFaceEmbeddingActivation`. Existing selfie-search bearer snapshots stay
immutable. New gallery and selfie searches resolve the latest event activation. A cluster corpus
remains generation-specific; version-4 activation does not mutate or silently reuse a corpus built
from another generation.

## Deployment and failure semantics

The release first deploys the API, migration, and worker support while version `4` has no jobs and
is not active. Staging must show a healthy application and a worker capable of claiming one bounded
version-4 smoke cohort before the full staging replay. Production promotion uses the exact image
successfully recorded by staging, consistent with ADR 0005.

Any mismatch in event identity, preview cohort, configuration, artifact hash, model hash, terminal
count, projection coverage, or worker contract stops before activation and preserves all evidence.
A worker or deployment failure uses the existing image rollback and lease/retry semantics. A
quality regression after activation rolls back only future searches by appending the prior
generation selection; already published result snapshots and all processing evidence remain
unchanged.

## Acceptance criteria

- Focused backend, worker, deployment, migration, activation, and enrollment tests pass; the full
  repository check and GitHub CI pass on the merge candidate.
- The branch is reconciled with current `origin/main`; the PR contains no ignored `var/` artifact or
  local/private path.
- The same immutable image is healthy on staging before any production promotion.
- Version `4` is not active during code deployment or while its event cohort is incomplete.
- The target environment reports an exact complete candidate cohort, zero nonterminal/failed/stale/
  technical states, and one compatible projection for every eligible event photo.
- The ordinary event page exposes version-4 accepted faces, and both gallery-origin and uploaded-
  selfie searches resolve version-4 event evidence without changing the `0.363` ranking threshold.
- Historical jobs, attempts, projections, activations, and bearer-result snapshots remain intact.
- Rollback to the prior generation is executable without deleting or rewriting evidence.
- No production infrastructure or pricing-affecting cloud resource is created without a separate
  explicit approval if the production environment is absent.
