# Session: smart-box Android traffic debugging and project handoff
Date: 2026-08-15
Duration: long-running multi-stage implementation and device-debugging session

## Current Truth (2026-08-22)

This file contains historical session notes as well as the current handoff. The
initial Android diagnosis below is historical and must not be read as the
current install state. The authoritative project plan is
`SMART-BOX-PLAN.md`.

- Linux source, release directory, and installed `/usr/local/lib/smart-box`
  backend/GUI are synchronized. Linux regression currently passes 129 tests.
- The latest Linux runtime includes direct routing for local multicast CIDRs and
  passes real core validation plus direct and mixed-proxy HTTP checks.
- The machine is currently fail-open after testing: `smart-box@e.service` and
  its watchdog are inactive, `SmartBox` is absent, and direct HTTP is working.
  The historical FlClash unit is not installed on this machine, so an active
  FlClash final-state assertion cannot be made here.
- `adb devices` currently recognizes vivo `V2352A` (`10AE6J03LC001JL`). Android
  JVM tests pass in the current checkout, and the live VPN/TUN, Telegram,
  Douyin, and DNS smoke matrix has now completed; profile-refresh and
  crash-recovery scenarios remain separate follow-up checks.
- Smart destination memory now persists in the cache `smart_memory` bucket.
  Focused tests, race tests, real-core profile checks, converter integration,
  and the Linux release gate all pass. A separate rollback copy restored the
  pre-change core byte-for-byte; the installed core remains on the modified
  Smart-memory branch.
- The connected vivo live matrix passed smart-box VPN start/stop, gVisor TUN,
  direct gstatic/Baidu/GitHub/Telegram probes, Telegram cold launch, Douyin
  feed/comment-entry responsiveness, and a filtered zero count for DNS EPERM,
  DNS-packet, protect, fdsan, fatal, panic, and crash messages. The phone was
  force-stopped back to direct networking after the test. The live report is
  `verification/android-live-20260822/`; it does not claim comment posting or
  media-stream completion.
- Raspberry Pi converter, cache-aware wrapper, and route-bypass services are
  active with a 24-hour provider refresh interval. No provider refresh is
  triggered by this handoff update.
- Linux settings and CLI now provide read-only Arch (pacman/paru) and CachyOS
  mirror benchmarking. A direct IPv4 throughput ranking was applied on
  2026-08-22: Arch starts with `mirrors.wsyu.edu.cn`, CachyOS starts with
  `mirror.nju.edu.cn`, and the system/user pacman lists retain measured
  fallbacks. Evidence and an independent-copy rollback are in
  `verification/mirror-speed-20260822/`.
- The release gate after installing the new Linux client passed: 129 Linux
  tests, converter tests, real-core checks, package checksums, Android JVM
  build, and final direct Baidu HTTP 200. The desktop and phone VPNs are both
  stopped at handoff.

The sections immediately following this note preserve the original 2026-08-15
diagnostic context and later dated implementation records.

## Context Snapshot

`smart-box` is a sing-box fork plus Android and Windows clients and a Raspberry
Pi subscription converter. The product work is substantially implemented, but
Android VPN traffic on one vivo Android 16 device is still blocked after the
TUN ingress boundary; the next task is to finish the controlled network-binding
experiment, implement only the confirmed fix, and run end-to-end validation.

## What Was Accomplished

- Added the core `smart` outbound group with latency scoring, destination
  memory, failure penalties, hysteresis, retries, command-stream state, schema,
  registration, and focused tests.
- Built a Raspberry Pi converter that merges Clash-family subscriptions,
  filters unreachable nodes, groups nodes by flag emoji, generates regional
  Smart groups plus a baseline Smart selector, and serves one private sing-box
  profile on TCP 38473.
- Added detailed routing for private networks, ads, domestic traffic, AI,
  streaming, Telegram, and gaming. Unrecognized regions have their own group.
- Rebranded the Android client to `smart-box`, package
  `io.nekohasekai.sfa.smartbox`, version
  `0.1.0-core.1.14.0-beta.14`, and made it coexist with upstream.
- Replaced Android's general profile workflow with one converter endpoint UI
  containing HTTP/HTTPS, host/IP, port, and masked private path fields.
- Removed Android software self-update, update-source, update-track, and APK
  installation features while preserving 30-minute profile refresh.
- Added a Windows WPF client and separate local application identity.
- Fixed a vivo TUN descriptor lifetime issue by duplicating the Android TUN FD
  with `F_DUPFD_CLOEXEC` and a minimum descriptor number of 1024.
- Moved the complete workspace from `C:\sing-box-smart` to
  `C:\workspace\smart-box` without dropping build outputs or Git metadata.

## Key Decisions & Rationale

| Decision | Why | Alternatives Rejected |
|----------|-----|-----------------------|
| Separate `smart-box` application/package/data identity | Must coexist with official sing-box | Reusing upstream package or data paths would cause conflicts |
| Converter owns all provider subscriptions | Clients need one stable endpoint and must not manage provider-specific formats | Parsing every provider independently in both clients duplicates logic and secrets |
| Baseline Smart selector plus regional Smart groups | User chooses global best or a preferred country while policy groups follow the baseline | A flat selector does not provide adaptive routing or consistent policy defaults |
| Duplicate TUN FD above 1024 | vivo asynchronously closed reused low-numbered descriptors | Retaining only the platform PFD did not prevent the vendor close race |
| Diagnose Android egress with two Kotlin socket cases | Isolates Android `Network.bindSocket` from Go, TFO, proxy protocol, and TUN parsing | More speculative changes would obscure the failing boundary |

## Current State

- **Working**: converter unit tests, core Smart tests, libbox tests, Windows build,
  Android build, package/display rebranding, converter health endpoint, profile
  generation, TUN descriptor retention, and gVisor TUN ingress observation.
- **Broken/Blocked**: on vivo `V2352A` (Android 16/API 36), protected outbound
  sockets time out while the VPN is active. `VpnService.protect()` returns true,
  but both direct LAN traffic and Smart-node traffic time out.
