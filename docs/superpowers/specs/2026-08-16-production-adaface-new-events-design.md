# Production AdaFace for New Events Design

- **Status:** Approved in conversation on 2026-08-16
- **Owner:** FindMe Photo
- **Related:** `2026-08-16-scrfd-production-detector-design.md`, ADR 0017, ADR 0019
- **ADR impact:** Conforms. Processing remains event-scoped, immutable and worker-backed; selfie
  queries and vectors remain ephemeral.

## Outcome

After deployment, every newly created event is pinned to SCRFD-10G_KPS plus AdaFace IR18
WebFace4M for gallery embeddings and selfie queries. Existing events retain their previously pinned
SFace generation and require no replay or backfill.

The first production threshold is `0.42`, explicitly provisional. It may change only through a new
immutable event generation; existing event search configuration is not rewritten.

## Design

- Persist the selected face-search generation on the event when the event is created. The default
  for new events is production AdaFace v5; existing rows are migrated to the current SFace v3
  generation so deployment cannot reinterpret them.
- Preview publication automatically queues the generation pinned by the owning event. AdaFace work
  uses contract `3/face_embedding/5`, the pinned SCRFD and AdaFace artifacts, 512 dimensions and the
  existing quality gate. It never falls back to SFace.
- Selfie submission freezes the event's exact generation, model, dimensions and threshold. The
  worker runs `1/selfie_query/2` with the same SCRFD landmarks and AdaFace recognizer.
- Search reads only compatible accepted projections for that event. Photos still processing are
  absent until their accepted projection is published; a failed photo does not block other photos
  or require an event-wide activation.
- Production Compose claims preview generation, AdaFace v5 and selfie-query v2. The local-only flag
  and canary limit are not production controls and remain unavailable outside local runtime.

## Failure and rollback

- Missing model artifacts, configuration mismatch, incompatible dimensions or invalid threshold
  fail closed before inference or ranking.
- Existing events continue using SFace during both rollout and rollback.
- Rollback changes only the default for events created after the rollback deployment. Events already
  pinned to AdaFace remain AdaFace; changing them requires an explicit event operation, never an
  implicit fallback.
- No production backfill is part of this rollout.

## Acceptance

- A pre-existing event resolves to its SFace generation before and after deployment.
- A newly created event resolves to AdaFace v5 with threshold `0.42`.
- Publishing a preview for the new event queues AdaFace v5 and produces a compatible 512D projection.
- A selfie for the new event uses AdaFace and ranks only compatible event-scoped projections.
- Production worker configuration includes the exact gallery and selfie identities; local-only
  gates are absent.
- Privacy and authorization tests remain green and no ordinary query vector is persisted.
