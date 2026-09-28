# LaTeX Review

面向论文修改的结构化审阅工具。目前仅完成工程骨架与公共数据契约；读取项目、生成差异和三栏报告会在后续票据实现。当前命令行的帮助和版本查询不会读取论文或运行 LaTeX 编译。

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

当前输入 `latex-review main.tex` 会在标准错误输出“审阅命令尚未实现”，退出码为 64，不会假称已生成报告。`--help`、`--version` 及无参数帮助返回 0。后续报告流程约定：完整报告 0、降级报告 2、任一比较版本不可读 4、内部错误 8；普通提示不改变成功状态。所有错误使用 `latex-review: 错误：…` 前缀输出到标准错误。帮助文字输出到标准输出。

契约结构、兼容性和使用样例见 [公共数据契约](Docs/data-contract.md)。运行时依赖只有 `jsonschema`；`pytest` 属于可选测试依赖。结构解析、渲染等后续依赖将在对应能力实现时引入。
