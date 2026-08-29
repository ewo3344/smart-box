# Upstream Sources And Attribution

smart-box is a modified distribution, not an official SagerNet release.

## Core

- Upstream: <https://github.com/SagerNet/sing-box>
- Imported base: `db1053f8bc16c860225afc97ac6417e42a81dc64`
- Local component: `core/`
- License: GNU General Public License, version 3 or later. The upstream
  `core/LICENSE` file and copyright notices are retained.

The smart-box core adds the adaptive `smart` outbound group, score and
destination-memory persistence, failure penalties, and the related API/schema
support. It remains compatible with the upstream configuration and build model
where those extensions are not used.

## Android Client

- Upstream: <https://github.com/SagerNet/sing-box-for-android>
- Imported base: `8f6343802a6d8e0fa478d9e642cbb58c147e671b`
- Local component: `android/`
- License: GNU General Public License, version 3 or later. The upstream
  `android/LICENSE` file and copyright notices are retained.

The smart-box Android client has its own package identity and consumes the
smart-box core. It is not an official upstream Android client and must not be
represented as one.

## Project Components

The Linux client, Windows client, and subscription converter in this repository
are smart-box-specific integration code. They invoke or consume the modified
core but do not change the licensing or attribution requirements of the
upstream components.

## Public Source Policy

This source repository intentionally excludes device signing keys, private
subscription URLs/tokens, generated runtime profiles, test-device logs,
verification snapshots, build outputs, and handoff archives. Releases publish
reproducible source and checksums, never local credentials.
