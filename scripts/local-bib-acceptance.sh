#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
exec .venv/bin/python -m experiments.bib_search.worker_acceptance.run "$@"
