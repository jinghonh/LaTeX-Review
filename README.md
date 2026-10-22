# LaTeX Review

用于论文修改的结构化审阅工具。比较 LaTeX 版本，生成 HTML 审阅报告，并支持当前论文的持续预览。

本地入口面向 macOS 和 Linux；本轮完成 macOS 验收。需要 Python 3.11+。默认无需安装 TeX；HTML 内容预览不保证与 PDF 排版一致。

## 安装

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

## 常用命令

比较 Git `HEAD` 与当前工作区，生成报告并打开浏览器：

```sh
latex-review main.tex --open-browser
```

报告默认保存在 `.latex-review/latest/`，包含 `report.html`、`diff.json` 和 `diagnostics.json`。

比较两个论文目录：

```sh
latex-review --old-dir A --new-dir B --entry main.tex --open-browser
```

预览当前论文，保存后自动更新：

```sh
latex-review-live main.tex --open-browser
```

持续预览默认使用 `.latex-review/live/`。启动成功后 CLI 返回，服务继续运行。

使用常驻入口发现服务。每篇论文最多运行一个持续预览和一个差异报告服务。

```sh
latex-review-local list
latex-review-local open
latex-review-local stop
```

入口默认地址为 `http://127.0.0.1:8765/`。收藏 CLI 输出的稳定论文链接。
报告在固定入口页面内显示，地址栏保持入口端口；工作进程替换后页面自动加载当前报告。
`--wait` 附着状态；Ctrl+C 只断开客户端。显式 `stop` 停止入口及受管服务。
升级前须手动停止旧版预览；新入口不接管未知进程。详见[持续预览与迁移](Docs/live-preview.md)。

项目配置使用 `.latex-review.toml`；命令行选项优先。更多选项见 `latex-review --help`。

## 功能与文档

| 需求 | 文档 |
| --- | --- |
| 安装、比较来源与配置 | [使用指南](Docs/user-guide.md) |
| 多入口与只读论文项目 | [外部项目配置](Docs/multi-entry-projects.md) |
| 当前论文持续预览与迁移 | [持续预览](Docs/live-preview.md) |
| 打开已有报告 | [报告预览](Docs/report-preview.md) |
| 中文翻译、术语表与译文导出 | [翻译指南](Docs/translation.md) |
| 阅读与添加批注 | [批注指南](Docs/report-annotations-user-guide.md) |
| 编译 PDF 与页面对比 | [编译与渲染](Docs/compiled-rendering.md) |
| 发布到私有服务器 | [报告发布](Docs/report-upload.md) |
| 智能体查询与验收 | [智能体接口](Docs/agent-tools.md) |
| 接入 GitHub Actions | [工作流配置](Docs/github-actions.md) |

翻译需要配置 LLM 接口和环境变量密钥，由用户手动触发。
PDF 编译通过 `--compile` 启用，需要安装 `latexmk` 和 TeX。

## 开发

```sh
python -m pip install '.[test]'
./scripts/verify-macos.sh
```

接口定义见[公共数据契约](Docs/data-contract.md)。
