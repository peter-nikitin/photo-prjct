#!/bin/sh

set -eu

root_dir="$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

certificate_dir="$tmp_dir/letsencrypt/live/photo-prjct"
mkdir -p "$certificate_dir" "$tmp_dir/rendered"

docker run --rm --entrypoint openssl \
    -v "$certificate_dir:/certificates" \
    certbot/certbot:v2.11.0 \
    req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /certificates/privkey.pem \
    -out /certificates/fullchain.pem \
    -subj /CN=findme-photo.ru >/dev/null 2>&1

validate_variant() {
    name="$1"
    alias="$2"
    rendered="$tmp_dir/rendered/$name.conf"

    docker run --rm \
        -e PUBLIC_DOMAIN=findme-photo.ru \
        -e PUBLIC_DOMAIN_ALIAS="$alias" \
        -v "$root_dir/deploy/nginx:/opt/nginx:ro" \
        -v "$tmp_dir/rendered:/rendered" \
        nginx:1.27-alpine \
        /bin/sh /opt/nginx/reload-nginx.sh --render "/rendered/$name.conf"

    if grep -Eq '^[[:space:]]*server_name[[:space:]]*;' "$rendered"; then
        echo "$name rendered an empty server_name directive" >&2
        exit 1
    fi
    grep -Fq 'return 308 https://findme-photo.ru$request_uri;' "$rendered"
    if [ -n "$alias" ]; then
        grep -Fq "server_name $alias;" "$rendered"
    elif grep -Fq 'server_name www.findme-photo.ru;' "$rendered"; then
        echo "$name retained the optional alias server" >&2
        exit 1
    fi

    docker run --rm \
        -v "$rendered:/etc/nginx/conf.d/default.conf:ro" \
        -v "$tmp_dir/letsencrypt:/etc/letsencrypt:ro" \
        nginx:1.27-alpine nginx -t
}

validate_variant alias www.findme-photo.ru
validate_variant no-alias ""
