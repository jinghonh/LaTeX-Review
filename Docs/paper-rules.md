# 论文规则诊断（#38）

`write_report` 在两侧结构解析成功后检查论文规则。结果同时写入 `diff.json`、`diagnostics.json` 和报告的诊断区。每条规则诊断保留原有的 `code`、`severity`、`message`、`source_old`、`source_new`，另有 `rule_status`（`existing`、`new`、`resolved`）、非空 `evidence`，以及列出全部命中位置的 `related_sources_old`、`related_sources_new`。两侧均有同一规则键为既有，仅新侧有为新增，仅旧侧有为已解决。位置按原始源文件映射；映射不确定时给出未知来源及理由。

| 代码 | 检查依据 | 级别 |
| --- | --- | --- |
| `bibliography_key_unresolved` | `\cite` 等已支持引用命令中的键在已读取 `.bib` 条目及手写 `\bibitem` 中均不存在 | 警告 |
| `unresolved_reference` | `\ref`、`\eqref`、`\autoref`、`\pageref` 的目标标签不存在 | 提示 |
| `duplicate_label` | 同一侧 `\label` 键定义超过一次；证据和关联位置列出每次定义 | 警告 |
| `figure_unreferenced`、`table_unreferenced` | 有唯一标签的图或表没有被上述交叉引用命令引用 | 提示 |
| `equation_number_changed` | 同一唯一标签的 `equation` 环境，两侧各有一个字面值 `\tag{}`，且值不同 | 警告 |
| `equation_number_unknown` | 同一标签的 `equation` 环境发生结构序位变化，或仅一侧可读出显式 `\tag{}`；最终编号无法确定 | 提示 |

扫描已展开的正文，忽略普通注释和常见逐字环境。正常删除引用不会产生“新增”问题；旧侧原有问题随引用删除会显示为“已解决”。手写 `\bibitem` 可证明引用键存在，即使其元数据仍无法结构化展示。没有唯一标签的图表不判定“未被引用”；重复标签由专门规则报告。未知宏、自定义引用命令、动态生成的键、复杂数学环境及计数器重定义不参与确定性判定。自动排版编号不会按源码顺序猜测；当前实现不读取编译 `.aux`，即使启用 `--compile` 也只以明确的字面 `\tag{}` 判断编号变化。

规则诊断仅供审阅，不构成发布阻断条件，也不单独改变命令退出码。来源读取、结构解析、报告资源或真实编译降级仍沿用原有退出码协议。结构解析整体失败时生成源码对比报告，规则检查不在缺乏可信结构的报告上运行。
