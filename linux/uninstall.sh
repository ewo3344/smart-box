#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() {
    printf 'smart-box uninstall: %s\n' "$*" >&2
    exit 1
}

prepare_runtime_services() {
    desktop_user=$1
    [ -n "$desktop_user" ] || fail "desktop user is required to remove SmartBox services"

    desktop_uid=$(id -u -- "$desktop_user" 2>/dev/null) || \
        fail "desktop user does not exist: $desktop_user"
    [ "$desktop_uid" -ne 0 ] || fail "desktop user must not be root"

    case "$desktop_user" in
        [!A-Za-z0-9]*|*[!A-Za-z0-9_.-]*)
            fail "desktop user cannot form a SmartBox service instance: $desktop_user"
            ;;
    esac
    service_unit="smart-box@$desktop_user.service"
    watchdog_unit="smart-box-watchdog@$desktop_user.service"
    helper_unit="smart-box-unmask@$desktop_user.service"
    cleanup_unit="smart-box-cleanup@$desktop_user.service"
}

remove_system() {
    [ "$(id -u)" -eq 0 ] || exit 1
    desktop_user=${1:-${SUDO_USER:-}}
    prepare_runtime_services "$desktop_user"
    systemctl --runtime unmask -- "$service_unit" "$watchdog_unit" >/dev/null 2>&1 || \
        fail "could not clear runtime masks for $service_unit and $watchdog_unit"
    systemctl stop -- "$service_unit" "$watchdog_unit" "$helper_unit" "$cleanup_unit" >/dev/null 2>&1 || true
    rm -f /usr/local/bin/smart-box /usr/local/bin/smart-box-profile
    rm -f /usr/local/lib/systemd/system/smart-box@.service
    rm -f /usr/local/lib/systemd/system/smart-box-watchdog@.service
    rm -f /usr/local/lib/systemd/system/smart-box-unmask@.service
    rm -f /usr/local/lib/systemd/system/smart-box-cleanup@.service
    rm -f /usr/local/lib/systemd/user/smart-box.service
    rm -f /etc/polkit-1/rules.d/49-smart-box.rules
    rm -f /usr/local/share/applications/smart-box.desktop
    rm -f /usr/local/share/icons/hicolor/192x192/apps/smart-box.png
    rm -rf /usr/local/lib/smart-box /usr/local/share/doc/smart-box
    systemctl daemon-reload
    exit 0
}

if [ "${1:-}" = "--system" ]; then
    remove_system "${2:-}"
fi

[ "$(id -u)" -ne 0 ] || {
    printf '%s\n' 'run this uninstaller as the desktop user' >&2
    exit 1
}

desktop_user=$(id -un)
systemctl stop -- "smart-box@$desktop_user.service" >/dev/null 2>&1 || true
systemctl --user stop app-FlClash@autostart.service >/dev/null 2>&1 || true
rm -f "$HOME/.local/state/smart-box/switch-state.json"
rm -f "$HOME/.config/autostart/smart-box.desktop"
pkexec "$script_dir/uninstall.sh" --system "$desktop_user"

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$HOME/.config/smart-box" "$HOME/.local/state/smart-box"
fi
printf '%s\n' 'smart-box removed; user data was preserved unless --purge was used'