- **Active hypothesis**: the vendor accepts `protect()` but does not reliably
  bind the protected socket to the underlying Android `Network`. The currently
  built diagnostic APK compares `protect(socket)` against
  `protect(socket) + defaultNetwork.bindSocket(socket)`.
- **Installation state**: the latest diagnostic APK build completed, but its
  final `adb install -r -d` was interrupted. Do not assume that build is on the
  phone; verify package version and reinstall it.
- **Android repository**: branch `feature/smart-group-android`, base commit
  `8f6343802a6d8e0fa478d9e642cbb58c147e671b`, intentionally dirty.
- **Core repository**: branch `feature/smart-group`, base commit
  `db1053f8bc16c860225afc97ac6417e42a81dc64`, intentionally dirty.
- **Modified files**: run `git status --short` separately in `android/` and
  `core/`. Important untracked core files include `protocol/group/smart.go`,
  `protocol/group/smart_probe_test.go`, `experimental/libbox/service_android.go`,
  both `service_tun_debug_*.go` files, and `test/config/smart.json`.

## Dead Ends (Don't Retry)

- Changing only TUN ownership/retention after the FD-1024 fix: TUN no longer
  disappears, but protected outbound sockets still time out.
- Assuming `protect(fd) == true` proves usable egress: a pure Kotlin protected
  socket also timed out.
- Blaming Go networking or TCP Fast Open: the pure Kotlin socket reproduced the
  failure.
- Blaming the Raspberry Pi listener or Wi-Fi path: `adb shell` over `wlan0`
  reached the same LAN endpoint with HTTP 200 when the app VPN path was not
  intercepting it.
- Staying on the `mixed` stack for diagnosis: it exposed only DNS ingress on
  this device. Temporary `gvisor` showed full TCP ingress and the expected
  routing decisions, narrowing the failure to outbound egress.

## Next Steps (Prioritized)

1. [ ] Reinstall the current arm64 diagnostic APK and capture only the two
   `diagnostic socket` results. First confirm whether `bindNetwork=true` is the
   sole successful case.
2. [ ] If confirmed, add underlying-network binding to the Go FD callback using
   a safely duplicated descriptor. Never let a temporary
   `ParcelFileDescriptor` close the core-owned socket FD.
3. [ ] Remove all temporary diagnostic sockets and verbose FD/TUN logging, then
   rebuild the AAR and Android APK.
4. [ ] Cold-start the app and verify both the persisted profile and generated
   configuration use the intended TUN stack. The profile was last restored to
   `mixed`; do not silently leave a diagnostic `gvisor` override behind.
5. [ ] Verify real traffic end to end: browser traffic counters increase,
   public IP changes through Smart, LAN direct routing works, baseline and
   regional selection work, policy selectors inherit baseline, fallback works,
   and a running profile refresh does not drop the VPN.
6. [ ] Run all focused tests and build checks listed below.

## Environment & Gotchas

- Windows source workspace was `C:\workspace\smart-box` at handoff. On CachyOS,
  use the extracted project root and relative paths.
- Phone: vivo `V2352A`, serial `10AE6J03LC001JL`, Android 16/API 36.
- Windows ADB was `C:\Android\Sdk\platform-tools\adb.exe`; use the CachyOS
  Android platform-tools equivalent.
- Raspberry Pi converter host is `192.168.2.102`, TCP port `38473`.
- Do not print, commit, or place in handoff documents any private subscription
  path, provider URL, token, node credential, or SSH password. Provider secrets
  belong only in the root-owned Raspberry Pi runtime configuration.
- Both Git worktrees intentionally contain extensive uncommitted product work.
  Never reset, checkout, clean, or overwrite those changes.
- The archive contains Windows/Android build outputs and caches for completeness.
  They are not portable; CachyOS should rebuild native outputs locally.
- ZIP extraction on some tools may lose the executable bit. If needed, run
  `chmod +x android/gradlew converter/deploy/smart-box-wrapper`.

## Key Code/Commands Reference

Run from the extracted root on CachyOS:

```bash
cd converter && go test ./...
cd ../core && go test ./protocol/group ./experimental/libbox
git diff --check
cd ../android && ./gradlew assembleOtherDebug
```

Current diagnostic APK name:

```text
android/app/build/outputs/apk/other/debug/
smart-box-0.1.0-core.1.14.0-beta.14-arm64-v8a-debug.apk
```

Temporary diagnostics that must be removed only after the hypothesis is tested:

```text
android/app/src/main/java/io/nekohasekai/sfa/bg/VPNService.kt
core/experimental/libbox/service.go
core/experimental/libbox/service_tun_debug_android.go
core/experimental/libbox/service_tun_debug_other.go
dist/smart-box-logdiag.go
dist/smart-box-logdiag.exe
dist/rewrite-bootstrap-dns.js
dist/bootstrap-profile.json.gz
```

## 2026-08-16 Complete Routing And Raspberry Pi Update

The converter routing work was expanded and deployed after this original
handoff was written. This section is newer than the Android diagnostics above.

### Implemented

- Added 25 selectors: the baseline plus 24 detailed policies for AI,
  Telegram, Netflix, Disney+, Max, Prime Video, Apple TV+, YouTube, TikTok,
  Bilibili, Spotify, other media, social networks, games, GitHub, development
  services, Apple, Microsoft, Google, speed tests, downloads, domestic
  domains, domestic IPs, and ads.
- AI defaults to its dedicated Fallback and excludes Hong Kong from both the
  pool and its explicit regional choices. Other policies expose global and
  every dynamically generated regional Smart group. Other proxy policies
  default to the baseline; Bilibili, downloads, and domestic policies default
  to DIRECT; ads default to REJECT.
- Kept four specialized shared Fallback probe pools (AI, Telegram, media, and
  games) plus the global Smart pool instead of creating a probe pool for every
  service.
- Added per-policy DoH transports. Foreign services use Cloudflare DoH;
  Bilibili, downloads, and domestic domains use AliDNS DoH. DNS detours follow
  the selected policy, so a manual region choice applies to both DNS and the
  connection.
