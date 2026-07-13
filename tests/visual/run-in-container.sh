#!/bin/sh
set -eu

mode="${1:-test}"
compose_file="docker-compose.visual.yml"

case "$mode" in
  test) inside_script="test:visual:inside" ;;
  update) inside_script="test:visual:update:inside" ;;
  *)
    echo "Usage: $0 [test|update]" >&2
    exit 2
    ;;
esac

cleanup() {
  docker compose -f "$compose_file" down --volumes --remove-orphans
}

trap cleanup EXIT INT TERM

docker compose -f "$compose_file" build visual-tests
docker compose -f "$compose_file" run --rm visual-tests npm run "$inside_script"
