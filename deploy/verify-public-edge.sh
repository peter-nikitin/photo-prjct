#!/bin/sh

set -eu

: "${PUBLIC_DOMAIN:?Set PUBLIC_DOMAIN}"
PUBLIC_DOMAIN_ALIAS="${PUBLIC_DOMAIN_ALIAS:-}"
probe_path='/__edge_verify__?source=deploy'
expected_location="https://$PUBLIC_DOMAIN$probe_path"

verify_redirect() {
    host="$1"
    scheme="$2"
    response="$(curl --silent --show-error --max-time 15 --output /dev/null \
        --write-out '%{http_code}\n%{redirect_url}\n' \
        "$scheme://$host$probe_path")"
    status="$(printf '%s\n' "$response" | sed -n '1p')"
    location="$(printf '%s\n' "$response" | sed -n '2p')"
    if [ "$status" != 308 ]; then
        echo "$host $scheme redirect returned $status instead of 308" >&2
        exit 1
    fi
    if [ "$location" != "$expected_location" ]; then
        echo "$host Location does not preserve the canonical path and query" >&2
        exit 1
    fi
}

verify_redirect "$PUBLIC_DOMAIN" http

health_status="$(curl --silent --show-error --max-time 15 --output /dev/null \
    --write-out '%{http_code}\n' "https://$PUBLIC_DOMAIN/health/")"
if [ "$health_status" != 200 ]; then
    echo "Canonical HTTPS health returned $health_status instead of 200" >&2
    exit 1
fi

if [ -n "$PUBLIC_DOMAIN_ALIAS" ]; then
    verify_redirect "$PUBLIC_DOMAIN_ALIAS" http
    verify_redirect "$PUBLIC_DOMAIN_ALIAS" https
fi
