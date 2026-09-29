# LaTeX Review 使用指南

LaTeX Review 是独立命令行工具。Codex 技能只负责在改稿后调用 CLI、读取结果并检查范围；人类也可以直接运行同一命令。报告生成不代表修改已通过审阅。

## 安装

正式支持 macOS，要求 Python 3.11 或更新版本。默认结构审阅不需要 LaTeX 引擎；显式编译需用户自行安装 `latexmk` 和所选引擎。联网安装时，在 LaTeX Review 工具仓库内创建虚拟环境并安装：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
latex-review --help
latex-review --version
```

如果 CLI 要在论文仓库中使用，可在已激活的目标虚拟环境中安装本工具仓库：

```sh
python -m pip install /path/to/LaTeX-Review
```

运行 Codex 的 shell 环境必须能找到该环境中的 `latex-review` 命令。检查方式是 `command -v latex-review`。本工具仓库包含项目级技能 `.agents/skills/latex-review/SKILL.md`；在 Codex 中打开本仓库时可选择 `$latex-review`。如需在其他仓库使用，可由用户手动复制该技能目录到目标仓库的 `.agents/skills/latex-review/`，或复制到 `~/.codex/skills/latex-review/` 供个人使用。设置了 `CODEX_HOME` 时，个人技能目录使用 `$CODEX_HOME/skills/latex-review/`。复制后重新加载 Codex 会话。技能不会自动安装 CLI、复制到其他仓库或设置全局钩子；运行审阅时仅在指定报告目录写入或替换报告，不修改论文源文件。

## 运行方式

### Git 提交与当前工作区

在论文仓库中执行：

```sh
latex-review main.tex
```

默认比较 Git `HEAD` 与磁盘工作区，并写入 `.latex-review/latest/`。工作区快照包含暂存和未暂存的已跟踪文件修改，也包含未忽略的未跟踪文件；它以磁盘内容为准，不会从索引回填已删除文件。因此报告可能包括本轮改稿前已经存在的工作区修改。需要比较特定版本时：

```sh
latex-review main.tex --old <旧版提交> --new <新版提交或worktree>
```

`--new worktree` 表示当前磁盘工作区。输出目录会在生成前被替换，只把报告文件放在此目录中；如需保留旧报告，请为新一轮使用新的 `--output` 路径。

### 两个项目目录

```sh
latex-review --old-dir ./before --new-dir ./after --entry main.tex \
  --output ./review-output
```

`--entry` 是两侧共同的相对入口。建议将报告输出目录放在两个输入目录之外。

### 两个独立文件

```sh
latex-review ./before.tex ./after.tex --output ./review-output
```

独立文件模式适用于入口文件本身即可构成审阅来源的情况。依赖文件仍须能按 CLI 的来源规则解析。

### 配置

CLI 默认读取当前目录的 `.latex-review.toml`，也可用 `--config path/to/config.toml` 指定。命令行选项覆盖配置，配置覆盖默认值。常用配置如下：

```toml
entry = "main.tex"
output = ".latex-review/latest"
ignore = ["build/**", "generated/**"]

[git]
default_old = "HEAD"
default_new = "worktree"

[diff]
comments = false

[render]
math = "mathjax"

