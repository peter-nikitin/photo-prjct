#!/bin/sh
set -eu
case "${IMAGE_ORIGIN_HEADER_SECRET:-}" in
    ''|*[!A-Za-z0-9_-]*) echo 'Invalid origin header configuration' >&2; exit 2 ;;
esac
[ "${#IMAGE_ORIGIN_HEADER_SECRET}" -ge 32 ] || {
    echo 'Origin header secret must contain at least 32 characters' >&2
    exit 2
}
umask 077
envsubst '${IMAGE_ORIGIN_HEADER_SECRET}' </etc/nginx/nginx.conf.template >/tmp/nginx.conf
if [ "$#" -gt 0 ]; then
    exec nginx -c /tmp/nginx.conf "$@"
fi
nginx -c /tmp/nginx.conf -t
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