- In energy-saving mode, AI/Telegram domain rules plus Android package and
  Windows process fallbacks run before the local-DNS/direct catch-all. This
  preserves the intended proxy path even when QUIC traffic cannot be sniffed.
  `games-cn` now uses `download-dns`, matching its `⬇️ 下载策略` connection
  route.
- Added Android package-name and Windows process-name fallback rules, strict
  domain rule priority, a resolve step, and Telegram/Netflix/media/domestic IP
  routing.
- Added a private mirror for 38 SRS rule sets. Downloads are bounded to 8
  workers and 4 MiB per file, validate SRS versions 1 through 5, write
  atomically, and retain the last valid cache on upstream failure.
- Added private rule endpoints with ETag, Last-Modified, 304 handling, token
  validation, and redacted request logging.
- Rule-set readiness now verifies every configured tag instead of accepting a
  same-sized map with a missing tag. A focused regression test covers the
  mismatched-tag case.
- Added `public_url` origin validation. The deployed origin is the Raspberry
  Pi LAN converter origin; the private token is intentionally not recorded
  here.
- Enabled `experimental.cache_file` and removed the obsolete
  `independent_cache` DNS option.
- Added unit, race, repeated, live-source, core-check, and real core-startup
  tests. The startup test loads all 38 remote rules and would catch errors that
  `smart-box-core check` alone cannot detect.

Full routing behavior and Fallback semantics are documented in
`converter/ROUTING.md`.

### Raspberry Pi State

- `smart-box-converter.service` and `smart-box.service` are active.
- The deployed converter serves 38/38 valid private rule endpoints and a
  complete profile. Online verification found 25 selectors, 26 DNS servers,
  and 64 ordered route rules. Node counts vary after each provider probe.
- The final 2026-08-16 deployment served 69 live nodes from four fresh sources;
  a fifth source returned an empty response and had no cache. All 38 rule sets
  loaded from valid converter cache, and the remaining sources kept the profile
  fully usable.
- Post-deployment checks confirmed subscription and rule-set ETag requests
  return 304, invalid tokens return 404, provider URLs do not appear in the
  profile, and the persisted profile exactly matches the current converter
  response. The active core process produced zero FATAL/WARN/PANIC entries.
  One `context canceled` FATAL belongs to the deliberately stopped old core
  PID during `systemctl restart`, not the active process.
- The deployed converter and local `dist` copy have SHA-256
  `79277f62a9b4626fb632239e50bc873b87858fa668fe2585a85a08e149af2e0b`.
- The converter runtime cache path was corrected from the old-brand
  `/var/lib/sing-box-smart-converter/cache` to
  `/var/lib/smart-box-converter/cache`, matching the systemd write policy.
  The old directory was deliberately retained.
- The core wrapper now uses `/var/lib/smart-box` as a systemd StateDirectory
  and working directory.
- The last validated profile is `/var/lib/smart-box/profile.json`.
- The persistent rule cache is `/var/lib/smart-box/cache.db`.
- The wrapper checks its core child every 5 seconds. If the child exits, the
  wrapper exits and systemd restarts the complete service instead of leaving a
  false-active wrapper.
- A controlled test stopped the converter, restarted the core service, and
  confirmed the core started from the persistent profile and rule cache. A
  second test terminated the core child and confirmed automatic wrapper and
  core recovery.

### Remote Backups

The following recovery copies exist on the Raspberry Pi:

```text
/usr/local/bin/smart-box-converter.bak-full-routing-20260816
/usr/local/bin/smart-box-converter.bak-routing-complete-20260816
/etc/smart-box-converter/config.json.bak-full-routing-20260816
/etc/smart-box-converter/config.json.bak-cache-dir-fix-20260816
/etc/systemd/system/smart-box.service.bak-full-routing-20260816
/usr/local/sbin/smart-box-wrapper.bak-full-routing-20260816
/var/lib/smart-box-converter/cache-backup-before-dir-fix-20260816/
```

Do not print or copy the live converter configuration into logs or handoff
documents; it contains provider URLs and the private path.

## 2026-08-16 Domain Overrides And Probe Isolation

### Android domain lists

- Added **Tools > Domain Allow/Proxy Lists** with persistent local settings,
  validation, conflict reporting, unsaved-change protection, and service
  reload integration.
- The allow list routes matching roots and subdomains through `DIRECT` and
  `local` DNS. The proxy list routes them through `🎯 基准 Smart` and
  `baseline-dns`; it does not reject traffic.
- Direct/Global modes remain first. In Rule and energy-saving modes, manual
  domain rules precede private, ad, energy-saving, and generated service
  routing in both DNS and connection rule arrays.
- Parsing accepts whitespace/comma/semicolon separators, wildcard or dotted
  roots, trailing dots, case variants, and IDNs. It rejects URLs, IPs, local
  suffixes, invalid labels, and any cross-list parent/child overlap. Covered
  child domains are removed automatically.
- Rules are applied only to runtime profile content. The downloaded converter
  profile remains unchanged.
- Four focused unit tests, `:app:compileOtherDebugKotlin`, and a complete
  `:app:assembleOtherDebug` build passed on 2026-08-16. Targeted Spotless IDE
  hook checks report all three new Kotlin files clean. The full repository
  check still reports 275 pre-existing CRLF-formatted files; do not run a bulk
  `spotlessApply` because it would rewrite unrelated worktree changes.
- Final installable artifacts are
  `dist/smart-box-0.1.0-core-1.14.0-beta.14-android-arm64.apk` (SHA-256
  `f916786a077894f1d296a48d4cde7ccd6941c48560e3c974ceecf60929d3727d`)
  and the corresponding `android-universal.apk` (SHA-256
  `9f036b91ceac749ec0cf340d60312f5354cd690d71c7c70dc1f7dcfa36924e41`).
  Both pass zipalign and APK Signature Scheme v2/v3 verification. They use the
  same `/home/e/.android/smart-box-device.keystore` certificate as the vivo
  diagnostic APK, so they can upgrade the existing phone installation.

### Raspberry Pi probe isolation

- Before the fix, traffic owned by converter UID 995 resolved through policy
  table 2022 and `tun0`, so a dead provider node could be marked reachable via
  another proxy.
