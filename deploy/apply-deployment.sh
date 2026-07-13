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
EXPECTED_PUBLIC_IPV4="${EXPECTED_PUBLIC_IPV4:-}"

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
    echo "Deployment diagnostics (compose up exit status: $compose_up_status)" >&2
    compose ps || true
    compose logs --tail=200 web nginx || true
}

install -d -m 0755 "$DEPLOY_ROOT"
ALLOWED_HOSTS="${ALLOWED_HOSTS:+$ALLOWED_HOSTS,}$PUBLIC_DOMAIN"
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    ALLOWED_HOSTS="$ALLOWED_HOSTS,$PUBLIC_DOMAIN_ALIAS"
fi

if [ -f "$DEPLOY_ROOT/deployed-image" ]; then
    cp "$DEPLOY_ROOT/deployed-image" "$DEPLOY_ROOT/previous-image"
fi
rm -f "$DEPLOY_ROOT/candidate-image"

umask 077
{
    printf 'APP_IMAGE=%s\n' "$APP_IMAGE"
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
    printf 'EXPECTED_PUBLIC_IPV4=%s\n' "$EXPECTED_PUBLIC_IPV4"
    printf 'MEDIA_STORAGE_BACKEND=%s\n' "${MEDIA_STORAGE_BACKEND:-filesystem}"
    printf 'MEDIA_S3_ENDPOINT_URL=%s\n' "${MEDIA_S3_ENDPOINT_URL:-https://storage.yandexcloud.net}"
    printf 'MEDIA_S3_REGION=%s\n' "${MEDIA_S3_REGION:-ru-central1}"
    printf 'MEDIA_S3_PUBLIC_BUCKET=%s\n' "${MEDIA_S3_PUBLIC_BUCKET:-}"
    printf 'MEDIA_S3_ACCESS_KEY_ID=%s\n' "${MEDIA_S3_ACCESS_KEY_ID:-}"
    printf 'MEDIA_S3_SECRET_ACCESS_KEY=%s\n' "${MEDIA_S3_SECRET_ACCESS_KEY:-}"
} > "$DEPLOY_ROOT/.env"

if [ -n "${GHCR_READ_TOKEN:-}" ]; then
    printf '%s\n' "$GHCR_READ_TOKEN" | docker login ghcr.io -u "${GHCR_USERNAME:?Set GHCR_USERNAME}" --password-stdin
fi

if [ "$DEPLOYMENT_TARGET" = "production" ]; then
    compose stop nginx || true
    reconcile_status=0
    sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh" || reconcile_status=$?
    if [ "$reconcile_status" -ne 0 ]; then
        echo "Certificate reconciliation failed; restarting the preceding edge" >&2
        compose start nginx || true
        exit 1
    fi
fi

if ! compose pull; then
    compose_up_status=125
    diagnostics
    exit 1
fi

compose_up_status=0
compose up -d --remove-orphans || compose_up_status=$?
echo "docker compose up exit status: $compose_up_status" >&2

attempt=1
max_attempts=12
while [ "$attempt" -le "$max_attempts" ]; do
    web_container="$(compose ps -q web)"
    running_image=""
    if [ -n "$web_container" ]; then
        running_image="$(docker inspect --format '{{.Config.Image}}' "$web_container" 2>/dev/null || true)"
    fi

    if [ "$running_image" = "$APP_IMAGE" ] && curl --fail-with-body --silent --show-error \
        --resolve "$PUBLIC_DOMAIN:$health_port:127.0.0.1" "$health_url"; then
        candidate_tmp="$(mktemp "$DEPLOY_ROOT/.candidate-image.XXXXXX")"
        trap 'rm -f "$candidate_tmp"' EXIT
        printf '%s\n' "$APP_IMAGE" > "$candidate_tmp"
        mv "$candidate_tmp" "$DEPLOY_ROOT/candidate-image"
        trap - EXIT
        exit 0
    fi

    if [ "$attempt" -lt "$max_attempts" ]; then
        echo "Deployment health check attempt $attempt failed; retrying" >&2
        attempt=$((attempt + 1))
        sleep 5
        continue
    fi

    echo "Deployment health check failed after $max_attempts attempts" >&2
    diagnostics
    exit 1
done
