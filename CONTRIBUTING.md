# Contributing

Thank you for helping improve VaM Package Manager. Bug reports, documentation corrections, translations, and focused pull requests are welcome.

## Before making a change

- For a substantial behavior change, open an issue describing the problem and proposed result first.
- Keep package identifiers, user text, filenames, and stored category identifiers separate from translated display text.
- Preserve preview validation, non-overwriting file operations, per-game isolation, and recovery records.
- Use disposable fixture directories. Do not develop against the only copy of a real game library.

## Development checks

Use Python 3.13 and Node.js 20+. From the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
npm ci
npx playwright install chromium
npm run test:ui
```

Browser tests invoke `python3`. On Windows, make sure that command resolves to the intended interpreter; backend tests can also use `py -3.13`. On Linux, Chromium may require system dependencies available through Playwright's installation tools.

Run checks relevant to your changes. File-management changes should cover interruption, conflicts, restoration, and data isolation using temporary resources. UI changes should include the relevant browser checks and both languages. Documentation-only changes need link, rendering, and content checks rather than a full application test run.

After changing `web/locales/en.json` or `web/locales/messages.en.json`, run:

```sh
python3 tools/build_locales.py
```

`tests/live_*.py` may use real credentials, external services, or account quota. Historical Windows probes have explicit opt-in controls and are not the isolated regression suite. Do not run either as an ordinary CI step.

## Pull requests

Describe the problem, the resulting behavior, and the checks you ran. Add regression coverage when it verifies meaningful behavior. For visible changes, include screenshots made with synthetic or permissioned, privacy-safe content.

Update `README.md` and `README.zh-CN.md` together when usage changes. Do not commit runtime directories, application data, secrets, game assets, local logs, generated reports, or personal screenshots. Keep third-party license notices with vendored code; avoid unrelated dependency changes.

By submitting a contribution, you agree that your contribution may be distributed under the project's [MIT License](LICENSE). Submit only material you have the right to contribute and identify any third-party material with its license. There is no separate contributor license agreement or copyright assignment required by this project.

Treat other contributors respectfully. Discuss the code and the problem; avoid personal attacks and do not share other people's private information.

## 中文说明

欢迎提交问题报告、文档修正、翻译和范围清晰的 Pull Request。较大的行为变更请先通过 Issue 讨论；文件操作必须保留预览校验、无覆盖移动、多游戏隔离及恢复能力。

使用临时模拟资源测试，不要拿唯一的真实游戏库做开发。更新用户说明时同步修改中英文 README；翻译词典更新后重新生成 `web/locales/en.js`。不要提交数据目录、密钥、游戏资源、运行时、日志或含个人信息的截图。

提交贡献意味着允许按本项目 MIT 许可分发该贡献。只提交你有权贡献的内容，并注明第三方来源与许可；本项目不另外要求签署 CLA 或转让版权。涉及敏感问题请先阅读 [SECURITY.md](SECURITY.md)。
