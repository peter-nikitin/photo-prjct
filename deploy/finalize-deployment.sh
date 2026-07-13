#!/bin/sh

set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"

[ "$#" -eq 1 ] || {
    echo "Usage: $0 EXPECTED_IMAGE" >&2
    exit 2
}

expected_image="$1"
candidate_file="$DEPLOY_ROOT/candidate-image"

if [ ! -f "$candidate_file" ] || [ "$(cat "$candidate_file")" != "$expected_image" ]; then
    echo "Candidate image does not match the expected image" >&2
    exit 1
fi

mv "$DEPLOY_ROOT/candidate-image" "$DEPLOY_ROOT/deployed-image"
rm -f "$DEPLOY_ROOT/previous-image"
