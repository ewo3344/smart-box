# smart-box 完整分流说明

converter 生成的是一份可以直接交给 smart-box core 的完整配置。客户端只需保存
converter 地址，不需要直接访问 GitHub，也不需要知道任何机场订阅地址。

## 策略层级

配置中的出站分为四层：

1. 原始节点：由 5 个订阅源解析、去重并进行 TCP/UDP 可达性检查。
2. 地区 Smart：按照节点名称中的国旗生成，例如 `🇯🇵 日本 Smart`。选择地区并不是
   固定到某一个节点，而是在该地区内继续自动挑选当前更合适的节点。
3. 共享自动池：`🚀 全局 Smart`、AI、Telegram、流媒体和游戏 Fallback。它们直接
   包含原始节点并执行主动探测。
4. 业务 selector：AI、Netflix、GitHub 等用户实际手动操作的策略。selector 本身不
   重复测速，而是选择基准、共享 Fallback、全局 Smart、某个地区 Smart 或 DIRECT。

这种结构允许每项业务独立选地区，同时避免为 20 多项业务各建一套重复探测任务。

## 可见策略与默认值

| 策略 | 默认选择 | 主要匹配内容 |
|---|---|---|
| `🎯 基准 Smart` | `🚀 全局 Smart` | 未被更具体规则命中的普通流量 |
| `🤖 AI Smart` | `🤖 AI Fallback` | OpenAI、Claude、Gemini、Perplexity 等 AI 服务；自动排除香港 |
| `✈️ Telegram Smart` | `✈️ Telegram Fallback` | Telegram 域名、应用和 IP 段；使用 Telegram 专用探测 |
| `🎥 Netflix Smart` | `🎯 基准 Smart` | Netflix 域名、应用和 IP 段 |
| `📽️ Disney+ Smart` | `🎯 基准 Smart` | Disney+ |
| `🎞️ Max Smart` | `🎯 基准 Smart` | Max/HBO |
| `🎬 Prime Video Smart` | `🎯 基准 Smart` | Prime Video |
| `🍎 Apple TV+ Smart` | `🎯 基准 Smart` | Apple TV+，优先于宽泛 Apple 规则 |
| `📹 YouTube Smart` | `🎯 基准 Smart` | YouTube，优先于宽泛 Google 规则 |
| `🎵 TikTok Smart` | `🎯 基准 Smart` | TikTok |
| `🇨🇳 抖音 Smart` | `DIRECT` | 中国抖音域名与 Android 应用；优先于 anti-AD 和海外 TikTok 规则 |
| `📺 Bilibili Smart` | `DIRECT` | Bilibili；可手动切到海外地区或流媒体 Fallback |
| `🎶 Spotify Smart` | `🎯 基准 Smart` | Spotify |
| `📺 流媒体 Smart` | `🎯 基准 Smart` | Twitch、Hulu、Vimeo、国际电视等其他海外媒体兜底 |
| `💬 社交 Smart` | `🎯 基准 Smart` | Discord、X/Twitter、Facebook、Instagram、WhatsApp |
| `🎮 游戏 Smart` | `🎯 基准 Smart` | 海外游戏平台及游戏服务 |
| `🐙 GitHub Smart` | `🎯 基准 Smart` | GitHub，优先于宽泛开发服务规则 |
| `🧑‍💻 开发服务 Smart` | `🎯 基准 Smart` | Docker、npm 和 category-dev 开发生态 |
| `🍎 Apple Smart` | `🎯 基准 Smart` | Apple 的非中国区服务 |
| `🪟 Microsoft Smart` | `🎯 基准 Smart` | Microsoft 的非中国区服务 |
| `🇬 Google Smart` | `🎯 基准 Smart` | Google 的非中国区服务 |
| `📈 测速 Smart` | `🎯 基准 Smart` | Speedtest、IPv6 测试等网络检测站点和应用 |
| `⬇️ 下载策略` | `DIRECT` | 下载器进程、国服/游戏国内下载和下载相关域名 |
| `🇨🇳 国内域名策略` | `DIRECT` | 国内域名以及 Apple/Microsoft/Google 中国区子集 |
| `🀄 国内 IP 策略` | `DIRECT` | 中国大陆 IP 段 |
| `🛡️ 广告策略` | `REJECT` | anti-AD 广告域名；可切 DIRECT 或任意 Smart 放行 |

除 `🤖 AI Smart` 外，其他业务策略都可以手动选择 `🚀 全局 Smart` 和每一个动态地区
Smart。AI 策略默认使用专用 Fallback，只显示非香港地区 Smart 和 DIRECT，避免通过
基准或全局池间接选回香港。国内和下载策略也可以反向切到基准、全局或指定地区；广告
策略可以在 REJECT、DIRECT、基准和地区之间切换。

