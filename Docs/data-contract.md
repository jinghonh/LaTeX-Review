# 公共数据契约 1.1

`src/latex_review/contract.py` 提供不可变数据类、`build_summary`、`validate_document` 与 `dumps`。`diff.json` 和独立的 `diagnostics.json` 分别按包内的 `diff.schema.json` 与 `diagnostics.schema.json` 验证；报告层使用同一审阅文档生成页面。

## 结构与含义

- `ReviewDocument` 包含格式版本、入口、旧新比较版本来源、两侧审阅节点、主变更、诊断和摘要。
- `ReviewNode` 保留 `raw_latex` 原文、`parent_id`、有序 `child_ids`、章节层次 `section_path` 和所属版本的原始源码位置。`normalized_latex` 与 `plain_text` 仅供比较与预览，不代替原文。
- `SourceLocation` 的 `file` 是非空的原始源文件相对路径；未知文件可填 `null`，但不得使用空字符串或绝对路径。行列号从 1 开始。未知文件或行号填 `null`，同时提供非空 `uncertainty_reason`；`confidence` 为独立的源码定位置信度。整侧不存在时，主变更中的对应位置直接为 `null`。
- `PrimaryChange` 是总数计数单位，包含旧新节点标识、旧新位置、独立的 `matching_confidence`、类别和明细。节点存在的一侧必须有 `SourceLocation` 对象，即使文件或行号未知。新增仅有新侧，删除仅有旧侧。匹配不确定仍可通过置信度表示，不得伪造源码坐标。
- `ChangeDetail` 记录所属主变更内的引用、公式等细节。`category_hits` 按包含该类别的**主变更**计数，同一主变更中的同类多个明细只命中一次；同一主变更可命中多个类别，所以各类别之和可以大于 `changes`。
- `ChangeDetail.source_old` 与 `source_new` 是可选位置；引用位置、段内公式等可在保留父主变更位置的同时标明各自的原始源码位置。缺侧字段可省略，已有 1.0 输出保持兼容。表格、移动与公式明细见[高级差异](advanced-diff.md)。
- 正文句子明细可选用 `old_sentences`、`new_sentences` 记录所属段落中从 1 开始的句子序号。修改可两侧都有，新增和删除只在存在的一侧给出；拆句与合句可包含多个序号。这些序号用于页面内句子定位，不是原始源码行号，也不增加主变更数。
- 1.1 增加 `move` 类别。`moved` 主变更携带双侧节点、章节路径与源码位置；移动后的局部改动仍是同一主变更内的明细。表格明细可选用从 1 开始的 `row_old`、`column_old`、`row_new`、`column_new` 标记可确定的行或列；未提供的坐标不代表第零行或第零列。
- `Summary.added_words` 与 `removed_words` 是正文词数，由后续比较器提供；注释主变更计入 `changes` 和 `comment` 类别，但不得计入这两个词数。`category_hits` 在构造时复制并冻结，避免构造后改动摘要。
- `Diagnostic` 使用固定严重级别、稳定代码、消息及可选的旧新位置。诊断另存独立文件，即使无诊断也为 `[]`。
- 规则诊断在 1.1 契约中增加可选的 `rule_status`、`evidence`、`related_sources_old`、`related_sources_new`；非规则诊断维持既有序列化。详见[论文规则诊断](paper-rules.md)。

`dumps` 使用 UTF-8 可表示的中文、字典键排序、两侧节点按 ID 排序、主变更和明细按 ID 排序、类别去重排序，并以换行结尾。`child_ids` 和 `section_path` 的顺序表示文档结构，保留原序。相同输入产生逐字节相同的输出。代表性样例见 `tests/snapshots/representative_diff.json`。

## 独立写作检查契约

写作检查不扩充 `ReviewDocument`、`diff.json` 或主要变更计数。冻结的 `checks.json` 使用包内独立 `checks.schema.json`（版本 `1.0`），知识文件、语义请求和语义响应分别使用 `knowledge.schema.json`、`semantic-request.schema.json` 与 `semantic-response.schema.json`。操作、配置、导出权限及验收限制见[写作检查](writing-check.md)。

- 输入由论文入口、原始字节文件哈希、展开实例图、解析器版本和检查相关配置绑定；差异/编译/翻译等无关显示选项不应令相同检查输入分裂。知识修订与内容哈希另参与检查输入身份。
- `SourceEvidence` 的 `start`/`end` 是原始文件解码后的 Unicode 字符偏移，采用左闭右开范围，CRLF 保持原样；同时携带原文件哈希、原文 `quote`、入口、include 实例和行列，不得从渲染文本逆推。
- 结果分开表达 `origin`、`assessment`、`review_state`、`lifecycle`、`delta` 与 `coverage`；AI 候选不等于确定性规则发现，不增加主要变更，也不计入确定性失败阈值。完整范围内零发现与部分/不支持/失败/过期状态不能混同。
- 知识 JSON 是权威源，SQLite 仅保存运行、历史、队列与索引。写入必须满足来源、修订和内容哈希前置条件；当前页面与原始证据也必须匹配当前输入，静态/历史页面没有活动写权限。
- 浏览器通过既有受管入口转发检查操作；共享接口为 `/api/v1/papers/{paper_id}/checks/…`，worker 使用 `/api/checks/…` 桥接，并继续检查 Host、Origin、凭据、论文、页面版本和初始嵌入的检查内容身份。异步启动返回可查询的任务标识，失败不会改变旧 CLI 协议或偷偷重试付费调用。
- 页面另嵌入与同一内容身份绑定的 `preview-diagnostics-data`，仅作展示适配。只有生产者已知且规则、目标、可靠位置和当前内容相符的预览诊断才合并到同一检查条目，保留双方来源；其余诊断独立展示，不伪造精确证据或作者决定能力。此适配不修改两个 JSON 契约、检查失败阈值或主要变更数。
- `POST …/checks/findings/{finding_key}/annotation` 是显式转入既有批注的操作，要求当前 `input_id`、`evidence_fingerprint` 和页面版本。worker 覆盖内部批注目录参数，服务使用可信发布清单和原有批注存储创建整单元锚点；不把源码偏移当作渲染坐标。相同论文、问题、规则版本及精确证据复用持久关联，已删除/解除关联的批注不自动重建。转换不更改问题生命周期、确认事实或正文；仅有效复检才能判断问题是否解决。

## 兼容性

当前生产者输出 `1.1`，验证器仍接受历史 `1.0`。消费者读取 `1.1` 时应把 `move` 视为新增类别，并将 `moved` 的双侧位置用于跳转；如只认识 `1.0`，可忽略未知可选坐标并把未知类别当作未分类，但不能把移动计成删除与新增两项。`1.0` 文档不得使用 `move` 类别或表格坐标；验证器按版本检查。主变更计数、词数与已有字段含义保持不变。消费者应忽略未知的可选字段；修改既有字段的含义、计数口径、空值含义、枚举值语义或删除必需字段时，必须显式升级格式版本并提供迁移说明。模式严格拒绝未声明字段，用于校验本版本生产者；消费者解析时可采取宽松策略。字段含义以本文件、模式和 `CONTEXT.md` 为准。