- Added and deployed `smart-box-converter-route-bypass.service` plus
  `/usr/local/sbin/smart-box-converter-route-bypass`. The converter service now
  requires this unit before startup.
- IPv4 and IPv6 both have priority 8998 `uidrange 995-995 lookup main`, followed
  by priority 8999 `uidrange 995-995 unreachable`. The second rule prevents a
  missing physical route from falling through to table 2022.
- Live route checks for public IPv4 and IPv6 destinations both selected
  `wlan0` and the physical LAN gateways. The bypass, converter, and core units
  were all enabled and active after a core restart.
- Remote recovery copy:
  `/etc/systemd/system/smart-box-converter.service.bak-probe-bypass-20260816`.

### Core identity

- There is no installed Debian `sing-box` package, `/usr/bin/sing-box`,
  `/usr/local/bin/sing-box`, or running process named `sing-box`.
- The active child is `/usr/local/bin/smart-box-core run -c
  /var/lib/smart-box/profile.json`.
- Its version is `smart-box-0.1.0-core-1.14.0-beta.14`, revision
  `db1053f8bc16c860225afc97ac6417e42a81dc64`, with the expected gVisor, QUIC,
  WireGuard, uTLS, and Clash API tags. That revision equals local
  `core/feature/smart-group` HEAD.
- The active profile contains 16 `type: smart` outbounds, which upstream
  sing-box does not implement. The core is therefore the smart-box fork that
  consumes converter output, not an upstream/original sing-box build. Format
  conversion itself remains in the separate converter service.
- Old `sing-box-smart-*` files and units are retained only as inactive history;
  both old units have `MainPID=0` and no process uses their binaries.

## 2026-08-16 AI Hong Kong Exclusion

- `🤖 AI Smart` now defaults to `🤖 AI Fallback` instead of the baseline.
- Hong Kong is excluded from the AI Fallback candidate set and from the AI
  selector's explicit regional choices. Baseline and global selectors are also
  omitted from AI choices because either could indirectly select Hong Kong.
- This guarantee applies in Rule and energy-saving modes. Global Clash mode
  intentionally precedes all service routing and can still use Hong Kong via
  the global pool.
- Preferred AI regions are Singapore, Japan, the United States, Taiwan, South
  Korea, Canada, the United Kingdom, Germany, and France. If none exist, the
  pool uses other non-Hong-Kong nodes. If Hong Kong is the only region, the
  pool uses DIRECT rather than silently adding Hong Kong back.
- Unit coverage includes preferred, non-preferred, and Hong-Kong-only inputs.
  Both mixed-region and Hong-Kong-only generated profiles pass the real
  smart-box core compatibility check. Full tests, 50 repeated runs, race, and
  vet passed.
- Deployed converter SHA-256:
  `4e40d90e82de50503cfb7563948e8a4b04579e60665b32e150b8a9491ad40f5e`.
  Remote backup:
  `/usr/local/bin/smart-box-converter.bak-ai-no-hk-20260816`.
- After deployment the live Hong Kong regional pool contained 13 nodes and AI
  Fallback contained 44 nodes, with an intersection of zero. AI exposed no
  Hong Kong, baseline, or global choice. Counts are refresh-dependent; the
  required invariant is zero overlap. Converter, route bypass, and core were
  active, core logs had no warning entries, and UID 995 remained on the
  physical IPv4/IPv6 `wlan0` routes.

## 2026-08-17 DNS EPERM and Douyin routing fix

The repeated Android log entry
`router: process DNS packet: operation not permitted` was reproduced with Trace
logging and was not an Android VPN permission failure. A representative lookup
for `stsdk.vivo.com.cn` matched the `ads` rule, selected `ads-dns`, and then
attempted the Cloudflare DoH connection through `🛡️ 广告策略`. That selector
defaults to the block outbound `REJECT`, whose dial returns `EPERM`; the DNS
router then surfaced that expected block as a packet-processing error.

The converter no longer creates `ads-dns`. Ad-domain lookups use
`baseline-dns`, while the resulting connections still use the manually
selectable `🛡️ 广告策略`. Default ad blocking therefore remains active, manual
DIRECT/Smart choices still work, and DNS is no longer deliberately dialed
through a block outbound.

Douyin comment failures had a separate routing collision:

- The overseas `tiktok.srs` contains the broad suffix `snssdk.com`.
- The domestic `cn.srs` also contains `snssdk.com`, but TikTok previously had
  higher priority, so unclassified Douyin API traffic could use an overseas
  TikTok route and DNS exit.
- The anti-AD set also contains shared ByteDance infrastructure such as
  `mssdk`, `tnc`, `open.snssdk.com`, and `tsearch.snssdk.com`; the ad rule was
  evaluated before application rules, so it could reject dependencies used by
  Douyin networking and request validation.

The converter now mirrors SagerNet's dedicated `douyin.srs` as rule set 39 and
adds `🇨🇳 抖音 Smart`, defaulting to DIRECT but exposing baseline, global, and
all regional Smart choices. `com.ss.android.ugc.aweme` and
`com.ss.android.ugc.aweme.lite`, plus the dedicated Douyin domain rule set, are
matched before both anti-AD and overseas TikTok. Douyin uses AliDNS through its
own selector in Rule mode and is forced direct with local DNS in energy-saving
mode. This intentionally prioritizes functional Douyin APIs over in-app ad
blocking; other applications continue to use the normal ad policy.

Validation completed locally:

- converter unit tests and 50 repeated runs;
- race detector and `go vet`;
- real core static compatibility checks for mixed-region and Hong-Kong-only
  profiles;
- real core startup with all 39 simulated remote rules;
- live download and parse checks for all 39 upstream SRS files.

The Raspberry Pi converter and local dist binary now have SHA-256
`1a9efe44e3e4dcd881662e6f187a128daa87a045d1fc83897076c18133197a13`.
The previous remote binary is backed up at
`/usr/local/bin/smart-box-converter.bak-before-douyin-dns-20260817`.
Post-deployment structure was 26 selectors, 26 DNS servers, 58 DNS rules, 68
route rules, and 39/39 available rule sets. The refresh published 121 live
nodes at that moment. `smart-box-converter.service` and `smart-box.service`
were active, the persisted core profile matched the live subscription byte for
byte, and converter UID 995 retained the physical-route/fail-closed policy
rules at priorities 8998/8999.

