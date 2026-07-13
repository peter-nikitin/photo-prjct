#!/bin/sh

set -eu

: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"

valid_hostname() {
    hostname="$1"
    [ "${#hostname}" -le 253 ] || return 1
    printf '%s\n' "$hostname" | grep -Eq \
        '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
}

if ! valid_hostname "$PUBLIC_DOMAIN"; then
    echo "PUBLIC_DOMAIN must be a valid DNS hostname" >&2
    exit 2
fi

if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    if ! valid_hostname "$PUBLIC_DOMAIN_ALIAS"; then
        echo "PUBLIC_DOMAIN_ALIAS must be empty or a valid DNS hostname" >&2
        exit 2
    fi
    if [ "$PUBLIC_DOMAIN_ALIAS" = "$PUBLIC_DOMAIN" ]; then
        echo "PUBLIC_DOMAIN_ALIAS must differ from PUBLIC_DOMAIN" >&2
        exit 2
    fi

    PUBLIC_DOMAIN_ALIAS_SERVER_NAME=" $PUBLIC_DOMAIN_ALIAS"
    HTTPS_ALIAS_SERVER="$(cat <<EOF
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${PUBLIC_DOMAIN_ALIAS};

    ssl_certificate /etc/letsencrypt/live/photo-prjct/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/photo-prjct/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    return 308 https://${PUBLIC_DOMAIN}\$request_uri;
}
EOF
)"
else
    PUBLIC_DOMAIN_ALIAS_SERVER_NAME=""
    HTTPS_ALIAS_SERVER=""
fi

export PUBLIC_DOMAIN PUBLIC_DOMAIN_ALIAS_SERVER_NAME HTTPS_ALIAS_SERVER

render_config() {
    output="$1"
    envsubst '${PUBLIC_DOMAIN} ${PUBLIC_DOMAIN_ALIAS_SERVER_NAME} ${HTTPS_ALIAS_SERVER}' \
        < /opt/nginx/https.conf.template > "$output"
}

if [ "${1:-}" = "--render" ]; then
    [ "$#" -eq 2 ] || {
        echo "Usage: $0 --render OUTPUT" >&2
        exit 2
    }
    render_config "$2"
    exit 0
fi

candidate="$(mktemp /etc/nginx/conf.d/.default.conf.XXXXXX)"
test_config="$(mktemp /tmp/nginx-candidate.conf.XXXXXX)"
trap 'rm -f "$candidate" "$test_config"' EXIT

render_config "$candidate"
sed "s|include /etc/nginx/conf.d/\\\*.conf;|include $candidate;|" \
    /etc/nginx/nginx.conf > "$test_config"

nginx -t -c "$test_config"
mv "$candidate" /etc/nginx/conf.d/default.conf

nginx -g "daemon off;" &
nginx_pid=$!

trap 'kill -TERM "$nginx_pid"; wait "$nginx_pid"' INT TERM
trap 'rm -f "$test_config"' EXIT

while kill -0 "$nginx_pid" 2>/dev/null; do
    sleep 21600 &
    wait "$!" || true
    kill -HUP "$nginx_pid" 2>/dev/null || break
done

wait "$nginx_pid"
