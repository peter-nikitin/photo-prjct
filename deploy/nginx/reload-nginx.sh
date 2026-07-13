#!/bin/sh

set -eu

ENABLE_HTTPS="${ENABLE_HTTPS:-true}"
case "$ENABLE_HTTPS" in
    true)
        cp /opt/nginx/https.conf /etc/nginx/conf.d/default.conf
        ;;
    false)
        cp /opt/nginx/http.conf /etc/nginx/conf.d/default.conf
        ;;
    *)
        echo "ENABLE_HTTPS must be true or false" >&2
        exit 1
        ;;
esac

nginx -t
nginx -g "daemon off;" &
nginx_pid=$!

trap 'kill -TERM "$nginx_pid"; wait "$nginx_pid"' INT TERM

while kill -0 "$nginx_pid" 2>/dev/null; do
    sleep 21600 &
    wait "$!" || true
    kill -HUP "$nginx_pid" 2>/dev/null || break
done

wait "$nginx_pid"