The phone was disconnected from ADB after deployment. Its installed profile
still needs an immediate subscription pull (or its next automatic update) and
a service reload before phone-side Trace verification can be completed.

## 2026-08-17 Android NAT exhaustion and runtime gVisor fix

### Confirmed root cause

After the Douyin ordering and DNS EPERM fixes were deployed, intermittent
comment failures still reproduced with this core error:

```text
inbound/tun[tun-in]: ipv4: tcp: NAT port space exhausted
```

The Android profile was using the `mixed` TUN stack. On the vivo V2352A, its
system TCP NAT consumed all 55,536 available ports in roughly five minutes.
The exhausted listener then also emitted many repetitions of:

```text
inbound/mixed[mixed-in]: tcp listener closed: accept tcp 127.0.0.1:20808: use of closed network connection
```

A controlled phone test changed only the TUN stack to `gvisor`. Six comment
panel opens succeeded, the VPN passed the former failure window, and all three
error counters stayed at zero. This separated the remaining failure from the
already-fixed Douyin route collision and ad-DNS EPERM path.

### Implementation

- Added
  `android/app/src/main/java/io/nekohasekai/sfa/bg/AndroidRuntimeProfile.kt`.
  It parses the profile as JSON and forces every Android runtime TUN inbound to
  `stack: gvisor` while preserving non-TUN inbounds and all other options.
- `BoxService` now applies local domain routing rules first and the Android TUN
  override second, immediately before handing content to libbox. The source
  subscription under `files/configs` is never rewritten by either transform.
- Added three focused `AndroidRuntimeProfileTest` cases for mixed/system TUN
  inputs, an already-gVisor input, and a profile without a TUN inbound.
- Changed `core/common/listener/listener_tcp.go` so a permanent `Accept` error
  is logged once and returns from the accept loop. Temporary accept errors are
  still retried, and expected shutdown closure remains silent. The regression
  test verifies both permanent and temporary behavior.

The gVisor decision is deliberately Android-only. The shared converter output
remains `mixed`, and no Raspberry Pi, Linux, or Windows deployment was changed
for this fix.

### Build and verification

The arm64 libbox archives were rebuilt with the repository's Go 1.25.5 target
(a historical verification build). The current exact release pin is the root
`TOOLCHAIN_VERSION` file (`go1.26.5`); `core/go.mod` still declares 1.25.5 as
the minimum language version:

```text
core/libbox.aar
android/app/libs/libbox.aar
SHA-256 5277652c08e60ec4733df207564f67e5b3c1e7195a9fe5fc3b072c6e2c3aa1bb

core/libbox-legacy.aar
android/app/libs/libbox-legacy.aar
SHA-256 8260b929da44601234a5599f6901ca95dc24bfdad4ddf96bc005bdf1147cc13d
```

Passed checks:

```text
./gradlew testOtherDebugUnitTest assembleOtherDebug
go test ./protocol/group
go test ./common/listener
go test -race ./common/listener
go vet ./common/listener
```

Both new Kotlin files report `IS CLEAN` through Spotless's single-file IDE
hook. Do not run a repository-wide `spotlessApply`: 274 unrelated CRLF files
still fail the full check. Host `go test ./experimental/libbox` remains blocked
before tests execute by the existing `oomprofile` linkname to the unavailable
`runtime/pprof.parseProcSelfMaps` symbol; Android libbox compilation and the
complete APK build both succeed because that host-only Linux file is excluded.

The signed installable arm64 artifact is:

```text
dist/smart-box-0.1.0-core-1.14.0-beta.14-android-arm64.apk
SHA-256 5ccbad5c6bcd35dc86f62fc03157f322a80f06480619a02040aca21a2b832136
Signer SHA-256 8de57370597def2d26d94973d5c63cee02e81ea3af2a20aab39a26e4808878b4
```

It passes zipalign and APK Signature Scheme v2/v3 verification. The signer is
the same as the existing vivo installation, so `adb install -r` preserved all
application data. Only the arm64 artifact was refreshed; do not advertise the
older universal APK as containing this arm64-only libbox rebuild.

### Final vivo phone result

- Installed on V2352A as package `io.nekohasekai.sfa.smartbox`; update time was
  2026-08-17 08:30:40 +0800.
- Restored `files/configs/1.json` from the pre-test backup and verified its TUN
  stack is still `mixed` after installation and service startup.
- Removed the prior generated runtime file before startup. The new build
  independently generated `files/configuration.json` with `stack: gvisor`,
  proving that the application override works without modifying the download.
- Cold-started Douyin Lite, switched videos, opened comment panels beyond the
  old five-minute threshold, and visibly loaded panels with 179 and 31
  comments. The smart-box process remained alive for more than ten minutes.
- Final process-log counts were `nat_exhausted=0`, `dns_eperm=0`,
  `listener_closed=0`, and `fatal_or_panic=0`.

## 2026-08-18 Raspberry Pi daily refresh and cache-only restart

- Changed the deployed converter provider refresh interval from 30 minutes to
  24 hours. The live value is `refresh_interval: "24h"` in the root-owned
  converter configuration.
- Changed `converter/deploy/smart-box-wrapper` from 1,800 seconds to 86,400
  seconds, so the Raspberry Pi core checks the local converted subscription
  once per day instead of every 30 minutes.
- Converter startup is now cache-aware. When the complete rule cache and at
  least one provider node cache are usable, it publishes a snapshot from those
  files and waits for the full refresh interval before contacting any provider.
  A new installation without usable cache still performs an initial refresh.
- Wrapper startup is also cache-aware. When `/var/lib/smart-box/profile.json`
  passes the core check, it starts the core from that file and waits for the
  full interval before requesting the local subscription. If no valid profile
  exists, it still downloads immediately for recovery.
- The old converter was stopped before its next scheduled half-hour refresh.
  The replacement started at 2026-08-17 23:58:52 +0800 from 137 cached nodes;
  its first provider refresh is expected around 2026-08-18 23:58:52 +0800.
  The wrapper restarted at 2026-08-18 00:00:07 +0800, so its first local
  subscription request is expected around 2026-08-19 00:00:07 +0800.
