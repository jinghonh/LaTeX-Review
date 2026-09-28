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

`write_report` 返回的 `ReportResult.document` 是实际序列化到 `diff.json` 的审阅文档，合并了比较、预览和资源诊断；页面摘要和变更卡片均从该文档生成。正文主变更按 #2 契约计数，引用等明细只命中类别，不另增总数。`pdf_converter` 默认自动查找 `pdftoppm`；传入空字符串可禁用 PDF 预览，传入可执行文件路径可指定受控转换器；`conversion_timeout` 默认 8 秒。转换器只读取图 PDF 的第一页，限制输出宽高及文件大小，失败则保留原 PDF 文件链接、图注、来源和未知尺寸提示。此接口不执行论文编译引擎。

完整默认命令行、退出码和流水线接入由主票 #18 负责。
