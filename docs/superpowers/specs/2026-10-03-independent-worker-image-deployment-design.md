# Independent worker-image deployment

- **Status:** Approved for implementation planning on 2026-10-03
- **Date:** 2026-10-03
- **Owner:** FindMe Photo
- **Related architecture:** [Current worker placement](../../architecture.md#current-architecture--implemented),
  [Accepted constraints](../../architecture.md#accepted-constraints)
- **Related ADRs:** [0028](../../adr/0028-operate-one-canonical-deployment.md),
  [0042](../../adr/0042-isolate-autoscaled-photo-worker-pools.md),
  [0049](../../adr/0049-retire-local-photo-worker-recovery-after-remote-acceptance.md),
  [0050](../../adr/0050-decouple-processing-queue-from-worker-builds.md),
  [0051](../../adr/0051-release-photo-worker-images-independently.md)
- **Related work:** [Remote-only photo-worker operations](2026-10-02-remote-only-photo-worker-operations-design.md)
- **ADR impact:** ADR 0051 is accepted and supersedes ADRs 0028, 0042 and 0049 only where
  they require one web/worker SHA or one coupled image rollout. This design conforms to ADR 0050's
  separation of processing work from worker release identity. The canonical deployment,
  remote-only placement, private worker API and compatible recovery constraints remain.

## Outcome and boundary

A backend release must not rebuild, transfer or restart an unchanged photo worker. A worker
release updates the image used by existing bulk/selfie VMs and by VMs started later, without
recreating a VM merely to change its container. A documentation-only change does not deploy
anything. This design changes image publication and container replacement, not the durable
processing queue, model-generation semantics, pool size limits, VM shapes or cloud folders.

Before this change, Deploy built the worker on every `main` push, embedded that commit SHA in its
OCI revision label, and expected web and worker to share it. Those former implementation facts
are not requirements retained by this design.

## Selected design

### Decide what to publish from the change itself

Deployment is classified after a change lands on `main`, before any image build or remote
mutation. A change containing **only documentation** does not build or publish an application
or worker image, contact the deployment VM, replace a container or update an Instance Group.
CI/document checks may still run. A failed earlier release is retried explicitly; a later
documentation-only change is not an implicit deployment retry.

The worker image is published only when its effective build inputs change: worker source and
requirements under `src/worker/`, `Dockerfile.worker`, `.dockerignore`, or the worker-image
build configuration. Models and other pinned assets described by the Dockerfile count as
worker inputs. A backend-only change does not publish or replace the worker, even when Django
and worker continue to communicate through their existing private API. A worker-only change
does not require rebuilding the web image solely to give both images the same commit SHA.
Changes outside those two simple cases deploy only the components they actually affect;
deployment/configuration changes are not disguised as documentation-only changes.

The worker image has a reusable base containing the large pinned models and dependencies.
Ordinary worker-code changes replace only the smaller code layers. Publishing a worker image
advances one registry pointer, `latest`, to the new complete image. The image behind that tag
remains an immutable registry object; operational release records may identify its resolved
digest, but jobs, attempts and the queue never contain or compare that identity. No per-web-
commit worker tag, release manifest or shared-SHA equality is needed to decide whether a
worker can claim a job.

### Running and newly started VMs

A newly created worker VM pulls the image currently addressed by `latest`, starts its
container, and completes the existing model warm-up and readiness before claiming work.
When a worker release is published and a VM is already running, that VM pulls the new image,
starts a replacement container alongside the old one, waits for its warm-up/readiness, then
stops new claims on the old container and switches claim ownership to the replacement. The
old container may finish its already leased work; interrupted work follows the existing lease
recovery path. A failed replacement before the switch leaves the old container serving.

If the bulk group currently has zero VMs, publishing `latest` is sufficient: no VM is
started solely for deployment, and the next autoscaled VM pulls the new image. The selfie
group retains its existing warm VM. Container replacement neither changes the group template
nor creates or deletes a VM. The temporary overlap must fit the approved VM shape; if it
cannot, the release must not claim zero-downtime replacement or silently increase capacity.

The first migration to this host-owned updater may change the existing VM template to install
it. The maintainer accepted a one-time selfie-processing pause at the current one-VM ceiling
while that VM restarts or is replaced; the bulk group remains at zero if idle. This is not
the normal behavior of subsequent worker-image releases, and it does not raise the ceiling.
For this one-time transition, pause claims in both pools, drain the sole selfie worker, and verify
zero live attempts. Merge/publish the worker image to `latest` and deploy the new web protocol and
migration while claims remain paused. Then patch the existing bulk and selfie templates to install
the updater. Because the groups use `OPPORTUNISTIC`, a template update does not restart the live
selfie VM; invoke `rollingRecreate` for its exact existing managed-instance ID at cap one. Keep bulk
at zero and do not add a second VM. Wait for the recreated selfie VM's updater, warm serving worker,
private API health, and fresh observations before unpausing claims. No legacy build field is retained
in the processing protocol solely to bridge this cutover.

Before `processing.0016` removes `ProcessingAttempt.worker_build`, canonical recovery may restore a
previous web image only when its read-only schema probe succeeds. After the column is dropped, an
incompatible previous web image is not a safe rollback, including when the migration command itself
reports failure after committing the drop. Recovery preserves the candidate package and
`.deployment-recovery/candidate.env` (mode 0600), leaves claims paused, and proceeds with a
compatible new-protocol candidate or forward fix; no reverse migration or compatibility column
is added.

Web and worker may advance at different times. Both sides of an overlapping release must
implement the active claim/lease/result and semantic processor contracts. A breaking contract
change requires an explicit compatible transition; the queue does not become a release router.

## Alternatives considered

1. Continue rebuilding and redeploying the worker for every web SHA. This retains the
   current coupling and pays the worker build/pull/startup cost without a worker change.
2. Publish the worker independently but recreate its VM on every worker release. This adds
   cold-start and disk churn despite the VM being healthy.
3. Publish only changed components, maintain one current worker-image pointer, and replace
   the container in place. This is the selected design.

## Acceptance criteria

1. A documentation-only change on `main` causes no image build/publication or remote deploy
   action; the running application and workers remain untouched.
2. A backend-only release deploys the backend while the worker image pointer, running worker
   containers and worker Instance Group templates remain unchanged.
3. A worker-input change publishes a new image without rebuilding heavy unchanged model
   layers. A running VM switches only after the replacement is ready; with bulk at zero,
   the next VM starts from the newly published image without a deployment-time scale-up.
4. A failed pull, start or warm-up before switch leaves the old worker serving and reports
   release failure. Existing jobs, attempts and accepted evidence are not rewritten.
5. Worker replacement and web-only deployment succeed without a shared web/worker SHA or a
   queue-side worker-build check, while active semantic processing contracts remain valid.
6. A post-migration deployment failure cannot restore an incompatible previous web image; it
   retains candidate recovery inputs and leaves recovery paused for a compatible forward fix.

## Out of scope

This specification does not authorize extra VMs, a larger VM shape, removal of model
warm-up, or changing autoscaling bounds. It does not choose a
new queue implementation or weaken private transport, caller authentication, lease fencing,
result validation, or model-generation provenance.
