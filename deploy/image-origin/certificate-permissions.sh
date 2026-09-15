#!/bin/sh
set -eu
# Certbot runs as owner root with supplementary group 101; no CHOWN capability is required.
chgrp 101 /etc/letsencrypt
chmod g+x /etc/letsencrypt
for directory in /etc/letsencrypt/live /etc/letsencrypt/archive; do
    [ -d "$directory" ] || continue
    chgrp -R 101 "$directory"
    chmod -R g+rX "$directory"
done
