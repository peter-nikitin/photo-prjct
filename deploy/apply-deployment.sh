#!/bin/sh
set -eu

cd "${DEPLOY_ROOT:-/opt/photo-prjct}"
compose() { docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml "$@"; }
if [ "$DEPLOY_WEB" = true ]; then
    printf '%s\n' 'DEPLOY_STAGE=pull-images'
    compose pull db web gallery-search-worker nginx certbot-renew
    printf '%s\n' 'DEPLOY_STAGE=commerce-check'
    compose run --rm --no-deps -T --entrypoint python web manage.py check --tag commerce_runtime
    printf '%s\n' 'DEPLOY_STAGE=database'
    compose up -d --wait db
    compose stop --timeout 0 gallery-search-worker
    compose stop web
    printf '%s\n' 'DEPLOY_STAGE=migrate'
    compose run --rm --no-deps -T --entrypoint python web manage.py migrate --noinput
    printf '%s\n' 'DEPLOY_STAGE=sync-feature-flags'
    compose run --rm --no-deps -T --entrypoint python web manage.py sync_feature_flags
    printf '%s\n' 'DEPLOY_STAGE=static'
    compose run --rm --no-deps -T --entrypoint python web manage.py collectstatic --noinput
    printf '%s\n' 'DEPLOY_STAGE=web'
    compose up -d --wait web
    compose up -d --no-deps gallery-search-worker
    compose up -d --wait --force-recreate --no-deps nginx
    compose up -d certbot-renew
    if ! compose up -d --no-deps queue-telemetry; then
        printf '%s\n' 'QUEUE_TELEMETRY_START=failed'
    fi
fi
update_worker() {
    profile=$1
    service=$2
    if [ -n "$(compose --profile "$profile" ps --status running -q "$service")" ]; then
        compose --profile "$profile" pull "$service"
        compose --profile "$profile" up -d --no-deps "$service"
    fi
}
[ "$DEPLOY_WORKER" = false ] || update_worker photo-worker photo-worker
[ "$DEPLOY_IMPORT" = false ] || update_worker import import-worker
[ "$DEPLOY_COMMERCE" = false ] || update_worker commerce commerce-worker
