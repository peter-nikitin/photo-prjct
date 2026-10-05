# Zero-downtime Django releases on the canonical VM

- Date: 2026-10-04
- Status: CI-push revision approved by maintainer on 2026-10-04
- Related architecture: [Current architecture — implemented](../../architecture.md#current-architecture--implemented), especially canonical Compose deployment, HTTPS edge, PostgreSQL, and isolated worker pools
- Related ADRs: [0003](../../adr/0003-docker-compose-yandex-cloud.md), [0007](../../adr/0007-nginx-certbot-https-edge.md), [0011](../../adr/0011-use-minimal-shared-https-rollout.md), [0028](../../adr/0028-operate-one-canonical-deployment.md), [0051](../../adr/0051-release-photo-worker-images-independently.md), [0053](../../adr/0053-reconcile-observability-independently-on-main.md)
- ADR impact: Conforms to ADRs 0003, 0007, 0011, 0028 as narrowed by revised 0051, and 0053.
  Two transient web slots are releases within the one canonical Compose deployment, not two
  environments or two durable release authorities.

## Outcome and scope

An ordinary Django release from the existing `Deploy` workflow keeps the public and private HTTPS
edge serving while the candidate image starts, becomes ready, and replaces the active image. No
operator SSH action, worker VM change, new cloud resource, or additional continuously running VM is
part of the release. The same VM, Compose project, Nginx container, PostgreSQL container and data
volume remain authoritative. Resource downsizing is separate work.

"Zero downtime" here means no planned interruption of requests caused by an ordinary Django
release: an already accepted request can finish on its original version, and a new request is routed
to a ready version. It does not mean availability during canonical-VM failure, VM resize or reboot,
PostgreSQL failure or an explicitly scheduled database upgrade. It does not promise that an
arbitrary incompatible schema or long-locking data migration can be deployed without impact.

## Current impediments

The current apply path stops Nginx, replaces the sole `web` service, and reconciles the database
service during ordinary releases. The web entrypoint also runs migrations and other database
setup whenever a web container starts. Nginx points at one fixed `web:8000` upstream, while the
local import worker calls that same fixed name. These behaviors prevent a ready candidate from
coexisting with the active web release, even though both can safely connect to the PostgreSQL
container on the same Compose network when their database contracts overlap.

## Selected design

Keep one persistent PostgreSQL service and one continuously serving Nginx edge. Provide two
alternating, otherwise equivalent Django/Gunicorn slots in the existing Compose project. Exactly
one slot is selected for new public, private worker, health and internal import requests; the
other is either an unselected candidate or stopped. Both slots use the same PostgreSQL database,
Lockbox-projected application credentials, persistent media authority and release configuration
appropriate to their immutable image. There is no database clone, new public endpoint, new
load balancer or new release manifest.

The selected slot is a durable, minimal part of the Nginx upstream configuration. The slot's
running container supplies the actual selected image identity; `latest` is only the registry
pointer used to fetch a candidate. A process restart or later CI retry reads the selected
upstream and running image. It does not require a successful-image marker, release manifest,
branch name or shared SHA check as another release-state authority.

For an ordinary release, the existing serialized `Deploy` workflow publishes the checked web
image as `latest`, connects to the canonical VM, pulls `latest`, prepares the environment,
validates the migration plan, performs only approved release setup, and starts the unselected
slot while Nginx continues routing to the selected one. The pulled image is fixed for this
handoff even if the registry pointer later moves. A local health result alone is insufficient:
the candidate must respond through the actual Nginx routing configuration before
the release is committed. The edge switch is an atomic, validated Nginx configuration reload, not
a container stop or recreation. Existing Nginx workers finish accepted requests on the predecessor;
the predecessor remains alive until those requests drain. Then the old slot can stop. At most two
web containers run during the bounded handoff.

Certificate renewal remains independent. A normal web release reuses the existing valid
certificate and does not stop Nginx or restart PostgreSQL. First certificate issuance, changed
hostnames, a PostgreSQL image/schema upgrade requiring a restart, and VM maintenance remain
separate operational changes with their own availability expectations.

The Nginx selection covers every route that currently relies on Django: ordinary public pages,
bearer-protected media and Order routes, `/health/`, the private photo-worker endpoint, loopback
metrics/diagnostics, and the local import-worker endpoint. The latter must use a stable internal
route to the selected slot instead of addressing a particular web slot by Compose service name.
It must remain inaccessible from the public listener and retain its existing authentication.

Versioned static URLs referenced by pages served just before the switch remain available after
the switch. The release may add new immutable static assets before switching but may not erase the
predecessor's assets while its pages can still request them. Public media access and authorization
stay with the existing Django/Object Storage/CDN contracts; deployment does not copy customer
media between slots.

## Shared database and compatibility contract

Colocation does not itself block a two-slot release: both Django processes connect to the same
PostgreSQL service over the private Compose network. It does mean the release consumes CPU and
RAM beside the database, and a database restart still takes down both slots. Candidate readiness
must be tested with both slots present on the current VM before any later VM downsizing.

The old and new web images must both work against the database state during overlap. Schema
changes follow an expand/use/contract sequence across releases: add compatible structures first,
switch readers and writers later, remove old structures only after no serving image depends on
them. A release with a destructive migration, an unbounded table rewrite, or a migration that
makes the selected image unable to serve must not enter this ordinary handoff. Its author must
split it into compatible releases or declare a separate maintenance procedure. Existing migration
history checks stay in place; no compatibility fallback inside customer requests is added.

Migrations, feature-definition synchronization, group bootstrap and other database-mutating
release setup are executed once in a controlled release phase, not by each web slot's process
entrypoint. Gunicorn startup itself is non-mutating. Release setup must be safe while the selected
web and current Commerce/import workers continue to run, or be split into a separate compatible
release. PostgreSQL and its volume are never replaced to switch Django images.

## Failure and recovery semantics

- Before the edge switch, a failed pull, setup, candidate start or readiness check leaves Nginx
  on the selected slot. The candidate is removed without stopping the selected slot or database.
- A failed Nginx validation or reload leaves the selected upstream intact. If a post-switch
  request check fails, the edge switches back to the still-running predecessor before stopping
  anything, provided the database compatibility contract holds. Otherwise the release fails
  visibly and requires a compatible forward fix; it must not claim rollback succeeded.
- A failed drain does not forcibly stop the predecessor or mark the release fully complete.
  Recovery reads the actual selected upstream and container images, not an assumed script phase
  or the current value of `latest`.
- CI reports success only after candidate health through the edge, route-switch read-back and
  predecessor drain succeed. Failure reports distinguish an intact predecessor,
  switched-but-undrained traffic and a completed release.
- Persistent data, certificates, worker groups and customer media are never deleted or replaced
  by web rollback. Existing bounded recovery and sensitive-log handling remain in force.

## Alternatives considered

1. Stop and replace the sole web service: no extra running memory, but it necessarily interrupts
   requests and is the current behavior.
2. Keep two web slots on the canonical VM behind the existing Nginx: selected. It removes the
   planned web-release interruption without another always-on VM or public routing layer.
3. Add a second web VM and load balancer: can also survive host replacement, but changes network,
   secrets, storage, deployment topology and recurring cost. It is outside this release's goal.

## Acceptance criteria

1. A web-only release does not stop or recreate Nginx or PostgreSQL and does not touch photo-worker
   groups or images. Documentation-only and worker-only classification remain unchanged.
2. With a request held open on the selected slot, a new image can start in the other slot, become
   ready, and receive new requests through Nginx while the held request completes on the old slot.
   Public and private worker routes both follow the same selected slot; the internal import route
   remains private and functional.
3. Failed candidate preparation and failed edge validation preserve service through the old slot.
   A post-switch failure has an observed compatible reverse switch or an explicit
   failed-forward-recovery state, never false success.
4. Both web slots can coexist with the one PostgreSQL container under representative VM memory
   pressure; normal candidate startup does not run a second migration or database bootstrap.
5. Pages opened before a switch still load their versioned static assets after it. Public HTTPS
   redirects, certificate trust, privacy headers, database persistence, Commerce/import workers,
   monitoring and representative customer routes remain correct.
6. The first activation from the current single `web` service retains the existing edge and
   database while introducing the second slot. Subsequent releases alternate slots through the
   same `Deploy` authority without an operator command on the VM. A documentation-only change
   performs no remote activation; a worker-only change leaves the web slot unchanged.

## Explicit non-goals

This design does not resize any VM, move or replicate PostgreSQL, provide VM-level high
availability, add a load balancer or Kubernetes, automate
arbitrary destructive migrations, or promise zero downtime for certificate or database
maintenance.
