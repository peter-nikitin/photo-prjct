#!/bin/sh

set -eu

: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
: "${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"

valid_hostname() {
    hostname="$1"
    [ "${#hostname}" -le 253 ] || return 1
    case "$hostname" in
        "" | *[!A-Za-z0-9.-]*) return 1 ;;
    esac
    printf '%s\n' "$hostname" | grep -Eq \
        '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
}

valid_hostname "$PUBLIC_DOMAIN" || {
    echo "PUBLIC_DOMAIN must be a DNS hostname" >&2
    exit 2
}
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    valid_hostname "$PUBLIC_DOMAIN_ALIAS" || {
        echo "PUBLIC_DOMAIN_ALIAS must be empty or a DNS hostname" >&2
        exit 2
    }
fi

certificate_volume="${COMPOSE_PROJECT_NAME}_letsencrypt"
certificate_path="/etc/letsencrypt/live/photo-prjct/fullchain.pem"
private_key_path="/etc/letsencrypt/live/photo-prjct/privkey.pem"
docker volume create "$certificate_volume" >/dev/null

if docker run --rm --entrypoint sh \
    -v "$certificate_volume:/etc/letsencrypt:ro" \
    certbot/certbot:v2.11.0 \
    -c 'test -f "$1" && test -f "$2"' sh "$certificate_path" "$private_key_path"; then
    echo "Certificate already exists"
    exit 0
fi

alias_arguments=""
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    alias_arguments="-d $PUBLIC_DOMAIN_ALIAS"
fi

docker run --rm --network host \
    -v "$certificate_volume:/etc/letsencrypt" \
    certbot/certbot:v2.11.0 certonly --standalone \
    --non-interactive --agree-tos --email "$LETSENCRYPT_EMAIL" \
    --cert-name photo-prjct -d "$PUBLIC_DOMAIN" $alias_arguments
