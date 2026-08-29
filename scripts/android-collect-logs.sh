#!/usr/bin/env bash
# Standardized, redacted Android diagnostics collector (T006).

set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ADB_BIN=${ADB:-adb}
PACKAGE_NAME=${SMART_BOX_ANDROID_PACKAGE:-io.nekohasekai.sfa.smartbox}
SERIAL=""
OUT_DIR="$ROOT/verification/android-logs-$(date +%Y%m%d-%H%M%S)"
SINCE=""
DURATION=0
CLEAR_LOG=0
KEEP_RAW=0

usage() {
    cat <<'EOF'
usage: scripts/android-collect-logs.sh [options]

Options:
  --serial SERIAL    select one authorized device
  --out DIR          output directory
  --since SPEC       pass a logcat time/line selector (default: current buffer)
  --duration SEC     stream logcat for SEC seconds instead of dumping buffer
  --clear            clear the device log buffer before collecting
  --keep-raw         retain unsanitized files (0600; default removes them)
  -h, --help         show this help
EOF
}

fail_usage() { usage >&2; printf 'error: %s\n' "$*" >&2; exit 64; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --serial) [[ $# -ge 2 ]] || fail_usage '--serial requires a value'; SERIAL=$2; shift ;;
        --out) [[ $# -ge 2 ]] || fail_usage '--out requires a value'; OUT_DIR=$2; shift ;;
        --since) [[ $# -ge 2 ]] || fail_usage '--since requires a value'; SINCE=$2; shift ;;
        --duration) [[ $# -ge 2 ]] || fail_usage '--duration requires a value'; DURATION=$2; shift ;;
        --clear) CLEAR_LOG=1 ;;
        --keep-raw) KEEP_RAW=1 ;;
        -h|--help) usage; exit 0 ;;
        *) fail_usage "unknown option: $1" ;;
    esac
    shift
done

[[ "$DURATION" =~ ^[0-9]+$ ]] || fail_usage '--duration must be an integer'
umask 077
mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/REPORT.md"
: > "$REPORT"
ADB_CMD=("$ADB_BIN")
[[ -n "$SERIAL" ]] && ADB_CMD+=( -s "$SERIAL" )
CAPTURE_FAILED=0

redact() {
    local source=$1 target=$2
    if [[ ! -f "$source" ]]; then : > "$target"; return; fi
    LC_ALL=C sed -E \
        -e 's#(Authorization:[[:space:]]*Bearer[[:space:]]+)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#((password|passwd|token|secret|api[_-]?key|cookie|authorization)[[:space:]]*[=:][[:space:]]*)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#(https?://[^[:space:]?]+)[?][^[:space:]]+#\1?<redacted>#g' \
        -e 's#((ss|ssr|vmess|vless|trojan)://)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#/data/(data|user)/[^[:space:]]+#/data/<redacted>#g' \
        -e 's#/home/[^[:space:]]+#/home/<redacted>#g' \
        "$source" > "$target"
    chmod 600 "$target"
}

capture() {
    local name=$1; shift
    local raw="$OUT_DIR/$name.raw" clean="$OUT_DIR/$name.log" rc
    if "$@" > "$raw" 2>&1; then rc=0; else rc=$?; fi
    if [[ "$rc" -ne 0 ]]; then CAPTURE_FAILED=1; fi
    redact "$raw" "$clean"
    if [[ "$KEEP_RAW" -eq 1 ]]; then chmod 600 "$raw"; else rm -f -- "$raw"; fi
    printf '%s\t%s\t%s\n' "$name" "$rc" "$clean" >> "$OUT_DIR/status.tsv"
    return 0
}

if ! command -v "$ADB_BIN" >/dev/null 2>&1; then
    printf '%s\n' '# Android log collection' '' 'RESULT=BLOCKED' 'Reason: adb executable not found' > "$REPORT"
    printf 'REPORT=%s\n' "$REPORT"
    exit 2
fi

devices_output=$("$ADB_BIN" devices -l 2>&1) || {
    printf '%s\n' '# Android log collection' '' 'RESULT=BLOCKED' 'Reason: adb devices failed' > "$REPORT"
    capture adb-devices "$ADB_BIN" devices -l
    printf 'REPORT=%s\n' "$REPORT"
    exit 2
}

ready_devices=()
while IFS=$'\t' read -r device_serial device_status; do
    [[ -n "$device_serial" ]] || continue
    [[ "$device_status" == "device" ]] || continue
    ready_devices+=("$device_serial")
done < <(printf '%s\n' "$devices_output" | awk 'NR > 1 && NF >= 2 {print $1 "\t" $2}')

if [[ -n "$SERIAL" ]]; then
    selected_status=$(printf '%s\n' "$devices_output" | awk -v wanted="$SERIAL" '$1 == wanted {print $2; exit}')
    if [[ "$selected_status" != "device" ]]; then
        printf '%s\n' '# Android log collection' '' 'RESULT=BLOCKED' "Reason: selected device is ${selected_status:-missing}" > "$REPORT"
        capture adb-devices "$ADB_BIN" devices -l
        printf 'REPORT=%s\n' "$REPORT"
        exit 2
    fi
elif [[ "${#ready_devices[@]}" -eq 1 ]]; then
    SERIAL=${ready_devices[0]}
else
    if [[ "${#ready_devices[@]}" -eq 0 ]]; then
        reason='no authorized ADB device'
    else
        reason='multiple authorized ADB devices; pass --serial'
    fi
    printf '%s\n' '# Android log collection' '' 'RESULT=BLOCKED' "Reason: $reason" > "$REPORT"
    capture adb-devices "$ADB_BIN" devices -l
    printf 'REPORT=%s\n' "$REPORT"
    exit 2
fi

# All subsequent operations target the selected device explicitly.
ADB_CMD=("$ADB_BIN" -s "$SERIAL")

: > "$OUT_DIR/status.tsv"
if [[ "$CLEAR_LOG" -eq 1 ]]; then
    capture logcat-clear "${ADB_CMD[@]}" logcat -c
fi
capture adb-devices "${ADB_CMD[@]}" devices -l
capture device-props "${ADB_CMD[@]}" shell getprop
capture package "${ADB_CMD[@]}" shell dumpsys package "$PACKAGE_NAME"
capture vpn "${ADB_CMD[@]}" shell dumpsys vpn
capture connectivity "${ADB_CMD[@]}" shell dumpsys connectivity
capture processes "${ADB_CMD[@]}" shell ps -A
capture interfaces "${ADB_CMD[@]}" shell ip -o addr show

if [[ "$DURATION" -gt 0 ]]; then
    if [[ -n "$SINCE" ]]; then
        capture logcat "${ADB_CMD[@]}" logcat -v threadtime -T "$SINCE"
    else
        capture logcat "${ADB_CMD[@]}" logcat -v threadtime
    fi &
    collector_pid=$!
    sleep "$DURATION"
    kill "$collector_pid" 2>/dev/null || true
    wait "$collector_pid" 2>/dev/null || true
else
    if [[ -n "$SINCE" ]]; then
        capture logcat "${ADB_CMD[@]}" logcat -d -v threadtime -T "$SINCE"
    else
        capture logcat "${ADB_CMD[@]}" logcat -d -v threadtime -t 5000
    fi
fi

# Keep a focused view for routine bug reports while retaining the complete
# redacted logcat capture above.
if [[ -f "$OUT_DIR/logcat.log" ]]; then
    grep -Ei 'SmartBox|sing-box|libbox|VPN|TUN|AndroidRuntime|FATAL|fdsan|network|connect|disconnect|crash|EPERM|protect' \
        "$OUT_DIR/logcat.log" > "$OUT_DIR/logcat-filtered.log" || true
    chmod 600 "$OUT_DIR/logcat-filtered.log"
fi

# Device properties contain fields such as `crash_type` for unrelated
# hardware daemons.  Restrict signatures to actual logcat captures so those
# properties cannot turn a clean SmartBox run into a false failure.
: > "$OUT_DIR/error-signatures.log"
for file in "$OUT_DIR/logcat.log" "$OUT_DIR/logcat-filtered.log"; do
    [[ -f "$file" ]] || continue
    grep -Ein 'FATAL EXCEPTION|AndroidRuntime|fdsan|panic:|operation[[:space:]]+not[[:space:]]+permitted|DNS[^[:cntrl:]]*EPERM|protect[^[:cntrl:]]*fail|(^|[^[:alnum:]_])crash(ed|ing)?([^[:alnum:]_]|$)' "$file" >> "$OUT_DIR/error-signatures.log" || true
done
sort -u -o "$OUT_DIR/error-signatures.log" "$OUT_DIR/error-signatures.log"
error_count=$(wc -l < "$OUT_DIR/error-signatures.log" | tr -d '[:space:]')
startup_count=$(grep -Eic 'start|started|onCreate|enable' "$OUT_DIR/logcat-filtered.log" 2>/dev/null || true)
stop_count=$(grep -Eic 'stop|stopped|onDestroy|disable' "$OUT_DIR/logcat-filtered.log" 2>/dev/null || true)
network_count=$(grep -Eic 'network|connect|disconnect|link|dns' "$OUT_DIR/logcat-filtered.log" 2>/dev/null || true)
crash_count=$(grep -Eic 'FATAL|AndroidRuntime|fdsan|panic|crash|exception' "$OUT_DIR/logcat-filtered.log" 2>/dev/null || true)

{
    printf '%s\n\n' '# Android diagnostic collection (T006)'
    printf 'Generated: %s\n' "$(date -Is)"
    printf 'Package: %s\n' "$PACKAGE_NAME"
    printf 'Device selector: %s\n\n' "${SERIAL:-auto}"
    printf '| Capture | adb rc | File |\n| --- | ---: | --- |\n'
    while IFS=$'\t' read -r name rc file; do
        [[ -n "$name" ]] || continue
        printf '| `%s` | %s | `%s` |\n' "$name" "$rc" "$(basename "$file")"
    done < "$OUT_DIR/status.tsv"
    printf '\nError signatures: **%s**\n' "$error_count"
    printf 'Capture failures: **%s**\n' "$CAPTURE_FAILED"
    printf 'Category counts: startup=%s, stop=%s, network=%s, crash=%s\n' \
        "$startup_count" "$stop_count" "$network_count" "$crash_count"
    if [[ "$error_count" -eq 0 && "$CAPTURE_FAILED" -eq 0 ]]; then
        printf 'Result: **PASS (no known signatures in captured logs)**\n'
    else
        printf 'Result: **FAIL (inspect capture logs and error-signatures.log)**\n'
    fi
    printf '\nKnown patterns counted: FATAL/AndroidRuntime, fdsan, panic, EPERM, protect failure, crash.\n'
} > "$REPORT"

printf 'REPORT=%s\n' "$REPORT"
if [[ "$error_count" -gt 0 || "$CAPTURE_FAILED" -gt 0 ]]; then exit 1; fi
exit 0