## Fallback 的准确含义

这里的 Fallback 不是一条“规则匹配失败后才执行”的路由规则，而是一个可手动选择的
Smart 自动节点池：

- `🤖 AI Fallback` 优先收纳新加坡、日本、美国、台湾、韩国、加拿大、英国、德国和
  法国节点，明确排除香港。如果首选地区全部缺失，则退回其他非香港节点；如果只剩
  香港节点，则退到 DIRECT，也不会重新使用香港。
- `✈️ Telegram Fallback` 使用新加坡、美国、日本和台湾节点，并通过 `telegram.org`
  探测线路是否真正能访问 Telegram。存在这些地区时不自动选择香港，但香港地区 Smart
  仍保留为手动选项；只有订阅中不存在其他可用地区时，香港才作为最后兜底。
- `📺 流媒体 Fallback` 使用全部节点，让 Smart 根据探测和连接失败惩罚自动选择。
- `🎮 游戏 Fallback` 使用日本、新加坡、香港、台湾和韩国节点。
- `🚀 全局 Smart` 是通用自动池，也是 `🎯 基准 Smart` 的默认项。

转换器会在新订阅和启动时读取的节点缓存中剔除“剩余流量”“套餐到期”“订阅更新”
“官网/教程”等订阅状态提示项，避免这些带有虚假代理端点的提示被 Smart 当成节点。

除 AI 和 Telegram 外，代理业务 selector 默认跟随 `🎯 基准 Smart`，因此只改一次基准地区即可让
多数业务一起跟随。AI 与 Telegram 默认各自使用专用 Fallback。当某项服务需要独立
策略时，可在对应 selector 中选择 Fallback 或某个地区。例如把 Netflix 选到
`🇯🇵 日本 Smart` 后，Netflix 的连接和 DNS 都从日本策略出站；其他业务仍继续跟随基准。

AI 的香港排除适用于 Rule 和节能模式。Global 模式按定义优先于所有业务规则，会把
包括 AI 在内的全部连接交给 `🚀 全局 Smart`，因此 Global 模式仍可能使用香港节点。

## Clash 模式

- Rule：按下面的完整规则顺序执行，是正常默认工作方式。
- Global：所有连接使用 `🚀 全局 Smart`，DNS 也使用全局 Smart 出站。
- Direct：所有连接直接出站，DNS 使用系统本地 DNS。
- `节能`：抖音强制直连，其他广告规则仍先执行；AI 和 Telegram 保持各自 Smart 与专用
  DNS，其余流量全部直连并使用本地 DNS。AI/Telegram 的域名规则、Android 包名和 Windows 进程名
  都位于节能模式的直连总规则之前，因此即使 QUIC 流量无法嗅探出域名，官方客户端
  仍不会被直连总规则提前截走。这个模式用于减少代理流量、主动连接和移动设备耗电。

## Android 手动域名黑白名单

Android 客户端在“工具 > 域名黑白名单”中提供一层仅保存在本机的手动覆盖：

- 白名单强制走 `DIRECT`，对应 DNS 强制使用 `local`。
- 黑名单不是拒绝访问，而是强制走 `🎯 基准 Smart`，对应 DNS 使用
  `baseline-dns`。如需拒绝广告，仍使用 `🛡️ 广告策略`。
- Direct 和 Global 总模式优先级最高；Rule 和节能模式下，手动名单优先于私网、广告、
  节能直连及下面所有业务规则。
- 根域自动匹配全部子域。输入支持 `*.`、前导点、尾点、大小写和中文 IDN；已被父域
  覆盖的重复子域会自动合并。
- 黑白名单之间存在同域或父子域重叠时不能保存，避免同一连接同时要求直连和代理。

客户端只在启动或重载核心时修改内存中的配置，不改写 converter 返回的订阅文件；
因此远程订阅更新不会清空手动名单，清空两份名单后则完全使用 converter 原配置。

## 路由优先级

顺序是配置语义的一部分，不能随意重排：

1. 协议嗅探和 DNS 劫持。
2. Direct/Global 模式的总规则。
3. Android 手动域名黑白名单（仅 Android 运行时存在）。
4. 私网 IP、本地域名和 private 规则集直连。
5. 中国抖音的 Android 包名和专属域名规则；在 Rule 模式交给 `🇨🇳 抖音 Smart`，在
   节能模式强制直连。它必须位于 anti-AD 和海外 TikTok 之前。
