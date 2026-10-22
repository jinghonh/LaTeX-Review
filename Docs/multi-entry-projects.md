# 多入口与只读论文项目

外部项目配置保存在应用缓存中。论文目录不需要 `.latex-review.toml`。
项目根目录必须同时包含所有入口和共享资源。

## 注册配置

先在论文目录之外保存以下 TOML 文件。路径均相对于项目根目录；包含宏路径相对于入口目录。

```toml
ignore = ["outputs/*", "Docs/*", ".latex-review/*"]

[project]
read_only = true
entries = ["tnnls/main.tex", "tnnls/supplement.tex"]

[source]
omit_macros = ["omitfloat"]

[source.include_macros]
sharedinput = "../paper/#1"
paperfloat = "../paper/floats/#1"

[source.environments]
IEEEkeywords = "keywords"
widefigure = "figure"
widetable = "table"

[compile]
enabled = true
timeout = 180
sequence = ["tnnls/main.tex", "tnnls/supplement.tex"]
include_paths = ["paper", "tnnls"]
```

注册配置：

```sh
latex-review-project /path/to/Reachable-Set-FNO --config /path/to/profile.toml
```

macOS 默认保存到 `~/Library/Caches/latex-review/projects/<项目标识>/project.toml`。
Linux 默认使用 `~/.cache/latex-review/projects/`。设置 `XDG_CACHE_HOME` 可以改变缓存位置。
应用按项目真实路径隔离配置，按入口隔离报告与翻译缓存。
只读项目的批注数据库也位于应用缓存。批注可以保存，源码替换会被拒绝。
应用缓存包含持久批注；清理缓存前请导出批注。

## 打开与比较

```sh
latex-review-live /path/to/Reachable-Set-FNO/tnnls/main.tex --open-browser
latex-review-live /path/to/Reachable-Set-FNO/tnnls/supplement.tex
latex-review /path/to/Reachable-Set-FNO/tnnls/main.tex --open-browser
latex-review /path/to/Reachable-Set-FNO/tnnls/supplement.tex --open-browser
```

升级后，已运行的常驻入口可能仍使用旧配置校验。已有服务可以继续使用。
需要重新加载常驻入口时，先保存工作，再运行 `latex-review-local stop`。
下一次打开论文会启动新版入口。

同一项目的已启动入口会显示在阅读页面顶部，可以直接切换。
每个入口保留独立批注和服务状态。差异报告默认比较 Git HEAD 与工作区。
使用 `--old REVISION` 选择其他提交。以 `latex-review --help` 列出的参数为准。

```mermaid
flowchart LR
    A[只读论文目录] --> B[应用缓存配置]
    B --> C[入口独立报告]
    A --> D[隔离编译副本]
    D --> C
    C --> E[预览与差异审阅]
    E --> F[外部批注数据库]
```

## 包含宏与编译约束

包含宏仅支持一个必选参数。`#1` 替换该参数，不执行任意 TeX 宏。
`omit_macros` 的调用会阻止之后具有同一参数的包含宏调用。
普通 `input/include`、注释和 verbatim 保持原有规则。
静态 `IfFileExists` 只展开当前版本中存在文件所对应的分支。
图表别名仍保留原始源码和精确位置。

编译在私有临时副本中运行。`include_paths` 限制需要复制的目录。
同一编译顺序中的入口必须位于同一目录。所有入口共享辅助文件目录。
应用先执行 `sequence`，最后重新编译当前入口。整个顺序共享超时预算。
持续预览中的“打开编译 PDF”提供最终排版。HTML 内容预览仍可能对复杂宏降级。
跨入口引用使用编译得到的编号；它们不会跳转到另一入口的具体段落。
