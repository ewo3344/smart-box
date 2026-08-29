# smart-box 快速参考指南

**版本**: v0.1.0  
**更新**: 2026-08-24

---

## 一页速查

### 版本管理

```bash
# 查看当前版本
scripts/version-manager.sh current

# 检查版本一致性
scripts/version-manager.sh check

# 更新版本号（所有组件）
scripts/version-manager.sh bump 0.2.0

# 验证版本号格式
scripts/version-manager.sh validate 1.0.0-rc.1
```

### Git 工作流

```bash
# 初始化仓库（首次）
scripts/init-git.sh

# 开发新功能
git checkout develop
git checkout -b feature/new-feature
git commit -m "feat(scope): description"
git push origin feature/new-feature
# 创建 PR → develop

# 发布新版本
git checkout -b release/v0.2.0 develop
scripts/version-manager.sh bump 0.2.0
# 更新 CHANGELOG.md
git commit -am "chore: prepare release 0.2.0"
# 测试和修复 bug
git checkout main
git merge --no-ff release/v0.2.0
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin main --tags
git checkout develop
git merge --no-ff release/v0.2.0

# 紧急修复
git checkout -b hotfix/v0.1.1 main
# 修复 bug
scripts/version-manager.sh bump 0.1.1
git commit -am "fix: critical bug"
git checkout main
git merge --no-ff hotfix/v0.1.1
git tag -a v0.1.1
git checkout develop
git merge --no-ff hotfix/v0.1.1
```

### 构建和测试

```bash
# Linux - 完整验证
cd /home/e/workspace/smart-box
scripts/verify-release.sh --allow-live

# Linux - 构建发布包
cd linux
./build-package.sh
cd ../dist/smart-box-0.1.0-linux-x86_64
./install.sh

# Android - 构建和测试
cd android
./gradlew testOtherDebugUnitTest
./gradlew assembleOtherDebug
../scripts/sign-android-device.sh  # 需要环境变量

# Windows - 构建
cd windows
.\scripts\build-windows.ps1

# Converter - 测试
cd converter
env GOTOOLCHAIN=go1.26.5 go test ./...
env GOTOOLCHAIN=go1.26.5 go test -race ./...

# Converter - 交叉编译 ARM64
env CGO_ENABLED=0 GOOS=linux GOARCH=arm64 GOTOOLCHAIN=go1.26.5 \
  go build -buildvcs=false -trimpath -ldflags '-s -w' \
  -o smart-box-converter-linux-arm64 .
```

### 运行和调试

```bash
# Linux - 启动客户端
smart-box

# Linux - 命令行操作
smart-box-profile status
smart-box-profile prepare
smart-box-profile validate
systemctl status "smart-box@$(id -un).service"
journalctl -u "smart-box@$(id -un).service" -f

# Linux - 源测速
smart-box-profile mirror-benchmark --repo all
smart-box-profile mirror-apply --repo arch

# Android - 查看日志
adb logcat | grep -E "SmartBox|VPN|TUN"
adb shell dumpsys package io.nekohasekai.sfa.smartbox

# 树莓派 - 服务状态
ssh pi@192.168.2.102
sudo systemctl status smart-box-converter.service
sudo systemctl status smart-box.service
sudo journalctl -u smart-box-converter.service -n 100
```

### 发布检查清单

```bash
# 1. 更新版本号
scripts/version-manager.sh bump 0.X.Y

# 2. 更新变更日志
# 编辑 CHANGELOG.md

# 3. 运行完整测试
scripts/verify-release.sh --allow-live  # Linux
cd android && ./gradlew test assembleOtherRelease
# Windows 测试（手动或 CI）

# 4. 构建所有平台
linux/build-package.sh
cd android && ./gradlew assembleOtherRelease
# Windows 构建

# 5. 生成校验和
cd dist
sha256sum smart-box-* > SHA256SUMS

# 6. 创建 Git tag
git tag -a v0.X.Y -m "Release version 0.X.Y"
git push origin main --tags

# 7. 创建 GitHub Release
# 上传产物和 CHANGELOG
```

