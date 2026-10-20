# Third-party notices

The [MIT License](LICENSE) at the repository root applies to the original VaM Package Manager code and documentation. It does not replace the licenses of third-party software, game resources, or services.

## Software included in this source repository

| Component | Version | Use | License and retained notice |
| --- | --- | --- | --- |
| [Marked](https://github.com/markedjs/marked) | 18.1.1 | Markdown rendering in `web/vendor/marked.js` | MIT; [complete bundled notice](web/vendor/marked-LICENSE.txt), including upstream Markdown attribution |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.17 | HTML sanitization in `web/vendor/purify.js` | Distributed here under the Apache-2.0 option; [complete license](web/vendor/dompurify-LICENSE.txt) |

Marked's retained notice credits MarkedJS, Christopher Jeffrey, and the upstream authors named in that notice. DOMPurify's distributed header credits Cure53 and other contributors. The vendored files and their copyright/license headers are retained unmodified.

DOMPurify is offered upstream under **Apache-2.0 OR MPL-2.0**. This distribution uses the Apache-2.0 option, whose full text is included above. Its upstream [license information](https://github.com/cure53/DOMPurify#what-about-the-licenses) describes the alternatives. No project-wide MIT relicensing of DOMPurify is intended.

Keep the applicable upstream license files and notices when redistributing these files, including as part of a portable application.

## Development dependencies

`package-lock.json` records the exact dependency versions and integrity values. `node_modules/` and browser binaries are not included in this source repository.

- Playwright, Playwright Core, and `@playwright/test`: Apache-2.0; used for browser tests. See the [upstream license](https://github.com/microsoft/playwright/blob/main/LICENSE).
- `@types/trusted-types`: MIT; an optional development dependency of DOMPurify. Preserve its package license if redistributing it.
- Downloaded browser binaries have their own licenses and notices. They are test dependencies, not part of this project's MIT grant.

## Optional runtime distributions

These components are **not included** in the source repository. Preparation scripts download them separately:

| Component | Preparation script | Upstream licensing |
| --- | --- | --- |
| CPython embeddable Windows distribution | `tools/prepare_runtime.py` | Python Software Foundation license and the additional licenses/notices supplied with the distribution; [Python license documentation](https://docs.python.org/3/license.html) |
| OpenAI Codex CLI | `tools/prepare_codex.py` | Apache-2.0 plus applicable bundled third-party notices; [upstream LICENSE](https://github.com/openai/codex/blob/main/LICENSE) and [NOTICE](https://github.com/openai/codex/blob/main/NOTICE) |

When preparing a binary release, preserve the notices and license texts required by the exact runtime distributions being shipped, alongside this project's `LICENSE` and this file. A download script or a link to an upstream license is not a substitute for including required notices in a binary distribution.

VaM itself, resource packages, previews supplied by creators, model services, and account credentials are not distributed by this repository. Their terms remain separate.

## Documentation screenshots

Images under `docs/images/` were captured from the running application using a temporary, synthetic demo library. The demo packages contain placeholder metadata, not working third-party game assets. These project-created screenshots are covered by the root MIT License. No personal data or credentials were used in the demo.

## 中文说明

根目录 MIT 许可适用于本项目原创代码与文档，不会替代第三方许可。已包含的 Marked 使用 MIT，DOMPurify 在本仓库按其 Apache-2.0 许可选项分发；对应完整许可文本、版权标记和上游说明均已保留。

Python、Codex CLI、开发依赖及浏览器并未打包进源码仓库。制作包含运行时的发行包时，须一并保留所分发版本要求的许可与通知文件。VaM、用户资源和服务商账号的条款独立适用。文档截图仅使用本项目创建的模拟数据，不包含第三方游戏素材或用户隐私。
