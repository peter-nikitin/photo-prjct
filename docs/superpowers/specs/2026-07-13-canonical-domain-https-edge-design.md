# Canonical Domain HTTPS Edge Design

## Status

Approved by the project maintainer on 2026-07-13.

## Goal

Serve the current single active environment at `https://findme-photo.ru/` through a trusted,
automatically renewed HTTPS edge while preserving the current VM's staging lifecycle and production
readiness restrictions.

## Current state

- Public DNS delegates `findme-photo.ru` to RU-CENTER DNS Master Anycast and resolves both the apex
  and `www.findme-photo.ru` to `111.88.151.64`.
- The current preemptible Yandex Cloud VM is the only active environment and remains staging.
- The staging deployment runs PostgreSQL and Django behind an HTTP-only Nginx overlay on port 80.
- The repository contains a separate Nginx and Certbot HTTPS overlay used only by the future
  production deployment path.
- `PUBLIC_DOMAIN` is appended to Django's `ALLOWED_HOSTS`, and Django already trusts
  `X-Forwarded-Proto: https` from the edge.
- The latest observed `main` staging deployment completed successfully, but the stored Yandex Cloud
  inventory still lacks stable resource IDs because read-only CLI discovery returns no resource
  payload before timing out.

The DNS failure that motivated the HTTP-only staging decision no longer exists. The topology in ADR
0009 therefore no longer matches the intended public behavior.

## Decision

Replace the environment-specific HTTPS overlay with one environment-neutral HTTPS edge overlay.
Use it for the current staging Compose project and later for both staging and production. Each
environment retains separate GitHub Environment configuration, Compose project names, certificate
volumes, application data, and deployment eligibility.

Record this durable change in ADR 0010. ADR 0010 supersedes ADR 0009 while retaining ADR 0007's
accepted Nginx and Certbot edge pattern. The current VM remains preemptible staging and is not made
production-ready by receiving the canonical domain.

## Alternatives considered

### Reuse the production-named overlay from staging

This requires fewer file changes but makes the staging deployment depend on a semantically
production-only file and leaves the accepted architecture and repository tests misleading.

### Add a separate staging HTTPS overlay

This keeps environment names explicit but duplicates Nginx, Certbot, certificate, and renewal
behavior. The two public edges could drift even though they have the same requirements.

### Use one shared HTTPS overlay

This is the selected option. It keeps the public edge independently testable and makes environment
isolation a property of configuration and Compose project identity instead of duplicated topology.

## Components and traffic flow

```text
Browser -- HTTP :80 --> Nginx -- canonical HTTPS redirect
                              `-- ACME HTTP-01 webroot

Browser -- HTTPS :443 --> Nginx --> Django/Gunicorn --> PostgreSQL

Certbot --> persistent certificate/account volume --> Nginx
```

Only Nginx publishes host ports 80 and 443. Django remains available only as `web:8000` on the
Compose network, and PostgreSQL remains private.

The shared HTTPS overlay is combined with `docker-compose.prod.yml`. The current environment uses
Compose project `photo-prjct-staging`; future production uses `photo-prjct-production`. Docker
therefore isolates their certificate and database volumes even though both deployments use the same
versioned overlay.

## Domain routing

`PUBLIC_DOMAIN` is the canonical hostname. `PUBLIC_DOMAIN_ALIAS` is an optional additional hostname
that receives a certificate but never reaches Django directly. For the current environment:

```text
PUBLIC_DOMAIN=findme-photo.ru
PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru
EXPECTED_PUBLIC_IPV4=111.88.151.64
```

`EXPECTED_PUBLIC_IPV4` is non-secret deployment configuration and is the DNS release-gate source of
truth for the current single-VM topology. The apex and alias must each return exactly that A address.
They must not publish an AAAA record while the VM has no configured public IPv6 endpoint. Supporting
multiple addresses, IPv6, a load balancer, or a CDN requires an explicit extension of this contract.

Routing behavior is:

| Request | Result |
| --- | --- |
| `http://findme-photo.ru/*` | HTTP 308 redirect to the same path on `https://findme-photo.ru/` |
| `http://www.findme-photo.ru/*` | HTTP 308 redirect to the same path on the canonical HTTPS host |
| `https://www.findme-photo.ru/*` | HTTP 308 redirect to the same path on the canonical HTTPS host |
| `https://findme-photo.ru/*` | Proxy to Django |
| `http://<configured-host>/.well-known/acme-challenge/*` | Serve the Certbot webroot without redirect |
| Unknown HTTP or HTTPS host | Reject without proxying to Django |

