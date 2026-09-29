# 三栏报告输出接口

主票 #16 提供 `write_report(old, new, comparison, output_dir)`，其中两侧来自 `parse_project`，比较结果来自 `compare_projects`。调用时仍须处在 `resolve_sources` 的作用域内，因为 Git 来源快照退出作用域后会删除。

```python
from latex_review import compare_projects, parse_project, resolve_sources, write_report

with resolve_sources(entry="main.tex", old_dir="old", new_dir="new") as pair:
    old = parse_project(pair.old.expand())
    new = parse_project(pair.new.expand())
    result = compare_projects(old, new)
    report = write_report(old, new, result, "review-output")
    print(report.html, report.diff_json)
```

输出目录含 `report.html`、`diff.json`、`diagnostics.json` 和双侧隔离的 `assets/old/`、`assets/new/`；成功转换的 PDF 预览放在 `previews/old/`、`previews/new/`。受管路径中的符号链接会被拒绝，写入时先生成临时文件再替换目标。页面可直接通过 `file://` 打开，节点与变更卡片均在生成时写入，不通过本地 `fetch` 加载。图片以相对路径引用；移动整个目录后仍可读取。页面使用已有内容预览与在线 MathJax 公式排版，资源失效及预览失败仍保留原文与提示。诊断列表直接取自最终审阅文档；段内引用和公式明细优先跳到对应子节点，无法唯一定位时回退到主变更节点。

`write_report` 返回的 `ReportResult.document` 是实际序列化到 `diff.json` 的审阅文档，合并了比较、预览和资源诊断；页面摘要和变更卡片均从该文档生成。正文主变更按 #2 契约计数，引用等明细只命中类别，不另增总数。`pdf_converter` 默认自动查找 `pdftoppm`；传入空字符串可禁用 PDF 预览，传入可执行文件路径可指定受控转换器；`conversion_timeout` 默认 8 秒。转换器只读取图 PDF 的第一页，限制输出宽高、时间及文件大小，失败则保留原 PDF 文件链接、图注、来源和未知尺寸提示。仅允许受控图片格式与 PDF 被复制或链接；SVG、HTML 等其他格式显示安全占位并产生可定位诊断。此接口不执行论文编译引擎。

命令行现已接入该接口；`latex-review main.tex` 默认比较 Git HEAD 与磁盘工作区，将报告写到 `.latex-review/latest/`，并写入独立的 `cache-meta.json`。缓存命中状态不写入 `diff.json`，因此冷热运行的机器语义数据保持一致。结构解析整体失败时另生成明确标识的源码对比报告，仍保留 `diff.json` 和 `diagnostics.json`。

结构解析成功的报告还会显示[论文规则诊断](paper-rules.md)的双版本状态、证据和源码位置；规则诊断本身不影响命令退出码。

## 阅读交互与单文件交付（#24）

`write_report` 可额外传入 `single_file=True`，返回值的 `single_html` 指向 `report-single.html`。该文件内嵌完整审阅数据、样式、脚本、双侧图片及 PDF 图片预览，可以单独搬运；原有报告目录仍照常生成。图片和 PDF 原件按 MIME 类型编码为数据地址，缺失、路径逃逸或不能内嵌的资源会明确报错。单项本地资源上限为 25 MiB，累计上限为 100 MiB；生成文件因 Base64 编码通常约为原资源体积的 4/3，实际体积以文件大小为准。在线 MathJax 是唯一的展示外链：断网或加载失败时，页面保留原始 TeX 并显示失败提示。

验收用的双侧一像素 PNG 样例生成的单文件为 23,410 字节；大小会随论文正文、图片和嵌入的报告数据增长。将该文件单独移至新路径并删除原报告目录后，本地 Chromium 的 `file://` 检查确认两侧图像、筛选、上下文折叠、变更跳转和节点同步滚动仍可用。

页面可开启按节点对应关系的同步滚动，按当前节点内的相对位置对齐。无对应节点时取最近的已配对相邻节点，且滚动时短暂抑制反向事件，避免反馈抖动。筛选后的变更可用按钮或 `Alt+↑`、`Alt+↓` 导航；输入框、选择框及可编辑内容不触发快捷键。阅读范围可在完整文档与变更上下文之间切换；上下文保留命中节点及其相邻节点，筛选后重新计算。仅新增或仅删除时，缺失侧从另一侧的邻近配对节点取得阅读上下文；完全没有配对节点时显示提示。点击变更或明细仍可定位双侧节点，缺侧给出明确提示。

源码跳转需显式设置 `editor_url_template="vscode://file/{path}:{line}:{column}"`，也可省略列号。仅接受此 `vscode://file/` 模板，路径按 URL 编码，行列从原文位置取得。目录、独立文件及当前工作区来源可生成链接；Git 历史版本没有可用的持久副本，因此不生成指向当前同名文件的链接。卡片始终展示原文位置并提供复制按钮；浏览器拒绝外部协议时可手动复制该文字。当前实现仅静态验证了 VS Code 模板及本机安装状态；本环境的浏览器安全策略禁止实际点击本地外部协议链接，因此未宣称编辑器端到端验证通过。其他编辑器协议未验证，也未开放自定义协议。

## 编译、页面对比与沙箱渲染（#33）

第二版的显式编译选项在写报告前生成额外诊断、编译产物与页面数据，再由同一报告接口嵌入可控位图及页面跳转。默认命令仍不运行 TeX。编译状态、页面数据和沙箱行为见[真实编译、页面对比与沙箱渲染](compiled-rendering.md)。
