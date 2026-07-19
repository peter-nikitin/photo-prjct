#!/bin/sh

set -eu

: "${DEPLOY_ROOT:?Set DEPLOY_ROOT}"
action="${1:-install}"

case "$action" in
    install|remove)
        ;;
    *)
        echo "Usage: $0 [install|remove]" >&2
        exit 2
        ;;
esac
case "$DEPLOY_ROOT" in
    *[!A-Za-z0-9_./-]*|'')
        echo "DEPLOY_ROOT contains unsupported characters" >&2
        exit 2
        ;;
esac
if ! command -v crontab >/dev/null 2>&1; then
    if [ "$action" = remove ]; then
        echo "No crontab command is installed; upload cleanup schedule is absent."
        exit 0
    fi
    echo "crontab is required to install upload cleanup" >&2
    exit 1
fi

current_tmp="$(mktemp)"
updated_tmp="$(mktemp)"
cleanup() {
    rm -f "$current_tmp" "$updated_tmp"
}
trap cleanup EXIT INT TERM HUP

crontab -l > "$current_tmp" 2>/dev/null || :
awk '
    $0 == "# BEGIN photo-prjct-upload-cleanup" { managed = 1; next }
    $0 == "# END photo-prjct-upload-cleanup" { managed = 0; next }
    !managed { print }
' "$current_tmp" > "$updated_tmp"

if [ "$action" = install ]; then
    {
        printf '%s\n' '# BEGIN photo-prjct-upload-cleanup'
        printf '17 3 * * * DEPLOY_ROOT=%s /bin/sh %s/deploy/run-upload-cleanup.sh >> %s/upload-cleanup.log 2>&1\n' \
            "$DEPLOY_ROOT" "$DEPLOY_ROOT" "$DEPLOY_ROOT"
        printf '%s\n' '# END photo-prjct-upload-cleanup'
    } >> "$updated_tmp"
fi

crontab "$updated_tmp"
if [ "$action" = install ]; then
    printf 'Installed daily upload cleanup using host timezone %s.\n' "$(date +%Z)"
else
    echo "Removed upload cleanup schedule."
fi
