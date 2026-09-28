# LaTeX Review

面向论文修改的结构化审阅工具。目前提供公共数据契约、双版本来源展开、结构解析与双侧内容预览的程序接口；差异引擎和完整三栏报告会在后续票据实现。当前命令行的帮助和版本查询不会读取论文或运行 LaTeX 编译。

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

`latex-review main.tex --inspect-sources` 可检查 Git HEAD 与磁盘工作区；也可指定两个目录、两个独立文件或两个 Git 提交。命令输出双侧的展开文本、依赖和来源映射 JSON，不生成报告。用法与程序接口见[比较来源与展开接口](Docs/source-resolution.md)。未加 `--inspect-sources` 的审阅命令仍在标准错误输出“审阅命令尚未实现”，退出码为 64。`--help`、`--version` 及无参数帮助返回 0。后续报告流程约定：完整报告 0、降级报告 2、任一比较版本不可读 4、内部错误 8；普通提示不改变成功状态。所有错误使用 `latex-review: 错误：…` 前缀输出到标准错误。帮助文字输出到标准输出。

契约结构、兼容性和使用样例见 [公共数据契约](Docs/data-contract.md)。解析和预览接口见 [结构解析与内容预览](Docs/structure-preview.md)。运行时依赖包含 `jsonschema` 和 `plasTeX`；`pytest` 属于可选测试依赖。
