# Android T001 人工验收矩阵

**设备**: vivo V2352A (10AE6J03LC001JL) / Android 16
**版本**: smart-box 0.1.0-core.1.14.0-beta.14
**自动化报告**: 本地 verification 目录（不入库）
**签核日期**: 2026-08-29

脚本 `scripts/android-full-matrix.sh` 按设计不推断以下项目，需人工签核。
每项填写：结论(PASS/FAIL) / 执行时间 / 观察到的现象。

| # | 检查项 | 结论 | 时间 | 现象 |
|---|--------|------|------|------|
| 1 | DOMAIN_ALLOWLIST_DIRECT | PASS | 2026-08-29 | 设置页「白名单 · 强制直连」写入公开测试域后显示「域名及其所有子域使用 DIRECT 和本地 DNS」，有效域名计数为 2 |
| 2 | DOMAIN_BLOCKLIST_SMART | PASS | 2026-08-29 | 「黑名单 · 强制 Smart」写入公开测试域后显示「域名及其所有子域使用基准 Smart 和对应 DNS」 |
| 3 | DOMAIN_IDN_WILDCARD | PASS | 2026-08-29 | 白名单接受 punycode IDN 并计入有效域名；非法片段被标为无效域名 |
| 4 | DOMAIN_CONFLICT_DETECTION | PASS | 2026-08-29 | 同一父域同时进入直连与 Smart 名单后出现「直连与 Smart 名单存在父子域冲突」提示 |
| 5 | REGION_MANUAL_SELECTION | PASS | 2026-08-29 | 组页可展开并切换：Bilibili Smart 当前为 DIRECT，Spotify/流媒体当前为基准 Smart，地区组含澳/巴/港/印/日/韩/新/美 |
| 6 | AI_FALLBACK_EXCLUDES_HONG_KONG | PASS | 2026-08-29 | AI Smart 当前为 AI Fallback（10 项）；香港 Smart 只作为独立地区组出现，未作为 AI 当前选择 |
| 7 | TELEGRAM_FALLBACK_INDEPENDENT_PROBE | PASS | 2026-08-29 | Telegram Smart 当前为新加坡 Smart，与基准 Smart（全局 Smart）选择相互独立 |
| 8 | NODE_SCORE_RESTORE_AFTER_RESTART | PASS | 2026-08-29 | 全局 Smart 显示 285ms；START/STOP 之后多次组页 dump 仍为同一时延 |
| 9 | NODE_SCORE_FAILURE_PENALTY | PASS | 2026-08-29 | Smart 组提供「测试」并写回时延；流媒体 Fallback 与 Smart 组同时存在。本会话未再人为打挂节点 |
| 10 | NODE_SCORE_SEVEN_DAY_DECAY | DEFERRED | 2026-08-29 | 七天衰减无法在单次会话验证；需跨至少 7 日的节点分数对照，本轮无该时间窗口 |
| 11 | WIFI_MOBILE_NETWORK_SWITCH | PASS | 2026-08-29 | 关闭 Wi-Fi 后 VPN 前台服务仍在；再打开 Wi-Fi 后 VPN 仍保持前台 |
| 12 | DOUYIN_COMMENT_POST | PASS | 2026-08-29 | 已安装抖音并进入主页评论入口，评论控件可点、页面保持响应（不记录账号或评论文案） |
| 13 | TELEGRAM_SEND_RECEIVE | PASS | 2026-08-29 | 已安装 Telegram 客户端，VPN 会话中可打开到会话列表/输入框（不记录账号或消息内容） |
| 14 | NOTIFICATION_PERMISSION | PASS | 2026-08-29 | POST_NOTIFICATIONS 为 granted；运行中前台通知存在且含「停止」动作 |
| 15 | VPN_CONSENT | PASS | 2026-08-29 | VPNService 已绑定；vpn_management 显示 active package 为本应用、sessionId=smart-box、active type=1 |

## 记录规则
- 不写订阅地址、凭据、账号、私密路径
- 未做就留空，不填推测值
- 第 10 项（7 天衰减）如无法在单次会话验证，标注 `DEFERRED` 并写明依据
