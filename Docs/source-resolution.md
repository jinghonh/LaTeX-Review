# 比较来源与展开接口

主票 #4 提供 `latex_review.resolve_sources`。使用 `with resolve_sources(...) as pair` 取得 `pair.old` 与 `pair.new`；每侧的 `ProjectSource.expand()` 返回 `ExpandedProject`。Git 提交和工作区来源在临时目录中完整物化，离开 `with` 后目录即被删除，因此下游解析必须在作用域内完成。

```python
from latex_review import resolve_sources

with resolve_sources(entry="main.tex", old_dir="paper-old", new_dir="paper-new") as pair:
    old = pair.old.expand()
    new = pair.new.expand()
    old_positions = old.origin_ranges(0, len(old.text))
```

来源形式互斥：`old_dir`、`new_dir` 与相对 `entry`；两个独立文件 `old_file`、`new_file`；或 Git 的相对/绝对 `entry`，可另指定成对的 `old_revision`、`new_revision`。Git 默认比较当前 `HEAD` 的完整对象树和当前磁盘工作区。两侧的 `identity.kind` 分别为 `git`、`worktree`、`directory` 或 `file`；Git 标识固定为完整提交 SHA。工作区副本依据 Git 索引列出已跟踪路径，再读取磁盘内容，同时纳入未忽略的未跟踪文件；已删除的文件不会从索引回填。被忽略的未跟踪依赖给出 `ignored_dependency` 诊断。

`ExpandedProject.text` 是包含文件替换后的文字；宏定义、调用、注释及资源命令原样保留。宏定义体内的依赖命令不会提前执行，并给出不确定性诊断。`dependencies` 逐次记录包含、图与文献资源；重复包含各有不同 `include_instance`。静态解析不能确定的依赖、缺依赖和包含循环保留原命令，并分别报告诊断。此层不执行 TeX 宏，也不构建审阅节点。

`ExpandedProject.origin_ranges(start, end)` 返回展开区间内所有来源片段。展开偏移和原文件偏移都是**零起始 Unicode 字符下标，区间右端不含**；行列均从 1 开始，结束行列指向右端排他位置。`\r\n` 算一个换行，多字节字符算一个字符。每个 `MappedRange` 同时记录展开区间、原文件相对路径及字符区间、行列、包含实例和 `confidence`。已确定内容为 `exact`；缺失或无法静态展开的包含命令为 `unknown`。跨文件节点和重复包含应保留返回的全部片段，不能合并成单个连续原文范围。此映射不改变公共 `ReviewNode` 输出契约；后续结构解析可用片段集合决定节点定位或明确标记不确定。

`SourceError` 提供稳定的 `code`、`side`、`message`、`path`。依赖层的非致命问题是 `SourceIssue`，包含 `code`、所属侧、原文件字符跨度和 `include_chain`。入口不存在、不可读或超出根目录时抛 `SourceError`；非入口依赖问题保留为诊断。目录越界和链接逃逸目前做基本边界校验，统一安全加固归后续安全票。

在完整审阅流水线接入前，可用 `latex-review ... --inspect-sources` 输出双侧展开文本、依赖、映射段和诊断的 JSON。支持 `latex-review --old-dir A --new-dir B --entry main.tex --inspect-sources`、`latex-review old.tex new.tex --inspect-sources`、`latex-review main.tex [--old REV --new REV] --inspect-sources`。未加此选项的审阅命令仍提示尚未实现。
