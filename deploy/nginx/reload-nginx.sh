#!/bin/sh

set -eu

cp /opt/nginx/https.conf /etc/nginx/conf.d/default.conf

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
