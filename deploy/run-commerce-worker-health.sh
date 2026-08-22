#!/bin/sh
set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"

project=photo-prjct
perl -e 'alarm shift; exec @ARGV' 30 docker compose --project-name "$project" \
    --env-file "$DEPLOY_ROOT/.env" \
    -f "$DEPLOY_ROOT/docker-compose.deployment.yml" \
    -f "$DEPLOY_ROOT/docker-compose.https.yml" \
    exec -T web sh -c \
    'exec python manage.py commerce_worker_health --max-ready-age-seconds "$COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS"'