6. 其他广告流量交给可切换的 `🛡️ 广告策略`。
7. `节能` 模式中的 AI、Telegram 白名单，然后是节能直连总规则。
8. Android 包名与 Windows 进程名兜底，解决 QUIC 或无法嗅探域名的应用流量。
9. AI、Telegram 和各个具体媒体平台。
10. 社交、国服、海外游戏、GitHub、开发服务和测速。
11. Apple/Microsoft/Google 中国区子集、下载器，再匹配三家的宽泛国际规则。
12. 其他海外媒体和国内域名。
13. 对仍未决定的域名执行 `resolve`，以便后续 IP 规则参与判断。
14. Telegram IP、Netflix IP、其他媒体 IP 和中国大陆 IP。
15. 最终交给 `🎯 基准 Smart`。

具体平台规则必须位于宽泛规则之前。例如 YouTube 在 Google 之前、Apple TV+ 在 Apple
之前、Bilibili 在国内域名和其他媒体之前；否则手动平台策略会被大类提前截走。

## DNS 分流

配置不共用一个固定的海外 DNS 出站。每项代理策略都有独立的 DoH transport，transport
的 `detour` 指向同名 selector：

```json
{
  "type": "https",
  "tag": "netflix-dns",
  "server": "1.1.1.1",
  "server_port": 443,
  "path": "/dns-query",
  "detour": "🎥 Netflix Smart"
}
```

国内域名、抖音、Bilibili 和下载策略使用 AliDNS DoH；海外服务使用 Cloudflare DoH。
每个 DNS transport 的 tag 不同，sing-box 1.14 会自动按 transport 隔离缓存。
`cache_capacity` 为 4096，并启用 `experimental.cache_file` 保存远程规则。广告域名使用
`baseline-dns` 解析，而不把 DoH 连接送入默认选中 `REJECT` 的广告 selector；否则 block
出站会返回 `EPERM`，上层就会不断记录 `router: process DNS packet: operation not permitted`。
广告的实际连接仍交给 `🛡️ 广告策略`，所以 REJECT、DIRECT 和地区 Smart 的手动选择仍然
有效。`games-cn` 使用 `download-dns`，连接则使用同一个 `⬇️ 下载策略`，避免 DNS 走游戏
代理、实际下载却直连所造成的出口地区不一致。

## 规则镜像与缓存

converter 镜像 39 份 `.srs`，来源为 DustinWin/ruleset_geodata 和
SagerNet/sing-geosite。客户端只访问：

```text
/rule-set/<private-token>/<tag>.srs
```

converter 对每份文件执行以下检查：

- 只允许内置的 HTTPS 上游地址。
- 最多并发下载 8 份。
- 单文件上限 4 MiB。
- 文件头必须是 `SRS`，版本必须在 1 到 5 之间。
- 临时文件写入、`fsync` 后在同一目录原子重命名。
- 更新失败时继续提供内存和磁盘中的最后有效版本。
- 就绪状态逐一检查全部配置 tag，而不只比较 map 数量；数量相同但 tag 错位时也不会
  发布 profile。
- 首次启动缺少任意必要规则时不发布不完整 profile。
- 私密端点支持 ETag、304 和 Last-Modified，错误 token 返回 404。

生成的 profile 使用 24 小时远程规则更新周期。Android 的应用私有目录、Windows 的
LocalAppData 工作目录和树莓派 `/var/lib/smart-box/cache.db` 都会保存客户端规则缓存。

## 树莓派持久状态

converter：

```text
/etc/smart-box-converter/config.json
/var/lib/smart-box-converter/cache/*.json
/var/lib/smart-box-converter/cache/rules/*.srs
```

core wrapper：

```text
/var/lib/smart-box/profile.json
/var/lib/smart-box/cache.db
```

profile、节点缓存和规则缓存都保留最后有效版本。即使 converter 暂时停止，重启
`smart-box.service` 也能从持久 profile 和 `cache.db` 启动。wrapper 每 5 秒检查 core
子进程；core 意外退出时 wrapper 也退出，由 systemd 重启整个服务，而不会留下一个
表面 active、实际没有 core 的空壳进程。

## 隐私边界

- 供应商 URL 只存在于树莓派 root 所有的运行配置中。
- 供应商 URL、节点凭据和私密 token 不写入日志、README 或状态接口。
- subscription 只包含转换后的节点参数、策略和私密规则镜像地址。
- `/api/v1/status` 只提供来源别名、数量、缓存状态、规则状态、时间和配置哈希。
- subscription 与 rule-set 请求在访问日志中统一显示为 `<redacted>`。
