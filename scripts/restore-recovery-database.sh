#!/bin/sh
set -eu

[ "$#" -eq 2 ] && [ "$1" = --dump ] && [ -f "$2" ] || {
    echo 'Usage: restore-recovery-database.sh --dump ABSOLUTE_PATH' >&2
    exit 2
}
case "$2" in /*) ;; *) echo 'Dump path must be absolute' >&2; exit 2 ;; esac
: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
dump=$2

compose() {
    docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.deployment.yml" "$@"
}

python3 - "$dump" <<'PY'
import hashlib
import sys

value = hashlib.sha256()
with open(sys.argv[1], 'rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
print(f'sha256={value.hexdigest()}')
PY

# The operator starts this only after Cloud Backup files have been extracted while db was stopped.
compose up -d --wait db
compose exec -T db pg_restore --list < "$dump" > /dev/null
compose exec -T db sh -c 'pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U "$POSTGRES_USER" --dbname "$POSTGRES_DB"' < "$dump"
compose run --rm --no-deps --entrypoint python web manage.py migrate --noinput
compose run --rm --no-deps --entrypoint python web manage.py verify_pgvector_face_embeddings
compose run --rm --no-deps --entrypoint python web manage.py verify_gallery_media_projection --all-events --require-clean
printf 'RESTORE_RESULT=verified\n'
