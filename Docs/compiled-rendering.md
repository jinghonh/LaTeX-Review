# 真实编译、页面对比与沙箱渲染

日常结构审阅不启动 TeX。仅在命令行传入 `--compile`，或在配置中明确设置 `[compile].enabled = true` 后，才对比较版本运行 `latexmk`。默认分别编译修改前和修改后两个隔离副本；`--compile-new-only` 只编译修改后版本。原论文目录、Git 索引和工作区文件不作为编译输出目录。两个来源读取失败时仍返回 4；编译或页面预览失败时保留结构报告，诊断进入 `diff.json` 和 `diagnostics.json`，退出状态为 2。

```sh
latex-review main.tex --compile
latex-review main.tex --compile --compile-new-only --tex-engine xelatex
latex-review main.tex --compile --sandbox-render --compile-timeout 60
```

可在 `.latex-review.toml` 中设置：

```toml
[compile]
enabled = true
new_only = false
engine = "pdflatex"
timeout = 90
sandbox_render = false
```

`engine` 支持 `pdflatex`、`xelatex`、`lualatex`。双侧编译的工具解析统一排除两个论文目录及各自直接上级目录，并排除当前目录与报告目录中的同名程序；版本探测在隔离副本中执行，高级模式下还须通过同一沙箱。找不到可信工具或无法在隔离环境中验证版本时给出诊断，工具不会被自动安装。每侧的编译状态在 `compiled/<side>/status.json`，保留 `compile.log`、成功生成的 `document.pdf` 和用于源码定位的 `document.synctex.gz`。编译在受限时间、进程数、内存、文件大小和输出量下运行，超限时终止整个编译进程组。`latexmk -norc` 禁止读取用户和项目的自动构建配置，TeX 引擎强制 `-no-shell-escape`，并限制 TeX 输入输出策略。隔离副本拒绝符号链接和过大的来源树。

有双侧文档且可用 `pdfinfo`、`pdftoppm` 时，`pages/visual.json` 记录页码配对、页面增删、像素差异以及主变更到页面的映射。报告展示双侧页面预览和差异图；页插入通过全局配对处理，后续页不直接按同一页码判定为变化。页面像素差异单独计数，不进入主变更总数，也不证明语义变化。源码到页码通过 `synctex` 查询；来源位置不可靠、跨页或查询结果不唯一时标记不确定，页面与结构视图只在确定时互相链接。缺少双侧文档时可展示已有一侧的页面，明确写明不可比较。

`--sandbox-render` 是额外的显式高级执行开关，必须同时启用编译。它在 macOS 使用 `sandbox-exec` 的默认拒绝策略：论文私有副本可读写，TeX 运行时与系统库只读，网络拒绝，进程执行限制为编译所需的程序。启动前实际测试可执行程序和越界文件读取；机制缺失或自检失败时拒绝执行并保留源码内容预览。此模式可用于受限地排版复杂宏和绘图，但不保证任意宏与外部程序都能运行。高级渲染只向报告嵌入由本工具编码的 PNG 位图，不嵌入源 PDF、SVG 或其他可执行页面内容。新生成的 PDF 仅作为单独下载文件提供。
