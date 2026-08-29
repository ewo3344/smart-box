# smart-box for CachyOS Linux

这是 smart-box 的 Linux x86_64 桌面客户端，面向 CachyOS/KDE。发布包包含定制
`smart-box-core`、PySide6 客户端、systemd 模板服务、桌面入口以及可回滚卸载器。

## 功能

- 使用 `SmartBox` TUN 接管本机 IPv4/IPv6 流量，同时保留
  `127.0.0.1:20808` mixed 代理。CachyOS 运行副本默认使用 gVisor TUN 栈，
  设置页也保留 System 和 Mixed 供兼容性切换。
- 提供 Rule、Global、Direct、节能四种模式，模式选择在重启核心后仍保留。
- 读取配置中的全部 selector，可分别选择基准 Smart、AI、Telegram、流媒体、
  社交、游戏、开发服务、下载、广告及区域策略。
- 提供仅保存在本机的域名白名单和黑名单。白名单强制 `DIRECT` 与本地 DNS；
  黑名单表示强制走 `🎯 基准 Smart`，不是拒绝访问。域名会标准化、合并父子域，
  两份名单存在交叉时拒绝保存。
- 手动拉取树莓派转换器当前配置，并先后使用真实 core 校验原始配置和 Linux
  运行副本。客户端不会在登录、启动服务或重启系统时自动拉取订阅。
- 参考 GUI.for.SingBox 的桌面布局，使用顶部导航、TUN 控制条、持续操作结果、实时与
  累计流量、连接数、内存、流量曲线和大号模式按钮。通过本地 Clash API 读取实时状态；
  控制接口仅监听 `127.0.0.1:20809`。
- 设置页提供持久化夜间模式。复选框具有明确的选中状态；运行日志页会显示自动刷新
  开启或暂停、最近刷新时间与行数，手动刷新期间也会显示进行中状态。
- 设置页的“系统源测速”会只读测试当前启用的 Arch 与 CachyOS `Server`，其中 Arch
  结果同时适用于 pacman 和 paru。结果保存在用户状态目录；“应用最快源”才会请求
  root 授权替换对应 mirrorlist，并保留应用前备份。它测速的是官方二进制包源，
  不会改动 paru 的 AUR Git 来源。
- 分流策略页为每个 selector 提供手动测速。测速调用本地 Clash API 的分组延迟接口，
  显示最快延迟、成功数和失败节点明细，不会自动切换当前选择，也不会在打开页面时
  批量发起请求。
- 启动或重启前直接停止 FlClash，不弹出切换确认。正常停止 smart-box 时仍保持 FlClash
  关闭；如果接管或重启验收失败，则先停止 smart-box、清理 TUN 与链路 DNS，再恢复原先
  正在运行的 FlClash。启动流程依次验证 systemd 服务、`SmartBox` TUN、控制 API，并行
  检查百度、gstatic、Google、GitHub 和 Telegram；启动验收要求连续两轮保留国内直连、
  基础联网和至少一条独立代理路径。单个海外站点短暂失败会显示降级，但不会直接关闭 TUN。
- 主服务完成 DNS 注册后会执行一次联网验收；同时由独立的
  `smart-box-watchdog@<用户>.service` 持续复检。它以 root 运行，但只接收固定实例的
  配置目录和 systemd 单元名；GUI 退出、最小化或控制 API 暂时失效都不会停止这层保护。
  连续确认 TUN 的关键链路不可用时，watchdog 会停止主服务，由主服务的清理路径撤销
  `SmartBox` TUN、关联路由与链路 DNS，并确认接口消失后恢复系统直连。
- 启动后向 systemd-resolved 注册 `SmartBox` 链路 DNS，停止时撤销并清理缓存，避免
  FlClash Fake-IP 或 WLAN 污染 DNS 在两套 TUN 之间残留。只有服务中固定的 DNS 注册与
  撤销 helper 以 root 执行；代理核心仍以桌面用户和受限能力运行。
- Linux 运行副本额外剔除“剩余天数”“请更新客户端”“永久域名”等订阅状态节点，
  防止 Smart 初始选择落到不可用的伪节点；树莓派原始配置不被改写。

## Fallback 与 Smart

