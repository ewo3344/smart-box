#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
require_fail_open=1
run_android=0

usage() {
    printf '%s\n' "usage: $0 [--allow-live] [--android]"
}

for argument in "$@"; do
    case "$argument" in
        --allow-live) require_fail_open=0 ;;
        --android) run_android=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
done

run() {
    printf '\n>>> %s\n' "$*"
    "$@"
}

cd "$root"

toolchain_file="$root/TOOLCHAIN_VERSION"
if [ ! -f "$toolchain_file" ]; then
    printf '%s\n' "release gate: missing $toolchain_file" >&2
    exit 1
fi
toolchain_version=$(awk '
    NF != 1 { exit 1 }
    { value = $1; count++ }
    END { if (count != 1) exit 1; print value }
' "$toolchain_file") || {
    printf '%s\n' "release gate: invalid $toolchain_file (expected one goX.Y.Z line)" >&2
    exit 1
}
if ! printf '%s\n' "$toolchain_version" | grep -Eq '^go[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf '%s\n' "release gate: invalid Go toolchain pin: $toolchain_version" >&2
    exit 1
fi

run python3 -m compileall -q linux
test_home_dir=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-tests.XXXXXX")
trap 'rm -rf -- "$test_home_dir"' EXIT HUP INT TERM
mkdir -p "$test_home_dir/.config" "$test_home_dir/.local/state"
run env \
    HOME="$test_home_dir" \
    XDG_CONFIG_HOME="$test_home_dir/.config" \
    XDG_STATE_HOME="$test_home_dir/.local/state" \
    PYTHONPATH=linux \
    QT_QPA_PLATFORM=offscreen \
    python3 -m unittest discover -s linux/tests -p 'test*.py' -q
run sh -n linux/build-package.sh linux/install.sh linux/uninstall.sh linux/smart-box linux/smart-box-profile
run systemd-analyze verify linux/smart-box@.service linux/smart-box-watchdog@.service linux/smart-box-cleanup@.service linux/smart-box-unmask@.service

if [ -x /usr/local/lib/smart-box/smart-box-core ]; then
    core=/usr/local/lib/smart-box/smart-box-core
elif [ -x "$root/dist/smart-box-0.1.0-linux-x86_64/bin/smart-box-core" ]; then
    core="$root/dist/smart-box-0.1.0-linux-x86_64/bin/smart-box-core"
else
    printf '%s\n' 'release gate: no smart-box core found' >&2
    exit 1
fi

run "$core" check --disable-color -D "$HOME/.local/state/smart-box" -c "$HOME/.config/smart-box/profile.json"
run "$core" check --disable-color -D "$HOME/.local/state/smart-box" -c "$HOME/.config/smart-box/runtime.json"

if [ -x "$root/converter/smart-box-converter-linux-arm64" ] || command -v go >/dev/null 2>&1; then
    (cd "$root/converter" && run env SMART_BOX_CORE="$core" GOTOOLCHAIN="$toolchain_version" go test ./...)
fi

run "$root/linux/build-package.sh"
(cd "$root/dist/smart-box-0.1.0-linux-x86_64" && run sha256sum -c SHA256SUMS)

for source in linux/smart_box_backend.py linux/smart_box_linux.py; do
    installed=/usr/local/lib/smart-box/$(basename "$source")
    package="$root/dist/smart-box-0.1.0-linux-x86_64/lib/$(basename "$source")"
    [ ! -e "$installed" ] || run cmp "$source" "$installed"
    run cmp "$source" "$package"
done

if [ "$run_android" -eq 1 ]; then
    run sh -c "cd '$root/android' && ./gradlew testOtherDebugUnitTest"
fi

if [ "$require_fail_open" -eq 1 ]; then
    if systemctl is-active --quiet smart-box@"$(id -un)".service; then
        printf '%s\n' 'release gate: smart-box service is still active' >&2
        exit 1
    fi
    if systemctl is-active --quiet smart-box-watchdog@"$(id -un)".service; then
        printf '%s\n' 'release gate: smart-box watchdog is still active' >&2
        exit 1
    fi
    if ip link show SmartBox >/dev/null 2>&1; then
        printf '%s\n' 'release gate: SmartBox interface is still present' >&2
        exit 1
    fi
    run curl --noproxy '*' -4 -L --connect-timeout 5 --max-time 10 -sS -o /dev/null \
        -w 'direct_baidu=%{http_code} %{time_total}\n' https://www.baidu.com/
fi

printf '%s\n' 'release gate: PASS'
