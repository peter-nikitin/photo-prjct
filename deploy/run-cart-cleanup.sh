#!/bin/sh

set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"

project=photo-prjct

lock_status=0
flock -n -E 75 "$DEPLOY_ROOT/cart-cleanup.lock" \
    docker compose --project-name "$project" \
    --env-file "$DEPLOY_ROOT/.env" \
    -f "$DEPLOY_ROOT/docker-compose.deployment.yml" \
    -f "$DEPLOY_ROOT/docker-compose.https.yml" \
    exec -T web python manage.py cleanup_expired_carts --limit 1000 || lock_status=$?

if [ "$lock_status" -eq 75 ]; then
    echo "Cart cleanup is already running; skipping."
    exit 0
fi
exit "$lock_status"
