# HTTPS Edge Design

## Status

Approved on 2026-07-13.

## Goal

Make the staging public domain HTTPS-only through a production-like reverse-proxy boundary while
keeping Django and PostgreSQL private to Docker Compose.

## Components and traffic flow

```text
Browser -- HTTP :80 --> Nginx -- redirect --> HTTPS :443 --> Nginx --> Django/Gunicorn --> PostgreSQL
                              |                                  |
                              `-- ACME challenge webroot          `-- X-Forwarded-Proto: https

Certbot -- persistent certificate and account volumes -- Nginx
```

Nginx publishes ports 80 and 443. `web` has no host `ports` mapping; it is addressed as `web:8000`
over the default Compose network. Nginx serves `/.well-known/acme-challenge/` from a shared volume,
redirects all other HTTP traffic to HTTPS, and proxies HTTPS traffic with standard forwarded headers.

## Certificate lifecycle

The GitHub deployment receives `PUBLIC_DOMAIN` from an Environment variable and `LETSENCRYPT_EMAIL`
from an Environment secret. If the certificate does not already exist, the workflow runs Certbot in
standalone mode while port 80 is free, then starts the Compose stack. Certbot subsequently renews
with the Nginx webroot every 12 hours. Nginx periodically reloads its configuration so it reads a
renewed certificate without Docker socket access or an external scheduler.

Certificate/account volumes are not removed by deployment or rollback. Deployment verifies the
domain through local `--resolve` HTTPS health probe after Compose reports healthy.

## Security and operations

Nginx adds baseline response headers, disables version tokens, and sets the proxy scheme header.
Django accepts the proxy scheme through `SECURE_PROXY_SSL_HEADER`, so secure admin/session behavior
works behind TLS. Nginx does not expose PostgreSQL or the Django port to the host.

HTTP-01 requires the domain A record to point to the VM and inbound ports 80/443. The default Yandex
Cloud security group already permits ingress; no cloud resource mutation is part of this change.

## Boundaries

No CDN, WAF, load balancer, rate limit policy, static file offload, multi-VM failover, or production
VM provisioning is included. Nginx is intentionally the scalable edge seam for those later choices.
