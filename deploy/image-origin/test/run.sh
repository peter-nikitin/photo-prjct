#!/bin/sh
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$repository"
compose=deploy/image-origin/test/compose.yml
mode=${1:-full}
case "$mode" in full|--cover-only) ;; *) echo 'Unknown acceptance mode' >&2; exit 2 ;; esac
run_compose() {
    if [ "$mode" = --cover-only ]; then
        docker compose -f "$compose" -f deploy/image-origin/test/cover-only.yml "$@"
    else
        docker compose -f "$compose" "$@"
    fi
}
# Reset only this disposable project's containers and generated test TLS/ACME volumes.
run_compose down --remove-orphans --volumes
run_compose run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py prepare
run_compose run --rm --no-deps certbot --version
run_compose run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py write
run_compose up --build --abort-on-container-exit --exit-code-from acceptance
.venv/bin/python deploy/image-origin/test/inspect-runtime.py "$mode"
run_compose run --rm --no-deps --entrypoint python certbot /reviewed-package/test/fixtures/acme-lifecycle.py verify
