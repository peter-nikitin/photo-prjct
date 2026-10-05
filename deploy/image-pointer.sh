#!/bin/sh
set -eu
operation=$1
pointer=$2
target=${3:-}
previous=${4:-}
case "$operation" in
    restore)
        [ -n "$target" ] || {
            echo "Previous registry target unavailable; running containers retain their observed handoff state" >&2
            exit 1
        }
        docker buildx imagetools create --tag "$pointer" "$target"
        echo "Registry pointer restored; running containers retain their observed handoff state"
        ;;
    advance)
        if ! docker buildx imagetools create --tag "$pointer" "$target"; then
            sh "$0" restore "$pointer" "$previous" || {
                echo "Registry pointer recovery failed" >&2
            }
            exit 1
        fi
        ;;
    *) exit 2 ;;
esac