Nginx configuration is rendered from environment values and uses explicit `server_name` blocks.
Redirects use the configured canonical domain rather than the request Host header. The deployment
adds the canonical domain and configured alias to `ALLOWED_HOSTS`; the alias is included for
defense in depth even though Nginx redirects it before proxying.

When separate environments exist, `findme-photo.ru` and `www.findme-photo.ru` remain production
names and staging moves to `staging.findme-photo.ru`. Staging can omit `PUBLIC_DOMAIN_ALIAS`.

## Certificate lifecycle

The initial certificate contains the exact configured DNS-name set and uses a stable certificate name
inside the environment-specific `letsencrypt` volume. Certificate and ACME account state survive
container replacement, deployment rollback, and image promotion. Deployment must never remove that
volume.

Before every deployment, certificate bootstrap compares the existing certificate's DNS SAN set with
the non-empty values from `PUBLIC_DOMAIN` and `PUBLIC_DOMAIN_ALIAS`. An exact match permits reuse. A
missing certificate or a mismatched SAN set stops the current edge container to free port 80, then
triggers one standalone Certbot issuance/reconciliation request for the exact desired set under the
stable certificate name. This supports the later move from the root domain to
`staging.findme-photo.ru` without silently serving a stale certificate. Reconciliation is not
retried automatically after failure, limiting accidental Let's Encrypt rate-limit consumption. If
it fails, the preceding edge configuration and certificate are restarted together.

If no certificate exists, deployment stops only the current edge container so port 80 becomes
available. PostgreSQL and Django continue running. Certbot performs one standalone HTTP-01 issuance
for the canonical domain and alias. The HTTPS stack starts only after the certificate is present.

After activation, Nginx serves the HTTP-01 webroot. Certbot checks renewal every 12 hours, and Nginx
periodically reloads so renewed files become active without Docker socket access. The first rollout
must also prove `certbot renew --dry-run` succeeds.

If initial issuance fails, deployment restarts the preceding HTTP edge, records diagnostics, leaves
the certificate volume intact, and exits unsuccessfully. Operators investigate DNS, port reachability,
and rate-limit state before retrying.

## Rollout strategy

Activation is split into two independently deployable changes.

### Preparation release

- Add ADR 0010, the shared HTTPS overlay, environment-rendered Nginx configuration, deployment
  support, tests, and operator documentation.
- Keep the staging workflow on the existing HTTP overlay.
- Render and validate Compose and Nginx locally with test certificate material.
- Configure the staging GitHub Environment with the canonical domain, alias, expected public IPv4,
  and certificate email before activation. Secret values remain outside the repository.

### Activation release

- Switch the staging workflow from the HTTP overlay to the shared HTTPS overlay.
- Bootstrap the certificate while only the old edge is stopped.
- Start Nginx and Certbot, verify the exact application image and local HTTPS routing, and expose a
  release candidate marker.
- Verify exact public A/AAAA answers, redirects, HTTPS health, canonical alias behavior, and
  certificate names from a GitHub-hosted runner.
- Finalize the release marker only after every public check succeeds.

After a stable HTTPS rollout and successful renewal dry run, remove the obsolete HTTP overlay and
staging Nginx configuration in a separate cleanup change. Keeping cleanup separate preserves an
immediate versioned fallback during activation.

## Deployment success contract

The deployment script writes a `candidate-image` marker after the requested immutable image and
local HTTPS edge pass health checks. It does not update `deployed-image` at that point.

The GitHub-hosted runner then verifies:

- the public canonical name returns exactly `EXPECTED_PUBLIC_IPV4` as its A answer and returns no
  AAAA answer;
