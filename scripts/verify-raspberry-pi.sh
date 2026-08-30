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

invalid_argument() {
    usage >&2
    printf '%s\n' "ERROR: $*" >&2
    exit 64
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

# Keep every SSH setting as an argument rather than interpolating a command
# string.  Host/user/port are validated so an option-looking value cannot be
# mistaken for another ssh option.  A raw IPv6 address is accepted and wrapped
# in brackets when constructing the user@host target.
host_for_validation=$host
case "$host_for_validation" in
    \[*\])
        host_for_validation=${host_for_validation#\[}
        host_for_validation=${host_for_validation%\]}
        ;;
esac
if [ -z "$host" ]; then
    :
elif [ -z "$host_for_validation" ] ||
     ! printf '%s\n' "$host_for_validation" | LC_ALL=C grep -Eq '^[A-Za-z0-9._:%-]+$' ||
     [ "${host_for_validation#-}" != "$host_for_validation" ]; then
    invalid_argument "invalid host"
fi
if ! printf '%s\n' "$user" | LC_ALL=C grep -Eq '^[A-Za-z0-9._-]+$' ||
   [ "${user#-}" != "$user" ]; then
    invalid_argument "invalid user"
fi
if ! printf '%s\n' "$port" | LC_ALL=C grep -Eq '^[0-9]{1,5}$' ||
   [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    invalid_argument "invalid port: $port (expected 1-65535)"
fi
if [ -n "$identity" ] && [ ! -f "$identity" ]; then
    invalid_argument "identity file does not exist: $identity"
fi

umask 077
mkdir -p "$out"
report="$out/REPORT.md"
raw="$out/remote.txt"
known_hosts="$out/known_hosts"

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

case "$host" in
    \[*\]) target="$user@$host" ;;
    *:*) target="$user@[$host]" ;;
    *) target="$user@$host" ;;
esac

run_ssh() {
    if [ -n "$identity" ]; then
        ssh -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 \
            -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$known_hosts" \
            -p "$port" -i "$identity" -- "$target"
    else
        ssh -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 \
            -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$known_hosts" \
            -p "$port" -- "$target"
    fi
}

# Keep the command read-only and return machine-readable key/value lines.
remote='set -u
printf "hostname=%s\\n" "$(hostname 2>/dev/null || true)"
printf "kernel=%s\\n" "$(uname -sr 2>/dev/null || true)"
for unit in smart-box-converter.service smart-box.service smart-box-converter-route-bypass.service; do
  printf "unit_%s=" "${unit%.service}"
  systemctl is-active "$unit" 2>/dev/null || true
done
printf "converter_pid=%s\\n" "$(pgrep -xo smart-box-converter 2>/dev/null || true)"
printf "core_pid=%s\\n" "$(pgrep -xo smart-box-core 2>/dev/null || true)"
printf "disk_root=%s\\n" "$(df -P / 2>/dev/null | awk "NR==2 {print \$5}")"
printf "memory=%s\\n" "$(free -m 2>/dev/null | awk "NR==2 {print \$3 \"/\" \$2 \"MiB\"}")"
printf "ip_rule_8998=%s\\n" "$({ ip rule show; ip -6 rule show; } 2>/dev/null | grep -c 8998 || true)"
printf "ip_rule_8999=%s\\n" "$({ ip rule show; ip -6 rule show; } 2>/dev/null | grep -c 8999 || true)"
printf "ip_rule_lines\\n"
{ ip rule show; ip -6 rule show; } 2>/dev/null | grep -E "8998|8999" || true
for path in /var/lib/smart-box/profile.json /var/lib/smart-box/cache.db; do
  if [ -f "$path" ]; then
    printf "file_%s=present,size=%s,sha256=%s\\n" "$(basename "$path")" "$(stat -c %s "$path" 2>/dev/null || printf 0)" "$(sha256sum "$path" 2>/dev/null | awk "{print \$1}")"
  elif sudo -n test -f "$path" 2>/dev/null; then
    printf "file_%s=present,size=%s,sha256=%s\\n" "$(basename "$path")" "$(sudo -n stat -c %s "$path" 2>/dev/null || printf 0)" "$(sudo -n sha256sum "$path" 2>/dev/null | awk "{print \$1}")"
  else
    printf "file_%s=missing\\n" "$(basename "$path")"
  fi
done'

if ! run_ssh <<EOF >"$raw" 2>&1
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
if ! grep -Eq '^ip_rule_8998=[1-9][0-9]*$' "$raw"; then
    failed=$((failed + 1))
fi
if ! grep -Eq '^ip_rule_8999=[1-9][0-9]*$' "$raw"; then
    failed=$((failed + 1))
fi
if ! grep -Eq '^file_profile.json=present,' "$raw"; then
    failed=$((failed + 1))
fi
if ! grep -Eq '^file_cache.db=present,' "$raw"; then
    failed=$((failed + 1))
fi

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
