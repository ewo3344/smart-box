# smart-box for Windows

这是 smart-box 的 Windows x64 桌面客户端。解压后的目录包含：

- `smart-box.exe` — WPF 托盘客户端
- `smart-box-core.exe` — 捆绑的 Smart core
- `README.md` — 本说明
- `config/` — 配置模板（不含订阅 URL 或账号）

本包在 Linux 上交叉编译。**尚未在 Windows 真机上做过托盘启动 / 系统代理 / core 重启验收。**

## 使用

1. 解压 zip，不要把 exe 单独拷走（core 必须和客户端在同一目录）。
2. 运行 `smart-box.exe`。客户端会最小化到通知区域。
3. 在窗口中填写树莓派转换器总订阅地址，点击拉取。校验通过的 `profile.json` 与 `settings.json` 写在 `%LOCALAPPDATA%\smart-box`。
4. 可选开启系统代理：`127.0.0.1:20808`（mixed）。

首次从旧版 `sing-box-smart` 升级时，若 `%LOCALAPPDATA%\smart-box` 还没有对应文件，客户端会从 `%LOCALAPPDATA%\sing-box-smart` 复制一次 `settings.json` 和 `profile.json`。

`config/settings.example.json` 只是字段模板。不要把真实订阅写进发布包；把 URL 填进客户端界面即可。

## 升级

解压新版本 zip，覆盖旧目录中的 `smart-box.exe` 和 `smart-box-core.exe`。`%LOCALAPPDATA%\smart-box` 下的数据会保留。

## 已知限制

- 客户端是 self-contained .NET 10，体积约 165MB。裁剪/framework-dependent 打包延到后续版本。
- 运行时验证需 Windows 机器，见 `RELEASE-CHECKLIST-v0.1.1.md`。
