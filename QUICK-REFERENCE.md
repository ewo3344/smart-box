# 命令速查

版本与分支规范见 `VERSION-CONTROL.md`；设计取舍见 `docs/DESIGN-NOTES.md`。

---

## smart-box-profile 子命令

```
prepare            生成本机运行配置
run                运行代理核心
fetch              拉取并验证订阅
set-url            保存订阅地址
set-stack          设置 Linux TUN 栈
mirror-benchmark   只读测速 pacman/paru 与 CachyOS 源
mirror-apply       应用最近一次源测速排序（需 root 授权）
validate           校验运行配置
dns                管理 SmartBox 链路 DNS
desktop-proxy      管理 KDE 应用使用的 SmartBox 本地代理
firewall           管理 SmartBox UFW 回程规则
cleanup            清理停止后残留的 SmartBox TUN
watchdog           执行 SmartBox 联网守护
status             输出运行状态
```

---

## 日常运行

```bash
smart-box                                        # 启动 GUI
smart-box-profile status
smart-box-profile validate
systemctl status "smart-box@$(id -un).service"
journalctl -u "smart-box@$(id -un).service" -f

smart-box-profile mirror-benchmark --repo all    # 只读测速
smart-box-profile mirror-apply --repo arch       # 需 root 授权
```

Android：

```bash
adb logcat | grep -E "SmartBox|VPN|TUN"
adb shell dumpsys package io.nekohasekai.sfa.smartbox
scripts/android-collect-logs.sh --serial <SERIAL>    # 脱敏日志采集
```

树莓派健康检查（只读）：

```bash
scripts/verify-raspberry-pi.sh --host <SSH_HOST>
```

---

## 测试与门禁

```bash
# Linux 完整回归
PYTHONPATH=linux QT_QPA_PLATFORM=offscreen \
  python3 -m unittest discover -s linux/tests -p 'test*.py'

# Linux 发布门禁
scripts/verify-release.sh --allow-live

# Converter（本机 go 版本可能高于发布工具链，必须显式 pin）
cd converter && env GOTOOLCHAIN=go1.26.5 go test ./...
cd converter && env GOTOOLCHAIN=go1.26.5 go test -race ./...

# 脚本语法
bash -n scripts/*.sh linux/build-package.sh

# 版本一致性（7 项须全 OK）
scripts/version-manager.sh check

# Submodule 指针安全（发布前必跑）
scripts/publish-submodules.sh --check
```

---

## 常用路径

```
~/.config/smart-box/profile.json     Converter 原始配置
~/.config/smart-box/runtime.json     本机运行副本
~/.config/smart-box/settings.json    本地设置
~/.local/state/smart-box/cache.db    Smart 分数与目标记忆
~/.local/state/smart-box/backups/    journal 备份
/var/lib/smart-box/profile.json      树莓派持久化配置（root 0600）
%LOCALAPPDATA%\smart-box             Windows 配置目录
```

---

## 故障排查

```bash
# 服务起不来
systemctl status "smart-box@$(id -un).service"
journalctl -u "smart-box@$(id -un).service" -n 50
smart-box-profile validate

# TUN 不可用
ip link show SmartBox
resolvectl status SmartBox
systemctl status "smart-box-watchdog@$(id -un).service"

# 恢复直连
systemctl stop "smart-box@$(id -un).service"
smart-box-profile cleanup        # 清理残留 TUN（服务须已停）

# Android VPN 起不来
adb logcat -s SmartBox:V
adb shell dumpsys vpn
```

回滚没有专门子命令。恢复旧配置从 `~/.local/state/smart-box/backups/` 手工取；
降级客户端重新安装对应版本的发布包。

---

## 环境变量

```bash
# Linux 测试
export PYTHONPATH=linux
export QT_QPA_PLATFORM=offscreen        # 无头

# Converter
export SMART_BOX_CORE=/path/to/smart-box-core
export SMART_BOX_LIVE_RULESETS=1        # 启用实时规则集测试
export GOTOOLCHAIN=go1.26.5             # 与 TOOLCHAIN_VERSION 一致

# Android 设备签名
export ANDROID_KEYSTORE_PASS=...
export ANDROID_KEY_ALIAS_PASS=...
scripts/sign-android-device.sh <in.apk> <out.apk> <keystore>
```
