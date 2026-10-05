# Independent worker-image deployment

- **Status:** CI-push revision approved by maintainer on 2026-10-04
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
- **ADR impact:** Revised ADR 0051 supersedes ADRs 0028, 0042 and 0049 where
  they require one web/worker SHA, a coupled image rollout, or SHA-tagged web selection. This
  design conforms to ADR 0050's separation of processing work from worker release identity. The canonical deployment,
  remote-only placement, private worker API and compatible recovery constraints remain.

## Outcome and boundary

A backend release must not rebuild, transfer or restart an unchanged photo worker. A worker
release updates the image used by existing bulk/selfie VMs and by VMs started later, without
recreating a VM merely to change its container. A documentation-only change does not deploy
anything. This design changes image publication and container replacement, not the durable
processing queue, model-generation semantics, pool size limits, VM shapes or cloud folders.
The revised release contract also covers Django and the canonical import/Commerce workers;
it does not move those local workers to the photo-worker VMs.

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

The outer release mechanism is identical for all deployable components: CI classifies effective
image inputs, builds and checks only changed images, advances each changed image's own `latest`,
then contacts only hosts running those components to pull and activate the selected image. The
web, photo-worker and import-worker images have separate pointers. Commerce uses the Django
application image and is activated only when its own effective code or deployment inputs require
it; an unrelated photo-worker change does not restart Commerce. The canonical host runs Django,
import and Commerce under Compose; the remote hosts run bulk/selfie under Compose. No Docker
Swarm control plane, registry polling timer, per-release manifest or shared image SHA is added.
Internal handoff remains appropriate to each service: Nginx switches ready Django slots, remote
photo workers warm before taking new claims, and local background workers drain accepted work.
The [Django specification](2026-10-04-zero-downtime-django-deployment-design.md) governs its
edge handoff and shared-database compatibility separately.

The worker image has a reusable base containing the large pinned models and dependencies.
Ordinary worker-code changes replace only the smaller code layers. Publishing a worker image
advances its registry pointer, `latest`, to the new complete image. Web and other deployable
worker images use their own independent `latest` pointers under the same CI release contract.
The image behind that tag remains an immutable registry object; operational release records may identify its resolved
digest, but jobs, attempts and the queue never contain or compare that identity. No per-web-
commit worker tag, release manifest or shared-SHA equality is needed to decide whether a
worker can claim a job.

### Running and newly started VMs

A newly created worker VM pulls the image currently addressed by `latest`, starts its
container, and completes the existing model warm-up and readiness before claiming work.
When a worker release is published and a VM is already running, the serialized Deploy workflow
reaches that VM by private SSH through the canonical VM and invokes a one-shot activation: pull
`latest`, start a replacement container alongside the old one, wait for its warm-up/readiness, then
stops new claims on the old container and switches claim ownership to the replacement. The
old container may finish its already leased work; interrupted work follows the existing lease
recovery path. A failed replacement before the switch leaves the old container serving.

Remote workers have no public SSH listener. The one-time access preparation reuses the existing
`VM_SSH_KEY` already projected to CI for canonical deployment, without creating another private
key or copying that private key onto the canonical VM. The corresponding public key is installed
on worker VMs for a worker user permitted to run the one-shot updater. The worker security group
permits port 22 only from the canonical VM's security-group ID over the private network. Reusing the key accepts that
its compromise exposes both canonical and worker hosts; this is an explicit maintainer decision.
The access change must be reviewed separately before cloud mutation. CI discovers running members of the
approved groups, invokes activation once per member, and verifies their ready/serving state. The
host does not periodically query GHCR: the existing updater timer is disabled and removed once
the push path is active. A failed connection or activation fails the release visibly rather than
silently waiting for a later host poll. The updater's warm/handoff logic remains reusable as the
one-shot host operation; it is not replaced by a bare `docker restart`.

CI retains the previous registry target only for recovery during this run. If activation fails
after `latest` advances, it restores that pointer when the prior target is known, reports the
failure, and leaves already-running containers to their observed handoff state. A partial
multi-host switch is made compatible and repaired forward; repointing the tag alone does not
claim to have rolled back running containers.

If the bulk group currently has zero VMs, publishing `latest` is sufficient: no VM is
started solely for deployment, and the next autoscaled VM pulls the new image. The selfie
group retains its existing warm VM. Container replacement neither changes the group template
nor creates or deletes a VM. The temporary overlap must fit the approved VM shape; if it
cannot, the release must not claim zero-downtime replacement or silently increase capacity.

The host-owned warm/handoff operation is already installed on the existing fleet. This revision
changes how it is triggered, not VM shape, group capacity, model contents or queue semantics.
One-time private SSH access and removal of the polling timer are operational cutover steps. Because
the existing selfie VM was created without this key, its template is updated and its sole managed
member is recreated once at the existing cap of one. This causes a bounded selfie-processing pause
until cloud-init, image pull and model warm-up complete; bulk remains at zero. Subsequent image
releases do not recreate VMs. The exact access and recreation commands require separate operational
approval immediately before execution. Pause and drain worker claims first; configure access,
recreate the sole selfie member and prove one-shot activation of the current image against the old
web protocol before merging a worker-changing package. Publish the compatible worker image and
new web protocol only after that proof, and unpause claims only after both are accepted live.

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
2. A backend-only release deploys the backend from its own `latest` pointer while the photo-worker
   image pointer, running containers and worker Instance Group templates remain unchanged.
3. A worker-input change publishes a new image without rebuilding heavy unchanged model
   layers. CI triggers each running VM once and it switches only after the replacement is ready;
   the updater timer is absent. With bulk at zero,
   the next VM starts from the newly published image without a deployment-time scale-up.
4. A failed pull, start or warm-up before switch leaves the old worker serving and reports
   release failure. Existing jobs, attempts and accepted evidence are not rewritten.
5. Worker replacement and web-only deployment succeed without a shared web/worker SHA or a
   queue-side worker-build check, while active semantic processing contracts remain valid.
6. A post-migration deployment failure cannot restore an incompatible previous web image; it
   retains candidate recovery inputs and leaves recovery paused for a compatible forward fix.
7. CI's private connection reuses the canonical deployment key and reaches only approved worker
   VMs through the canonical VM. A failed activation is visible in Deploy; no public worker SSH
   ingress, periodic image polling, extra key, or extra VM is introduced. The one-time selfie
   recreation is explicit and verified before removing the timer.
8. Import and Commerce use the same CI publish/pull/activate trigger; unrelated component changes
   do not restart them, and their existing accepted work follows their drain contract.

## Out of scope

This specification does not authorize extra VMs, a larger VM shape, removal of model
warm-up, or changing autoscaling bounds. It does not choose a
new queue implementation or weaken private transport, caller authentication, lease fencing,
result validation, or model-generation provenance.
