#!/bin/sh
set -eu

# Installed as a root-owned, narrowly sudo-allowed command after operator review.
# Never execute the mutable deployment package as root.
action="${1:-}"
case "$action" in install|verify|remove) ;; *) exit 2 ;; esac

if [ -n "${FINDME_METRICS_TEST_ROOT:-}" ]; then
    [ "$(id -u)" -ne 0 ] || exit 2
    base="$FINDME_METRICS_TEST_ROOT"
    package="$base/package"
    candidate="$base/candidate"
    runtime="$base/runtime"
    config_dir="$base/config"
    systemd_dir="$base/systemd"
else
    [ "$(id -u)" -eq 0 ] || exit 2
    package=/usr/local/lib/findme-worker-pool-metrics-package
    candidate=/opt/photo-prjct/deploy/worker-pools
    runtime=/usr/local/lib/findme-worker-pools
    config_dir=/etc/findme-worker-pools
    systemd_dir=/etc/systemd/system
    [ "$(stat -c '%U:%G:%a' "$package")" = root:root:755 ] || exit 1
fi

config='{"deploy_root":"/opt/photo-prjct","cloud":"/opt/photo-prjct/worker-pools-observation.json"}'
service=findme-worker-pool-metrics.service
timer=findme-worker-pool-metrics.timer

source_file() {
    case "$1" in
        metrics.py|metrics.service|metrics.timer) printf '%s/%s\n' "$package" "$1" ;;
        *) exit 2 ;;
    esac
}

target_file() {
    case "$1" in
        metrics.py) printf '%s/metrics.py\n' "$runtime" ;;
        metrics.service) printf '%s/%s\n' "$systemd_dir" "$service" ;;
        metrics.timer) printf '%s/%s\n' "$systemd_dir" "$timer" ;;
        *) exit 2 ;;
    esac
}

verify_package() {
    [ -d "$package" ] || return 1
    for name in metrics.py metrics.service metrics.timer; do
        source="$(source_file "$name")"
        [ -f "$source" ] && [ -r "$source" ] || return 1
        if [ -z "${FINDME_METRICS_TEST_ROOT:-}" ]; then
            [ "$(stat -c '%U:%G:%a' "$source")" = root:root:644 ] || return 1
        fi
    done
}

verify_installed() {
    verify_package || return 1
    for name in metrics.py metrics.service metrics.timer; do
        cmp -s "$(source_file "$name")" "$(target_file "$name")" || return 1
    done
    [ -f "$config_dir/metrics.json" ] || return 1
    [ "$(cat "$config_dir/metrics.json")" = "$config" ] || return 1
    systemctl is-enabled --quiet "$timer" || return 1
    systemctl is-active --quiet "$timer" || return 1
}

case "$action" in
    install)
        verify_package || exit 1
        for name in metrics.py metrics.service metrics.timer; do
            cmp -s "$(source_file "$name")" "$candidate/$name" || exit 1
            target="$(target_file "$name")"
            if [ -e "$target" ]; then cmp -s "$(source_file "$name")" "$target" || exit 1; fi
        done
        if [ -e "$config_dir/metrics.json" ]; then
            [ "$(cat "$config_dir/metrics.json")" = "$config" ] || exit 1
        fi
        if [ -z "${FINDME_METRICS_TEST_ROOT:-}" ]; then
            systemd-analyze verify "$package/metrics.service" "$package/metrics.timer" >/dev/null
            [ -f /opt/photo-prjct/worker-pools-observation.json ] || exit 1
        fi
        install -d -m 0755 "$runtime" "$config_dir" "$systemd_dir"
        for name in metrics.py metrics.service metrics.timer; do
            install -m 0644 "$(source_file "$name")" "$(target_file "$name")"
        done
        printf '%s\n' "$config" > "$config_dir/metrics.json"
        chmod 0644 "$config_dir/metrics.json"
        systemctl daemon-reload
        systemctl enable --now "$timer"
        systemctl start "$service"
        verify_installed
        ;;
    verify) verify_installed ;;
    remove)
        verify_package || exit 1
        for name in metrics.py metrics.service metrics.timer; do
            target="$(target_file "$name")"
            [ ! -e "$target" ] || cmp -s "$(source_file "$name")" "$target" || exit 1
        done
        [ ! -e "$config_dir/metrics.json" ] || \
            [ "$(cat "$config_dir/metrics.json")" = "$config" ] || exit 1
        if [ -e "$(target_file metrics.timer)" ]; then
            systemctl stop "$timer"
            systemctl stop "$service"
            systemctl disable "$timer"
        fi
        rm -f "$(target_file metrics.py)" "$(target_file metrics.service)" \
            "$(target_file metrics.timer)" "$config_dir/metrics.json"
        systemctl daemon-reload
        ;;
esac
