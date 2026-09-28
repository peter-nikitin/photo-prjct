#!/bin/sh
set -eu

# Build the sole canonical application package, including the exact shared cloud transport.
[ "$#" -eq 1 ] || exit 2
repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
archive=$1
case "$archive" in /*) ;; *) exit 2 ;; esac
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM
cd "$repository_root"
tar --exclude=__pycache__ --exclude='*.pyc' -cf "$archive" \
    docker-compose.deployment.yml docker-compose.https.yml deploy
package="$scratch/deploy/worker-pools/_canonical/processing/services"
mkdir -p "$package"
cp src/backend/processing/__init__.py "$package/../__init__.py"
cp src/backend/processing/services/__init__.py "$package/__init__.py"
cp src/backend/processing/services/worker_pool_cloud.py "$package/worker_pool_cloud.py"
tar -rf "$archive" -C "$scratch" deploy/worker-pools/_canonical
