# Remote-only photo-worker operations

- **Status:** Approved for implementation planning on 2026-10-02; production transition still requires an exact operation review
- **Date:** 2026-10-02
- **Owner:** FindMe Photo
- **Related architecture:** [Current worker placement](../../architecture.md#current-architecture--implemented),
  [Accepted constraints](../../architecture.md#accepted-constraints)
- **Related ADRs:** [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md),
  [0046](../../adr/0046-isolate-worker-pool-management-in-a-separate-folder.md),
  [0049](../../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
- **Related work:** [Historical AdaFace backfill](2026-10-02-historical-adaface-backfill-and-local-worker-retirement-design.md),
  [Worker-pool runbook](../../runbooks/worker-pools.md)
- **ADR impact:** Conforms to ADR 0049 as revised before this release is merged. Its
  remote-only recovery decision supersedes ADRs 0042/0046 only after the fleet has served
  real work and its initial release is durably committed. The separate-folder, private-TLS,
  durable-job and bounded-capacity decisions remain unchanged.

## Outcome and boundary

Bulk photo processing and selfie inference have one production placement: their existing
isolated Yandex Instance Groups. The canonical VM continues to own Django, PostgreSQL,
Nginx, the private worker API, the queue/coordinator, monitoring, and import/commerce
workers, but cannot run photo or selfie workers as a fallback. A remote outage pauses
photo processing or selfie inference until the remote pool is repaired; durable jobs,
leases, accepted results, media and vectors are not reset or moved.

The approved bulk 0..1 and selfie 1..1 ceilings, existing groups and worker folder,
image/model identities, billing scope and worker authorization remain unchanged. This
decision does not start historical backfill, activate an event, remove SFace/JSON evidence,
delete a VM or disk, or authorize new cloud resources.

## Current transition

The initial remote release has already served real photo and selfie work, but its durable
receipt is `verified` / `remote-pending-acceptance`. Its pinned build is
`18b8c27eb18b65fcf663b0e713df6142ebd39321` and pinned worker image digest is
`sha256:1b4073b781995e89b0ee0025a98a7739f4e98b776ad91c77c643a483ef2f1bbc`.
These identities are historical evidence, not permission to run a command without fresh
read-back. The 2026-10-02 `complete` attempt failed before commit because the shared
observability verifier requires an on-host `worker-bulk` container whenever photo
processing is enabled. That requirement is incompatible with the serving remote topology.
The receipt remained `verified`, its original recovery snapshot remained present, and
the public health endpoint returned HTTP 200 after the failure.

Ordinary Deploy is fenced while this receipt is pending. Repeating the same pinned
`complete`, merging a PR to trigger ordinary Deploy, editing the marker directly, or
temporarily starting local workers is not a valid way out of this state.

## Selected design

### One-time finalization of the already serving fleet

Provide a single reviewed, checksum-bound operator action for this exact pending receipt.
It runs through the canonical deployment authority and serialization lock, not an
arbitrary shell edit or a new application rollout. Before committing, it must compare
the live receipt, deployed web image, both group IDs, worker build and immutable digest
with the approved candidate; verify public/web/private health, actual remote membership,
claims, current leases, provider/disk settlement, native collector freshness and the
application's web log probe. A bulk floor of zero is valid only with the accepted
scale policy and no unexplained retained boot disk; a warm selfie member must be
serving at the pinned build. The action uses the existing release journal's verified
to committed transition and then removes only the identified original local recovery
snapshot/package. It does not reinstall the old application package, rebuild images,
change group scale policy or dispatch historical work.

Failure before journal commit leaves the candidate `verified` and the recovery inputs
intact. If cleanup fails after commit, the action reports partial completion and a
repeat reconciles only the exact remaining owned recovery files; it never runs an old
local `abort`. A fresh read-back must establish `committed`, the exact fleet marker,
healthy remote claims and absence of the original recovery inputs. No success is inferred
from a green workflow alone. This is a one-time transition, not a second permanent
deployment mode.

### Steady remote-only deployment and recovery

After the transition, the canonical deployment has no local photo/selfie Compose
services, local claim credential or authorization, local placement selector, local
worker-container observability assertion, or local restoration branch. The application
log probe still verifies the web log path. Worker health comes from the remote groups,
coordinator, actual claims/attempts, provider state and collector. Immutable build/digest
checks remain as release identity checks; they do not substitute for real work.

Future compatible releases use one normal remote fleet rollout. If a remote worker or
private network fails, stop or pause affected claims, preserve durable attempts and
repair or replace a member inside the existing cap. An older image may be selected only
if it supports every active event's vector generation and processing contract. Otherwise
repair forward; never reactivate on-host photo workers. The backfill runbook starts only
after this remote-only release is deployed and read back, then follows its independent
per-event dry-run, approval and activation gates.

Local development fixtures may exercise the worker protocol, but are not a supported
production placement or recovery path.

## Alternatives considered

1. Abort to local placement and repeat the first activation with a new SHA. This would
   undo a working remote cutover, rerun customer-serving migration and contradict the
   maintainer's remote-only decision.
2. Keep `local` and `remote` branches and merely skip the failing local-container check.
   This leaves obsolete claims, credentials and recovery behavior as a second production
   mode with ongoing deployment complexity.
3. Finalize the already serving remote receipt once, then remove the local production
   path. This is the selected design.

## Acceptance criteria

1. The current release transitions from `verified` to `committed` only when its exact
   pinned web/worker identities and actual remote processing/serving state are proved.
   No repeat cutover, new VM, model backfill or database reset occurs.
2. The original on-host photo-worker recovery snapshot is removed only after the
   committed marker is durable; a failed or partial transition is distinguishable and
   recoverable without editing the marker by hand.
3. A normal deployment from the new reviewed SHA starts no local photo/selfie container,
   exposes no local photo claim path, and cannot silently restore those services.
   Web/DB/private API/import/commerce/monitoring stay healthy.
4. Remote bulk zero-to-one and idle-zero behavior, one warm selfie worker, immutable
   result links, queue/lease recovery and public health remain intact. A remote outage
   leaves work pending rather than changing placement.
5. Historical AdaFace enrollment remains a separately approved operation; the old
   recognition/model cleanup remains separate.

## Failure, privacy and authorization boundaries

The one-time finalizer and remote-only release are production mutations needing exact
fresh scope, command, impact and operator approval. Do not print credentials, object
keys, raw selfies, embeddings or bearer links. An uncertain provider operation or
unhealthy remote group stops finalization rather than choosing a local fallback.
