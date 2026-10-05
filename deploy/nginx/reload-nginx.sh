#!/bin/sh

set -eu

: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"
mode="${1:-}"
if [ "$mode" = "--render" ]; then
    output="${2:?Set render output}"
    shift 2
elif [ "$mode" = "--apply" ]; then
    shift
fi
DJANGO_SLOT=""
verify_startup=1
if [ "$#" -gt 0 ]; then
    [ "$#" -eq 2 ] && [ "$1" = "--slot" ] || {
        echo "Usage: $0 [--render OUTPUT|--apply] [--slot web|web-next]" >&2
        exit 2
    }
    DJANGO_SLOT="$2"
    verify_startup=0
elif [ -f /opt/nginx/selected-slot ]; then
    DJANGO_SLOT="$(cat /opt/nginx/selected-slot)"
else
    DJANGO_SLOT=web
fi
case "$DJANGO_SLOT" in
    web|web-next) ;;
    *) echo "Invalid Django upstream slot" >&2; exit 2 ;;
esac

valid_hostname() {
    hostname="$1"
    [ "${#hostname}" -le 253 ] || return 1
    case "$hostname" in
        *[!A-Za-z0-9.-]*) return 1 ;;
    esac
    printf '%s\n' "$hostname" | grep -Eq \
        '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$'
}

normalize_hostname() {
    printf '%s\n' "$1" | tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz'
}

if ! valid_hostname "$PUBLIC_DOMAIN"; then
    echo "PUBLIC_DOMAIN must be a valid DNS hostname" >&2
    exit 2
fi

if [ "$verify_startup" = 1 ]; then
    # Ordinary startup must preserve interrupted-switch evidence. Only an
    # explicit --slot operation may reconcile the installed config and marker.
    if ! installed_configuration="$(nginx -T 2>/dev/null)"; then
        echo "Cannot inspect installed Nginx configuration for Django startup" >&2
        exit 2
    fi
    installed_slot="$(printf '%s\n' "$installed_configuration" | awk '
        /upstream django_upstream[[:space:]]*\{/ { in_upstream = 1; next }
        in_upstream && /}/ { in_upstream = 0 }
        in_upstream && /server / { print $2 }
    ')"
    if [ -f /opt/nginx/selected-slot ]; then
        if [ "$installed_slot" != "$DJANGO_SLOT:8000;" ]; then
            echo "Django startup selection mismatch: persisted=$DJANGO_SLOT installed=$installed_slot; explicit switch required" >&2
            exit 2
        fi
    else
        # A successfully inspected fresh stock config or healthy legacy web is
        # the only markerless first-activation state that may choose initial web.
        case "$installed_slot" in
            ''|'web:8000;') ;;
            *) echo "Missing Django selection does not match the initial web upstream" >&2; exit 2 ;;
        esac
    fi
    if ! wget -q -T 3 -O /dev/null --header "Host: $PUBLIC_DOMAIN" "http://$DJANGO_SLOT:8000/health/"; then
        echo "Django startup requires a healthy selected slot: $DJANGO_SLOT" >&2
        exit 2
    fi
fi

if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    if ! valid_hostname "$PUBLIC_DOMAIN_ALIAS"; then
        echo "PUBLIC_DOMAIN_ALIAS must be empty or a valid DNS hostname" >&2
        exit 2
    fi
    if [ "$(normalize_hostname "$PUBLIC_DOMAIN_ALIAS")" = \
        "$(normalize_hostname "$PUBLIC_DOMAIN")" ]; then
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
    access_log /var/log/nginx/access.log selfie_search_safe;

    ssl_certificate /etc/letsencrypt/live/photo-prjct/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/photo-prjct/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    add_header Referrer-Policy "no-referrer" always;

    # Bearer URLs are secrets. Keep any location-level failure out of persisted logs.
    location ~ ^/events/[^/]+/selfie-search/$ {
        error_log /dev/null emerg;
        return 308 https://${PUBLIC_DOMAIN}\$request_uri;
    }

    location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {
        error_log /dev/null emerg;
        return 308 https://${PUBLIC_DOMAIN}\$request_uri;
    }

    location ~ ^/orders/[^/]+/access/[^/]+/[^/]+(?:/|$) {
        error_log /dev/null emerg;
        add_header Cache-Control "private, no-store" always;
        add_header Referrer-Policy "no-referrer" always;
        add_header Vary "Cookie" always;
        add_header X-Content-Type-Options "nosniff" always;
        return 308 https://${PUBLIC_DOMAIN}\$request_uri;
    }

    location / {
        return 308 https://${PUBLIC_DOMAIN}\$request_uri;
    }
}
EOF
)"
else
    PUBLIC_DOMAIN_ALIAS_SERVER_NAME=""
    HTTPS_ALIAS_SERVER=""
fi

PRIVATE_WORKER_SERVER=""
if [ -n "${WORKER_POOL_PRIVATE_API_IPV4:-}" ]; then
    if [ "$PUBLIC_DOMAIN" != "findme-photo.ru" ] || ! printf '%s\n' "$WORKER_POOL_PRIVATE_API_IPV4" | awk -F. '
        NF != 4 { exit 1 }
        { for (i=1; i<=4; i++) if ($i !~ /^[0-9]+$/ || $i > 255 || (length($i)>1 && substr($i,1,1)=="0")) exit 1 }
        !($1==10 || ($1==172 && $2>=16 && $2<=31) || ($1==192 && $2==168)) { exit 1 }
    '; then
        echo "Private worker edge requires canonical domain and explicit RFC1918 IPv4" >&2
        exit 2
    fi
    PRIVATE_WORKER_SERVER="$(cat /opt/nginx/private-worker.conf.template)"
fi

export PUBLIC_DOMAIN PUBLIC_DOMAIN_ALIAS_SERVER_NAME HTTPS_ALIAS_SERVER PRIVATE_WORKER_SERVER DJANGO_SLOT

render_config() {
    output="$1"
    envsubst '${PUBLIC_DOMAIN} ${PUBLIC_DOMAIN_ALIAS_SERVER_NAME} ${HTTPS_ALIAS_SERVER} ${PRIVATE_WORKER_SERVER} ${DJANGO_SLOT}' \
        < /opt/nginx/https.conf.template > "$output"
}

if [ "$mode" = "--render" ]; then
    render_config "$output"
    exit 0
fi

candidate="$(mktemp /etc/nginx/conf.d/.default.conf.XXXXXX)"
test_config="$(mktemp /tmp/nginx-candidate.conf.XXXXXX)"
trap 'rm -f "$candidate" "$test_config"' EXIT

render_config "$candidate"
{
    printf '%s\n' \
        'worker_processes auto;' \
        'error_log stderr;' \
        'events { worker_connections 1024; }' \
        'http {' \
        '    include /etc/nginx/mime.types;'
    printf '    include %s;\n' "$candidate"
    printf '%s\n' '}'
} > "$test_config"

nginx -t -c "$test_config"
if [ "$mode" = "--apply" ]; then
    previous="$(mktemp /etc/nginx/conf.d/.previous.conf.XXXXXX)"
    cp /etc/nginx/conf.d/default.conf "$previous"
    mv "$candidate" /etc/nginx/conf.d/default.conf
    if ! nginx -s reload; then
        mv "$previous" /etc/nginx/conf.d/default.conf
        exit 1
    fi
    if ! nginx -T 2>/dev/null | grep -Fq "server $DJANGO_SLOT:8000;"; then
        mv "$previous" /etc/nginx/conf.d/default.conf
        nginx -s reload
        exit 1
    fi
    rm -f "$previous"
    exit 0
fi
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
