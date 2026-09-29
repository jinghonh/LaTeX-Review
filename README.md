# LaTeX Review

面向论文修改的结构化审阅工具。运行一次命令即可生成三栏报告、机器可读差异和独立诊断文件；默认不运行 LaTeX 编译器。

## 快速开始

首版正式支持 macOS，要求 Python 3.11 或更新版本。建议在干净虚拟环境中安装：

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install .
latex-review --help
latex-review --version
```

开发与验证入口：

```sh
python -m pip install '.[test]'
./scripts/verify-macos.sh
```

端到端合成样例及私有论文验收方法见[端到端验收](Docs/end-to-end-acceptance.md)。

安装、目录/文件/Git 来源模式、配置、Codex 技能和报告解读见[使用指南](Docs/user-guide.md)。

`latex-review main.tex` 默认比较 Git `HEAD` 与当前磁盘工作区，写入 `.latex-review/latest/report.html`、`diff.json`、`diagnostics.json`。可用 `--old`、`--new` 指定两个提交，或用 `--old-dir A --new-dir B --entry main.tex` 比较目录，也可直接提供两个独立文件。工作区快照包含暂存及未暂存内容、未忽略的未跟踪依赖；输出目录不参与快照。`--inspect-sources` 仅输出来源展开 JSON。用法与程序接口见[比较来源与展开接口](Docs/source-resolution.md)。

配置从当前目录的 `.latex-review.toml` 读取，也可用 `--config` 指定；显式命令行选项覆盖配置，配置覆盖默认值。支持 `entry`、`output`、`ignore`、`[git].default_old/default_new` 与 `[diff].comments`；`--no-comments` 可覆盖配置中的注释审阅开关。首版的 `--math` 仅支持 `mathjax`，`--format` 仅支持 `html,json`；其他渲染与差异开关只接受首版固定值，未知或不支持的配置会报错。首版不支持 `--compile`。完整报告返回 0，降级报告返回 2，来源不可读取返回 4，内部错误返回 8；一般提示不改变成功状态。无效参数或配置返回 64。

契约结构、兼容性和使用样例见 [公共数据契约](Docs/data-contract.md)。解析和预览接口见 [结构解析与内容预览](Docs/structure-preview.md)，比较接口见[结构化内容差异](Docs/structured-content.md)。首版只比较图资源路径，不检测同一路径下的图片内容变化；表格只做整体差异，不定位单元格。运行时依赖包含 `jsonschema` 和 `plasTeX`；`pytest` 属于可选测试依赖。
