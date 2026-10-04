#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py sync_feature_flags
python manage.py bootstrap_photographer_group
python manage.py collectstatic --noinput

exec "$@"
