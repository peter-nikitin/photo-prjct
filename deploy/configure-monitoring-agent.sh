#!/bin/sh
set -eu

DEB_CONFIG_DIR=/etc/yandex/unified_agent
DEB_CONFIG_PATH=/etc/yandex/unified_agent/config.yml
DEB_SERVICE=unified-agent
MANAGED_CONFIG_DIR=/etc/yc/unified_agent
MANAGED_CONFIG_PATH=/etc/yc/unified_agent/config.yml
MANAGED_SERVICE=unified_agent
CONFIG_DIR=''
CONFIG_PATH=''
SERVICE_NAME=''
AGENT_BINARY=''
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
TEMPLATE_PATH="$SCRIPT_DIR/monitoring/unified-agent.yml.template"
FOLDER_ID=''
temporary_dir=''
candidate_config=''
previous_config=''
had_previous=0
config_promoted=0
agent_preexisting=0
agent_installed_by_attempt=0
service_was_enabled=0
service_was_active=0

usage() {
    echo "Usage: $0 --folder-id FOLDER_ID" >&2
    exit 64
}

fail() {
    echo "$1" >&2
    exit 1
}

cleanup() {
    exit_status=$?
    trap - 0 HUP INT TERM

    if [ "$exit_status" -ne 0 ] && [ "$agent_installed_by_attempt" -eq 1 ]; then
        systemctl stop "$SERVICE_NAME" || true
        systemctl disable "$SERVICE_NAME" || true
        rm -f "$CONFIG_PATH" || true
        dpkg --remove yandex-unified-agent || true
    elif [ "$exit_status" -ne 0 ] && [ "$agent_preexisting" -eq 1 ] && \
        [ "$config_promoted" -eq 1 ]; then
        if [ "$had_previous" -eq 1 ]; then
            mv "$previous_config" "$CONFIG_PATH" || true
        else
            rm -f "$CONFIG_PATH" || true
        fi
        if [ "$service_was_enabled" -eq 1 ]; then
            systemctl enable "$SERVICE_NAME" || true
        else
            systemctl disable "$SERVICE_NAME" || true
        fi
        if [ "$service_was_active" -eq 1 ]; then
            systemctl restart "$SERVICE_NAME" || true
        else
            systemctl stop "$SERVICE_NAME" || true
        fi
    fi

    if [ -n "$temporary_dir" ]; then
        rm -rf "$temporary_dir"
    fi
    exit "$exit_status"
}

require_supported_host() {
    [ "$(id -u)" -eq 0 ] || fail "configure-monitoring-agent.sh must run as root"
    [ "$(uname -m)" = "x86_64" ] || fail "Unified Agent deb installation requires x86_64"
    [ -r /etc/os-release ] || fail "Unified Agent deb installation requires Ubuntu"

    # /etc/os-release must report ID=ubuntu and an officially packaged release.
    . /etc/os-release
    [ "${ID:-}" = "ubuntu" ] || fail "Unified Agent deb installation requires Ubuntu"

    case "${VERSION_ID:-}" in
        16.04) ubuntu_name=ubuntu-16.04-xenial ;;
        18.04) ubuntu_name=ubuntu-18.04-bionic ;;
        20.04) ubuntu_name=ubuntu-20.04-focal ;;
        22.04) ubuntu_name=ubuntu-22.04-jammy ;;
        24.04) ubuntu_name=ubuntu-24.04-noble ;;
        *) fail "Unsupported Ubuntu version for Unified Agent deb installation" ;;
    esac
}

install_agent_if_missing() {
    if command -v unified_agent >/dev/null 2>&1; then
        return
    fi

    ua_version=$(curl --fail --silent --show-error --connect-timeout 10 --max-time 60 \
        https://storage.yandexcloud.net/yc-unified-agent/latest-version) || \
        fail "Could not obtain the official Unified Agent version"
    [ -n "$ua_version" ] || fail "Official Unified Agent version was empty"

    package_path="$temporary_dir/yandex-unified-agent_${ua_version}_amd64.deb"
    curl --fail --silent --show-error --connect-timeout 10 --max-time 120 \
        --output "$package_path" \
        "https://storage.yandexcloud.net/yc-unified-agent/releases/${ua_version}/deb/${ubuntu_name}/yandex-unified-agent_${ua_version}_amd64.deb" || \
        fail "Could not download the official Unified Agent deb package"
    dpkg -i "$package_path" || fail "Could not install the official Unified Agent deb package"
    agent_installed_by_attempt=1
    use_deb_layout
}

use_deb_layout() {
    CONFIG_DIR=$DEB_CONFIG_DIR
    CONFIG_PATH=$DEB_CONFIG_PATH
    SERVICE_NAME=$DEB_SERVICE
}

use_managed_layout() {
    CONFIG_DIR=$MANAGED_CONFIG_DIR
    CONFIG_PATH=$MANAGED_CONFIG_PATH
    SERVICE_NAME=$MANAGED_SERVICE
}

is_deb_agent_installed() {
    dpkg-query -W -f='${db:Status-Status}\n' yandex-unified-agent 2>/dev/null | grep -qx installed && \
        systemctl cat "$DEB_SERVICE" >/dev/null 2>&1
}

is_managed_agent_installed() {
    [ -f "$MANAGED_CONFIG_PATH" ] && systemctl cat "$MANAGED_SERVICE" >/dev/null 2>&1
}

snapshot_existing_agent() {
    AGENT_BINARY=$(command -v unified_agent || true)
    if [ -z "$AGENT_BINARY" ]; then
        return
    fi

    agent_preexisting=1
    if is_deb_agent_installed; then
        use_deb_layout
    elif is_managed_agent_installed; then
        use_managed_layout
    else
        fail "Existing Unified Agent does not match the supported deb or managed VM layout"
    fi

    if systemctl is-enabled --quiet "$SERVICE_NAME"; then
        service_was_enabled=1
    fi
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        service_was_active=1
    fi
    if [ -f "$CONFIG_PATH" ]; then
        cp -p "$CONFIG_PATH" "$previous_config"
        had_previous=1
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --folder-id)
            [ "$#" -ge 2 ] || usage
            FOLDER_ID=$2
            shift 2
            ;;
        *) usage ;;
    esac
done

[ -n "$FOLDER_ID" ] || usage
case "$FOLDER_ID" in
    *[!A-Za-z0-9-]*) fail "Folder ID has unsupported characters" ;;
esac

require_supported_host
[ -f "$TEMPLATE_PATH" ] || fail "Unified Agent template is missing"

temporary_dir=$(mktemp -d) || fail "Could not create a temporary directory"
candidate_config="$temporary_dir/config.yml"
previous_config="$temporary_dir/config.yml.previous"
trap cleanup 0 HUP INT TERM

snapshot_existing_agent
install_agent_if_missing
AGENT_BINARY=$(command -v unified_agent || true)
[ -n "$AGENT_BINARY" ] || fail "Unified Agent installation did not provide unified_agent"
[ -n "$CONFIG_PATH" ] || fail "Unified Agent installation did not select a supported layout"

mkdir -p "$CONFIG_DIR"

sed "s|__YANDEX_CLOUD_FOLDER_ID__|$FOLDER_ID|g" "$TEMPLATE_PATH" > "$candidate_config"
"$AGENT_BINARY" --config "$candidate_config" check-config

mv "$candidate_config" "$CONFIG_PATH"
config_promoted=1
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl is-active "$SERVICE_NAME"
printf 'Unified Agent version: %s\n' "$("$AGENT_BINARY" --version)"

config_promoted=0
rm -rf "$temporary_dir"
temporary_dir=''
trap - 0 HUP INT TERM
