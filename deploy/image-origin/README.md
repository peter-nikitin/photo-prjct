# Isolated gallery image origin

This package is independent of the application deployment. Deploying Django with
`gallery-cdn-images=off` needs no image-origin VM, CDN, DNS, certificate or origin credentials.
Provision and validate those resources separately, then perform staff acceptance, then public
activation. The application and storage credential boundaries remain separate.

## Local gate

Run `sh deploy/image-origin/test/run.sh` from a checkout with Docker Compose 2.24.4 or newer and the
project `.venv`. The wrapper resets only this named test project's generated TLS/ACME volumes.
It checks a fresh default-path `apply.sh` directory with the pinned Certbot's offline webroot
write/cleanup/renewal lifecycle, then serves the remaining challenge through UID 101 Nginx.
No ACME server is contacted. After these one-off Certbot steps finish, the image phase runs:

```sh
docker compose -f deploy/image-origin/test/compose.yml up --build --abort-on-container-exit --exit-code-from acceptance
```

Use the wrapper for the complete gate: the image phase requires its ACME fixture preparation.
It also checks Docker's effective CPU/memory quotas and private-data absence in Nginx/imgproxy logs.

The deployed Nginx and imgproxy services have 1.75 vCPU and 3200 MiB of hard limits; fixture
services, including the one-vCPU burst-test client, are not deployed on the origin VM. The
transformer has 1.5 vCPU, 3 GiB, four workers and a 64-request queue. Nginx allows up to 128
concurrent image requests and paces a shared burst of up to 256 requests at 100 requests/second
instead of rejecting an ordinary 100-image page. Requests beyond that bounded
burst can still receive 429. The worker count bounds transform CPU/memory. Processing/download/write
budgets are 3/2/1 seconds and the origin HTTP budget is four seconds. There is no result cache or
media host mount.

The fixture uses one generated 1600×1067 JPEG under 100 distinct immutable preview keys. This
checks the minimum compute envelope and complete cold transforms; it is not a real-corpus visual
or transfer-size acceptance. Task 7 owns those production measurements. Local TLS keys exist only
in the disposable test volume; no private key is committed.

The HTTP redirect fixture emits a verified 302 to a valid JPEG with counted endpoint/target reads.
Both the HTTPS origin and direct imgproxy reject this source without either counter increasing.
This is evidence for the production S3 source allowlist, not an independent test of transport
redirect handling for an allowed source; `IMGPROXY_MAX_REDIRECTS=0` remains configured.

## Apply and rollback interface

Task 6 supplies reviewed files and a protected process environment containing:

- `IMAGE_ORIGIN_RELEASE`: the 40-character repository SHA.
- `PRIVATE_MEDIA_S3_BUCKET`, `GALLERY_IMGPROXY_KEY`, `GALLERY_IMGPROXY_SALT`.
- `IMAGE_ORIGIN_HEADER_SECRET`, `IMAGE_ORIGIN_S3_ACCESS_KEY_ID`,
  `IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY`.
- `IMAGE_ORIGIN_PROBE_PATH`: a signed `gallery-v1` path for an existing accepted preview. It has no
  CDN `md5`/`expires` query because this is the authenticated local origin probe.

Run `sh deploy/image-origin/apply.sh`. It installs under `/opt/photo-prjct-image-origin/releases`,
retains root-private release configuration, validates Compose and Nginx, checks a loopback
candidate on ports 18080/18443/18082, and only then replaces the public containers. A failed public
check restores the previous package. The certificate and ACME directories remain outside releases.
Repeated application of identical files/configuration is supported; an existing SHA cannot be
silently replaced with different files or credentials.

One-command rollback:

```sh
sh /opt/photo-prjct-image-origin/current/apply.sh rollback
```

`check.sh` verifies trusted origin TLS plus content type, success cache headers, complete progressive
JPEG, and dimensions. The signed URI/header travel through curl stdin, never process arguments.
No key, path, URI or query is emitted by the check.

`apply.sh` force-recreates the public Nginx container on both apply and rollback. Compose's
service hash does not include the contents of the mounted `nginx.conf.template`, so an ordinary
`up` can otherwise leave the previous runtime limits active after a successful package deploy.

## Certificate and monitoring ownership

Provisioning/issuance is external to `apply.sh`; no script here changes DNS or requests a live
certificate. The origin uses `img-origin.findme-photo.ru`. Initial Certbot issuance must include
`--deploy-hook 'sh /etc/certificate-permissions.sh'`; the packaged renewal command includes that
hook. The Certbot entrypoint prepares only the ACME webroot and its two challenge directories as
root:101 0750; Certbot writes the public HTTP-01 files as 0644. It runs as root with supplementary
group 101 and no capabilities. `apply.sh` invokes that entrypoint with `--version` before starting
the candidate, without requesting a certificate. Initial issuance and renewal through the same
service also prepare those permissions. Nginx stays UID 101 with a read-only ACME mount; release
configuration and other private installation directories keep their restrictive modes.
The deploy hook grants the Nginx group 101 read/traverse access to issued certificates, while keys remain
unreadable by other users. After successful renewal, the external renewal job runs
`docker compose exec nginx nginx -c /tmp/nginx.conf -s reload`.

Unified Agent pulls the two private aggregate endpoints at port 18081. The bundled njs module
stores eight fixed counters in 32 KiB; exceptions are ignored without changing image responses.
Nginx logs only generated request ID, status, byte count and timings. Error logs and imgproxy logs
are discarded because upstream error messages contain request/source URLs. libvips recreates
technical orientation/DPI tags after metadata stripping; descriptive and copyright metadata are
removed. No Pro options are used, and deprecated/unknown imgproxy settings fail startup.

Read [the alert contract](monitoring/alerts.md) before activation. A documented S3 403 signal is
still a Task 6 activation prerequisite: native imgproxy source errors are not an S3-status signal,
and the current managed Object Storage reference documents no status label. Do not enable staff
delivery until this gap and all other live gates are resolved.

Image pins were read from `docker buildx imagetools inspect` against GHCR, Docker Hub and the
official Quay MinIO mirror on 2026-09-15. Configuration behavior was checked against the
[v4.0.12 source](https://github.com/imgproxy/imgproxy/tree/v4.0.12) and the running images.
