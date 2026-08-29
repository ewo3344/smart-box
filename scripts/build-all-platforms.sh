#!/bin/sh
# Run the reproducible, non-destructive release checks for every platform.
# Device/runner checks are reported as BLOCKED until their environment exists.

set -u

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
toolchain_file="$root/TOOLCHAIN_VERSION"
toolchain=go1.26.5
if [ -r "$toolchain_file" ]; then
    toolchain=$(sed -n '1p' "$toolchain_file" | tr -d '[:space:]')
fi

out="$root/verification/build-all-platforms-$(date +%Y%m%d-%H%M%S)"
allow_missing=0
run_release=1

usage() {
    printf '%s\n' \
        "usage: $0 [--allow-missing] [--skip-release] [--out DIR]" \
        "  --allow-missing  allow unavailable Windows/Android/device checks" \
        "  --skip-release   skip the long core/package release gate" \
        "  --out DIR        write logs and report to DIR"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --allow-missing) allow_missing=1 ;;
        --skip-release) run_release=0 ;;
        --out)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            out=$2
            shift
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
    shift
done

umask 077
mkdir -p "$out"
report="$out/REPORT.md"
status_tsv="$out/status.tsv"
: > "$status_tsv"

failed=0
blocked=0

record() {
    printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$status_tsv"
}

run_step() {
    name=$1
    shift
    log="$out/$name.log"
    printf '>>> %s\n' "$name"
    printf '$'
    printf ' %s' "$@"
    printf '\n'
    if "$@" >"$log" 2>&1; then
        printf 'PASS  %s\n' "$name"
        record "$name" PASS "$log"
    else
        rc=$?
        printf 'FAIL  %s (exit %s)\n' "$name" "$rc"
        record "$name" "FAIL($rc)" "$log"
        failed=$((failed + 1))
    fi
}

blocked_step() {
    name=$1
    reason=$2
    log="$out/$name.log"
    printf '%s\n' "BLOCKED $name: $reason" > "$log"
    printf 'BLOCKED %s: %s\n' "$name" "$reason"
    record "$name" BLOCKED "$log"
    blocked=$((blocked + 1))
}

test_home=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-release.XXXXXX")
trap 'rm -rf -- "$test_home"' EXIT HUP INT TERM
mkdir -p "$test_home/.config" "$test_home/.local/state"

run_step linux_compile python3 -m compileall -q "$root/linux"
run_step linux_unit env \
    HOME="$test_home" \
    XDG_CONFIG_HOME="$test_home/.config" \
    XDG_STATE_HOME="$test_home/.local/state" \
    PYTHONPATH="$root/linux" \
    QT_QPA_PLATFORM=offscreen \
    python3 -m unittest discover -s "$root/linux/tests" -p 'test*.py' -q
run_step shell_syntax sh -n \
    "$root/linux/build-package.sh" "$root/linux/install.sh" \
    "$root/linux/uninstall.sh" "$root/linux/smart-box" \
    "$root/linux/smart-box-profile" "$root/scripts"/*.sh

if command -v systemd-analyze >/dev/null 2>&1; then
    run_step systemd_units systemd-analyze verify \
        "$root/linux/smart-box@.service" \
        "$root/linux/smart-box-watchdog@.service" \
        "$root/linux/smart-box-cleanup@.service" \
        "$root/linux/smart-box-unmask@.service"
else
    blocked_step systemd_units "systemd-analyze is not installed"
fi

if command -v go >/dev/null 2>&1 && [ -f "$root/converter/go.mod" ]; then
    run_step converter_unit env GOTOOLCHAIN="$toolchain" sh -c "cd '$root/converter' && go test ./..."
    run_step converter_race env GOTOOLCHAIN="$toolchain" sh -c "cd '$root/converter' && go test -race ./..."
    run_step converter_vet env GOTOOLCHAIN="$toolchain" sh -c "cd '$root/converter' && go vet ./..."
else
    blocked_step converter_unit "Go or converter module is unavailable"
    blocked_step converter_race "Go or converter module is unavailable"
    blocked_step converter_vet "Go or converter module is unavailable"
fi

if [ "$run_release" -eq 1 ]; then
    if [ -x "$root/scripts/verify-release.sh" ]; then
        if [ -x /usr/local/lib/smart-box/smart-box-core ] || [ -x "$root/dist/smart-box-0.1.0-linux-x86_64/bin/smart-box-core" ]; then
            run_step release_gate sh "$root/scripts/verify-release.sh" --allow-live
        else
            blocked_step release_gate "no validated smart-box core binary"
        fi
    else
        blocked_step release_gate "scripts/verify-release.sh is missing"
    fi
fi

if [ -x "$root/android/gradlew" ] && command -v java >/dev/null 2>&1; then
    run_step android_jvm sh -c "cd '$root/android' && ./gradlew :app:testOtherDebugUnitTest --no-daemon --console=plain"
else
    blocked_step android_jvm "Android Gradle wrapper or Java is unavailable"
fi

if command -v pwsh >/dev/null 2>&1; then
    if command -v dotnet >/dev/null 2>&1; then
        run_step windows_verify pwsh -NoLogo -NoProfile -File "$root/scripts/verify-windows.ps1" -OutputDirectory "$out/windows"
    else
        blocked_step windows_verify ".NET SDK is unavailable"
    fi
else
    blocked_step windows_verify "PowerShell 7 is unavailable on this host"
fi

if [ -n "${RASPBERRY_PI_HOST:-}" ] && [ -x "$root/scripts/verify-raspberry-pi.sh" ]; then
    run_step raspberry_pi env \
        RASPBERRY_PI_HOST="$RASPBERRY_PI_HOST" \
        RASPBERRY_PI_USER="${RASPBERRY_PI_USER:-e}" \
        "$root/scripts/verify-raspberry-pi.sh"
else
    blocked_step raspberry_pi "set RASPBERRY_PI_HOST to run the remote health check"
fi

{
    printf '%s\n\n' '# smart-box platform verification'
    printf '%s\n\n' "Generated: $(date -Is)"
    printf '| Step | Status | Log |\n| --- | --- | --- |\n'
    while IFS="	" read -r name status log; do
        printf '| `%s` | **%s** | `%s` |\n' "$name" "$status" "$(basename "$log")"
    done < "$status_tsv"
    printf '\n- Failed: **%s**\n- Blocked: **%s**\n' "$failed" "$blocked"
} > "$report"

printf '\nReport: %s\n' "$report"
if [ "$failed" -gt 0 ]; then
    exit 1
fi
if [ "$blocked" -gt 0 ] && [ "$allow_missing" -ne 1 ]; then
    exit 2
fi
exit 0

