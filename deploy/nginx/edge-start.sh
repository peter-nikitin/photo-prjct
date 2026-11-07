#!/bin/sh
set -eu
if [ -s /etc/letsencrypt/live/photo-prjct/fullchain.pem ] && [ -s /etc/letsencrypt/live/photo-prjct/privkey.pem ]; then
    template=/opt/edge/https.conf
else
    template=/opt/edge/http.conf
fi
umask 077
cp "$template" /etc/nginx/conf.d/default.conf
nginx -t
# Renew runs independently; reload also picks up newly renewed certificate material.
(while sleep 3600; do nginx -s reload || true; done) &
exec nginx -g 'daemon off;'
