#!/bin/sh
# Read-only Raspberry Pi health check. Credentials are supplied by SSH config/key.

set -u

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
host=${RASPBERRY_PI_HOST:-}
user=${RASPBERRY_PI_USER:-e}
port=${RASPBERRY_PI_PORT:-22}
identity=${RASPBERRY_PI_IDENTITY:-}
out=${RASPBERRY_PI_OUT:-"$root/verification/raspberry-pi-$(date +%Y%m%d-%H%M%S)"}
allow_unreachable=0

usage() {
    printf '%s\n' "usage: $0 [--host HOST] [--user USER] [--port PORT] [--identity FILE] [--out DIR] [--allow-unreachable]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; host=$2; shift ;;
        --user) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; user=$2; shift ;;
        --port) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; port=$2; shift ;;
        --identity) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; identity=$2; shift ;;
        --out) [ "$#" -ge 2 ] || { usage >&2; exit 64; }; out=$2; shift ;;
        --allow-unreachable) allow_unreachable=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 64 ;;
    esac
    shift
done

umask 077
mkdir -p "$out"
report="$out/REPORT.md"
raw="$out/remote.txt"

if [ -z "$host" ]; then
    printf '%s\n' 'BLOCKED: set RASPBERRY_PI_HOST or pass --host' > "$report"
    printf '%s\n' "Report: $report"
    [ "$allow_unreachable" -eq 1 ] && exit 0
    exit 2
fi
command -v ssh >/dev/null 2>&1 || {
    printf '%s\n' 'BLOCKED: ssh is not installed' > "$report"
    printf '%s\n' "Report: $report"
    [ "$allow_unreachable" -eq 1 ] && exit 0
    exit 2
}

ssh_args="-o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 -o StrictHostKeyChecking=accept-new -p $port"
if [ -n "$identity" ]; then
    ssh_args="$ssh_args -i $identity"
fi
target="$user@$host"

# Keep the command read-only and return machine-readable key/value lines.
remote='set -u
printf "hostname=%s\\n" "$(hostname 2>/dev/null || true)"
printf "kernel=%s\\n" "$(uname -sr 2>/dev/null || true)"
for unit in smart-box-converter.service smart-box.service smart-box-converter-route-bypass.service; do
  printf "unit_%s=" "${unit%.service}"
  systemctl is-active "$unit" 2>/dev/null || true
done
printf "converter_pid="; pgrep -xo smart-box-converter 2>/dev/null || true
printf "core_pid="; pgrep -xo smart-box-core 2>/dev/null || true
printf "disk_root="; df -P / 2>/dev/null | awk "NR==2 {print \\$5}"
printf "memory="; free -m 2>/dev/null | awk "NR==2 {print \\$3 \"/\" \\$2 \"MiB\"}"
for path in /var/lib/smart-box/profile.json /var/lib/smart-box/cache.db; do
  if [ -f "$path" ]; then
    printf "file_%s=present,size=%s,sha256=%s\\n" "$(basename "$path")" "$(stat -c %s "$path" 2>/dev/null || printf 0)" "$(sha256sum "$path" 2>/dev/null | awk "{print \\$1}")"
  else
    printf "file_%s=missing\\n" "$(basename "$path")"
  fi
done'

if ! sh -c "ssh $ssh_args '$target'" <<EOF >"$raw" 2>&1
$remote
EOF
then
    printf '%s\n' "BLOCKED: SSH check failed for $target (see remote.txt)" > "$report"
    printf '%s\n' "Report: $report"
    [ "$allow_unreachable" -eq 1 ] && exit 0
    exit 2
fi

failed=0
for expected in \
    'unit_smart-box-converter=active' \
    'unit_smart-box=active' \
    'unit_smart-box-converter-route-bypass=active'; do
    if ! grep -Fxq "$expected" "$raw"; then
        failed=$((failed + 1))
    fi
done

{
    printf '%s\n\n' '# Raspberry Pi health verification'
    printf '%s\n\n' "Target: $target:$port"
    printf '%s\n\n' 'The SSH command is read-only; private credentials are not included.'
    printf '%s\n' 'remote output:'
    sed -E 's/(token|password|secret|authorization)[=:][^[:space:]]+/\1=<redacted>/Ig' "$raw"
    if [ "$failed" -eq 0 ]; then
        printf '\nResult: **PASS**\n'
    else
        printf '\nResult: **FAIL** (%s required checks)\n' "$failed"
    fi
} > "$report"

printf '%s\n' "Report: $report"
[ "$failed" -eq 0 ]
