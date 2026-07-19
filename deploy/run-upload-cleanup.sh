#!/bin/sh

set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"

target="$(cat "$DEPLOY_ROOT/deployment-target")"
project="$(cat "$DEPLOY_ROOT/compose-project-name")"

case "$target" in
    staging|production)
        ;;
    *)
        echo "Invalid recorded deployment target" >&2
        exit 2
        ;;
esac

case "$project" in
    *[!A-Za-z0-9_.-]*|'')
        echo "Invalid recorded Compose project name" >&2
        exit 2
        ;;
esac

lock_status=0
flock -n -E 75 "$DEPLOY_ROOT/upload-cleanup.lock" \
    docker compose --project-name "$project" \
    --env-file "$DEPLOY_ROOT/.env" \
    -f "$DEPLOY_ROOT/docker-compose.prod.yml" \
    -f "$DEPLOY_ROOT/docker-compose.https.yml" \
    exec -T web python manage.py cleanup_stale_uploads || lock_status=$?

if [ "$lock_status" -eq 75 ]; then
    echo "Upload cleanup is already running; skipping."
    exit 0
fi
exit "$lock_status"
