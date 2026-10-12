# LaTeX Review

面向论文修改的结构化审阅工具。运行一次命令即可生成以新稿为主体的审阅报告、机器可读差异和独立诊断文件；默认不运行 LaTeX 编译器。

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

`latex-review main.tex` 默认比较 Git `HEAD` 与当前磁盘工作区，写入 `.latex-review/latest/report.html`、`diff.json`、`diagnostics.json`、`cache-meta.json`。可用 `--old`、`--new` 指定两个提交，或用 `--old-dir A --new-dir B --entry main.tex` 比较目录，也可直接提供两个独立文件。工作区快照包含暂存及未暂存内容、未忽略的未跟踪依赖；输出目录不参与快照。`--inspect-sources` 仅输出来源展开 JSON。用法与程序接口见[比较来源与展开接口](Docs/source-resolution.md)。

浏览器工具无法打开本地文件页面时，运行 `latex-review-preview .latex-review/latest`，打开输出的 `http://127.0.0.1:端口/report.html`。服务仅监听本机，自动选择端口，并提供完整报告目录；按 Ctrl+C 停止。两个命令均支持 `--open-browser`：`latex-review main.tex --open-browser` 会生成报告、启动预览并打开默认浏览器；`latex-review-preview .latex-review/latest --open-browser` 会预览已有报告并打开默认浏览器。详细行为及智能体验收流程见[报告预览](Docs/report-preview.md)。

报告可通过本机服务按需调用配置的 LLM 接口，把两版中包含变化的完整段落、标题和图注译为中文。支持逐项或批量翻译、英中对照、术语表、本机缓存及含译文报告导出；默认不自动请求 API，第一版不翻译表格。接口地址和模型配置在 `[translation]`，所有项目自动读取 `~/.config/latex-review/config.toml`（或 `$XDG_CONFIG_HOME/latex-review/config.toml`）的翻译默认值，项目可逐项覆盖；术语表合并且项目同名词条优先。密钥来自环境变量。配置与使用步骤见[变更内容翻译](Docs/translation.md)。

配置私有发布服务器后，执行 `latex-review main.tex --open-browser --upload` 可生成报告、通过 SSH 发布静态页面和已有译文，再打开本地预览。主页提供论文选择和最近 10 次发布历史；网站使用 HTTPS 和账号验证。上传失败保留本地结果，退出码为 16。配置与运维见[私有报告发布](Docs/report-upload.md)。

GitHub 拉取请求的报告产物与评论发布须由仓库维护者按[工作流启用说明](Docs/github-actions.md)显式配置。模板默认不在本仓库运行。

需要查看实际版式时，显式传入 `--compile`。命令会在两个私有副本中调用已安装的 `latexmk`，保留各侧编译文档、日志、状态及页面预览；`--compile-new-only` 只编译修改后版本。`--tex-engine` 可选 `pdflatex`、`xelatex` 或 `lualatex`。复杂宏和绘图可再加 `--sandbox-render`，要求 macOS 的隔离机制通过自检，否则拒绝高级执行。工具不会自动安装 TeX；缺少工具或某侧编译失败时，结构报告仍会保留。详细用法和产物见[真实编译、页面对比与沙箱渲染](Docs/compiled-rendering.md)。

项目配置从当前目录的 `.latex-review.toml` 读取，也可用 `--config` 指定；显式命令行选项覆盖配置，配置覆盖默认值。翻译设置先读取用户全局默认值，再由项目配置覆盖。支持 `entry`、`output`、`ignore`、`[git].default_old/default_new`、`[diff].comments`、`[compile]`、`[translation]` 和 `[macros.名称]`；`--no-comments` 可覆盖配置中的注释审阅开关。`--math` 仅支持 `mathjax`，`--format` 仅支持 `html,json`；其他渲染与差异开关只接受既定值，未知或不支持的配置会报错。默认不编译；命令行 `--compile` 或配置 `[compile].enabled = true` 才启用编译。完整报告返回 0，降级或编译失败报告返回 2，来源不可读取返回 4，内部错误返回 8；一般提示不改变成功状态。无效参数或配置返回 64。

