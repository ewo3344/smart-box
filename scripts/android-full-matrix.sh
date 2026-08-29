#!/usr/bin/env bash
# Android T001 device-matrix gate.
#
# The gate is deliberately fail-closed: a missing/ambiguous ADB device is
# reported as BLOCKED and never presented as a successful device test.  The
# script only installs with `adb install -r`; it never uninstalls a package.

set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ANDROID_ROOT="$ROOT/android"
PACKAGE_NAME="io.nekohasekai.sfa.smartbox"
ACTIVITY_NAME="$PACKAGE_NAME/io.nekohasekai.sfa.compose.MainActivity"
ADB_BIN=${ADB:-adb}
RUN_DATE=${SMART_BOX_MATRIX_DATE:-$(date +%Y%m%d)}
OUT_DIR="$ROOT/verification/android-full-matrix-$RUN_DATE"
SERIAL=""
APK=""
KEEP_LOGS=0
POLL_SECONDS=${SMART_BOX_ANDROID_POLL_SECONDS:-45}
TMP_HOME=""
GRADLE_CACHE="${SMART_BOX_GRADLE_USER_HOME:-${GRADLE_USER_HOME:-${HOME:-$ROOT}/.gradle}}"
REPORT=""
RUN_ID=""
DEVICE_STATUS="BLOCKED"
STATIC_STATUS="NOT_RUN"
INSTALL_STATUS="NOT_RUN"
BACKUP_STATUS="NOT_RUN"
START_STATUS="NOT_RUN"
STOP_STATUS="NOT_RUN"
FAILURES=0
BLOCKED=0
MANUAL=0
SCRIPT_STARTED=0
PREEXISTING_ACTIVE=0
UI_LAST=""
PACKAGE_DUMP=""
LOCAL_CERT=""
REMOTE_CERT=""
ADB_CMD=()
APK_TOOLING_STATUS="UNKNOWN"

usage() {
    cat <<'EOF'
usage: scripts/android-full-matrix.sh [options]

Options:
  --serial SERIAL     select one ADB device (required when more than one is attached)
  --apk FILE          validate and install an APK with adb install -r (preserves data)
  --out DIR           write evidence to DIR (default: verification/android-full-matrix-YYYYMMDD)
  --keep-logs         retain private, unsanitized command output beside sanitized logs
  -h, --help          show this help

Environment:
  ADB=PATH                         adb executable (default: adb)
  SMART_BOX_ANDROID_POLL_SECONDS  lifecycle polling timeout (default: 45)
  SMART_BOX_SKIP_STATIC=1         record static gate as MANUAL_REQUIRED (still non-zero)
EOF
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 64
}