### Commit 消息规范

```bash
# 格式
<type>(<scope>): <subject>

# Type
feat      # 新功能
fix       # Bug 修复
docs      # 文档
style     # 格式
refactor  # 重构
perf      # 性能优化
test      # 测试
chore     # 构建/工具

# Scope
core      # Core (Go)
linux     # Linux 客户端
android   # Android 客户端
windows   # Windows 客户端
converter # Converter

# 示例
git commit -m "feat(android): add multi-subscription support"
git commit -m "fix(linux): resolve memory leak in traffic monitor"
git commit -m "docs: update installation guide"
```

### 常用路径

```bash
# 配置文件
~/.config/smart-box/profile.json     # Linux: Converter 原始配置
~/.config/smart-box/runtime.json     # Linux: 运行副本
~/.config/smart-box/settings.json    # Linux: 本地设置
~/.local/state/smart-box/cache.db    # Linux: Smart 缓存
/var/lib/smart-box/profile.json      # 树莓派: 持久化配置
%LOCALAPPDATA%\smart-box             # Windows: 配置目录

# 日志
journalctl -u "smart-box@$(id -un).service" -n 200
adb logcat -s SmartBox
# Windows 事件查看器

# 备份
~/.local/state/smart-box/backups/    # Linux: journal 备份
verification/*/                       # 测试证据
```

### 故障排查

```bash
# Linux - 服务无法启动
systemctl status "smart-box@$(id -un).service"
journalctl -u "smart-box@$(id -un).service" -n 50
smart-box-profile validate  # 检查配置

# Linux - TUN 不可用
ip link show SmartBox
resolvectl status SmartBox
systemctl status "smart-box-watchdog@$(id -un).service"

# Linux - 恢复直连（fail-open）
systemctl stop "smart-box@$(id -un).service"
# Watchdog 会自动清理 TUN 和 DNS

# Android - VPN 无法启动
adb logcat -s SmartBox:V
adb shell dumpsys vpn
# 检查 VPN 权限和 TUN 权限

# 树莓派 - Converter 不响应
ssh pi@192.168.2.102
sudo systemctl status smart-box-converter.service
curl http://192.168.2.102:38473/healthz
sudo journalctl -u smart-box-converter.service -n 100

# 回滚到上一版本
smart-box-profile rollback --to-version 0.1.0
# 或手动恢复 ~/.local/state/smart-box/backups/
```

### 性能指标（参考值）

```bash
# 启动时间
Linux:   < 3 秒（有缓存）
Android: < 5 秒（冷启动）
Windows: < 3 秒

# 内存占用
Linux:   ~150 MB（桌面）
Android: ~80 MB（运行中）
Windows: ~120 MB

# 连接延迟
Smart 选择: < 10 ms（有缓存）
首次连接:   < 100 ms（本地网络）

# CPU 占用
空闲:   < 1%
活跃:   5-10%（有流量）
```

### 环境变量

```bash
# Android 签名
export ANDROID_KEYSTORE_PASS="your-password"
export ANDROID_KEY_ALIAS_PASS="your-password"
scripts/sign-android-device.sh

# Converter 测试
export SMART_BOX_CORE=/path/to/smart-box-core
export SMART_BOX_LIVE_RULESETS=1  # 启用实时规则集测试
export GOTOOLCHAIN=go1.26.5  # 与根 TOOLCHAIN_VERSION 保持一致

# Linux 测试
export PYTHONPATH=linux
export QT_QPA_PLATFORM=offscreen  # 无头测试
```

### 关键文档链接

- [完整开发计划](./DEVELOPMENT-PLAN.md)
- [项目总结](./PROJECT-SUMMARY.md)
- [版本控制](./VERSION-CONTROL.md)
- [变更日志](./CHANGELOG.md)
- [Linux README](./linux/README.md)
- [Converter README](./converter/README.md)
- [路由规则](./converter/ROUTING.md)

---

**提示**: 将此文件加入书签或打印，随时查阅常用命令。
