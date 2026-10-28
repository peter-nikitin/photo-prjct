#!/bin/sh
set -eu

[ "$#" -eq 2 ] && [ "$1" = --dump ] && [ -f "$2" ] || {
    echo 'Usage: rehearse-recovery.sh --dump ABSOLUTE_PATH' >&2
    exit 2
}
case "$2" in /*) ;; *) echo 'Dump path must be absolute' >&2; exit 2 ;; esac
: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
dump=$2
name=recovery_rehearsal_$$
compose() {
    docker compose --env-file "$DEPLOY_ROOT/.env" -f "$DEPLOY_ROOT/docker-compose.deployment.yml" "$@"
}
cleanup() {
    compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' sh "$name" || true
}
trap cleanup EXIT HUP INT TERM
python3 - "$dump" <<'PY'
import hashlib
import sys

value = hashlib.sha256()
with open(sys.argv[1], 'rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
print(f'sha256={value.hexdigest()}')
PY
compose up -d --wait db
compose exec -T db pg_restore --list < "$dump" > /dev/null
compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$1"' sh "$name"
compose exec -T db sh -c 'pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U "$POSTGRES_USER" --dbname "$1"' sh "$name" < "$dump"
compose run --rm --no-deps -e "DB_NAME=$name" --entrypoint python web manage.py migrate --noinput
compose run --rm --no-deps -e "DB_NAME=$name" --entrypoint python web manage.py verify_pgvector_face_embeddings
compose run --rm --no-deps -e "DB_NAME=$name" --entrypoint python web manage.py verify_gallery_media_projection --all-events --require-clean
printf 'REHEARSAL_RESULT=verified\n'