`Smart` 是定制 core 的持续测速组，会根据延迟、失败惩罚、容差和近期记忆在候选节点中
自动选择。未承载流量的组在核心启动和网络接口变化时不会测活；某组第一次实际承载
TCP/UDP 流量后，才会延迟启动该组的后台探测，手动测速仍立即执行。区域 Smart 只在
对应地区内选择，基准 Smart 则允许手动指定全局或某一地区。延迟、近期失败和成功时间
会写入 `cache.db`，下次启动在第一条连接前恢复；延迟历史在 7 天内逐步衰减为中性，
近期失败短期降权，每轮后台探测保留 4 个当前优选并轮换探索 4 个其他节点。单个业务
连接最多尝试 8 个候选且最多并发竞速 2 个；失败惩罚会让后续连接继续尝试尚未失败的
节点，避免并发重连遍历整个大池。手动地区选择和自动评分分别存储，切换地区不会清空
节点历史；订阅中的节点连接参数变化时，配置指纹会隔离旧分数。

`Fallback` 是业务专用的 Smart 候选池，不是“规则失效后随便找节点”。AI Fallback
排除香港并优先新加坡、日本、美国、台湾等可用地区；Telegram Fallback 使用
`https://telegram.org` 单独测活；流媒体和游戏也使用各自的候选范围。上层 selector
仍可手动改成某个区域、基准 Smart 或 DIRECT，Fallback 只负责默认自动选择。

## 安装

系统需要 `/usr/bin/python3`、PySide6、systemd、用户会话管理器和 Polkit。
CachyOS 当前环境已经具备这些组件。

```bash
cd /home/e/workspace/smart-box/dist/smart-box-0.1.0-linux-x86_64
./install.sh
smart-box
```

安装器只在一次 Polkit 授权期间写入 `/usr/local`。core 保持 root 所有且不携带永久文件
能力；系统级模板单元 `smart-box@<用户>.service` 仍以桌面用户身份运行，只在该进程内
授予：

```text
cap_dac_read_search,cap_net_admin,cap_net_bind_service,cap_net_raw,cap_sys_ptrace+ep
```

精确的 Polkit 规则只允许本机活动的 `wheel` 用户管理与自己用户名匹配的 smart-box
主单元，以及启动同用户名的 `smart-box-unmask@<用户>.service` 一次性辅助单元；规则
不会授予 `manage-unit-files`，也不能管理其他系统服务。辅助单元以 root 运行，但唯一的
命令固定为解除该用户主单元与 watchdog 的两个 runtime mask，不启动代理、不改其他
单元。安装或升级本身也会在 root 安装阶段执行同一组精确 unmask，因此此前 fail-open
验收留下的临时 mask 不会继续阻断 TUN 启动。服务默认不启用开机自启，客户端“登录时
打开”也只打开桌面/托盘，不自动接管网络。

## 文件位置

```text
/usr/local/lib/smart-box/                 程序与代理核心
/usr/local/lib/systemd/system/smart-box@.service
/usr/local/lib/systemd/system/smart-box-watchdog@.service
/usr/local/lib/systemd/system/smart-box-unmask@.service
~/.config/smart-box/settings.json         本机设置与订阅地址
~/.config/smart-box/profile.json          转换器原始配置
~/.config/smart-box/runtime.json          Linux 本机运行副本
~/.local/state/smart-box/cache.db          选择与测速缓存
```

配置目录权限为 `0700`，文件权限为 `0600`。界面默认遮蔽私密订阅路径，日志和发布包不
包含该路径。

## 命令行检查

```bash
smart-box-profile status
smart-box-profile prepare
smart-box-profile validate
systemctl status "smart-box@$(id -un).service"
systemctl status "smart-box-watchdog@$(id -un).service"
journalctl -u "smart-box@$(id -un).service" -n 200 --no-pager
journalctl -u "smart-box-watchdog@$(id -un).service" -n 200 --no-pager

# 只读测速当前启用的 Arch（pacman/paru 共用）和 CachyOS 源
smart-box-profile mirror-benchmark --repo all
# 仅在确认测速结果后显式请求 root 授权应用
smart-box-profile mirror-apply --repo arch
smart-box-profile mirror-apply --repo cachyos
```

## 卸载

```bash
./uninstall.sh
```

默认保留用户配置。`./uninstall.sh --purge` 同时删除配置、订阅运行副本和缓存；卸载前
会停止 smart-box 和 FlClash。
