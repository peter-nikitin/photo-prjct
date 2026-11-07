#!/bin/sh
set -eu

# Called only after an operator explicitly starts the deployment following restore.
cd "${DEPLOY_ROOT:-/opt/photo-prjct}"
compose() { docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml "$@"; }
printf '%s\n' 'DEPLOY_STAGE=stop-workers'
compose --profile photo-worker --profile import --profile commerce stop photo-worker import-worker commerce-worker
printf '%s\n' 'DEPLOY_STAGE=pull-images'
compose pull db web nginx certbot-renew
compose --profile photo-worker pull photo-worker
compose --profile import pull import-worker
printf '%s\n' 'DEPLOY_STAGE=commerce-check'
compose run --rm --no-deps -T --entrypoint python web manage.py check --tag commerce_runtime
printf '%s\n' 'DEPLOY_STAGE=database'
compose up -d --wait db
printf '%s\n' 'DEPLOY_STAGE=migrate'
compose run --rm --no-deps -T --entrypoint python web manage.py migrate --noinput
printf '%s\n' 'DEPLOY_STAGE=static'
compose run --rm --no-deps -T --entrypoint python web manage.py collectstatic --noinput
printf '%s\n' 'DEPLOY_STAGE=web'
compose up -d --wait web
compose up -d --wait --force-recreate --no-deps nginx
compose up -d certbot-renew
