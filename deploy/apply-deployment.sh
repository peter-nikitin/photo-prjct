#!/bin/sh
set -eu

cd "${DEPLOY_ROOT:-/opt/photo-prjct}"
compose() { docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml "$@"; }
photo_worker_roles='photo-worker photo-worker-face photo-worker-appearance photo-worker-bib'
worker_containers() {
    docker ps --filter label=com.docker.compose.project=photo-prjct --filter label=com.docker.compose.oneoff=False "$@"
}
active_photo_worker=
if [ "$DEPLOY_WEB" = true ] || [ "$DEPLOY_WORKER" = true ]; then
    running_services="$(worker_containers --format '{{.Label "com.docker.compose.service"}}')"
    for role in $photo_worker_roles; do
        if printf '%s\n' "$running_services" | grep -Fxq "$role"; then
            active_photo_worker=true
            break
        fi
    done
fi

if [ "$DEPLOY_WEB" = true ]; then
    printf '%s\n' 'DEPLOY_STAGE=pull-images'
    compose pull db web gallery-search-worker archive-worker nginx certbot-renew
    printf '%s\n' 'DEPLOY_STAGE=commerce-check'
    compose run --rm --no-deps -T --entrypoint python web manage.py check --tag commerce_runtime
    printf '%s\n' 'DEPLOY_STAGE=database'
    compose up -d --wait db
    compose stop gallery-search-worker
    compose stop archive-worker
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
    compose up -d --no-deps archive-worker
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
if [ -n "$active_photo_worker" ]; then
    printf '%s\n' 'DEPLOY_STAGE=photo-worker'
    if [ "$DEPLOY_WORKER" = true ]; then
        compose --profile photo-worker pull photo-worker
    fi
    defined_services="$(compose --profile photo-worker config --services)"
    set --
    retired_roles=
    for role in $photo_worker_roles; do
        if printf '%s\n' "$defined_services" | grep -Fxq "$role"; then
            set -- "$@" "$role"
        else
            retired_roles="$retired_roles $role"
        fi
    done
    [ "$#" -gt 0 ]
    compose --profile photo-worker up -d --no-deps --pull never --timeout 180 "$@"
    python3 deploy/verify-photo-worker.py
    for role in $retired_roles; do
        ids="$(worker_containers -a -q --filter "label=com.docker.compose.service=$role")"
        if [ -n "$ids" ]; then
            docker stop --time 180 $ids
            docker rm $ids
        fi
    done
fi
[ "$DEPLOY_IMPORT" = false ] || update_worker import import-worker
[ "$DEPLOY_COMMERCE" = false ] || update_worker commerce commerce-worker
printf '%s\n' 'DEPLOY_STAGE=image-prune'
if docker image prune -a -f >/dev/null; then
    printf '%s\n' 'DEPLOY_IMAGE_PRUNE_RESULT=success'
else
    printf '%s\n' 'DEPLOY_IMAGE_PRUNE_RESULT=failure'
fi
