# Security policy

## Scope

Reports concerning the current 1.0.x source line are welcome. Older internal development snapshots are not separate supported releases. This policy does not promise a response or fix deadline.

Relevant issues include path traversal, unintended file overwrite/deletion, cross-library data exposure, session-token leaks, credential disclosure, and unsafe rendering of package metadata or AI output.

## Reporting a vulnerability

If this GitHub repository offers **Security → Report a vulnerability**, use that private reporting channel. Private vulnerability reporting must be enabled by the repository owner; adding this document does not enable it.

If that option is unavailable, open an issue asking the maintainer for a private reporting channel. **Do not include exploit details, credentials, personal paths, game assets, or private data in that public issue.** No private email address or external reporting service is designated here.

Once a private channel is agreed, provide the affected version, impact, a minimal reproduction using synthetic files, and relevant redacted diagnostics. Avoid sending a complete application `data/` directory.

## Safe diagnostics

- Remove API keys, authorization headers, session URLs/tokens, account identifiers, and personal file paths.
- The local browser session is authenticated by a token. Do not publish its full launch URL or `data/server.json`.
- Keep the application on loopback; exposing it through a public proxy is outside the intended deployment model.
- Test file-operation reports with disposable fixtures. Never send third-party packages without permission.

## 中文说明

当前接受 1.0.x 源码版本的安全问题报告，不承诺固定响应或修复期限。

如果仓库已启用 **Security → Report a vulnerability**，请通过该入口私密报告。该功能需要仓库所有者在 GitHub 设置中启用，本文件不会自动开启它。没有此入口时，请先开一个仅请求私密联系方式的 Issue，不要公开漏洞细节或敏感数据。

报告时使用模拟文件复现，隐藏 API Key、会话令牌、账号与个人路径；不要上传完整 `data/`、`data/server.json` 或未经许可的游戏资源。应用仅用于本机回环访问，不应通过公共代理开放。
