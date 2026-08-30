# v0.1.1 Release Checklist

**Target Date**: 2026-09-12
**Type**: Patch Release
**Focus**: 测试覆盖和稳定性收敛

---

## 准备阶段（Release - 7 days）

- [x] 创建 release 分支: `git checkout -b release/v0.1.1`
- [x] 更新版本号: `./scripts/version-manager.sh bump 0.1.1 --yes`
- [x] 验证版本一致性: `./scripts/version-manager.sh check`
- [x] 更新 CHANGELOG.md（将 `[未发布]` 改为 `[0.1.1] - 2026-09-12`）
- [x] 冻结新功能（仅接受 bugfix）

---

## 测试阶段（Release - 5 days）

### Linux

- [x] `scripts/verify-release.sh --allow-live`
- [x] Linux 单元测试全部通过
- [x] 发布清单 checksum 验证通过

### Converter

- [x] `cd converter && env GOTOOLCHAIN=go1.26.5 go test ./...`
- [x] `env GOTOOLCHAIN=go1.26.5 go test -race ./...`

### Android

- [ ] `scripts/android-full-matrix.sh --serial 10AE6J03LC001JL`
  - 验收标准：`START=PASS STOP=PASS FAILURES=0 BLOCKED_COUNT=0`
  - `RESULT=MANUAL_REQUIRED` + `exit 2` 为预期（15 项人工计数）
  - **BLOCKED**：0.1.1 覆盖安装因签名不匹配未执行，未重跑矩阵。阻塞可发布。
- [x] `docs/MANUAL-MATRIX-T001.md` 签核完成（12/15 PASS，1/15 FAIL 第 9 项，2/15 DEFERRED 第 10、12 项）

### 树莓派

- [x] `scripts/verify-raspberry-pi.sh --host smart-box-pi --out verification/pi-health-<timestamp>`
- [x] converter/core 服务 active
- [x] Route-bypass 规则生效

### Windows

- [x] 交叉编译产物生成（Linux 上 `scripts/build-windows.ps1`，产出 `dist/smart-box-0.1.1-windows-x64.zip`，PE32+）
- [ ] 运行时验证（需 Windows 机器：托盘启动、系统代理、core 崩溃重启）
      **未完成**：无 Windows 真机。Release Notes 已标注运行时未验证。阻塞可发布。

### 性能回归

- [ ] 对比 v0.1.0：启动时间 / 内存占用 / 连接建立延迟
      未完成：本轮无对比数据。非本 patch 硬阻塞，但不构成可发布。
- [ ] 无明显回归（±10% 以内）
      同上。

---

## 构建阶段（Release - 3 days）

- [ ] `scripts/build-all-platforms.sh`
      未完成：本分支未单独执行；Linux 单测已由 `verify-release.sh` 覆盖。非阻塞。
- [x] 生成 SHA256SUMS
- [ ] 验证所有产物可安装：
  - [x] Linux: tar.gz 解压 + `install.sh`（2026-08-30 冒烟：两 unit active，卸载后 SmartBox 消失、直连恢复，日常出口重装 0.1.1）
  - [ ] Android: APK 安装（覆盖安装保留数据）
        **BLOCKED**：`~/.android/debug.keystore` SHA-256 `2e8d0212…` ≠ 设备 0.1.0 签名 `8de57370…`。未卸载、未 `install -r`。阻塞可发布。
  - [ ] Windows: zip 解压 + exe 启动
        部分：Linux 上已解压（README + config + 两 exe）且 PE32+ 确认；**exe 启动未做**。阻塞可发布。

### Submodule 发布门禁

- [x] `scripts/publish-submodules.sh --check`
- [x] 输出 `CHECK PASS`（gitlink 在 fork 远端可达且含 smart 代码）

---

## 文档阶段（Release - 2 days）

- [x] 更新 README.md（如有变更）（本 patch 无需改产品 README）
- [x] 编写 `docs/RELEASE-NOTES-v0.1.1.md`
- [x] 更新版本兼容性说明（如有新依赖或平台要求变化）（`docs/DEVICE-MATRIX.md`）
- [x] 确认 `docs/MANUAL-MATRIX-T001.md` 无订阅 URL、Token、账号、私密路径

---

## 发布日

### Git 操作

- [ ] 合并 release 分支到 main: `git checkout main && git merge --no-ff release/v0.1.1`
- [ ] 创建 tag: `git tag -a v0.1.1 -m "Release v0.1.1"`
- [ ] 推送: `git push origin main --tags`
- [ ] 合并 release 分支到 develop: `git checkout develop && git merge --ff-only release/v0.1.1`
- [ ] 推送 develop: `git push origin develop`

### GitHub Release

- [ ] 创建 GitHub Release（关联 v0.1.1 tag）
- [ ] 上传发布产物：
  - smart-box-0.1.1-linux-x86_64.tar.gz + SHA256
  - smart-box-0.1.1-android-arm64.apk + SHA256
  - smart-box-0.1.1-windows-x64.zip + SHA256
- [ ] 粘贴 `docs/RELEASE-NOTES-v0.1.1.md` 内容
- [ ] 标记为 Latest Release

### 冒烟测试

- [ ] 从 GitHub Release 下载 Linux tar.gz，全新安装，验证可启动
- [ ] `git clone --recursive https://github.com/ewo3344/smart-box.git` submodule 可递归克隆

---

## 发布后（Release + 1 day）

- [ ] 监控 GitHub Issues 和社区反馈（如有）
- [ ] 更新 `SMART-BOX-PLAN.md` 和 `DEVELOPMENT-PLAN.md` 的里程碑状态
- [ ] 删除本地 release 分支: `git branch -d release/v0.1.1`

---

## 回滚预案

如果发布后 24 小时内发现严重问题：

1. 撤回 GitHub Release（标记为 Pre-release 或草稿）
2. 不删除 Git tag（保留历史）
3. 从 main 创建 hotfix 分支修复
4. 发布 v0.1.2 替代

---

## 发布前结论（2026-08-30）

**不可发布。** 目标日 2026-09-12，距今约 13 天。本结论供放行判断，**不是发布信号**。禁止因此合并 `main`、打 `v0.1.1` tag、或创建 GitHub Release。

必须保持开放（不得改写成 PASS）：

- **Windows 运行时未验证**（交叉编译产物，无 Windows 真机：托盘启动、系统代理、core 重启）
- **Android 0.1.1 覆盖安装 BLOCKED**（签名不匹配：本机 debug.keystore `2e8d0212…` ≠ 设备 0.1.0 `8de57370…`；未卸载、未安装）

其余未勾且不构成放行的项：发布日 Git/GitHub/冒烟全部未做（硬边界）；性能回归未采数；`build-all-platforms.sh` 未单独跑。

已知缺陷（不改写为 PASS）：T001 第 9 项 FAIL（手动 urlTest 写 `failures`，组页不显示 +500；计划 v0.1.2）。

gitlink 仍为脏工作树（`M android` / `M core`），未暂存。

---

**维护者**: @ewo3344
**首次创建**: 2026-08-30
**最后更新**: 2026-08-30（vivo 0.1.1 覆盖安装 BLOCKED，结论：不可发布）
