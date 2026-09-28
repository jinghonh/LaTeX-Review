# 公共数据契约 1.0

`src/latex_review/contract.py` 提供不可变数据类、`build_summary`、`validate_document` 与 `dumps`。`diff.json` 和独立的 `diagnostics.json` 分别按包内的 `diff.schema.json` 与 `diagnostics.schema.json` 验证。当前只定义输出契约，不生成审阅报告。

## 结构与含义

- `ReviewDocument` 包含格式版本、入口、旧新比较版本来源、两侧审阅节点、主变更、诊断和摘要。
- `ReviewNode` 保留 `raw_latex` 原文、`parent_id`、有序 `child_ids`、章节层次 `section_path` 和所属版本的原始源码位置。`normalized_latex` 与 `plain_text` 仅供比较与预览，不代替原文。
- `SourceLocation` 的 `file` 是非空的原始源文件相对路径；未知文件可填 `null`，但不得使用空字符串或绝对路径。行列号从 1 开始。未知文件或行号填 `null`，同时提供非空 `uncertainty_reason`；`confidence` 为独立的源码定位置信度。整侧不存在时，主变更中的对应位置直接为 `null`。
- `PrimaryChange` 是总数计数单位，包含旧新节点标识、旧新位置、独立的 `matching_confidence`、类别和明细。节点存在的一侧必须有 `SourceLocation` 对象，即使文件或行号未知。新增仅有新侧，删除仅有旧侧。匹配不确定仍可通过置信度表示，不得伪造源码坐标。
- `ChangeDetail` 记录所属主变更内的引用、公式等细节。`category_hits` 按包含该类别的**主变更**计数，同一主变更中的同类多个明细只命中一次；同一主变更可命中多个类别，所以各类别之和可以大于 `changes`。
- `ChangeDetail.source_old` 与 `source_new` 是可选位置；引用位置、段内公式等可在保留父主变更位置的同时标明各自的原始源码位置。缺侧字段可省略，已有 1.0 输出保持兼容。结构化内容用法见[结构化内容差异接口](structured-content.md)。
- `Summary.added_words` 与 `removed_words` 是正文词数，由后续比较器提供；注释主变更计入 `changes` 和 `comment` 类别，但不得计入这两个词数。`category_hits` 在构造时复制并冻结，避免构造后改动摘要。
- `Diagnostic` 使用固定严重级别、稳定代码、消息及可选的旧新位置。诊断另存独立文件，即使无诊断也为 `[]`。

`dumps` 使用 UTF-8 可表示的中文、字典键排序、两侧节点按 ID 排序、主变更和明细按 ID 排序、类别去重排序，并以换行结尾。`child_ids` 和 `section_path` 的顺序表示文档结构，保留原序。相同输入产生逐字节相同的输出。代表性样例见 `tests/snapshots/representative_diff.json`。

主票 #8 的比较器接口、匹配置信度与正文词数规则见 [核心结构匹配与正文差异](core-comparison.md)。

## 兼容性

格式版本目前为 `1.0`。消费者应忽略未知的可选字段；在 `1.x` 中新增可选字段保持兼容。修改既有字段的含义、计数口径、空值含义、枚举值语义或删除必需字段时，必须显式升级格式版本并提供迁移说明。模式当前严格拒绝未声明字段，用于校验本版本的生产者；消费者解析时可采取宽松策略。字段含义以本文件、模式和 `CONTEXT.md` 为准。