- Post-start checks found no provider-cache or rule-cache timestamp changes,
  no converter outbound TCP connection, no startup refresh log, no local
  subscription GET, and no change to the persisted core profile.
- Both `smart-box-converter.service` and `smart-box.service` were active. The
  deployed converter SHA-256 is
  `42f252cdb7f3a5630ee735ae91e9ebb24117cad1527dbd0b3d28b0e0d5fbfa2c`.
- Recovery copies are:
  `/usr/local/bin/smart-box-converter.bak-20260817-daily-refresh`,
  `/usr/local/sbin/smart-box-wrapper.bak-20260817-daily-refresh`, and
  `/etc/smart-box-converter/config.json.bak-20260817-daily-refresh`.
- Focused verification passed `go test ./...`, `go test -race ./...`, `go vet
  ./...`, wrapper `sh -n`, and the static Linux ARM64 cross-build.

## 2026-08-19 Telegram perpetual-loading diagnosis and routing fix

- The vivo phone's Telegram package is `org.telegram.messenger.web` 12.9.2. It
  had no ANR or native crash and could reach Telegram web endpoints and DC IPs,
  but the original route was `Telegram Smart -> baseline -> global Smart`.
- The global Smart pool contained provider status entries such as remaining
  traffic, subscription update time, and tutorial notices. These entries had
  proxy-shaped fields and could be selected as if they were usable nodes.
- A live selector test changed only `Telegram Smart` to the Japan regional
  Smart group. Telegram immediately rendered its chat list and established
  active connections to `91.108.56.118` through Japanese nodes.
- The converter now filters provider status/tutorial entries both while parsing
  a fresh subscription and while bootstrapping from existing JSON cache.
- `Telegram Smart` now defaults to `Telegram Fallback`. That pool prefers
  Singapore, United States, Japan, and Taiwan, tests with
  `https://telegram.org`, and excludes Hong Kong from automatic selection when
  another region exists. Hong Kong remains available as an explicit regional
  selector and as a last resort for a Hong-Kong-only subscription.
- The ARM64 converter deployed at 2026-08-19 10:28:02 +0800 has SHA-256
  `41484a4a84d6a8140c9e3332d9d2f1e186f2f0eea548e91e58c9857daba50b66`.
  Its remote rollback copy is
  `/usr/local/bin/smart-box-converter.bak-telegram-routing-20260819` with the
  prior SHA-256
  `42f252cdb7f3a5630ee735ae91e9ebb24117cad1527dbd0b3d28b0e0d5fbfa2c`.
- Only `smart-box-converter.service` was restarted. It loaded 137 cached nodes
  and scheduled the next refresh for 24 hours later; no provider refresh ran.
  `smart-box.service` kept PID 170338 and its persisted profile timestamp stayed
  at 2026-08-19 08:07:25 +0800.
- The generated profile contains zero provider-status pseudo-nodes. Its
  Telegram default is `Telegram Fallback`, its Telegram probe URL is
  `https://telegram.org`, and its automatic Telegram pool contains zero Hong
  Kong nodes.
- Verification passed converter unit tests, race tests, `go vet`, ARM64 static
  build, generated-profile checks against the local core, 39-rule-set startup,
  remote health checks, and a rollback rehearsal on an independent copy.

## 2026-08-19 CachyOS Linux x86_64 desktop client

### Delivered client

- Added a native PySide6 desktop and tray client under `linux/`. It includes
  status and transfer counters, Rule/Global/Direct/`节能` controls, all 26
  selectors, offline and live regional selection, domain allow/proxy lists,
  masked subscription editing, manual pull and validation, logs, autostart, and
  FlClash switching.
- Built the static x86_64 Smart core at
  `dist/smart-box-0.1.0-linux-x86_64/bin/smart-box-core`. Its SHA-256 is
  `47dd5dd0210236f443af384bffe553b9b69562f45cfca445cce20334d4179ed0`.
  The binary identifies itself as `smart-box-0.1.0-core-1.14.0-beta.14` with
  gVisor, QUIC, DHCP, WireGuard, uTLS, and Clash API tags.
- Built the installable directory and compressed archive at
  `dist/smart-box-0.1.0-linux-x86_64/` and
  `dist/smart-box-0.1.0-linux-x86_64.tar.gz`. `SHA256SUMS` covers every program,
  service, configuration, icon, and documentation file inside the directory.
- Installed the current build under `/usr/local` on this CachyOS machine. The
  source and installed backend, GUI, and service hashes match byte for byte.

### Runtime and privilege model

- The converter response is preserved as `~/.config/smart-box/profile.json`.
  Every local change is regenerated atomically in `runtime.json`: TUN interface
  `SmartBox`, gVisor stack, mixed listener `127.0.0.1:20808`, Clash API
  `127.0.0.1:20809`, local cache path, selected mode and selector overrides,
  domain lists, and info-level logging.
- `smart-box@e.service` is a system template but runs the core as user `e`.
  The core file has no permanent file capabilities. The unit grants only
  `CAP_DAC_READ_SEARCH`, `CAP_NET_ADMIN`, `CAP_NET_BIND_SERVICE`, `CAP_NET_RAW`,
  and `CAP_SYS_PTRACE`, enables `NoNewPrivileges`, and applies filesystem,
  kernel, address-family, SUID/SGID, and personality restrictions.
- `/etc/polkit-1/rules.d/49-smart-box.rules` allows an active local `wheel` user
  to manage only `smart-box@<same-user>.service`. It cannot authorize any other
  unit. The two fixed, root-owned DNS registration/revert helpers run privileged;
  the proxy core does not.
- The service is disabled by default and the GUI is not in autostart. Neither
  login, reboot, GUI launch, nor core start pulls a subscription. Pulling remains
  an explicit client action.

### FlClash, TUN, and DNS findings

- Starting the service while `FlClash` exists fails before core launch with a
  clear conflict message. The desktop switch stops FlClash, waits for its TUN
  to disappear, starts smart-box, and automatically restores FlClash on any
  failure. Normal smart-box stop can also restore it.
