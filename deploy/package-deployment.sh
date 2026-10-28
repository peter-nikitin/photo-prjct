#!/bin/sh
set -eu
[ "$#" -eq 1 ] || exit 2
case "$1" in /*) ;; *) echo 'Package path must be absolute' >&2; exit 2 ;; esac
repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repository_root"
tar --exclude=__pycache__ --exclude='*.pyc' -cf "$1" \
    docker-compose.deployment.yml deploy scripts/restore-recovery-database.sh \
    scripts/rehearse-recovery.sh
