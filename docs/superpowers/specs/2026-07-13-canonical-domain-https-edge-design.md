# Canonical Domain HTTPS Edge Design

## Status

Approved by the project maintainer on 2026-07-13. Simplified on the same date for the current
one-VM, one-domain rollout.

## Goal

Serve the current single active environment at `https://findme-photo.ru/` through a trusted,
automatically renewed HTTPS edge without turning the current preemptible staging VM into production.

## Current state

- Public DNS delegates `findme-photo.ru` to RU-CENTER and points the apex and
  `www.findme-photo.ru` to `111.88.151.64`; no AAAA answer was observed on 2026-07-13.
- The current preemptible Yandex Cloud VM is the only active environment and remains staging.
- Staging currently uses an HTTP-only Nginx overlay on port 80.
- Production is not provisioned.
- Stable Yandex Cloud resource IDs are still unknown because read-only CLI discovery has not
  returned a resource payload.

The repository can prepare the HTTPS edge without activating it on the live staging VM. Activation
and removal of the temporary HTTP edge remain separate releases.

## Decision

Use one environment-neutral Compose overlay for Nginx and Certbot in every public environment.
Keep the operational flow deliberately small for the current topology:

1. issue a certificate only when the environment-specific certificate volume has none;
2. apply the requested immutable application image and HTTPS edge;
3. verify the exact canonical redirects and trusted HTTPS health with `curl`;
4. atomically write `deployed-image` only after every check succeeds;
5. if apply or verification fails, restore the previous `APP_IMAGE` and reconcile the preceding
   edge in the same deploy process.

There is no independent release-state machine or workflow-level recovery job. DNS is an operator
preflight for first activation, not a deployment-script gate. This is appropriate while one VM owns
one public endpoint and deployments are infrequent.

ADR 0011 governs the shared edge, minimal rollout, and environment assignment. The current VM
remains staging and retains its preemptible availability characteristics after HTTPS activation.

## Components and traffic flow

```text
Browser -- HTTP :80 --> Nginx -- canonical HTTPS redirect
                              `-- ACME HTTP-01 webroot

Browser -- HTTPS :443 --> Nginx --> Django/Gunicorn --> PostgreSQL

Certbot --> environment-specific persistent volumes --> Nginx
```

Only Nginx publishes host ports 80 and 443. Django remains private at `web:8000`, and PostgreSQL
remains private. The shared overlay combines with `docker-compose.prod.yml`. Compose project names
isolate database and certificate volumes between environments.

## Domain routing

`PUBLIC_DOMAIN` is the canonical hostname. `PUBLIC_DOMAIN_ALIAS` is optional and redirects to the
canonical host. The intended activation values are:

```text
PUBLIC_DOMAIN=findme-photo.ru
PUBLIC_DOMAIN_ALIAS=www.findme-photo.ru
```

| Request | Result |
| --- | --- |
| `http://findme-photo.ru/*` | 308 to the same path and query on canonical HTTPS |
| `http://www.findme-photo.ru/*` | 308 to canonical HTTPS |
| `https://www.findme-photo.ru/*` | 308 to canonical HTTPS |
| `https://findme-photo.ru/*` | Proxy to Django |
| `http://<configured-host>/.well-known/acme-challenge/*` | Serve the Certbot webroot |
| Unknown HTTP or HTTPS host | Reject without proxying to Django |

Nginx configuration is rendered atomically from environment values. Redirects use the configured
canonical hostname, never the request Host header. A private loopback listener exposes only
`/health/` for the container health check. The public default servers never reach Django.

When separate environments exist, the apex and `www` remain production names and staging moves to
`staging.findme-photo.ru` with its own Compose project and certificate volume.

## Certificate lifecycle

The pinned Certbot image checks only whether the stable `photo-prjct` certificate exists. When it is
missing, deployment stops Nginx to release port 80 and performs one non-interactive standalone
HTTP-01 issuance for the configured canonical hostname and optional alias. It does not retry in a
loop. Existing certificate/account state is preserved in the environment-specific Docker volume.

