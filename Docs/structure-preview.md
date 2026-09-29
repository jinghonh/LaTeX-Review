# 结构解析与内容预览接口

主票 #7 在 #4 的展开结果上提供 `parse_project(expanded)`，返回 `ParsedProject`。其 `review_nodes` 是 #2 公共契约的 `ReviewNode` 元组，可直接交给后续差异引擎；`nodes` 中的 `ParsedNode` 额外保留展开区间、`origin_ranges()` 返回的**全部**来源片段、标签、引用键、交叉引用键、图资源与节点诊断。`labels` 将标签映射到审阅节点。`diagnostics` 包含来源层 `SourceIssue` 转成的公共 `Diagnostic`，以及解析警告。没有可靠的单一原文区间时，公共位置明确标记不确定，完整片段仍可由 `ParsedNode.origins` 查询。

`render_preview(old, new)` 返回 `PreviewResult(html, diagnostics)`；预览阶段继续使用 plasTeX DOM 呈现文本格式、行内公式、引用与交叉引用，HTML 包含两侧统一样式的连续阅读视图。每个审阅节点有稳定 ID；页面锚点加 `old-` 或 `new-` 前缀。公式以原始 TeX 留在 HTML 中，由默认 MathJax 3 在线排版；加载失败或超时时显示明确提示，原始公式仍可阅读。页面明确声明是内容预览，不代表最终编译版式。未知内容会显示原文，所有块节点都可展开查看 LaTeX 原文；资源目前显示路径与图注，后续报告层处理资源复制或缩略图。

```python
from latex_review import resolve_sources, parse_project, render_preview

with resolve_sources(entry="main.tex", old_dir="old", new_dir="new") as pair:
    before = parse_project(pair.old.expand())
    after = parse_project(pair.new.expand())
    preview = render_preview(before, after)
    # preview.html、preview.diagnostics 和 before.review_nodes 可供后续流水线使用。
```

解析通过静态扫描识别未知宏和环境，字符扫描构建有精确原文跨度的结构树；只有允许的行内排版宏进入 plasTeX DOM 预览。节点原文、来源片段与公共位置都以 Unicode 字符零起始半开跨度和一基行列为准。未知宏、未知环境和未能静态展开的依赖局部回退；复杂宏或自定义类可能降级。解析阶段不运行本地 TeX 引擎，也不载入论文指定的包或执行宏定义。完整三栏报告、统一诊断文件和退出码由命令行流水线提供。
