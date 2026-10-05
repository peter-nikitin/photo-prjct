# 0051: Publish changed images and activate them from CI

- Status: Accepted
- Date: 2026-10-03
- Deciders: project maintainer; accepted on 2026-10-03, CI-push revision accepted on 2026-10-04,
  existing-key reuse accepted on 2026-10-05
- Supersedes: ADR 0028, ADR 0042 and ADR 0049 for shared web/worker SHA, coupled image rollout,
  and SHA-tagged web release selection
- Superseded by: none

## Context

The worker pools are isolated, but the former canonical release path coupled worker images to each
web commit, rebuilding and restarting unchanged workers. ADR 0050 removes image identity from
processing jobs and attempts, so the queue no longer needs to select workers by release. The
initial worker updater introduced a host timer that checks the registry repeatedly; this hides
activation outside the CI release and delays a ready image unnecessarily. Web and local workers
also need the same simple release trigger as remote photo workers.

## Decision drivers

- Build and activate only components whose effective inputs changed.
- Let new worker VMs use the current image and update existing VMs without replacing them.
- Preserve warm-up, authenticated transport, leases and semantic processing contracts.
- Keep CI as the explicit deployment trigger and one canonical deployment authority. Do not add a
  polling timer, release manifest, orchestrator or standing VM.

## Considered options

1. Keep building and rolling web and worker images together.
2. Publish worker images separately but recreate their VMs for each update.
3. Publish changed components to their own `latest` pointer and have CI activate running hosts;
   retain Compose and the existing in-place readiness/handoff mechanisms.

## Decision

Select option 3. Deploy publishes only components whose effective inputs changed. Documentation-
only changes publish nothing and contact no application host. Each deployable image has its own
`latest` pointer, advanced only after its build and checks pass. A backend-only release leaves
photo-worker images and containers untouched; a photo-worker-only release leaves web untouched.
The Git commit identifies source in CI, not a runtime equality constraint between components.

CI explicitly contacts each running host that owns a changed component, pulls that component's
`latest`, and invokes its existing readiness and handoff operation. The canonical VM is reached
through the current protected deployment path. Remote photo-worker VMs have no public SSH: CI
reaches only their private addresses through the canonical VM. The existing CI deployment SSH key
is reused for both hops; its private half stays in CI and only its public half is installed on
worker VMs. This deliberately shares a credential across canonical and worker hosts, so a key
compromise affects both. Worker ingress permits SSH only from the canonical VM over the private
network. This private path is a separately reviewed one-time access change, not an invitation to
perform routine manual VM deployments. The registry-checking host timer is
removed after CI activation works. A zero-size bulk pool has no host to contact; its next VM
pulls `latest` at boot. CI never starts a VM solely to deploy an image.

The same outer contract applies to web, remote bulk/selfie workers and canonical import/Commerce
workers: changed-image publication, one CI-triggered host pull, readiness, then handoff. Their
inner handoff remains service-specific: Django switches two Compose slots through Nginx; remote
photo workers warm a replacement before changing claim ownership; local background workers drain
their accepted work before replacement. Existing Compose remains the runtime; Docker Swarm is not
introduced. Image IDs or digests may be recorded for diagnosis and explicit recovery, but are not
release manifests, queue fields, shared-SHA gates or a second deployment authority. A failed
candidate leaves the previously serving container in place where the existing handoff permits it.
If activation fails after advancing `latest`, CI restores that pointer to its previously selected
image when available and reports failure. The already-running container remains the source of
truth for service; an incomplete multi-host activation is repaired forward under the compatible
contract, not hidden by a tag change.

Web and worker versions may overlap only when both support the active claim, lease, result and
semantic processor contracts. A breaking change requires an explicit compatible transition.
This decision does not change remote worker placement, private API, processor contracts, VM
shapes, group ceilings or queue semantics.

## Consequences

### Positive

- Unchanged components avoid build, transfer and interruption.
- Scale-from-zero uses the current worker image without a deployment-time VM start.
- Jobs, attempts, media and results remain independent of image release identity.
- The CI run is the visible trigger for both publication and activation; hosts do not poll GHCR.

### Negative

- Independent releases require active compatibility across web and worker versions.
- A warm replacement temporarily uses extra memory on its existing VM.
- `latest` is a pointer, not a recovery record; recovery needs a known compatible prior image.
- Reusing the canonical deployment key increases the hosts reachable if it is compromised;
  private network access and the worker user's limited command authority bound that exposure.
- Private CI-to-worker access and current-image activation must be proven before merging a
  worker-changing release or removing the timer.

## Validation and recovery

Verify documentation-only and component-specific changes select the stated images and hosts;
verify CI reaches running remote workers only through the private path, triggers each activation
once, and waits for ready/serving state. Verify no updater timer remains and a new VM pulls current
`latest`. Recovery selects a known compatible previous image, not an assumed old value of
`latest`. Canonical web recovery is bounded by the live schema-compatibility probe; after
`processing.0016` drops `ProcessingAttempt.worker_build`, an incompatible previous web is not a
rollback candidate. See the [worker-pool runbook](../runbooks/worker-pools.md) and
[deployment runbook](../runbooks/deployment.md).

## References

- [Independent worker-image deployment specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md)
- [Zero-downtime Django deployment specification](https://github.com/peter-nikitin/photo-prjct/blob/b1594a2ebdadf9fea8edbe7c4dfab6615b3b0bc7/docs/superpowers/specs/2026-10-04-zero-downtime-django-deployment-design.md)
- [ADR 0050: Decouple processing jobs and attempts from worker builds](0050-decouple-processing-queue-from-worker-builds.md)
- [ADR 0028: One canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0042: Isolated worker pools](0042-isolate-autoscaled-photo-worker-pools.md)
- [ADR 0049: Remote-only recovery](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