- canonical-name HTTP redirects to canonical HTTPS;
- when `PUBLIC_DOMAIN_ALIAS` is non-empty, the alias returns the same exact A/no-AAAA answers, its
  HTTP and HTTPS requests redirect to the canonical host without changing the path or query, and
  its name is present in the certificate SAN set;
- `https://$PUBLIC_DOMAIN/health/` returns HTTP 200;
- the served certificate is trusted, unexpired, and its DNS SAN set covers every non-empty
  configured name.

A versioned finalization command verifies that `candidate-image` still equals the expected image and
atomically promotes it to `deployed-image`. Production promotion continues to trust only
`deployed-image`, so a locally healthy but publicly unavailable staging release is never eligible.

## Failure handling and rollback

Every failure reports Compose status and bounded logs for `web`, `nginx`, and `certbot`. Certificate
checks may report file presence and public metadata but must never print private key material,
credentials, or GitHub secrets.

During first activation, certificate bootstrap recovery is an internal, marker-independent branch
of the deployment script because bootstrap occurs before local HTTPS health and before
`candidate-image` exists. It restarts the preceding HTTP edge and leaves the current application
containers and `deployed-image` untouched.

If local HTTPS health succeeds but later public verification fails, the workflow invokes a versioned
rollback command in a failure-only step. That command verifies `candidate-image`, restores the
previous successful application image and the HTTP overlay, checks restored HTTP health, and leaves
`deployed-image` pointing to the previous successful image.

After the obsolete HTTP overlay is removed, the same rollback interface restores the previous
successful application image while retaining the already validated shared HTTPS edge and certificate
volume, then verifies local and public HTTPS health. Cleanup cannot occur until certificate issuance,
public routing, and renewal dry run have succeeded, so post-cleanup rollback never depends on
reissuing a certificate. Both rollback modes preserve PostgreSQL, application data, certificate
volumes, the previous successful marker, and failure diagnostics. A manual git revert remains the
follow-up that removes rejected deployment configuration, but public recovery does not wait for it.

The preparation release is independently reversible because it does not activate a new runtime
path. No database migration or Yandex Cloud resource mutation is part of either release.

## Verification strategy

Repository-contract tests are written before behavior changes and cover:

- one shared HTTPS overlay used by both deployment paths;
- private Django and PostgreSQL services;
- explicit canonical and alias routing;
- HTTP 308 canonical redirects and rejection of unknown Host values;
- certificate issuance for every configured name;
- exact certificate SAN reconciliation when configured names change;
- conditional alias DNS, redirect, and certificate checks when the alias is configured;
- persistent certificate volumes and renewal behavior;
- candidate and finalized image marker semantics;
- activation rollback to the previous image and HTTP edge, followed after cleanup by rollback to the
  previous image on the validated HTTPS edge;
- staging and production environment isolation;
- absence of certificate secrets or key material in tracked files.

Static and integration checks include Compose rendering, Nginx syntax validation with temporary test
certificate material, POSIX shell syntax, targeted repository tests, and `git diff --check`. The
activation release adds live HTTP redirect, HTTPS health, certificate-name, and renewal dry-run
checks.

## Documentation impact

- Add ADR 0010 and mark ADR 0009 superseded in the ADR index and record.
- Update `docs/architecture.md` to describe publicly routed HTTPS on the current single active
  staging environment without changing its lifecycle classification.
- Replace README statements that DNS is unroutable or staging is necessarily HTTP-only.
- Update the Yandex Cloud inventory with verified stable facts only. Record that CLI resource
  discovery remains incomplete if it still returns no payload; do not guess resource IDs.
- Create a decision-complete implementation plan with separate preparation, activation, and cleanup
  phases and proportional automated-test context for each phase.

## Boundaries

This design does not:

- create, resize, start, reserve, or otherwise mutate Yandex Cloud resources;
- provision the later production VM or make the current VM production-ready;
- change database schema, domain models, or product behavior;
- introduce a CDN, WAF, load balancer, managed certificate service, or new public IP;
- create `staging.findme-photo.ru` before a separate production environment exists;
- broaden public access beyond ports 80 and 443.