- File capabilities did not survive launch from the CachyOS user systemd
  manager; this caused `TUNSETIFF: operation not permitted`. Moving the core to
  the restricted system template produced the intended effective capability
  set and created `SmartBox` successfully.
- Both converter-default `mixed` and native `system` stacks created the TUN but
  stalled all TUN TCP sessions before outbound dispatch. gVisor passed Direct
  and Rule traffic immediately, so it is the CachyOS default; all three stacks
  remain selectable in Settings.
- After FlClash stopped, systemd-resolved otherwise fell back to WLAN DNS and
  returned polluted Telegram/OpenAI addresses. Service startup now registers
  `172.19.0.2` and `fdfe:dcba:9876::2` on `SmartBox` with routing domain `~.` and
  flushes the resolver cache. Stop reverts the link and flushes again before
  FlClash returns.
- The cached converter response still contained eight provider status/tutorial
  pseudo-nodes. Linux removes them and all Smart references only from the local
  runtime copy, reducing 137 raw entries to 129 usable nodes. The first global
  Smart candidate is now a real node. This complements, but does not mutate,
  the converter-side filter.

### Verified behavior and final state

- The real core validates both the downloaded profile and generated runtime.
  Python compilation, nine focused backend tests, shell syntax checks, systemd
  unit verification, archive checksums, and five 1160x780 UI screenshots pass.
- A controlled live switch produced an active service, `SmartBox` TUN, listeners
  on ports 20808/20809, 26 selectors, and all four modes. systemd-resolved
  returned `149.154.167.99` for Telegram and `172.66.0.243` for OpenAI during
  the test.
- Rule-mode requests returned HTTP 204 from gstatic, HTTP 200 from Telegram,
  HTTP 401 from the unauthenticated OpenAI models endpoint, and HTTP 204 through
  the local mixed proxy. Mode PATCH requests for Global, Direct, `节能`, and
  Rule each returned 204 and the API reported the requested mode.
- Baseline, AI, and Telegram selections were restored to Global Smart,
  AI Fallback, and Telegram Fallback. They persisted across a full service
  restart; Telegram Fallback returned HTTP 200 before and after that restart.
- Final machine state intentionally leaves `smart-box@e.service` inactive,
  `app-FlClash@autostart.service` active, the `FlClash` TUN present, and FlClash
  Fake-IP DNS restored. The installed profile is ready, so launching `smart-box`
  and pressing “切换到 smart-box” does not require another pull.

## 2026-08-21 Stability Package Refresh

- Core Smart candidate accounting now bounds TCP and UDP setup attempts, records
  a node as successful only after response bytes arrive, and demotes a node on
  the first real pre-response transport failure. The source branch is
  `core/protocol/group/smart.go`.
- The rebuilt Linux core is
  `dist/smart-box-0.1.0-linux-x86_64/bin/smart-box-core` with SHA-256
  `e1dd8053bd51ab089fbb267aad213fa59c4272f377a05495e5072ac7b84a5a34`.
  The offline archive is
  `dist/smart-box-0.1.0-linux-x86_64.tar.gz` with SHA-256
  `3956bcfde63d9e55785c721975f69dae70f7aee177893df1c9ee079ecb726885`.
- The installable Android arm64 APK is
  `dist/smart-box-0.1.0-core-1.14.0-beta.14-android-arm64.apk` with SHA-256
  `c570a2909f6e8211612ee26ad53348bca67ddf19222f590b53e701a4d9da1db4`.
  It uses package `io.nekohasekai.sfa.smartbox`, min SDK 24, and APK Signature
  Scheme v2. Android 5/6 use the matching `legacy-android-5-arm64.apk` artifact
  with SHA-256 `a2bbc2561ec6767be52c5b65310ace9ae6c315ed621fd2dc66d5a342cf85ff1d`.
- `core/libbox.aar` and both Android `app/libs` AARs are synchronized. Main AAR
  SHA-256 is `15c6a0fda9cff54da68efa4eb504e2a96a8cf90450ff19e8c0a03acfb3db1512`;
  legacy AAR SHA-256 is `e247331c9d3ab52c51198412dbec9d89634bd8006ef694058c40e9f302205e2b`.
- Linux Python tests (86), Go Smart group tests, race tests, vet, runtime/profile
  checks, package checks, and service static checks pass. Android main and legacy
  unit tests and arm64 assemblies pass. The phone is currently absent from
  `adb devices`, so device VPN regression remains pending.
- At the time this package section was recorded, the machine was intentionally
  fail-open for daily use: smart-box core and watchdog were inactive, the
  `SmartBox` TUN was absent, and direct Baidu access returned HTTP 200. The
  subsequent desktop installation and its current fail-open state are recorded
  below.
- Stability artifacts and independent-copy rollback checks are recorded in
  `verification/stability-package-20260821/linux/` and
  `verification/stability-package-20260821/android/`.

## 2026-08-21 Desktop stability install and fail-open verification

- Installed the refreshed desktop core at
  `/usr/local/lib/smart-box/smart-box-core`; its SHA-256 is
  `e1dd8053bd51ab089fbb267aad213fa59c4272f377a05495e5072ac7b84a5a34`.
  The installed backend and GUI match the release package with SHA-256
  `b1aeba7f0824a1217e0c1a412b5410c6a7dc6f48ab889d4e76f088b9fcaf5cd5` and
  `bd94a84882e272f81efaf184d2e7e2ac48043d580426cf04c1e115dff34e96d0`.
- The old and new cores both validate the existing `profile.json` and
  `runtime.json`. Linux regression tests now report 87 tests passed; the
  release checksum manifest and installed systemd units also validate.
- `smart-box@e.service` and its watchdog remain `masked-runtime/inactive`.
  `SmartBox` does not exist, FlClash is not running, and direct Baidu returns
  HTTP 200. Do not unmask or start the proxy during ordinary desktop use until
  a deliberate live TUN regression window is available.
- The installation rollback bundle is
  `verification/desktop-install-20260821/`. Its explicit-target `ROLLBACK.sh`
  restored an independent copy of the core, backend, and GUI to all three
  archived pre-install hashes, then the restored core validated both configs.
