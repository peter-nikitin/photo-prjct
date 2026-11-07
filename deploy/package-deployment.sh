#!/bin/sh
set -eu
[ "$#" -eq 1 ] || exit 2
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "$1" in /*) ;; *) exit 2 ;; esac
cd "$root"
tar -cf "$1" docker-compose.deployment.yml deploy/apply-deployment.sh \
    deploy/nginx/http.conf deploy/nginx/https.conf deploy/nginx/edge-start.sh \
    deploy/certbot/renew-certificates.sh deploy/certbot/reconcile-certificate.sh