[[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || fail "SMART_BOX_MATRIX_DATE must be YYYYMMDD"
[[ "$POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "SMART_BOX_ANDROID_POLL_SECONDS must be a positive integer"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --serial)
            [[ $# -ge 2 ]] || fail "--serial requires a value"
            SERIAL=$2
            shift
            ;;
        --serial=*) SERIAL=${1#*=} ;;
        --apk)
            [[ $# -ge 2 ]] || fail "--apk requires a value"
            APK=$2
            shift
            ;;
        --apk=*) APK=${1#*=} ;;
        --out)
            [[ $# -ge 2 ]] || fail "--out requires a value"
            OUT_DIR=$2
            shift
            ;;
        --out=*) OUT_DIR=${1#*=} ;;
        --keep-logs) KEEP_LOGS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown option: $1" ;;
    esac
    shift
done

umask 077
mkdir -p "$OUT_DIR"
RUN_ID=$(date +%H%M%S)
REPORT="$OUT_DIR/matrix-report-$RUN_ID.txt"
: > "$REPORT"

TMP_HOME=$(mktemp -d "${TMPDIR:-/tmp}/smart-box-android-matrix.XXXXXX")
mkdir -p "$TMP_HOME/.config" "$TMP_HOME/.local/state"

cleanup() {
    local rc=$?
    # Remove only the temporary host home.  Device state is cleaned explicitly
    # by stop_flow; no package uninstall or data wipe is performed here.
    if [[ -n "$TMP_HOME" && -d "$TMP_HOME" ]]; then
        rm -rf -- "$TMP_HOME"
    fi
    return "$rc"
}
trap cleanup EXIT

mark_failure() {
    FAILURES=$((FAILURES + 1))
}

mark_blocked() {
    BLOCKED=$((BLOCKED + 1))
}

mark_manual() {
    MANUAL=$((MANUAL + 1))
}

report_line() {
    printf '%s\n' "$*" >> "$REPORT"
}

display() {
    printf '%s\n' "$*"
    report_line "$*"
}

redact_file() {
    local input=$1
    local output=$2
    # Logcat can contain subscription URLs, bearer tokens, cookies and local
    # paths.  Keep diagnostics useful while removing credential-bearing data.
    if [[ ! -f "$input" ]]; then
        : > "$output"
        return 0
    fi
    if ! LC_ALL=C sed -E \
        -e 's#(Authorization:[[:space:]]*Bearer[[:space:]]+)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#((password|passwd|token|secret|api[_-]?key|cookie|authorization)[[:space:]]*[=:][[:space:]]*)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#(https?://[^[:space:]?]+)[?][^[:space:]]+#\1?<redacted>#g' \
        -e 's#((ss|ssr|vmess|vless|trojan)://)[^[:space:]]+#\1<redacted>#Ig' \
        -e 's#/data/(data|user)/[^[:space:]]+#/data/<redacted>#g' \
        -e 's#/home/[^[:space:]]+#/home/<redacted>#g' \
        "$input" > "$output"; then
        printf '%s\n' '<redaction failed; inspect the raw file only when --keep-logs was explicitly requested>' > "$output"
    fi
    chmod 600 "$output"
}

command_repr() {
    local arg
    printf '$'
    for arg in "$@"; do
        printf ' %q' "$arg"
    done
}

# Capture a command without allowing a failed diagnostic command to abort the
# gate.  CAPTURE_RC and CAPTURE_LAST are set for the caller.
CAPTURE_RC=0
CAPTURE_LAST=""
capture() {
    local name=$1
    shift
    local safe=${name//[^A-Za-z0-9_.-]/_}
    local raw="$OUT_DIR/${safe}-${RUN_ID}.raw"
    local clean="$OUT_DIR/${safe}-${RUN_ID}.log"
    local rc
    if "$@" > "$raw" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    redact_file "$raw" "$clean"
    if [[ "$KEEP_LOGS" -eq 0 ]]; then
        rm -f -- "$raw"
    else
        chmod 600 "$raw"
    fi
    CAPTURE_RC=$rc
    CAPTURE_LAST=$clean
    report_line "CAPTURE=$name RC=$rc FILE=$clean"
    report_line "COMMAND=$(command_repr "$@")"
    {
        printf '%s\n' "OUTPUT_BEGIN=$name"
        sed -n '1,12p' "$clean"
        printf '%s\n' "OUTPUT_END=$name"
    } >> "$REPORT"
    printf '  %-28s rc=%s (%s)\n' "$name" "$rc" "$clean"
    return 0
}

set_adb_command() {
    ADB_CMD=("$ADB_BIN")
    if [[ -n "$SERIAL" ]]; then
        ADB_CMD+=( -s "$SERIAL" )
    fi
}

adb_call() {
    "${ADB_CMD[@]}" "$@"
}

capture_adb() {
    local name=$1
    shift
    capture "$name" adb_call "$@"
}

capture_adb_shell() {
    local name=$1
    shift
    capture "$name" adb_call shell "$@"
}

run_static_gate() {
    local before after gradlew
    if [[ "${SMART_BOX_SKIP_STATIC:-0}" == "1" ]]; then
        STATIC_STATUS=MANUAL_REQUIRED
        mark_manual
        report_line "STATIC_GATE=MANUAL_REQUIRED (SMART_BOX_SKIP_STATIC=1)"
        display "STATIC_GATE=MANUAL_REQUIRED"
        return
    fi

    gradlew="$ANDROID_ROOT/gradlew"
    if [[ ! -x "$gradlew" ]]; then
        STATIC_STATUS=FAIL
        mark_failure
        report_line "STATIC_GATE=FAIL (missing executable $gradlew)"
        display "STATIC_GATE=FAIL (missing gradlew)"
        return
    fi

    if [[ -d "$ANDROID_ROOT/app/src/androidTest" ]]; then
        report_line "INSTRUMENTATION_TESTS=PRESENT"
    else
        report_line "INSTRUMENTATION_TESTS=NOT_PRESENT (device checks use dynamic UI smoke flow)"
    fi
    capture "android-matrix-shell-syntax" bash -n "$ROOT/scripts/android-full-matrix.sh"
    if [[ "$CAPTURE_RC" -ne 0 ]]; then
        STATIC_STATUS=FAIL
        mark_failure
        report_line "STATIC_GATE=FAIL (matrix shell syntax)"
        display "STATIC_GATE=FAIL (matrix syntax)"
        return
    fi

    before=$(git -C "$ANDROID_ROOT" status --porcelain=v1 2>/dev/null || true)
    capture "android-jvm-unit" env \
        HOME="$TMP_HOME" \
        XDG_CONFIG_HOME="$TMP_HOME/.config" \
        XDG_STATE_HOME="$TMP_HOME/.local/state" \
        GRADLE_USER_HOME="$GRADLE_CACHE" \
        bash -c 'cd "$1" && exec ./gradlew :app:testOtherDebugUnitTest --no-daemon --console=plain' \
        _ "$ANDROID_ROOT"
    if [[ "$CAPTURE_RC" -ne 0 ]]; then
        STATIC_STATUS=FAIL
        mark_failure
        report_line "STATIC_GATE=FAIL (JVM task exit $CAPTURE_RC)"
    else
        STATIC_STATUS=PASS
        report_line "STATIC_GATE=PASS"
    fi
    after=$(git -C "$ANDROID_ROOT" status --porcelain=v1 2>/dev/null || true)
    if [[ "$before" != "$after" ]]; then
        STATIC_STATUS=FAIL
        mark_failure
        report_line "ANDROID_WORKTREE=CHANGED"
        printf 'ANDROID_WORKTREE=CHANGED (the matrix does not edit the Android subrepo)\n'
    else
        report_line "ANDROID_WORKTREE=UNCHANGED"
    fi
    display "STATIC_GATE=$STATIC_STATUS"
}

discover_device() {
    local devices_output
    local -a ready_devices=()
    if ! command -v "$ADB_BIN" >/dev/null 2>&1; then
        DEVICE_STATUS=BLOCKED
        BLOCKED=$((BLOCKED + 1))
        report_line "DEVICE=BLOCKED (adb executable not found: $ADB_BIN)"
        display "DEVICE=BLOCKED (adb not found)"
        return 1
    fi

    if ! devices_output=$("$ADB_BIN" devices -l 2>&1); then
        DEVICE_STATUS=BLOCKED
        mark_blocked
        report_line "DEVICE=BLOCKED (adb devices failed)"
        local devices_error="$OUT_DIR/adb-devices-error-$RUN_ID.raw"
        local devices_error_clean="$OUT_DIR/adb-devices-error-$RUN_ID.log"
        printf '%s\n' "$devices_output" > "$devices_error"
        redact_file "$devices_error" "$devices_error_clean"
        rm -f -- "$devices_error"
        report_line "ADB_DEVICES_ERROR_FILE=$devices_error_clean"
        display "DEVICE=BLOCKED (adb devices failed)"
        return 1
    fi
    capture "adb-devices" "$ADB_BIN" devices -l
    report_line "ADB_DEVICES_BEGIN"
    sed -n '1,40p' "$CAPTURE_LAST" >> "$REPORT"
    report_line "ADB_DEVICES_END"

    while IFS=$'\t' read -r serial status; do
        [[ -n "$serial" ]] || continue
        [[ "$status" == "device" ]] || continue
        ready_devices+=("$serial")
    done < <(printf '%s\n' "$devices_output" | awk 'NR > 1 && NF >= 2 {print $1 "\t" $2}')

    if [[ -n "$SERIAL" ]]; then
        local selected_status
        selected_status=$(printf '%s\n' "$devices_output" | awk -v wanted="$SERIAL" '$1 == wanted {print $2; exit}')
        if [[ "$selected_status" != "device" ]]; then
            DEVICE_STATUS=BLOCKED
            mark_blocked
            report_line "DEVICE=BLOCKED (serial $SERIAL is ${selected_status:-missing})"
            display "DEVICE=BLOCKED (serial $SERIAL unavailable)"
            return 1
        fi
    elif [[ "${#ready_devices[@]}" -eq 1 ]]; then
        SERIAL=${ready_devices[0]}
    elif [[ "${#ready_devices[@]}" -eq 0 ]]; then
        DEVICE_STATUS=BLOCKED
        mark_blocked
        report_line "DEVICE=BLOCKED (no authorized device)"
        display "DEVICE=BLOCKED (no authorized ADB device)"
        return 1
    else
        DEVICE_STATUS=BLOCKED
        mark_blocked
        report_line "DEVICE=BLOCKED (multiple devices; pass --serial)"
        display "DEVICE=BLOCKED (multiple devices; pass --serial)"
        return 1
    fi

    set_adb_command
    DEVICE_STATUS=READY
    report_line "DEVICE=READY"
    report_line "DEVICE_SERIAL=$SERIAL"
    display "DEVICE=READY serial=$SERIAL"
    return 0
}

find_tool() {
    local requested=$1
    shift
    if [[ -n "$requested" && -x "$requested" ]]; then
        printf '%s\n' "$requested"
        return 0
    fi
    local candidate
    for candidate in "$@"; do
        if [[ -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    command -v "${1##*/}" 2>/dev/null || true
}

apk_package_name() {
    local analyzer aapt output
    analyzer=$(find_tool "${APKANALYZER:-}" \
        "${ANDROID_HOME:-}/cmdline-tools/latest/bin/apkanalyzer" \
        "${ANDROID_SDK_ROOT:-}/cmdline-tools/latest/bin/apkanalyzer" \
        "/opt/android-sdk/cmdline-tools/latest/bin/apkanalyzer")
    if [[ -n "$analyzer" && -x "$analyzer" ]]; then
        APK_TOOLING_STATUS="AVAILABLE"
        output=$("$analyzer" manifest application-id "$APK" 2>/dev/null || true)
        output=${output##*$'\n'}
        [[ -n "$output" ]] && { printf '%s\n' "$output"; return 0; }
    fi
    aapt=$(find_tool "${AAPT:-}" \
        "${ANDROID_HOME:-}/build-tools/37.0.0/aapt" \
        "${ANDROID_SDK_ROOT:-}/build-tools/37.0.0/aapt" \
        "/opt/android-sdk/build-tools/37.0.0/aapt")
    if [[ -n "$aapt" && -x "$aapt" ]]; then
        APK_TOOLING_STATUS="AVAILABLE"
        output=$("$aapt" dump badging "$APK" 2>/dev/null || true)
        printf '%s\n' "$output" | sed -n "s/^package: name='\([^']*\)'.*/\1/p" | head -n 1 || true
        return 0
    fi
    APK_TOOLING_STATUS="UNAVAILABLE"
    return 1
}

normalise_digest() {
    printf '%s' "$1" | tr -d ':[:space:]' | tr '[:upper:]' '[:lower:]'
}

apk_certificate_digest() {
    local signer output tool
    tool=$(find_tool "${APKSIGNER:-}" \
        "${ANDROID_HOME:-}/build-tools/37.0.0/apksigner" \
        "${ANDROID_SDK_ROOT:-}/build-tools/37.0.0/apksigner" \
        "/opt/android-sdk/build-tools/37.0.0/apksigner")
    [[ -n "$tool" && -x "$tool" ]] || return 1
    output=$("$tool" verify --print-certs "$APK" 2>/dev/null || true)
    signer=$(printf '%s\n' "$output" | sed -n -E \
        's/.*SHA-256 digest:[[:space:]]*([0-9A-Fa-f:]+).*/\1/p' | head -n 1 || true)
    [[ -n "$signer" ]] || return 1
    normalise_digest "$signer"
}

remote_certificate_digest() {
    local file=$1
    local value
    value=$(grep -Eio 'sha-?256[^0-9a-f]*([0-9a-f]{2}:?){32}' "$file" 2>/dev/null | head -n 1 || true)
    if [[ -z "$value" ]]; then
        value=$(grep -Eio '([0-9a-f]{2}:){31}[0-9a-f]{2}' "$file" 2>/dev/null | head -n 1 || true)
    fi
    [[ -n "$value" ]] || return 1
    value=$(printf '%s' "$value" | grep -Eo '([0-9A-Fa-f]{2}:?){32}' | head -n 1 || true)
    [[ -n "$value" ]] || return 1
    normalise_digest "$value"
}

package_installed() {
    local output
    output=$(adb_call shell pm path "$PACKAGE_NAME" 2>/dev/null || true)
    [[ "$output" == *"package:"* ]]
}

validate_and_install_apk() {
    [[ -n "$APK" ]] || {
        if package_installed; then
            INSTALL_STATUS=EXISTING
            BACKUP_STATUS=NOT_RUN
            report_line "INSTALL=EXISTING (no --apk supplied)"
            display "INSTALL=EXISTING (no --apk)"
        else
            INSTALL_STATUS=MANUAL_REQUIRED
            mark_manual
            report_line "INSTALL=MANUAL_REQUIRED (package is not installed; pass --apk)"
            display "INSTALL=MANUAL_REQUIRED (pass --apk)"
        fi
        return
    }
    if [[ ! -f "$APK" ]]; then
        INSTALL_STATUS=FAIL
        mark_failure
        report_line "INSTALL=FAIL (APK not found: $APK)"
        display "INSTALL=FAIL (APK not found)"
        return
    fi

    local local_package
    local_package=$(apk_package_name || true)
    if [[ -z "$local_package" && "$APK_TOOLING_STATUS" == "UNAVAILABLE" ]]; then
        INSTALL_STATUS=MANUAL_REQUIRED
        mark_manual
        report_line "INSTALL=MANUAL_REQUIRED (apkanalyzer/aapt unavailable; no install attempted)"
        display "INSTALL=MANUAL_REQUIRED (APK tooling unavailable)"
        return
    fi
    if [[ "$local_package" != "$PACKAGE_NAME" ]]; then
        INSTALL_STATUS=FAIL
        mark_failure
        report_line "INSTALL=FAIL (APK package=${local_package:-unknown}, expected=$PACKAGE_NAME)"
        display "INSTALL=FAIL (APK package mismatch)"
        return
    fi
    report_line "APK_PACKAGE=$local_package"
    report_line "APK_SHA256=$(sha256sum "$APK" | awk '{print $1}')"

    if [[ -n "$PACKAGE_DUMP" && -s "$PACKAGE_DUMP" ]]; then
        LOCAL_CERT=$(apk_certificate_digest || true)
        REMOTE_CERT=$(remote_certificate_digest "$PACKAGE_DUMP" || true)
        report_line "APK_CERT_SHA256=${LOCAL_CERT:-unknown}"
        report_line "DEVICE_CERT_SHA256=${REMOTE_CERT:-unknown}"
        if [[ -n "$LOCAL_CERT" && -n "$REMOTE_CERT" && "$LOCAL_CERT" != "$REMOTE_CERT" ]]; then
            INSTALL_STATUS=FAIL
            mark_failure
            report_line "INSTALL=FAIL (signature mismatch; no uninstall attempted)"
            display "INSTALL=FAIL (signature mismatch; no uninstall)"
            return
        elif package_installed && [[ -z "$REMOTE_CERT" ]]; then
            INSTALL_STATUS=MANUAL_REQUIRED
            mark_manual
            report_line "INSTALL=MANUAL_REQUIRED (could not establish installed signature; no replacement attempted)"
            display "INSTALL=MANUAL_REQUIRED (signature unknown)"
            return
        fi
    fi

    if package_installed; then
        backup_app_data
    else
        BACKUP_STATUS=NOT_APPLICABLE
        report_line "BACKUP=NOT_APPLICABLE (package not installed before first install)"
    fi

    capture "apk-install" adb_call install -r "$APK"
    if [[ "$CAPTURE_RC" -eq 0 ]]; then
        INSTALL_STATUS=PASS
        report_line "INSTALL=PASS (adb install -r; data preserved)"
        display "INSTALL=PASS (adb install -r)"
    else
        INSTALL_STATUS=FAIL
        mark_failure
        report_line "INSTALL=FAIL (adb install -r exit $CAPTURE_RC; no uninstall attempted)"
        display "INSTALL=FAIL (adb install -r)"
    fi
}

backup_app_data() {
    local backup="$OUT_DIR/app-data-$RUN_ID.tar.gz"
    local error_file="$OUT_DIR/app-data-$RUN_ID.error"
    local rc
    # run-as is available for debuggable builds.  A release build may reject
    # it; retain that fact as a manual requirement rather than fabricating a
    # successful backup.
    if adb_call exec-out run-as "$PACKAGE_NAME" sh -c \
        'cd /data/data/io.nekohasekai.sfa.smartbox && tar -cf - .' \
        2>"$error_file" | gzip -c > "$backup"; then
        rc=0
    else
        rc=$?
    fi
    chmod 600 "$backup" "$error_file" 2>/dev/null || true
    if [[ "$rc" -eq 0 && -s "$backup" ]]; then
        BACKUP_STATUS=PASS
        rm -f -- "$error_file"
        report_line "BACKUP=PASS FILE=$backup (mode 600)"
        display "BACKUP=PASS"
    else
        BACKUP_STATUS=MANUAL_REQUIRED
        mark_manual
        rm -f -- "$backup"
        redact_file "$error_file" "$OUT_DIR/app-data-$RUN_ID.error.log"
        rm -f -- "$error_file"
        report_line "BACKUP=MANUAL_REQUIRED (run-as backup unavailable; no data was deleted)"
        display "BACKUP=MANUAL_REQUIRED"
    fi
}

ui_dump() {
    local name=$1
    local raw="$OUT_DIR/${name}-${RUN_ID}.xml.raw"
    local clean="$OUT_DIR/${name}-${RUN_ID}.xml"
    local rc
    if adb_call shell uiautomator dump /sdcard/smart-box-matrix-window.xml >/dev/null 2>&1 \
        && adb_call exec-out cat /sdcard/smart-box-matrix-window.xml > "$raw" 2>/dev/null; then
        rc=0
    else
        rc=$?
    fi
    if [[ "$rc" -eq 0 && -s "$raw" ]]; then
        redact_file "$raw" "$clean"
        [[ "$KEEP_LOGS" -eq 1 ]] || rm -f -- "$raw"
        UI_LAST="$clean"
    else
        : > "$clean"
        rm -f -- "$raw"
    fi
    report_line "UI_DUMP=$name RC=$rc FILE=$clean"
    printf '  %-28s rc=%s (%s)\n' "ui-$name" "$rc" "$clean"
    return 0
}

ui_bounds_for_mode() {
    local mode=$1
    [[ -n "$UI_LAST" && -s "$UI_LAST" ]] || return 1
    python3 - "$UI_LAST" "$mode" <<'PY'
import re
import sys
import xml.etree.ElementTree as ET

path, mode = sys.argv[1:]
patterns = {
    "start": ("start", "启\u52a8", "啟\u52d5", "начать", "시작"),
    "stop": ("stop", "停止", "останов", "중지"),
}
try:
    root = ET.parse(path).getroot()
except (OSError, ET.ParseError):
    raise SystemExit(1)

items = []
for node in root.iter():
    text = (node.attrib.get("text") or "").strip()
    desc = (node.attrib.get("content-desc") or "").strip()
    value = " ".join((text, desc)).casefold()
    if not value:
        continue
    score = 0
    for pattern in patterns.get(mode, ()):
        p = pattern.casefold()
        if value == p:
            score = max(score, 100)
        elif p in value:
            score = max(score, 50)
    if not score:
        continue
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
    if not match:
        continue
    x1, y1, x2, y2 = map(int, match.groups())
    if x2 <= x1 or y2 <= y1:
        continue
    if node.attrib.get("enabled", "true") != "true":
        continue
    # Prefer a clickable exact match, then the largest exact match.
    clickable = node.attrib.get("clickable", "false") == "true"
    items.append((score + (10 if clickable else 0), (x2 - x1) * (y2 - y1), (x1 + x2) // 2, (y1 + y2) // 2))

if items:
    _, _, x, y = sorted(items, reverse=True)[0]
    print(f"{x} {y}")
    raise SystemExit(0)
raise SystemExit(1)
PY
}

ui_click_mode() {
    local mode=$1
    local bounds x y
    bounds=$(ui_bounds_for_mode "$mode" || true)
    if [[ ! "$bounds" =~ ^[0-9]+[[:space:]][0-9]+$ ]]; then
        return 1
    fi
    IFS=' ' read -r x y <<< "$bounds"
    adb_call shell input tap "$x" "$y" >/dev/null 2>&1
}

runtime_snapshot() {
    local name=$1
    capture_adb_shell "$name-vpn" dumpsys vpn
    local vpn_file=$CAPTURE_LAST
    capture_adb_shell "$name-services" dumpsys activity services "$PACKAGE_NAME"
    local service_file=$CAPTURE_LAST
    capture_adb_shell "$name-interfaces" ip -o addr show
    local interface_file=$CAPTURE_LAST
    capture_adb_shell "$name-processes" ps -A
    local process_file=$CAPTURE_LAST
    LAST_VPN_FILE=$vpn_file
    LAST_SERVICE_FILE=$service_file
    LAST_INTERFACE_FILE=$interface_file
    LAST_PROCESS_FILE=$process_file
}

LAST_VPN_FILE=""
LAST_SERVICE_FILE=""
LAST_INTERFACE_FILE=""
LAST_PROCESS_FILE=""

runtime_active_from_files() {
    local package_seen=0
    local target_package='io\.nekohasekai\.sfa\.smartbox|io\.nekohasekai\.sfa'
    if [[ -s "$LAST_VPN_FILE" ]] \
        && grep -Eqi "$target_package" "$LAST_VPN_FILE" \
        && grep -Eqi 'established|connected|running|active|mInterface[=:][[:space:]]*[^[:space:]]*tun|tun[0-9]' "$LAST_VPN_FILE"; then
        package_seen=1
    fi
    if [[ -s "$LAST_SERVICE_FILE" ]] \
        && grep -Eqi "$target_package" "$LAST_SERVICE_FILE" \
        && grep -Eqi 'VPNService|ProxyService' "$LAST_SERVICE_FILE" \
        && grep -Eqi 'started|running|ServiceRecord|isRunning=true|bound=true' "$LAST_SERVICE_FILE"; then
        package_seen=1
    fi
    if [[ -s "$LAST_PROCESS_FILE" ]] \
        && grep -Eqi "$target_package" "$LAST_PROCESS_FILE" \
        && grep -Eqi 'VPNService|ProxyService' "$LAST_PROCESS_FILE"; then
        package_seen=1
    fi
    # `ip addr` cannot identify which VPN owns a tun interface.  Never use a
    # generic tun0/tun1 as proof of SmartBox activity, otherwise another VPN
    # can be stopped or make the lifecycle gate report a false positive.
    [[ "$package_seen" -eq 1 ]]
}

wait_runtime_state() {
    local wanted=$1
    local elapsed=0
    while (( elapsed < POLL_SECONDS )); do
        runtime_snapshot "poll-$wanted-$elapsed"
        if [[ "$wanted" == "active" ]] && runtime_active_from_files; then
            return 0
        fi
        if [[ "$wanted" == "stopped" ]] && ! runtime_active_from_files; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

check_error_signatures() {
    local found=0
    local file
    # Scope the scan to this invocation.  A date directory may contain prior
    # runs; an old failure must not poison a later clean run.
    for file in "$OUT_DIR"/*-"$RUN_ID".log "$OUT_DIR"/*-"$RUN_ID".xml "$OUT_DIR"/*-"$RUN_ID".*.log; do
        [[ -f "$file" ]] || continue
        if grep -Eqi 'FATAL EXCEPTION|panic:|fdsan|operation not permitted|DNS.*EPERM|protect.*fail' "$file"; then
            report_line "ERROR_SIGNATURE=$file"
            found=1
        fi
    done
    if [[ "$found" -eq 1 ]]; then
        mark_failure
        display "ERROR_SIGNATURE=FOUND"
    else
        report_line "ERROR_SIGNATURE=NONE"
        display "ERROR_SIGNATURE=NONE"
    fi
}

stop_via_ui() {
    ui_dump "before-stop"
    if ui_click_mode stop; then
        report_line "STOP_BUTTON=CLICKED_DYNAMIC_UI"
        return 0
    fi
    return 1
}

run_device_lifecycle() {
    local initially_active=0
    runtime_snapshot initial
    if runtime_active_from_files; then
        initially_active=1
        PREEXISTING_ACTIVE=1
        report_line "PREEXISTING_RUNTIME=ACTIVE"
        if stop_via_ui && wait_runtime_state stopped; then
            report_line "PREEXISTING_RUNTIME=STOPPED_FOR_TEST"
        else
            START_STATUS=MANUAL_REQUIRED
            STOP_STATUS=MANUAL_REQUIRED
            mark_manual
            report_line "LIFECYCLE=MANUAL_REQUIRED (could not stop pre-existing runtime through UI)"
            return
        fi
    else
        report_line "PREEXISTING_RUNTIME=STOPPED"
    fi

    capture "launch-activity" adb_call shell am start -W -n "$ACTIVITY_NAME"
    if [[ "$CAPTURE_RC" -ne 0 ]]; then
        START_STATUS=FAIL
        mark_failure
        report_line "START=FAIL (activity launch exit $CAPTURE_RC)"
    else
        sleep 1
        ui_dump "before-start"
        if ui_click_mode start; then
            SCRIPT_STARTED=1
            report_line "START_BUTTON=CLICKED_DYNAMIC_UI"
            capture_adb "logcat-start" logcat -d -v threadtime -t 2000
            if wait_runtime_state active; then
                START_STATUS=PASS
                report_line "START=PASS (service/VPN/TUN observed)"
                display "START=PASS"
            else
                START_STATUS=MANUAL_REQUIRED
                mark_manual
                report_line "START=MANUAL_REQUIRED (button clicked but runtime not observed; inspect VPN consent/profile)"
                display "START=MANUAL_REQUIRED"
            fi
            ui_dump "after-start"
        else
            START_STATUS=MANUAL_REQUIRED
            mark_manual
            report_line "START=MANUAL_REQUIRED (dynamic Start control unavailable; no fixed coordinate used)"
            display "START=MANUAL_REQUIRED"
        fi
    fi

    if [[ "$START_STATUS" == "PASS" ]]; then
        capture_adb_shell "running-connectivity" dumpsys connectivity
        capture_adb_shell "running-package" dumpsys package "$PACKAGE_NAME"
        ui_dump "running"
        if stop_via_ui; then
            capture_adb "logcat-stop" logcat -d -v threadtime -t 2000
            if wait_runtime_state stopped; then
                STOP_STATUS=PASS
                report_line "STOP=PASS (service/VPN/TUN disappeared)"
                display "STOP=PASS"
            else
                STOP_STATUS=FAIL
                mark_failure
                report_line "STOP=FAIL (runtime remained active after UI stop)"
                display "STOP=FAIL"
            fi
        else
            STOP_STATUS=MANUAL_REQUIRED
            mark_manual
            report_line "STOP=MANUAL_REQUIRED (dynamic Stop control unavailable)"
            display "STOP=MANUAL_REQUIRED"
        fi
        ui_dump "after-stop"
    else
        STOP_STATUS=NOT_RUN
    fi

    if [[ "$SCRIPT_STARTED" -eq 1 && "$PREEXISTING_ACTIVE" -eq 0 && "$STOP_STATUS" != "PASS" ]]; then
        # Do not leave a newly started VPN behind after a failed UI stop.  This
        # is a cleanup action only; it never removes the application or data.
        capture "cleanup-force-stop" adb_call shell am force-stop "$PACKAGE_NAME"
        report_line "CLEANUP_FORCE_STOP_RC=$CAPTURE_RC"
        if [[ "$CAPTURE_RC" -eq 0 ]]; then
            wait_runtime_state stopped || true
        fi
    fi

    # Restore a runtime that was active before the test, using the same dynamic
    # UI path.  This avoids leaving a user's existing connection unexpectedly
    # stopped while still exposing a failed restoration in the report.
    if [[ "$initially_active" -eq 1 ]]; then
        ui_dump "restore-before-start"
        if ui_click_mode start && wait_runtime_state active; then
            report_line "PREEXISTING_RUNTIME=RESTORED"
        else
            mark_failure
            report_line "PREEXISTING_RUNTIME=RESTORE_FAILED"
        fi
    fi
}

record_manual_matrix_items() {
    # These checks require account/profile-specific actions and cannot be
    # truthfully inferred from adb process state.  The report is explicit so a
    # human can complete them on the same captured device run.
    local item
    for item in \
        DOMAIN_ALLOWLIST_DIRECT \
        DOMAIN_BLOCKLIST_SMART \
        DOMAIN_IDN_WILDCARD \
        DOMAIN_CONFLICT_DETECTION \
        REGION_MANUAL_SELECTION \
        AI_FALLBACK_EXCLUDES_HONG_KONG \
        TELEGRAM_FALLBACK_INDEPENDENT_PROBE \
        NODE_SCORE_RESTORE_AFTER_RESTART \
        NODE_SCORE_FAILURE_PENALTY \
        NODE_SCORE_SEVEN_DAY_DECAY \
        WIFI_MOBILE_NETWORK_SWITCH \
        DOUYIN_COMMENT_POST \
        TELEGRAM_SEND_RECEIVE \
        NOTIFICATION_PERMISSION \
        VPN_CONSENT; do
        report_line "$item=MANUAL_REQUIRED"
    done
    mark_manual
    display "MANUAL_MATRIX=REQUIRED (15 account/network/UI checks)"
}

write_summary() {
    local result exit_status
    if [[ "$FAILURES" -gt 0 ]]; then
        result=FAIL
        exit_status=1
    elif [[ "$BLOCKED" -gt 0 ]]; then
        result=BLOCKED
        exit_status=2
    elif [[ "$MANUAL" -gt 0 ]]; then
        result=MANUAL_REQUIRED
        exit_status=2
    else
        result=PASS
        exit_status=0
    fi
    report_line ""
    report_line "RESULT=$result"
    report_line "FAILURES=$FAILURES"
    report_line "BLOCKED_COUNT=$BLOCKED"
    report_line "MANUAL_REQUIRED_COUNT=$MANUAL"
    report_line "EXIT_STATUS=$exit_status"
    {
        printf '\nRESULT=%s FAILURES=%s BLOCKED=%s MANUAL_REQUIRED=%s\n' "$result" "$FAILURES" "$BLOCKED" "$MANUAL"
        printf 'REPORT=%s\n' "$REPORT"
    }
    exit "$exit_status"
}

{
    printf '%s\n' '# Android full device matrix (T001)'
    printf 'RUN_DATE=%s\n' "$RUN_DATE"
    printf 'RUN_ID=%s\n' "$RUN_ID"
    printf 'PACKAGE=%s\n' "$PACKAGE_NAME"
    printf 'ACTIVITY=%s\n' "$ACTIVITY_NAME"
    printf 'APK=%s\n' "${APK:-<none>}"
    printf 'OUT_DIR=%s\n' "$OUT_DIR"
    printf 'TEMP_HOME_CLEANUP=ON_EXIT\n'
} >> "$REPORT"

display "REPORT=$REPORT"
run_static_gate

if ! discover_device; then
    # Static/JVM evidence is still useful without a phone, but the device gate
    # remains blocked and exits non-zero by design.
    check_error_signatures
    write_summary
fi

capture_adb_shell "device-props" getprop
capture_adb_shell "package-before" dumpsys package "$PACKAGE_NAME"
PACKAGE_DUMP=$CAPTURE_LAST
capture_adb_shell "vpn-before" dumpsys vpn
capture_adb_shell "connectivity-before" dumpsys connectivity
capture_adb_shell "interfaces-before" ip -o addr show
capture_adb_shell "processes-before" ps -A
capture_adb "logcat-before" logcat -d -v threadtime -t 2000

validate_and_install_apk
if [[ "$INSTALL_STATUS" == "PASS" || "$INSTALL_STATUS" == "EXISTING" ]]; then
    run_device_lifecycle
else
    START_STATUS=NOT_RUN
    STOP_STATUS=NOT_RUN
    report_line "LIFECYCLE=NOT_RUN (install prerequisite status=$INSTALL_STATUS)"
    display "LIFECYCLE=NOT_RUN (install prerequisite)"
fi
capture_adb_shell "package-after" dumpsys package "$PACKAGE_NAME"
capture_adb_shell "vpn-after" dumpsys vpn
capture_adb_shell "connectivity-after" dumpsys connectivity
capture_adb_shell "interfaces-after" ip -o addr show
capture_adb_shell "processes-after" ps -A
capture_adb "logcat-after" logcat -d -v threadtime -t 2000
check_error_signatures
record_manual_matrix_items
write_summary
