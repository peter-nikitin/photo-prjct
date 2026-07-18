#!/bin/sh

set -eu

while true; do
    if ! certbot renew --webroot --webroot-path /var/www/certbot --quiet; then
        echo "Certificate renewal attempt failed; retrying after 12 hours" >&2
    fi
    sleep 43200
done
