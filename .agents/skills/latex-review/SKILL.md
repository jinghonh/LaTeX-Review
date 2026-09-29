---
name: latex-review
description: 在 LaTeX 论文完成一批修改后，或用户明确要求结构化改稿审阅时，运行 LaTeX Review 并核对机器差异、诊断与修改范围。
---

# LaTeX Review

用户要求审阅时使用此技能；完成一批论文改稿后，除非用户要求跳过，也运行一次。命令行只提供审阅证据，是否符合用户意图仍须核对。

## 运行审阅

1. 先确认用户要求修改的章节、文件和对比基线。查看 **git status --short** 与 **git diff --name-status HEAD**。默认来源是磁盘工作区，包含暂存及未暂存的已跟踪修改和未忽略的未跟踪文件；其中也可能有本轮之前的工作。
2. 用 **command -v latex-review** 确认命令行工具已安装。如果找不到，说明可在 Python 3.11 或更新版本的环境中运行 **python -m pip install /path/to/LaTeX-Review** 安装本工具，并参见本仓库的[使用指南](../../../Docs/user-guide.md)。不要自行安装软件或修改论文源文件。
3. 按用户给出的来源选择模式。常用的 Git 模式为：

~~~sh
latex-review main.tex --output .latex-review/latest
~~~

指定 Git 版本可添加 **--old REV --new REV**；新版可用 **worktree** 表示当前磁盘工作区。目录模式使用 **--old-dir OLD --new-dir NEW --entry path/to/main.tex --output REPORT_DIR**；两个独立文件使用 **latex-review OLD.tex NEW.tex --output REPORT_DIR**。为避免报告混入来源内容，建议目录模式把报告输出放在两个输入目录之外。

4. 运行时记录退出码和标准错误。工具会先替换所选报告目录，只在专用报告目录中输出文件；若该目录已有用户文件，改用新的输出路径。默认不传 **--compile**。
5. 若 **diff.json** 存在，将其作为 JSON 读取并检查摘要、每项主变更及双侧源码位置。无论是否有差异文件，都独立读取存在的 **diagnostics.json**，检查每项诊断的严重级别、代码、说明和源码位置；来源失败时也阅读标准错误。对照用户指定范围、Git 变更清单及删除路径，指出超范围修改、删除、缺失依赖、源码定位不确定和需要判断的警告或错误。
6. 若存在 **report.html**，默认执行浏览器验收：用 **latex-review-preview REPORT_DIR** 启动预览，保存本次进程或终端会话标识，并读取标准输出中的实际 HTTP 地址。命令不可用时，可在已安装本项目的同一 Python 环境运行 **python -m latex_review.serve REPORT_DIR**。服务就绪后，用可用且允许访问本机网页的浏览器工具打开该地址。
7. 核对页面内容与机器数据，查看实际渲染截图，检查图片、公式及错误提示；对报告中存在的筛选、阅读范围、主变更导航和句子明细跳转各执行一次，确认双侧整句着色与实际落点。分别记录通过、失败和未完成的项目；句子锚点缺失时按定位未完成记录，不能把段落高亮当作句子定位成功。外部编辑器协议跳转独立记录。若工具缺失或访问被禁止，保留机器数据检查结果，明确将渲染和交互验收标为未完成；遵守工具限制，不通过更换地址或工具规避访问禁令。
8. 验收成功、失败或中断后，都在清理步骤中停止本次启动的服务：向保存的终端会话发送 Ctrl+C，或仅向本次进程发送终止信号，并确认进程退出。用户明确要求继续预览时才保留。服务已停止时，说明临时地址已失效。
9. 向用户提供 **report.html** 的完整路径及报告目录，简述主变更、诊断、范围、删除检查结果及浏览器验收完成情况。支持本地文件链接时链接报告入口。

小型报告可用 Python 提取机器数据，避免直接打印所有节点原文：

~~~sh
python - <<'PY'
import json
from pathlib import Path

root = Path(".latex-review/latest")
diff = json.loads((root / "diff.json").read_text(encoding="utf-8"))
diagnostics = json.loads((root / "diagnostics.json").read_text(encoding="utf-8"))
print("summary:", diff["summary"])
for change in diff["changes"]:
    print(change["kind"], change["categories"], change["summary"])
    print("  old:", change["source_old"])
    print("  new:", change["source_new"])
for item in diagnostics["diagnostics"]:
    print(item["severity"], item["code"], item["message"])
PY
~~~

若报告使用了自定义输出目录，读取时将示例中的路径改为实际目录。

## 解释退出码和诊断

- **0**：完整报告已生成。仍须核对变更与诊断；该退出码不代表修改通过人工审阅。
- **2**：报告已生成，但含警告或错误诊断，也可能是降级解析或源码对比回退。读取两个 JSON 文件并说明限制，不要称为无异常审阅。
- **4**：输入来源无法读取。修复入口、提交或依赖问题；目录中可能只有失败诊断，没有完整报告。
- **8**：命令行内部错误。保留错误输出并说明审阅未完成，不要把部分文件当作完整报告。
- **64**：参数或配置无效。修正命令或配置；例如仅编译新版本或沙箱渲染选项须同时启用编译。

仅含 **info** 诊断时退出码可能仍为 0，应按任务上下文阅读。任何退出码都不能代替对报告内容的判断。

## 首版边界

当前正式支持 macOS，要求 Python 3.11 或更新版本。报告是内容预览，不保证最终排版；阅读页面显示可预览内容与简短占位，详细诊断留在机器数据中。公式展示使用联网 MathJax；不可用时原位显示“此处暂无法预览”。简单表格可比较并定位确定的单元格、行列变化，复杂表格保留整体变化与不确定性。同路径图片文件的字节指纹变化可形成图明细，不能据此断言视觉内容变化。正文按段落等独立结构计主变更，句子作为内部明细展示双侧完整内容和对应定位。若输入文件被删除，Git 状态可能记录删除，但缺失依赖会妨碍常规结构比较；同时报告 Git 删除和相关诊断。

仅在用户明确要求时使用 **--compile** 进行整篇 LaTeX 编译；默认审阅不编译整篇，也不自动安装 TeX。报告生成可按需调用已有 TeX 环境制作复杂片段预览，失败时保留报告并在原位显示“此处暂无法预览”。显式整篇编译在隔离副本中运行，缺少工具或某侧失败时保留报告并返回 2；来源读取失败仍返回 4。可用 **--compile-new-only** 只编译修改后版本；需要复杂宏受限渲染时再显式启用 **--sandbox-render**。具体选项与产物见[编译说明](../../../Docs/compiled-rendering.md)。

目录、文件及 Git 模式、配置、报告分享和源码位置的版本含义见[使用指南](../../../Docs/user-guide.md)。
