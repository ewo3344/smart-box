#!/bin/sh
set -eu

usage() {
    printf 'usage: ANDROID_KEYSTORE_PASS=... ANDROID_KEY_ALIAS_PASS=... %s INPUT.apk OUTPUT.apk [KEYSTORE]\n' "$0" >&2
    exit 64
}

[ "$#" -ge 2 ] || usage
input=$1
output=$2
keystore=${3:-${ANDROID_DEVICE_KEYSTORE:-$HOME/.android/smart-box-device.keystore}}
alias=${ANDROID_DEVICE_KEY_ALIAS:-androiddebugkey}
apksigner=${APKSIGNER:-}

if [ -z "$apksigner" ]; then
    for candidate in \
        "${ANDROID_HOME:-}/build-tools/37.0.0/apksigner" \
        "${ANDROID_SDK_ROOT:-}/build-tools/37.0.0/apksigner" \
        /opt/android-sdk/build-tools/37.0.0/apksigner \
        /opt/android-sdk/build-tools/36.0.0/apksigner; do
        if [ -x "$candidate" ]; then
            apksigner=$candidate
            break
        fi
    done
fi

[ -x "$apksigner" ] || { printf 'apksigner not found\n' >&2; exit 1; }
[ -f "$input" ] || { printf 'input APK not found: %s\n' "$input" >&2; exit 1; }
[ -f "$keystore" ] || { printf 'keystore not found: %s\n' "$keystore" >&2; exit 1; }
[ -n "${ANDROID_KEYSTORE_PASS:-}" ] || { printf 'ANDROID_KEYSTORE_PASS is required\n' >&2; exit 1; }
[ -n "${ANDROID_KEY_ALIAS_PASS:-}" ] || { printf 'ANDROID_KEY_ALIAS_PASS is required\n' >&2; exit 1; }

mkdir -p "$(dirname -- "$output")"
ANDROID_KEYSTORE_PASS=$ANDROID_KEYSTORE_PASS \
ANDROID_KEY_ALIAS_PASS=$ANDROID_KEY_ALIAS_PASS \
"$apksigner" sign \
    --ks "$keystore" \
    --ks-key-alias "$alias" \
    --ks-pass env:ANDROID_KEYSTORE_PASS \
    --key-pass env:ANDROID_KEY_ALIAS_PASS \
    --out "$output" \
    "$input"

"$apksigner" verify --verbose "$output" >/dev/null
printf 'SIGNED_APK=%s\n' "$output"
sha256sum "$output"
