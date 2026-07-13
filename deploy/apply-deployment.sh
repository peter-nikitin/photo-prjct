#!/bin/sh

set -u

: "${DEPLOYMENT_TARGET:?Set DEPLOYMENT_TARGET to staging or production}"
: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${APP_IMAGE:?Set APP_IMAGE}"
: "${SECRET_KEY:?Set SECRET_KEY}"
: "${DEBUG:?Set DEBUG}"
: "${ALLOWED_HOSTS:?Set ALLOWED_HOSTS}"
: "${DB_NAME:?Set DB_NAME}"
: "${DB_USER:?Set DB_USER}"
: "${DB_PASSWORD:?Set DB_PASSWORD}"
: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"
requested_image="$APP_IMAGE"

case "$DEPLOYMENT_TARGET" in
    staging)
        overlay_file="$DEPLOY_ROOT/docker-compose.staging.yml"
        health_port=80
        health_url="http://$PUBLIC_DOMAIN/health/"
        ;;
    production)
        : "${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL}"
        overlay_file="$DEPLOY_ROOT/docker-compose.https.yml"
        health_port=443
        health_url="https://$PUBLIC_DOMAIN/health/"
        ;;
    *)
        echo "DEPLOYMENT_TARGET must be staging or production" >&2
        exit 2
        ;;
esac

compose() {
    docker compose --project-name "$COMPOSE_PROJECT_NAME" \
        --env-file "$DEPLOY_ROOT/.env" \
        -f "$DEPLOY_ROOT/docker-compose.prod.yml" \
        -f "$overlay_file" "$@"
}

diagnostics() {
    compose ps || true
    compose logs --tail=100 web nginx || true
}

env_tmp=""
marker_tmp=""
cleanup() {
    rm -f ${env_tmp:+"$env_tmp"} ${marker_tmp:+"$marker_tmp"}
}
trap cleanup EXIT

replace_env_image() {
    image="$1"
    env_tmp="$(mktemp "$DEPLOY_ROOT/.env.restore.XXXXXX")"
    awk -v image="$image" '
        BEGIN { replaced = 0 }
        /^APP_IMAGE=/ && !replaced { print "APP_IMAGE=" image; replaced = 1; next }
        { print }
        END { if (!replaced) print "APP_IMAGE=" image }
    ' "$DEPLOY_ROOT/.env" > "$env_tmp"
    chmod 600 "$env_tmp"
    mv "$env_tmp" "$DEPLOY_ROOT/.env"
    env_tmp=""
}

recover_previous_deployment() {
    if [ -z "$previous_image" ]; then
        echo "No previous APP_IMAGE is available for recovery" >&2
        return 1
    fi
    replace_env_image "$previous_image" || return 1
    unset APP_IMAGE
    compose up -d --remove-orphans || return 1
    echo "Previous application image and edge reconciled" >&2
}

fail_with_recovery() {
    echo "$1" >&2
    if ! recover_previous_deployment; then
        echo "Previous deployment recovery failed" >&2
        diagnostics
    fi
    exit 1
}

install -d -m 0755 "$DEPLOY_ROOT"
previous_image=""
if [ -f "$DEPLOY_ROOT/.env" ]; then
    previous_image="$(sed -n 's/^APP_IMAGE=//p' "$DEPLOY_ROOT/.env" | head -n 1)"
fi

ALLOWED_HOSTS="${ALLOWED_HOSTS:+$ALLOWED_HOSTS,}$PUBLIC_DOMAIN"
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    ALLOWED_HOSTS="$ALLOWED_HOSTS,$PUBLIC_DOMAIN_ALIAS"
fi

umask 077
{
    printf 'APP_IMAGE=%s\n' "$requested_image"
    printf 'SECRET_KEY=%s\n' "$SECRET_KEY"
    printf 'DEBUG=%s\n' "$DEBUG"
    printf 'ALLOWED_HOSTS=%s\n' "$ALLOWED_HOSTS"
    printf 'DB_NAME=%s\n' "$DB_NAME"
    printf 'DB_USER=%s\n' "$DB_USER"
    printf 'DB_PASSWORD=%s\n' "$DB_PASSWORD"
    printf 'DB_HOST=db\n'
    printf 'DB_PORT=5432\n'
    printf 'PUBLIC_DOMAIN=%s\n' "$PUBLIC_DOMAIN"
    printf 'PUBLIC_DOMAIN_ALIAS=%s\n' "$PUBLIC_DOMAIN_ALIAS"
    printf 'MEDIA_STORAGE_BACKEND=%s\n' "${MEDIA_STORAGE_BACKEND:-filesystem}"
    printf 'MEDIA_S3_ENDPOINT_URL=%s\n' "${MEDIA_S3_ENDPOINT_URL:-https://storage.yandexcloud.net}"
    printf 'MEDIA_S3_REGION=%s\n' "${MEDIA_S3_REGION:-ru-central1}"
    printf 'MEDIA_S3_PUBLIC_BUCKET=%s\n' "${MEDIA_S3_PUBLIC_BUCKET:-}"
    printf 'MEDIA_S3_ACCESS_KEY_ID=%s\n' "${MEDIA_S3_ACCESS_KEY_ID:-}"
    printf 'MEDIA_S3_SECRET_ACCESS_KEY=%s\n' "${MEDIA_S3_SECRET_ACCESS_KEY:-}"
} > "$DEPLOY_ROOT/.env"

if [ -n "${GHCR_READ_TOKEN:-}" ]; then
    if ! printf '%s\n' "$GHCR_READ_TOKEN" | \
        docker login ghcr.io -u "${GHCR_USERNAME:?Set GHCR_USERNAME}" --password-stdin; then
        fail_with_recovery "Container registry login failed"
    fi
fi

if [ "$DEPLOYMENT_TARGET" = production ]; then
    compose stop nginx || true
    if ! sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh"; then
        fail_with_recovery "Certificate bootstrap failed"
    fi
fi

if ! compose pull; then
    fail_with_recovery "Deployment image pull failed"
fi

compose_up_status=0
compose up -d --remove-orphans || compose_up_status=$?
echo "docker compose up exit status: $compose_up_status" >&2
if [ "$compose_up_status" -ne 0 ]; then
    fail_with_recovery "Deployment Compose reconciliation failed"
fi

attempt=1
max_attempts=12
while [ "$attempt" -le "$max_attempts" ]; do
    web_container="$(compose ps -q web)"
    running_image=""
    if [ -n "$web_container" ]; then
        running_image="$(
            docker inspect --format '{{.Config.Image}}' "$web_container" 2>/dev/null || true
        )"
    fi
    if [ "$running_image" = "$requested_image" ] && \
        curl --fail-with-body --silent --show-error --max-time 15 \
            --resolve "$PUBLIC_DOMAIN:$health_port:127.0.0.1" "$health_url"; then
        break
    fi
    if [ "$attempt" -eq "$max_attempts" ]; then
        fail_with_recovery "Requested deployment failed local health verification"
    fi
    echo "Deployment health check attempt $attempt failed; retrying" >&2
    attempt=$((attempt + 1))
    sleep 5
done

if [ "$DEPLOYMENT_TARGET" = production ] && \
    ! sh "$DEPLOY_ROOT/deploy/verify-public-edge.sh"; then
    fail_with_recovery "Requested deployment failed public HTTPS smoke verification"
fi

marker_tmp="$(mktemp "$DEPLOY_ROOT/.deployed-image.XXXXXX")"
printf '%s\n' "$requested_image" > "$marker_tmp"
mv "$marker_tmp" "$DEPLOY_ROOT/deployed-image"
marker_tmp=""