[compile]
enabled = false
new_only = false
engine = "pdflatex"
timeout = 90
sandbox_render = false
```

可调整的设置为 `entry`、`output`、`ignore`、`[git].default_old`、`[git].default_new`、`[diff].comments`、`[render].math` 与上述 `[compile]` 选项；`math` 仅支持 `mathjax`。`[render].copy_assets` 固定为 `true`、`[render].show_unknown_macros` 固定为 `true`、`[diff].move_detection` 固定为 `false`、`[diff].citation_semantics` 固定为 `true`、`[diff].formula_token_diff` 固定为 `true`。`--comments`、`--no-comments` 可覆盖注释审阅配置。默认忽略源码注释；启用后注释变化独立计入主变更总数，但不计入正文增删词数。默认也不编译；命令行 `--compile` 或配置 `enabled = true` 才启用双侧真实编译，`--compile-new-only` 可只编译新侧，`--tex-engine` 可指定引擎，`--sandbox-render` 须与编译同时启用。编译产物、限制和沙箱要求见[编译说明](compiled-rendering.md)。

`--inspect-sources` 仅输出双侧来源展开 JSON，不生成审阅报告。

## 读取结果

一次完整报告目录至少包含：

- `report.html`：供人阅读的三栏报告。
- `diff.json`：节点、主变更、源码位置、摘要及诊断。
- `diagnostics.json`：独立诊断清单，即使没有诊断也会生成。
- 存在受控图片资源时包含 `assets/`；成功转换图 PDF 时包含 `previews/`。

`diff.json` 中 `summary.changes` 与主变更数对应；引用、公式等变更明细归属于主变更，不另行重复计数。类别统计可能重叠。审阅时应检查每项变更的 `kind`、`categories`、摘要和双侧位置，再检查 `diagnostics.json` 中所有警告和错误。`info` 诊断可能不改变退出码，但仍应按任务上下文阅读。

论文规则诊断还会标出问题是既有、新增或已解决，并列出判断证据及各处源码位置；详见[论文规则诊断](paper-rules.md)。规则诊断即使为警告也不单独改变退出码。

| 退出码 | 含义 | 处理 |
| --- | --- | --- |
| `0` | 完整报告已生成 | 仍须人工核对变更和诊断；不代表审阅通过 |
| `2` | 报告已生成，但有非规则警告或错误诊断；也可能是降级源码对比报告或显式编译失败 | 检查 `diff.json` 和 `diagnostics.json`，说明解析、编译或定位限制 |
| `4` | 输入来源无法读取 | 修复入口、提交或依赖问题；可能只留下失败诊断 |
| `8` | CLI 内部错误 | 保存错误输出并报告未能完成审阅 |
| `64` | 参数或配置无效 | 修正命令/配置；仅新侧编译及沙箱渲染须同时启用编译 |

CLI 输出的 HTML 路径只是报告入口，不是审阅结论。不要将报告生成说成内容正确、范围符合或编译成功。

## 检查改稿范围和删除

运行前记下用户要求修改的章节和文件。运行后：

1. 查看 `git status --short`，并用 `git diff --name-status HEAD` 核对已跟踪文件变化；前者也能显示未跟踪和已删除路径。
2. 从 `diff.json` 检查全部主变更的 `kind`、`source_old`、`source_new` 和摘要，确认变更落在预期章节或文件中。
3. 重点核查 `removed` 主变更、Git 中的文件删除、缺失依赖诊断和源码定位不确定项。被删除的依赖可能无法产生常规结构化变更，因此 Git 删除清单与报告诊断应一并查看。
4. 将警告/错误诊断、非目标范围变化和意外删除逐项交给用户决定；不要自行把它们判定为可接受。

如果 `git status` 中包含本轮之前的修改，默认报告同样会纳入这些磁盘工作区内容。必要时选择更合适的 `--old` 基线或两目录模式；不要声称报告只覆盖了本轮编辑，除非来源确实如此。

## 分享报告和理解源码位置

分享时复制或压缩整个报告目录，而不是只发 `report.html`。页面使用相对路径引用 `assets/` 和 `previews/`，将整个目录移动到其他位置后仍能读取；可直接用浏览器打开 `report.html`。公式展示会联网加载 MathJax。断网或依赖加载失败时，原始 TeX 和失败提示仍保留，因此报告不是完全离线包。

报告中的源码位置属于各自比较版本的原始源文件，并非展开后的临时文件路径。Git 模式的旧侧位置对应所选旧提交，新侧位置对应所选新提交或当前工作区；它们不一定对应现在磁盘上的文件。Git 历史版本没有持久副本，报告会显示路径和行号，但不会把旧侧链接错误地指向当前同名文件。目录和独立文件模式的位置则对应各自提供的来源。

## 生成样例报告

以下命令先创建物理路径已解析的临时目录，再写入旧版和新版合成论文并审阅。解析物理路径可避免 macOS 临时目录软链接被 CLI 的输出路径保护规则拒绝：

```sh
sample_root="$(cd "$(mktemp -d /tmp/latex-review-sample.XXXXXX)" && pwd -P)"
mkdir -p "$sample_root/old" "$sample_root/new"
cat > "$sample_root/old/main.tex" <<'EOF'
\section{Overview}
The draft is ready.
EOF
cat > "$sample_root/new/main.tex" <<'EOF'
\section{Overview}
The revised draft is ready.
EOF
latex-review --old-dir "$sample_root/old" \
  --new-dir "$sample_root/new" --entry main.tex \
  --output "$sample_root/report"
```

命令行应打印 `report.html` 的路径。读取 `report/diff.json` 可核实一个文本主变更和新旧来源位置；`report/diagnostics.json` 应存在。退出码 `0` 表示这次样例生成了完整报告，不代表真实论文经过审阅。报告目录中有 `report.html`、两个 JSON 文件及存在时的资源目录。

在当前仓库干净 Python 虚拟环境中运行以上样例的命令和实际结果见[端到端验收记录](end-to-end-acceptance.md)。
