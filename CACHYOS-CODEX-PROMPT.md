# CachyOS Codex continuation prompt

Paste the following into Codex when continuing work in this CachyOS workspace:

```text
继续维护 /home/e/workspace/smart-box 中的 smart-box 项目。先完整阅读
README.md、HANDOFF.md、linux/README.md 和 converter/ROUTING.md，再分别在
android/ 与 core/ 执行 git status --short。项目根目录本身不是 Git 工作树。

Current milestone: CachyOS Linux x86_64 desktop client is complete.

重要约束：
1. android/ 与 core/ 的大量未提交改动都是需要保留的现有成果。禁止 reset、
   checkout、clean、覆盖或提交，除非用户之后明确要求。
2. 不得在回复、日志、补丁或新文档中输出订阅私密路径、供应商 URL、token、
   节点凭据或 SSH 密码。界面和诊断输出必须继续遮蔽这些数据。
3. Do not pull the Raspberry Pi provider subscription. 除非用户明确要求立即拉取，
   不得调用转换器的刷新接口，不得重启任何会触发供应商拉取的远端服务。
4. Linux 客户端的订阅拉取必须保持纯手动。登录、重启、打开 GUI、启动 core、
   切换模式或切换策略均不得隐式拉取。
5. 涉及 TUN、DNS 或 FlClash 的实测必须设置自动恢复；无论成功还是失败，最终都要
   恢复下述机器状态。不得让两套 TUN 同时接管网络。
6. 修改前先复现并保留证据，修改后运行与风险相称的测试；不要重复 HANDOFF.md
   已记录失败且没有新假设的实验。

当前稳定交付：
- Linux 源码：linux/
- 发布目录：dist/smart-box-0.1.0-linux-x86_64/
- 发布包：dist/smart-box-0.1.0-linux-x86_64.tar.gz
- 启动命令：smart-box
- 系统服务：smart-box@e.service
- FlClash 用户服务：app-FlClash@autostart.service
- 本机运行配置：~/.config/smart-box/runtime.json
- 转换器原始配置：~/.config/smart-box/profile.json
- 定制 core SHA-256：
  47dd5dd0210236f443af384bffe553b9b69562f45cfca445cce20334d4179ed0

已完成的 Linux 功能包括 PySide6 桌面与托盘、状态和流量统计、Rule/Global/
Direct/节能模式、26 个 selector、运行中选择与停机预选地区、域名白名单强制
DIRECT、域名黑名单强制基准 Smart、手动订阅校验与原子替换、日志、登录启动设置、
FlClash 冲突检测和失败回滚。不要把“黑名单”误改成阻断，它在当前产品语义中表示
强制代理。

Linux runtime 必须与转换器原始配置隔离。它继续使用已在本机实测通过的 gVisor
TUN 栈、SmartBox 接口、127.0.0.1:20808 mixed 监听和 127.0.0.1:20809 Clash API。
启动期间 systemd-resolved 应把 172.19.0.2 与 fdfe:dcba:9876::2 注册到 SmartBox
并设置路由域 ~.；停止期间应撤销链路 DNS 并刷新缓存。不得把这些 Linux 本机覆盖
写回 profile.json 或树莓派配置。

继续保留订阅状态伪节点过滤。当前原始配置有 137 个代理节点，Linux runtime 应只有
129 个可用代理节点且状态伪节点为 0。AI Fallback 自动候选中香港节点必须为 0；香港
区域 Smart 仍可供其他策略手动选择。默认选择应保持：基准为“🚀 全局 Smart”、
AI 为“🤖 AI Fallback”、Telegram 为“✈️ Telegram Fallback”。

权限边界不得扩大：core 文件不带永久 capability；smart-box@.service 仍以桌面用户
运行，并仅在进程内授予 HANDOFF.md 记录的能力。Polkit 规则只允许活动的本地 wheel
用户管理与其用户名匹配的 smart-box 模板实例。服务和 GUI 均保持默认不自启。

每次发布前至少执行以下离线验收：
- python3 -m compileall -q linux
- PYTHONPATH=linux python3 -m unittest discover -s linux/tests -v
- sh -n linux/build-package.sh linux/install.sh linux/uninstall.sh linux/smart-box
  linux/smart-box-profile
- systemd-analyze verify linux/smart-box@.service
- 使用真实 core 检查 profile.json 与生成的 runtime.json
- 在发布目录执行 sha256sum -c SHA256SUMS
- 比对源码、发布目录和 /usr/local 已安装文件的哈希

只有确实修改 UI 时才重做截图验收；需要实测网络时，必须使用 trap 或等价机制保证
恢复。最终状态字段必须逐项确认并报告：
smart-box@e.service=inactive
app-FlClash@autostart.service=active
SmartBox interface=absent
FlClash interface=present

开始新改动前，先说明准备验证的具体问题和唯一首个实验，然后直接执行。没有新的
缺陷或功能请求时，不要无理由改动稳定实现；只复核交付状态并向用户说明运行
smart-box 后点击“切换到 smart-box”即可使用现有配置。
```
