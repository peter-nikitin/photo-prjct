# 0051: Release photo-worker images independently

- Status: Accepted
- Date: 2026-10-03
- Deciders: project maintainer; explicitly accepted in conversation on 2026-10-03
- Supersedes: ADR 0028, ADR 0042 and ADR 0049 only for a shared web/worker SHA and coupled
  image rollout
- Superseded by: none

## Context

The worker pools are isolated, but the former canonical release path coupled worker images to each
web commit, rebuilding and restarting unchanged workers. ADR 0050 removes image identity from
processing jobs and attempts, so the queue no longer needs to select workers by release.

## Decision drivers

- Keep web releases independent from healthy workers.
- Let new worker VMs use the current image and update existing VMs without replacing them.
- Preserve warm-up, authenticated transport, leases and semantic processing contracts.
- Keep one canonical deployment authority and existing capacity limits; do not add a release
  manifest or standing VM.

## Considered options

1. Keep building and rolling web and worker images together.
2. Publish worker images separately but recreate their VMs for each update.
3. Publish changed components and replace a worker container in place after it is warm.

## Decision

Select option 3. Deploy publishes only components whose effective inputs changed. Documentation-
only changes do not publish images or contact the canonical VM. Backend-only releases leave the
worker image pointer, running containers and group templates unchanged. Worker-input changes build
the worker image on a reusable base, then advance `latest` only after image checks succeed. A worker
VM started later pulls that image; an existing host warms and switches a replacement container
without a VM restart. An empty bulk group remains empty until queue demand starts a VM.

The web image remains SHA-tagged and immutable. Worker image digests and revision labels are
operational evidence, not queue or attempt identity. Web and worker versions may overlap only when
both support the active claim, lease, result and semantic processor contracts. A breaking change
requires an explicit compatible transition. A failed replacement leaves the previous container
serving until the candidate is ready.

Installing the host updater on the current fleet is a one-time template transition. At the existing
one-VM selfie ceiling, the maintainer accepts pausing processing while claims are drained and that
VM is recreated to load the updater. The existing bulk group remains at zero while idle; no second
VM is added. Later worker releases update containers in place and do not change group templates.

This decision supersedes the shared-SHA and coupled-image requirements in ADRs 0028, 0042 and
0049 only. It does not change the canonical deployment, remote worker placement, private API,
processor contracts, VM shapes, group ceilings or queue semantics.

## Consequences

### Positive

- Web-only releases avoid worker build, transfer and interruption.
- Scale-from-zero uses the current worker image without a deployment-time VM start.
- Jobs, attempts, media and results remain independent of image release identity.

### Negative

- Independent releases require active compatibility across web and worker versions.
- A warm replacement temporarily uses extra memory on its existing VM.
- `latest` is a pointer, not a recovery record; operators need a resolvable compatible digest.

## Validation and recovery

Verify documentation-only, backend-only and worker-input changes select the stated components;
verify a running worker switches only after warm readiness and that a new VM pulls current
`latest`. Recovery selects a prior immutable worker digest only when it supports current semantic
contracts. Canonical web recovery is additionally bounded by the live schema-compatibility probe;
after `processing.0016` drops `ProcessingAttempt.worker_build`, an incompatible previous web is
not a rollback candidate. See the [worker-pool runbook](../runbooks/worker-pools.md) and
[deployment runbook](../runbooks/deployment.md).

## References

- [Independent worker-image deployment specification](../superpowers/specs/2026-10-03-independent-worker-image-deployment-design.md)
- [ADR 0050: Decouple processing jobs and attempts from worker builds](0050-decouple-processing-queue-from-worker-builds.md)
- [ADR 0028: One canonical deployment](0028-operate-one-canonical-deployment.md)
- [ADR 0042: Isolated worker pools](0042-isolate-autoscaled-photo-worker-pools.md)
- [ADR 0049: Remote-only recovery](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
