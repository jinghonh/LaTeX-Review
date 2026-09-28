# 核心结构匹配与正文差异

主票 #8 提供 `match_nodes(old, new)` 和 `compare_projects(old, new, review_comments=False)`。参数均为 `parse_project` 返回的 `ParsedProject`；调用方须在 `resolve_sources` 的作用域内完成来源展开与解析。比较结果的 `document` 是现有 1.0 公共契约的 `ReviewDocument`，可交给 `dumps` 与 `validate_document`。`mapping` 是供后续结构化差异分析复用的双侧映射；`token_changes` 按主变更标识保留词元操作，公共契约中的 `ChangeDetail` 同时给出可显示的增删片段。

```python
with resolve_sources(entry="main.tex", old_dir="old", new_dir="new") as pair:
    old = parse_project(pair.old.expand())
    new = parse_project(pair.new.expand())
    result = compare_projects(old, new, review_comments=True)
    diff_json = dumps(result.document)
    old_to_new = result.mapping.old_to_new
```

匹配先采用双方唯一的显式标签，再采用同章节、同类型下唯一的规范化内容。其余候选结合内容相似度、已确定的前后邻居和相对位置评分；只有双方最佳候选都明显优于次优候选且达到阈值，才建立配对。子节点须先有已配对的父节点；父节点已可靠配对时，即使章节标题改变，子节点的唯一标签仍可锚定重写内容。重复标签不会独占配对权。解析器有时把标题后的 `\label` 同时附在标题元数据及下一段原文中；比较时将该标签归属标题，不把它误当作下一段的文字或匹配键，原文仍保留在节点中。标题路径比较会忽略开头的章节编号。不同类型及不同章节不配对；跨章节移动识别由第一点五版处理。`NodeMapping.pairs` 给出置信度和理由；未配对节点给出最佳候选分数及拒绝理由，正文主变更按删除与新增输出并附带同样信息。每侧节点最多参加一对，排序和序列差异均确定，同一输入反复比较得到相同结果。

正文扫描把普通空白和换行归一为可显示的 `␠` 边界词元，因此空白数量或换行方式变化本身不生成语义变更，有无空白仍可区分。控制词后由 TeX 忽略的空白不形成边界。注释默认按 TeX 规则移除；`ReviewNode.raw_latex` 和解析器的来源片段仍保留原始源码。命令连同可选及必选参数、引用、交叉引用、行内公式、`\verb` 与 `verbatim`、`Verbatim`、`lstlisting`、`minted` 环境保持为完整词元；其内部空白不折叠。未转义的 `%` 到行尾及紧随的换行被识别为注释，`\%` 是正文字符，逐字内容中的 `%` 是逐字内容。数学片段和引用键改动能出现在可显示词元差异中，但不作为正文词数；`\cite`、`\citep`、`\citet`、`\nocite`、`\parencite` 和 `\textcite` 的引用键变化可命中 `citation` 明细，包括带两组可选参数的命令。

正文词数的确定规则：连续的 Unicode 拉丁字母、十进制数字、词内撇号及附着的组合符号算一词；每个汉字算一词；其他非空白文字字符按单个字符算一词。标点、普通空白边界、表示不换行空格的 `~`、引用、交叉引用、公式及逐字内容不计词数；`~` 与普通空格仍是不同词元。常见排版命令（如 `\textbf`、`\emph`）的文本参数参与词数，但整个命令仍是一个受保护词元，因此改动参数时按该词元的旧、新文本分别计数。词数来自实际增删词元；纯空白修改为零。一个段落内的文字与引用变化归入同一主变更，分类明细可重叠，摘要按主变更计数。

`review_comments` 默认关闭。开启后，段内及独立行注释以独立的 `comment` 审阅节点和主变更记录，计入总数和分类命中数，不计正文增删词数。相同注释按顺序稳定对齐，修改、插入和删除分别记录。注释节点仅在开启时追加到两侧 `ReviewDocument` 节点列表；公共数据格式无需升级。

主票 #10 已在同一 `compare_projects` 接口上加入公式、引用、图和表的专门明细；计数、来源与回退规则见[结构化内容差异接口](structured-content.md)。三栏报告仍由 #16 消费比较结果。