After activation, Certbot checks renewal every 12 hours through the webroot, and Nginx reloads
periodically so renewed files become active without Docker socket access. Activation is complete
only after a `certbot renew --dry-run` succeeds.

This minimal bootstrap intentionally does not modify an existing certificate when domain settings
change. Adding, removing, or replacing the alias requires an operator-controlled maintenance action:
remove the named certificate from that environment's certificate volume and rerun deployment to
issue it for the new configured names. This avoids automatic reissuance and accidental rate-limit
consumption in an otherwise one-domain setup.

## Deployment and recovery contract

The deploy script remembers the prior `APP_IMAGE` from the protected remote `.env`, writes the
requested environment, and applies the selected Compose overlay. It then verifies:

- the running web container uses the requested immutable image;
- the VM-local edge health endpoint succeeds;
- on the HTTPS path, canonical HTTP and optional alias HTTP/HTTPS requests return exact 308
  redirects preserving path and query;
- canonical `https://$PUBLIC_DOMAIN/health/` returns HTTP 200 with normal TLS trust validation.

Only then does it atomically replace `deployed-image` with the requested image. Production
promotion continues to accept only the image recorded there.

If registry login, certificate bootstrap, image pull, Compose reconciliation, local health, or the
public HTTPS smoke test fails, the same process restores the prior image in `.env`, reconciles the
preceding overlay, emits bounded service diagnostics if recovery also fails, and exits non-zero.
PostgreSQL, application data, certificate volumes, and the existing `deployed-image` marker are not
removed or replaced by a failed attempt.

## Rollout strategy

### Preparation release

- Add ADR 0011, the shared overlay, rendered Nginx configuration, minimal certificate bootstrap,
  smoke checks, in-process recovery, tests, and operator documentation.
- Keep the staging workflow on `docker-compose.staging.yml` and port 80.
- Validate Compose, shell, Nginx, and focused deployment behavior without requesting a live
  certificate or changing Yandex Cloud resources.

### Activation release

- Configure the staging GitHub Environment with `PUBLIC_DOMAIN`, optional
  `PUBLIC_DOMAIN_ALIAS`, and secret `LETSENCRYPT_EMAIL`.
- Manually confirm that configured names resolve to the intended VM and ports 80/443 are reachable.
- Switch staging to `docker-compose.https.yml` and observe the deployment to a terminal result.
- Confirm public redirects, trusted HTTPS health, browser trust, release marker, and renewal dry run.

### Cleanup release

After successful activation and renewal validation, remove `docker-compose.staging.yml` and
`deploy/nginx/staging.conf`. Cleanup does not alter certificate or database volumes.

## Verification strategy

Repository and behavioral tests cover:

- a shared HTTPS overlay with only Nginx publishing ports;
- explicit canonical, optional alias, ACME, and unknown-host routing;
- alias and no-alias Nginx rendering;
- one-shot issuance only when the certificate is absent;
- exact redirect and trusted health smoke behavior;
- restoration of the prior `APP_IMAGE` on failed apply;
- atomic `deployed-image` update only after all checks succeed;
- persistent certificate state and renewal behavior;
- staging remaining HTTP-only during preparation.

Static checks include Compose rendering, Nginx syntax with temporary test certificates, POSIX shell
syntax, targeted pytest suites, and `git diff --check`. Live activation adds external `curl` checks,
browser certificate inspection, and a renewal dry run.

## Boundaries

This design does not:

- activate HTTPS during the preparation release;
- mutate Yandex Cloud resources or make the current VM production-ready;
- provision production or create `staging.findme-photo.ru`;
- automate DNS inspection or certificate-name changes;
- add a load balancer, CDN, WAF, managed certificate, IPv6 endpoint, or new public IP;
- change database schema, domain models, or product behavior.
