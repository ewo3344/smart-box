# Windows 配置模板

客户端**不会**从本目录自动加载配置。运行时文件在：

```text
%LOCALAPPDATA%\smart-box\settings.json
%LOCALAPPDATA%\smart-box\profile.json
```

- `settings.example.json` 展示 `settings.json` 的字段。`SubscriptionUrl` 请留空或只在本机填写，不要提交真实地址。
- `profile.json` 由客户端从转换器拉取并校验后写入数据目录，发布包不附带实时订阅内容。
