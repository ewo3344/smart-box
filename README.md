# smart-box

> **Upstream notice:** smart-box is an independently maintained modified
> distribution of [SagerNet/sing-box](https://github.com/SagerNet/sing-box) and
> [SagerNet/sing-box-for-android](https://github.com/SagerNet/sing-box-for-android).
> It is not an official SagerNet release, is not affiliated with SagerNet, and
> is not endorsed by SagerNet. Upstream copyright notices, source attribution,
> and GPL-3.0-or-later licensing are retained.

smart-box combines a modified sing-box core with Linux, Windows, Android, and
Raspberry Pi integration code. It adds an adaptive `smart` outbound group,
per-destination node memory, persisted scores, failed-dial penalties, regional
selection, and detailed policy routing.

The exact imported source bases and local changes are documented in
[UPSTREAMS.md](UPSTREAMS.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `core/` | Modified sing-box core submodule, including the `smart` outbound. |
| `android/` | Modified Android client submodule. |
| `linux/` | CachyOS/KDE desktop client, packaging, systemd units, and tests. |
| `windows/` | Windows WPF desktop client. |
| `converter/` | Private Raspberry Pi subscription converter and routing generator. |
| `scripts/` | Reproducible build, signing, release verification, and version tools. |

`core/` and `android/` are separate public repositories so their upstream
histories and GPL notices remain clear:

- <https://github.com/ewo3344/smart-box-core>
- <https://github.com/ewo3344/smart-box-android>

## Clone

Clone with every nested dependency:

```bash
git clone --recurse-submodules https://github.com/ewo3344/smart-box.git
cd smart-box
git submodule update --init --recursive
```

The recursive update is required because the component repositories also use
their own upstream submodules.

## Smart routing

The `smart` outbound is an adaptive candidate group. It uses URL-test latency,
recent connection outcomes, failure penalties, score decay, and a small
exploration set to choose candidates. Scores and destination preferences are
stored locally by the client and restored after restart. A group begins
background probing only after it carries real traffic, while an explicit manual
test still probes immediately.

The converter generates regional Smart groups and service selectors. Each
selector can follow the baseline, use an automatic Fallback pool, use a
specific region, or be direct where appropriate. The AI policy intentionally
excludes Hong Kong from its automatic and manual regional choices. See
[converter/ROUTING.md](converter/ROUTING.md) for the full policy matrix.

## Versioning

`VERSION` is the smart-box product version. The upstream core version remains a
separate compatibility value in `android/version.properties` and Linux/Windows
metadata. Check or update product metadata from the repository root:

```bash
scripts/version-manager.sh check
scripts/version-manager.sh current
scripts/version-manager.sh bump 0.2.0
```

The version tool updates only smart-box product metadata. It does not rewrite
the upstream core version or create a Git tag; create and push a reviewed tag
after a successful build and test run.

## Linux

The Linux client targets CachyOS/KDE and provides TUN lifecycle management,
fail-open cleanup, a tray application, policy selectors, domain allow/proxy
lists, mirror benchmarks, logs, traffic statistics, and per-group latency
tests. It intentionally stops FlClash before taking ownership of the network
path and restores normal connectivity when startup validation fails.

Prerequisites include Go, Python 3, PySide6, systemd, Polkit, and build tools.
Build a self-contained x86_64 package from the repository root:

```bash
./scripts/build-linux.sh
package_dir="dist/smart-box-$(tr -d '\r\n' < VERSION)-linux-x86_64"
cd "$package_dir"
./install.sh
smart-box
```

The installer asks for Polkit authorization only for its narrowly scoped
system installation. It does not contain a subscription address, profile,
cache, signing key, or device state. See [linux/README.md](linux/README.md)
for runtime behavior and rollback details.

## Windows

Build on Windows with Go, .NET SDK, and the recursively initialized core
submodule available on `PATH`:

```powershell
.\scripts\build-windows.ps1
```

The output is created below `dist/`. The Windows client uses
`%LOCALAPPDATA%\smart-box` for local settings and a validated runtime profile,
runs in the notification area, controls the bundled Smart core, and can switch
the Windows system proxy at `127.0.0.1:20808`.

## Android

The Android client requires Java 17, Android SDK/NDK, Go, and an initialized
Go mobile toolchain. Install `gomobile` and run `gomobile init` so
`$GOPATH/bin/gobind` exists, then initialize the `core/` and `android/`
submodules. Build the Play debug APKs on Windows with:

```powershell
.\scripts\build-android.ps1
```

Artifacts are written to `dist/`. To sign an APK, supply a keystore path and
the passwords through environment variables; no keystore or certificate is
stored in this repository:

```bash
ANDROID_KEYSTORE_PASS=... ANDROID_KEY_ALIAS_PASS=... \
  scripts/sign-android-device.sh INPUT.apk OUTPUT.apk KEYSTORE
```

The client has its own package identity, `io.nekohasekai.sfa.smartbox`, and is
not an official upstream Android build. It preserves a downloaded converter
profile and applies Android-specific TUN adjustments only to its runtime copy.

## Raspberry Pi converter

The converter accepts private provider URLs from a root-owned configuration
outside this repository, validates and merges profiles, probes candidates, and
serves one private sing-box profile plus cached binary rule sets. It is designed
to keep source credentials and private endpoint tokens out of clients, logs,
and source control.

```bash
cd converter
go test ./...
```

Start from [converter/config.example.json](converter/config.example.json), copy
it outside the checkout, and populate it with a deployment-specific private
token and source URLs. The Raspberry Pi deployment units are in
`converter/deploy/`.

## Verification

Run the portable source checks after initializing submodules:

```bash
scripts/verify-release.sh
```

Use `scripts/verify-release.sh --android` when the Android SDK and Gradle
environment are available. Release artifacts, generated profiles, runtime
caches, device logs, signing material, and local subscription configuration are
intentionally ignored. Published artifacts belong in GitHub Releases with
their checksums.

## License and attribution

The smart-box integration code is GPL-3.0-or-later; see [LICENSE](LICENSE).
The `core/` and `android/` submodules retain their upstream GPL licenses,
copyright notices, and additional attribution. Do not represent smart-box as
an official SagerNet product.
