#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() {
    printf 'smart-box install: %s\n' "$*" >&2
    exit 1
}

prepare_runtime_services() {
    desktop_user=$1
    [ -n "$desktop_user" ] || fail "desktop user is required to clear runtime service masks"

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
}

unmask_runtime_services() {
    if ! systemctl --runtime unmask -- "$service_unit" "$watchdog_unit"; then
        fail "could not clear runtime masks for $service_unit and $watchdog_unit"
    fi
}

install_system() {
    desktop_user=${1:-${SUDO_USER:-}}
    [ "$(id -u)" -eq 0 ] || fail "system installation requires root"
    [ -n "$desktop_user" ] || fail "desktop user is required for system installation"
    prepare_runtime_services "$desktop_user"
    [ -x "$script_dir/bin/smart-box-core" ] || fail "missing bin/smart-box-core"
    [ -f "$script_dir/lib/smart_box_linux.py" ] || fail "missing Linux client"
    [ -f "$script_dir/systemd/smart-box-unmask@.service" ] || \
        fail "missing systemd/smart-box-unmask@.service"
    [ -f "$script_dir/systemd/smart-box-cleanup@.service" ] || \
        fail "missing systemd/smart-box-cleanup@.service"

    install -d -m 0755 /usr/local/lib/smart-box
    install -d -m 0755 /usr/local/bin
    install -d -m 0755 /usr/local/lib/systemd/system
    install -d -m 0755 /usr/local/share/applications
    install -d -m 0755 /usr/local/share/icons/hicolor/192x192/apps
    install -d -m 0755 /usr/local/share/doc/smart-box
    install -d -m 0755 /etc/polkit-1/rules.d

    install -o root -g root -m 0755 "$script_dir/bin/smart-box-core" /usr/local/lib/smart-box/smart-box-core
    install -o root -g root -m 0644 "$script_dir/lib/smart_box_backend.py" /usr/local/lib/smart-box/smart_box_backend.py
    install -o root -g root -m 0644 "$script_dir/lib/smart_box_linux.py" /usr/local/lib/smart-box/smart_box_linux.py
    install -o root -g root -m 0755 "$script_dir/bin/smart-box" /usr/local/bin/smart-box
    install -o root -g root -m 0755 "$script_dir/bin/smart-box-profile" /usr/local/bin/smart-box-profile
    install -o root -g root -m 0644 "$script_dir/systemd/smart-box@.service" /usr/local/lib/systemd/system/smart-box@.service
    install -o root -g root -m 0644 "$script_dir/systemd/smart-box-watchdog@.service" /usr/local/lib/systemd/system/smart-box-watchdog@.service
    install -o root -g root -m 0644 "$script_dir/systemd/smart-box-unmask@.service" /usr/local/lib/systemd/system/smart-box-unmask@.service
    install -o root -g root -m 0644 "$script_dir/systemd/smart-box-cleanup@.service" /usr/local/lib/systemd/system/smart-box-cleanup@.service
    install -o root -g root -m 0644 "$script_dir/config/smart-box.rules" /etc/polkit-1/rules.d/49-smart-box.rules
    install -o root -g root -m 0644 "$script_dir/config/smart-box.desktop" /usr/local/share/applications/smart-box.desktop
    install -o root -g root -m 0644 "$script_dir/icons/smart-box.png" /usr/local/share/icons/hicolor/192x192/apps/smart-box.png
    install -o root -g root -m 0644 "$script_dir/README.md" /usr/local/share/doc/smart-box/README.md

    setcap -r /usr/local/lib/smart-box/smart-box-core 2>/dev/null || true
    rm -f /usr/local/lib/systemd/user/smart-box.service
    systemctl daemon-reload || fail "systemd daemon reload failed"
    unmask_runtime_services
    exit 0
}

if [ "${1:-}" = "--system" ]; then
    install_system "${2:-}"
fi

[ "$(id -u)" -ne 0 ] || fail "run this installer as the desktop user"
[ -x "$script_dir/bin/smart-box-core" ] || fail "run install.sh from the unpacked release directory"
/usr/bin/python3 -c 'import PySide6' 2>/dev/null || fail "PySide6 is not installed for /usr/bin/python3"
command -v pkexec >/dev/null 2>&1 || fail "pkexec is required"

desktop_user=$(id -un)
pkexec "$script_dir/install.sh" --system "$desktop_user"
mkdir -p "$HOME/.config/smart-box" "$HOME/.local/state/smart-box"
chmod 700 "$HOME/.config/smart-box" "$HOME/.local/state/smart-box"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

/usr/local/bin/smart-box-profile --version
printf '%s\n' 'smart-box installation complete'
