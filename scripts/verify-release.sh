#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
run_android=0

usage() {
    printf '%s\n' "usage: $0 [--android]"
}

for argument in "$@"; do
    case "$argument" in
        --android) run_android=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
done

run() {
    printf '\n>>> %s\n' "$*"
    "$@"
}

[ -f "$root/core/go.mod" ] && [ -f "$root/android/version.properties" ] || {
    printf '%s\n' 'verify-release: submodules are not initialized; run git submodule update --init --recursive' >&2
    exit 1
}
command -v go >/dev/null 2>&1 || {
    printf '%s\n' 'verify-release: Go is required' >&2
    exit 1
}

cd "$root"
run scripts/version-manager.sh check
run python3 -m compileall -q linux
test_home_dir=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-tests.XXXXXX")
build_dir=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-core.XXXXXX")
trap 'rm -rf -- "$test_home_dir" "$build_dir"' EXIT HUP INT TERM
mkdir -p "$test_home_dir/.config" "$test_home_dir/.local/state"
run env \
    HOME="$test_home_dir" \
    XDG_CONFIG_HOME="$test_home_dir/.config" \
    XDG_STATE_HOME="$test_home_dir/.local/state" \
    PYTHONPATH=linux \
    QT_QPA_PLATFORM=offscreen \
    python3 -m unittest discover -s linux/tests -p 'test*.py' -q
run sh -n \
    linux/build-package.sh linux/install.sh linux/uninstall.sh linux/smart-box linux/smart-box-profile \
    scripts/build-linux.sh scripts/sign-android-device.sh scripts/verify-release.sh scripts/version-manager.sh
run systemd-analyze verify \
    linux/smart-box@.service \
    linux/smart-box-watchdog@.service \
    linux/smart-box-cleanup@.service \
    linux/smart-box-unmask@.service

core="$build_dir/smart-box-core"
version=$(tr -d '\r\n' < "$root/VERSION")
upstream_version=$(sed -n 's/^UPSTREAM_VERSION=//p' "$root/android/version.properties" | head -n 1)
(
    cd "$root/core"
    run go build \
        -tags "with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api" \
        -trimpath \
        -ldflags "-X github.com/sagernet/sing-box/constant.Version=smart-box-${version}-core-${upstream_version}" \
        -o "$core" \
        ./cmd/sing-box
)
run "$core" version
(cd "$root/converter" && run env SMART_BOX_CORE="$core" go test ./...)

if [ "$run_android" -eq 1 ]; then
    command -v gomobile >/dev/null 2>&1 || {
        printf '%s\n' 'verify-release: gomobile is required for Android verification' >&2
        exit 1
    }
    go_path=$(go env GOPATH)
    [ -x "$go_path/bin/gobind" ] || {
        printf '%s\n' 'verify-release: gomobile is not initialized; run gomobile init' >&2
        exit 1
    }
    install -d "$root/android/app/libs"
    (
        cd "$root/core"
        run env SMART_BOX_LIBBOX_VERSION="smart-box-${version}-core-${upstream_version}" \
            go run ./cmd/internal/build_libbox -debug
    )
    cp "$root/core/libbox.aar" "$root/android/app/libs/libbox.aar"
    cp "$root/core/libbox-legacy.aar" "$root/android/app/libs/libbox-legacy.aar"
    (cd "$root/android" && run ./gradlew testOtherDebugUnitTest)
fi

printf '%s\n' 'source verification: PASS'
