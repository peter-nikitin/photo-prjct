#!/bin/sh
set -eu
cd "${DEPLOY_ROOT:-/opt/photo-prjct}"
compose() { docker compose --project-name photo-prjct --env-file .env -f docker-compose.deployment.yml "$@"; }
# Run only after DNS and NLB are ready for both names.
compose --profile acme run --rm --no-deps --entrypoint /bin/sh certbot -c '
    test -n "$LETSENCRYPT_EMAIL"
    exec certbot certonly --webroot -w /var/www/certbot --non-interactive --agree-tos \
      --email "$LETSENCRYPT_EMAIL" --cert-name photo-prjct \
      -d findme-photo.ru -d www.findme-photo.ru
'
compose up -d --wait --force-recreate --no-deps nginx
