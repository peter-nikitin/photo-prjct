#!/bin/sh
set -eu

mode="${1:-test}"
compose_file="docker-compose.visual.yml"

dependency_key=$(
  {
    git hash-object Dockerfile.visual-tests
    git hash-object package-lock.json
    git hash-object src/backend/requirements.txt
  } | git hash-object --stdin
)
VISUAL_TEST_IMAGE="photo-prjct-visual-deps:${dependency_key}"
export VISUAL_TEST_IMAGE

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

if ! docker image inspect "$VISUAL_TEST_IMAGE" >/dev/null 2>&1; then
  docker compose -f "$compose_file" build visual-tests
fi
docker compose -f "$compose_file" run --rm visual-tests npm run "$inside_script"
