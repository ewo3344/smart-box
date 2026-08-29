# Upstream Sources And Attribution

smart-box is a modified distribution, not an official SagerNet release.

## Core

- Upstream: <https://github.com/SagerNet/sing-box>
- Imported base: `db1053f8bc16c860225afc97ac6417e42a81dc64`
- Local component: `core/`
- Public smart-box ref: `6039b0bd46cc605845f5c00840eee73505690a3e`
- Public source: <https://github.com/ewo3344/smart-box-core>
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
- Public smart-box ref: `fd6e4589fe249e8b69c0f4888077e43ee5a64906`
- Public source: <https://github.com/ewo3344/smart-box-android>
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

## Submodule Publish Flow

`core/` and `android/` are gitlinks to the public forks above, not to SagerNet
baselines. Daily work may sit on an uncommitted SagerNet-based worktree; that
tree is not the published pointer.

Publish in one direction only:

1. 工作树 — keep smart-box changes in the local `core/` and `android/` checkouts.
2. 快照 — create a commit on the fork history that contains those changes.
3. push fork — `git push` that snapshot to `ewo3344/smart-box-core` or
   `ewo3344/smart-box-android` so the commit is reachable as a ref.
4. 更新 gitlink — only after `scripts/publish-submodules.sh --check` passes,
   move the superproject gitlink to that fork snapshot.

`scripts/publish-submodules.sh --check` is fail-closed:

- A gitlink SHA that `git ls-remote` on the fork cannot see is rejected.
- A core pointer whose tree lacks `protocol/group/smart.go` is rejected.
- An Android pointer whose `app/build.gradle.kts` lacks
  `applicationId = "io.nekohasekai.sfa.smartbox"` is rejected.

Do not use `git add android`, `git add core`, `git add .`, `git add -A`, or
`git commit -a`. Those commands can rewind the public gitlink onto a SagerNet
baseline with no smart-box code. Do not run `git submodule update` to “clean”
a dirty smart worktree.

HEAD gitlinks must remain the fork snapshots that contain smart code
(`6039b0bd…` / `fd6e4589…` or later fork-reachable successors).

## Public Source Policy

This source repository intentionally excludes device signing keys, private
subscription URLs/tokens, generated runtime profiles, test-device logs,
verification snapshots, build outputs, and handoff archives. Releases publish
reproducible source and checksums, never local credentials.