- Detached `ip rule` priorities 9000-9010 and table 2022 still look like
  FlClash residue. They have no confirmed ownership marker and were left
  untouched; the default route and direct traffic are currently healthy.
- Android static/unit checks passed for the signed arm64 APK, but `adb devices`
  has no connected phone. VPN, DNS, Telegram, and Douyin live regression remain
  pending a connected device.

## 2026-08-21 Android device update and signing correction

- The V2352A phone was reachable as `adb` device `10AE6J03LC001JL`. Its existing
  `io.nekohasekai.sfa.smartbox` package used certificate SHA-256
  `8de57370597def2d26d94973d5c63cee02e81ea3af2a20aab39a26e4808878b4`.
- The generic refreshed APK was correctly rejected by Android because it was
  signed with a different debug certificate (`2e8d0212...`). Before changing
  anything, the app data was archived to
  `verification/android-device-20260821/pre-update/app-data.tar` with SHA-256
  `dede2f69bda51d3f80d0b19ddeca68097992cd7f5fd95d705816c6e128191592`.
- A device-compatible copy was generated and installed in place, preserving
  the existing profile data. The reusable artifact is
  `dist/smart-box-0.1.0-core-1.14.0-beta.14-android-arm64-device-signed.apk`
  with SHA-256
  `20236ebb7e188ba393ebc9f1335a1ada800a6e32a389281a0da1646979f0cee7`.
  Its certificate matches the phone. The source generic APK was left
  unchanged.
- `scripts/sign-android-device.sh` now performs this signing without storing
  passwords; it takes `ANDROID_KEYSTORE_PASS` and `ANDROID_KEY_ALIAS_PASS` from
  the environment and defaults to `$HOME/.android/smart-box-device.keystore`.
- After installation, the package data files remained present and the phone's
  smart-box VPN was explicitly stopped. The phone later disconnected from ADB,
  so a fresh controlled Telegram/Douyin traffic assertion is still pending;
  no such assertion is claimed by this update.
- A local `flclash --version` probe briefly launched the GUI despite the flag;
  those exact GUI/Core processes were stopped immediately. The `FlClash`
  interface and 7890 listeners are now absent, `smart-box@e.service` remains
  masked, and direct Baidu access is HTTP 200. Detached FlClash policy rules
  remain untouched for ownership reasons.
- Full artifacts, exact commands/results, and a dry-run rollback script are in
  `verification/android-device-20260821/`. The live rollback was not applied;
  the updated package remains installed.

## 2026-08-21 Persistent Smart scores and r18 desktop install

- Smart node quality now survives a core restart in the independent
  `smart_score` cache bucket. On startup, each Smart group restores standard
  URL-test latency, recent success/failure state, and immediately places the
  lowest-cost (highest-quality) compatible node first, before scheduling a new
  probe. Explicit selector/region choices remain in the selector cache and are
  not overwritten by automatic scoring.
- Business connections update success and failure state but never refresh an
  old URL-test RTT. A failed latest probe makes the old RTT neutral; successful
  probe latency decays toward neutral over seven days. Recent failures retain a
  short penalty, writes are coalesced asynchronously and flushed on shutdown,
  and duplicate probe results are counted only once.
- Every real node receives a one-way fingerprint of its connection parameters,
  excluding its display tag. The stable namespace is `smart-box-nodes-v1`.
  Reordering or renaming nodes does not discard useful history; changing one
  endpoint invalidates only that endpoint. The cache prunes removed/replaced
  identities per group, removes corrupt/future/older-than-eight-day orphan
  records at startup, and repeats global cleanup every 24 hours.
- Background probing now combines four current leaders with four rotating
  exploration candidates. This preserves good startup choices without starving
  new or previously unknown nodes. A business connection still limits attempts
  to eight candidates and at most two concurrent hedges.
- Linux adds the namespace and fingerprints only to the generated
  `~/.config/smart-box/runtime.json`; the existing `profile.json` SHA-256 remains
  `e02382dcb0ae809c85aa4ebf111b1888d7aa6c935e8929c3d209e324b63b3689`.
  All 18 Smart groups have complete identity maps. The previous runtime is kept
  at `~/.config/smart-box/runtime.json.before-r18-persistent-score` with mode
  `0600`. No subscription fetch occurred during this change.
- The no-TUN restart integration test started with `slow`, measured `fast` at
  11 ms versus `slow` at 251 ms, selected `fast`, restarted the core, and again
  selected `fast` before any new probe. Core normal/race/Linux-386 tests, vet,
  converter tests, Linux 108-test suite, deterministic builds, runtime checks,
  package checks, and independent-copy rollback tests pass. Full core testing
  retains only the known netns permission, libbox linkname, and direct
  `1.1.1.1:443` TLS-fragment environment failures.
- Installed package is `smart-box-local 0.1.0.r20260821.18-1`; Pacman reports
  31 files and zero altered files. Installed core SHA-256 is
  `5666afab955e6044adc10c9d9c367bf8468ebefddaa231f5a0fa64b9829ab1cb`.
  The offline tarball SHA-256 is
  `6dd8b4db129d4962ebb539bbe4e2447125fe4c7a02085be7a9e29ff1d5de9acc`;
  the Pacman package SHA-256 is
  `b693625c0be7933260b22fe57c34f60727b67b5289920c0259574be88cb48f9f`.
- A watchdog-protected live test produced an active TUN, both local listeners,
  Baidu HTTP 200, gstatic HTTP 204, and GitHub HTTP 200. The mandatory exit
  cleanup then stopped the main service/watchdog, removed `SmartBox`, and
  restored direct Baidu HTTP 200. Final state intentionally leaves the tray GUI
  active in `smart-box-r18-gui.service`, while `smart-box@e.service` and its
  watchdog are inactive and no TUN owns the network.
- Complete source diff, original hashes, exact results, package, and tested
  rollback are in `verification/persistent-node-score-20260821/`. The first
  ordinary use after r18 starts with an empty Smart score bucket; manual or
  activated background probes populate it, and subsequent starts reuse it.
