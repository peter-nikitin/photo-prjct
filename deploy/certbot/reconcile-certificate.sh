#!/bin/sh

set -eu

: "${COMPOSE_PROJECT_NAME:?Set COMPOSE_PROJECT_NAME}"
: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
: "${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"

valid_hostname() {
    case "$1" in
        "" | *[!A-Za-z0-9.-]*) return 1 ;;
    esac
    return 0
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

normalize_names() {
    tr ',' '\n' |
        sed -n 's/^[[:space:]]*DNS:[[:space:]]*//p' |
        tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz' |
        LC_ALL=C sort -u
}

desired_names="$(
    {
        printf 'DNS:%s\n' "$PUBLIC_DOMAIN"
        if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
            printf 'DNS:%s\n' "$PUBLIC_DOMAIN_ALIAS"
        fi
    } | normalize_names
)"

docker volume create "$certificate_volume" >/dev/null

certificate_exists=false
existing_output=""
if existing_output="$(docker run --rm --entrypoint openssl \
    -v "$certificate_volume:/etc/letsencrypt:ro" \
    certbot/certbot:v2.11.0 \
    x509 -in "$certificate_path" -noout -ext subjectAltName 2>/dev/null)"; then
    certificate_exists=true
    existing_names="$(printf '%s\n' "$existing_output" | normalize_names)"
    if [ "$existing_names" = "$desired_names" ]; then
        echo "Certificate SAN set already matches configured public domains"
        exit 0
    fi
fi

alias_arguments=""
if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    alias_arguments="-d $PUBLIC_DOMAIN_ALIAS"
fi
renewal_argument=""
if [ "$certificate_exists" = true ]; then
    renewal_argument="--force-renewal"
fi

docker run --rm --network host \
    -v "$certificate_volume:/etc/letsencrypt" \
    certbot/certbot:v2.11.0 certonly --standalone --non-interactive --agree-tos \
    --email "$LETSENCRYPT_EMAIL" --cert-name photo-prjct \
    -d "$PUBLIC_DOMAIN" $alias_arguments $renewal_argument