第一点五版可在 `[macros.名称]` 中设置受控宏占位预览；两侧引用会各自显示 `.bib` 作者、题目和年份。支持范围、配置样例和参考文献语义边界见[参考文献与宏占位预览](Docs/bibliography-macros.md)。

解析缓存默认放在报告目录旁，可用 `--cache-dir` 指定位置，`--no-cache` 绕过，`--clear-cache` 清理。缓存元信息包含两侧命中状态、依赖内容指纹和依赖关系；修改章节或宏会重新解析受影响的比较版本，图片或文献内容变化会更新审阅依赖指纹。详见[缓存与资源变化](Docs/cache-assets.md)。

智能体可用 `latex-review-query` 精简查询已有报告，用 `latex-review-check-scope` 核对文件修改范围。生成命令自动写入 `run.json`；`latex-review-record-acceptance` 可另行记录浏览器验收声明及证据路径。命令和数据含义见[智能体接口](Docs/agent-tools.md)。无效参数或配置在输出准备前不会覆盖旧报告；读取清单时仍须核对本次进程退出码。

契约结构、兼容性和使用样例见 [公共数据契约](Docs/data-contract.md)。解析和预览接口见 [结构解析与内容预览](Docs/structure-preview.md)，比较接口见[结构化内容差异](Docs/structured-content.md)。同路径图片内容变化按文件字节指纹产生图明细；可确定的表格单元格和行列变化会标记坐标。运行时依赖包含 `jsonschema` 和 `plasTeX`；`pytest` 属于可选测试依赖。

## 当前论文持续预览

运行 `latex-review-live main.tex --open-browser`，启动只读取当前论文的本机 HTML 预览。服务不读取 Git，不比较历史版本，也不生成差异报告。保存正文、章节、图片或参考文献后，服务自动更新页面。按 Ctrl+C 停止服务。

```sh
latex-review-live main.tex --open-browser
# 入口位于子目录时，显式指定论文项目根目录：
latex-review-live tex/main.tex --root . --open-browser
```

持续预览与审阅报告共用字体、配色、页头、工具栏和正文样式。持续预览使用居中正文；工具栏提供可收起的搜索与章节导航、刷新状态和诊断入口。自动更新尽量保留当前章节、滚动位置、搜索词和面板展开状态。

持续预览支持英文 → 简体中文的逐段翻译和全文翻译。点击段落、标题或图注旁的“翻译此处”，或点击“翻译全文”。页面默认英中对照，可切回英文原文。译文可缓存，并导出英中对照单文件 HTML。保存论文后，未变内容继续使用缓存；已变内容等待手动重新翻译。表格、参考文献和独立公式保留原文。

翻译复用全局及项目 `[translation]` 配置和术语表。可用 `--config` 指定项目配置，或用 `--translation-cache-dir` 指定独立缓存目录。详见[翻译配置与操作](Docs/translation.md)。

英中对照采用逐段等宽双栏。英文在左，中文在右。正文空间不足时，每段改为英文在上、中文在下。悬停句子突出两侧对应内容；点击固定，再次点击或按 Esc 取消。选择文字后可使用批注操作。标题译文位于原标题下方；图注使用双栏；图、表和独立公式占整行。中文侧显示翻译状态；对应关系不完整时保留译文并提供重试。导出保留相同布局，实时更新尽量保留阅读模式和当前段落位置。

默认产物目录为论文项目内的 `.latex-review/live`，可用 `--output` 指定独立目录。页面展示预览诊断；更新失败时保留上次成功的页面。公式使用在线 MathJax。HTML 内容预览不保证与 PDF 排版一致。此命令无需安装 TeX；PDF 图片缺少转换工具时会显示资源链接及诊断。服务仅监听 `127.0.0.1`。
