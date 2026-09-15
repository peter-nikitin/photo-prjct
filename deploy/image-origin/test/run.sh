#!/bin/sh
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$repository"
compose=deploy/image-origin/test/compose.yml
# Reset only this disposable project's containers and generated test TLS/ACME volumes.
docker compose -f "$compose" down --remove-orphans --volumes
docker compose -f "$compose" run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py prepare
docker compose -f "$compose" run --rm --no-deps certbot --version
docker compose -f "$compose" run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py write
docker compose -f "$compose" up --build --abort-on-container-exit --exit-code-from acceptance
.venv/bin/python deploy/image-origin/test/inspect-runtime.py
docker compose -f "$compose" run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py verify
