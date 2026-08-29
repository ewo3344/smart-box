# smart-box subscription converter

This service privately aggregates multiple Clash-family subscriptions and
serves one complete profile for the `smart-box` fork. Provider URLs are
read from a root-owned runtime configuration and never appear in responses or
logs.

## Behavior

- Fetches every provider in parallel every 30 minutes.
- Supports Clash `ss`, `vmess`, `vless`, `trojan`, `hysteria2`, `tuic`, and
  `anytls` proxies.
- Converts Clash TLS, uTLS, Reality, WebSocket, gRPC, Hysteria2 obfuscation,
  and common protocol fields to sing-box JSON.
- Performs bounded concurrent reachability checks before publishing nodes.
- Deduplicates equivalent nodes across providers and makes all outbound tags
  unique.
- Uses emoji flag pairs in node names to build one Smart group per region.
- Places nodes without a recognized flag in `❓ 未识别 Smart`.
- Creates a `🎯 基准 Smart` selector for global automatic routing or a chosen
  emoji-derived region.
- Creates 25 visible policy selectors for AI, Telegram, Douyin, individual streaming
  platforms, other media, social networks, gaming, GitHub, development
  services, Apple, Microsoft, Google, speed tests, downloads, domestic
  domains/IPs, and advertising.
- Lets policies select generated regional Smart groups. AI defaults to its
  dedicated Fallback and excludes Hong Kong from both automatic and explicit
  regional choices. Other proxy policies follow the baseline by default;
  Bilibili, downloads, and domestic traffic default to direct; advertising
  defaults to reject.
- Shares four specialized Fallback probe pools across related selectors rather
  than duplicating one active probe pool per service. The global Smart pool is
  the general-purpose automatic fallback.
- Provides Rule, Global, Direct, and energy-saving (`节能`) Clash modes.
  The energy-saving mode keeps AI and Telegram on Smart, uses local DNS by
  default, and routes everything else directly to save proxy traffic, mobile
  data, and battery. AI/Telegram domain, Android package, and Windows process
  matches precede the direct catch-all, including traffic that cannot be
  classified by domain sniffing.
- Includes private-network, ad-blocking, domestic, AI, streaming, messaging,
  gaming, and configurable policy routing rules.
- Preserves the last successful provider cache when a source is unavailable.
- Mirrors 39 binary SRS rule sets behind the same private token, validates the
  SRS magic/version, enforces a 4 MiB limit, and atomically preserves the last
  valid file when an upstream rule source fails. Readiness checks every exact
  configured tag, so a same-sized but incomplete cache cannot publish a
  profile.
- Gives each policy its own DoH transport through the selected outbound, so a
  manually selected region is also used for that service's DNS resolution.
- Enables the sing-box cache file so clients can start with cached remote rule
  sets while the converter is temporarily unavailable.
- Serves an immediately usable cached profile while a background refresh runs.

The complete selector matrix, route priority, DNS mapping, and Fallback
semantics are documented in [ROUTING.md](ROUTING.md).

TCP protocols are checked by connecting to their server port. QUIC protocols
are checked for DNS/UDP reachability; the Smart group in the client performs
the authoritative URL probe through each proxy and penalizes failed dials.

On a host that also runs smart-box TUN routing, converter probes must not be
sent through that TUN. Otherwise a dead node can appear reachable through a
different working proxy. The Raspberry Pi deployment therefore installs
`smart-box-converter-route-bypass.service` before both converter and core. It
adds IPv4 and IPv6 policy rules for the converter service UID:

```text
8998: uidrange <converter-uid>-<converter-uid> lookup main
8999: uidrange <converter-uid>-<converter-uid> unreachable
```

The first rule forces probes through the physical main route. The second is a
fail-closed guard: if that route is missing, probes are rejected instead of
falling through to smart-box's `tun0` policy table. The converter service has a
hard systemd dependency on the bypass service, so it cannot start without the
rules.

## Endpoints

```text
GET /subscription/<private-token>
GET /rule-set/<private-token>/<tag>.srs
GET /healthz
GET /api/v1/status
```

The subscription response uses `ETag` and a five-minute private cache policy.
An invalid subscription or rule-set token returns 404. Both private response
types support `ETag`; rule files also expose `Last-Modified`. The status
endpoint contains only source aliases, counts, cache state, generation time,
rule-set health, and an output hash.

## Configuration

Copy `config.example.json` outside the repository and fill in the private
provider URLs, a cryptographically random `public_path`, and `public_url`.
`public_url` is the origin clients can use to reach the converter. It accepts
only an absolute HTTP/HTTPS origin with scheme, host, and optional port; user
information, query strings, fragments, and extra paths are rejected. The
service rejects private tokens shorter than 24 characters.

The Raspberry Pi deployment uses:

```text
/usr/local/bin/smart-box-converter
/usr/local/bin/smart-box-core
/etc/smart-box-converter/config.json
/var/lib/smart-box-converter/cache
smart-box-converter.service
smart-box-converter-route-bypass.service
TCP 38473
```

The Raspberry Pi core wrapper stores its last validated profile and sing-box
rule cache separately:

```text
/var/lib/smart-box/profile.json
/var/lib/smart-box/cache.db
```

Both files survive service restarts. The wrapper monitors the core child and
exits on an unexpected core failure so systemd can restart the complete stack.
When valid provider caches and a validated core profile already exist, both
services start from those files and wait for the configured refresh interval
before making their first update request. The Raspberry Pi deployment uses a
24-hour refresh interval.

These names and paths are distinct from upstream sing-box, so an official
installation is not replaced.

## Development

On CachyOS/Linux, run the complete converter checks and produce the deployed
ARM64 binary with:

The exact compiler pin is the root [`TOOLCHAIN_VERSION`](../TOOLCHAIN_VERSION)
file (`go1.26.5`). The imported core module's `go 1.25.5` directive remains
its minimum language version; it is intentionally lower than this release pin.

```bash
cd /path/to/smart-box/converter
env GOTOOLCHAIN=go1.26.5 go test ./...
env GOTOOLCHAIN=go1.26.5 go test -count=50 ./...
env GOTOOLCHAIN=go1.26.5 go test -race ./...
env GOTOOLCHAIN=go1.26.5 go vet ./...
env SMART_BOX_CORE=/tmp/smart-box-core-check GOTOOLCHAIN=go1.26.5 \
  go test -run 'TestGeneratedProfile(AcceptedByCore|StartsWithRemoteRuleSets)' -v .
env CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOTOOLCHAIN=go1.26.5 \
  go build -buildvcs=false -trimpath -ldflags '-s -w' \
  -o smart-box-converter-linux-arm64 .
```

The optional live-source test downloads all 39 upstream SRS files and asks the
real core to parse them:

```bash
env SMART_BOX_LIVE_RULESETS=1 SMART_BOX_CORE=/tmp/smart-box-core-check \
  GOTOOLCHAIN=go1.26.5 go test -run TestLiveRuleSetSourcesAcceptedByCore -v .
```

On Windows, the equivalent cross-build is:

```powershell
cd C:\workspace\smart-box\converter
$env:GOTOOLCHAIN = (Get-Content ..\TOOLCHAIN_VERSION -Raw).Trim()
go test ./...
$env:GOOS = "linux"
$env:GOARCH = "arm64"
$env:CGO_ENABLED = "0"
go build -buildvcs=false -trimpath -o ..\dist\smart-box-converter-linux-arm64 .
```

Validate a generated profile with the full-feature fork build:

```text
smart-box-core check -c profile.json
```

The core must be built with at least `with_quic` and `with_utls` for
Hysteria2/TUIC and Reality/uTLS nodes.
