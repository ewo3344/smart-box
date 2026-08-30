# v0.1.1 Release Checklist

**Target Date**: 2026-09-12
**Type**: Patch Release
**Focus**: 测试覆盖和稳定性收敛

---

## 准备阶段（Release - 7 days）

- [ ] 创建 release 分支: `git checkout -b release/v0.1.1`
- [ ] 更新版本号: `./scripts/version-manager.sh bump 0.1.1 --yes`
- [ ] 验证版本一致性: `./scripts/version-manager.sh check`
- [ ] 更新 CHANGELOG.md（将 `[未发布]` 改为 `[0.1.1] - 2026-09-12`）
- [ ] 冻结新功能（仅接受 bugfix）

---

## 测试阶段（Release - 5 days）

### Linux

- [ ] `scripts/verify-release.sh --allow-live`
- [ ] Linux 单元测试全部通过
- [ ] 发布清单 checksum 验证通过

### Converter

- [ ] `cd converter && env GOTOOLCHAIN=go1.26.5 go test ./...`
- [ ] `env GOTOOLCHAIN=go1.26.5 go test -race ./...`

### Android

- [ ] `scripts/android-full-matrix.sh --serial 10AE6J03LC001JL`
  - 验收标准：`START=PASS STOP=PASS FAILURES=0 BLOCKED_COUNT=0`
  - `RESULT=MANUAL_REQUIRED` + `exit 2` 为预期（15 项人工计数）
- [ ] `docs/MANUAL-MATRIX-T001.md` 签核完成（12/15 PASS，3/15 DEFERRED）

### 树莓派

- [ ] `scripts/verify-raspberry-pi.sh --host smart-box-pi --out verification/pi-health-<timestamp>`
- [ ] converter/core 服务 active
- [ ] Route-bypass 规则生效

### Windows（环境就绪时）

- [ ] `scripts/verify-windows.ps1 -OutputDirectory verification\windows-verify-<timestamp>`
- [ ] 构建成功，托盘应用可启动

### 性能回归

- [ ] 对比 v0.1.0：启动时间 / 内存占用 / 连接建立延迟
- [ ] 无明显回归（±10% 以内）

---

## 构建阶段（Release - 3 days）

- [ ] `scripts/build-all-platforms.sh`
- [ ] 生成 SHA256SUMS
- [ ] 验证所有产物可安装：
  - [ ] Linux: tar.gz 解压 + `install.sh`
  - [ ] Android: APK 安装（覆盖安装保留数据）
  - [ ] Windows: zip 解压 + exe 启动

### Submodule 发布门禁

- [ ] `scripts/publish-submodules.sh --check`
- [ ] 输出 `CHECK PASS`（gitlink 在 fork 远端可达且含 smart 代码）

---

## 文档阶段（Release - 2 days）

- [ ] 更新 README.md（如有变更）
- [ ] 编写 `docs/RELEASE-NOTES-v0.1.1.md`
- [ ] 更新版本兼容性说明（如有新依赖或平台要求变化）
- [ ] 确认 `docs/MANUAL-MATRIX-T001.md` 无订阅 URL、Token、账号、私密路径

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

**维护者**: @ewo3344
**首次创建**: 2026-08-30
**最后更新**: 发布日填写
