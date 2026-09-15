#!/bin/sh
set -eu

# apply creates a root-private webroot. Share only these public challenge directories with
# Nginx's group; Certbot remains their sole writer, even with all capabilities dropped.
umask 077
mkdir -p /var/www/acme/.well-known/acme-challenge
for directory in /var/www/acme /var/www/acme/.well-known /var/www/acme/.well-known/acme-challenge; do
    chgrp 101 "$directory"
    chmod 0750 "$directory"
done
exec certbot "$@"
