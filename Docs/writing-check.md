# 论文写作检查

写作检查是独立于版本差异、编译错误和翻译的辅助检查。持续预览与差异报告共用「阅读、检查、知识、语义审查、覆盖」五个面板；切换面板不重新生成论文，也不重置阅读位置。窄窗口使用横向标签栏，标签支持方向键、Home、End；证据抽屉支持 Escape 返回触发按钮。

本扩展仍在累计交付中；平台、真实论文、模型和性能的实际验收状态见[验收记录](design/writing-check/acceptance.md)，不能把合成夹具或本地假模型测试视作真实模型验收。原有功能的 macOS 验收不自动覆盖本扩展。

## 确定性检查

```sh
latex-review-check run main.tex --all
latex-review-check run main.tex --changed --format json --output checks-result.json
latex-review-check run --project /path/to/paper --all-entries
latex-review-check query --run RUN_ID --format json --evidence --limit 100 --offset 0
```

`--changed` 会比较上次可用基线；全局引用、定义次序等规则必要时回退到全量扫描。输出明确记录 `evaluation_mode`、`fallback_reason` 和比较基线，不把一次全量执行包装成真正的增量优化。无基线、输入不兼容或覆盖不足时不能据此宣称问题已修复。

有匹配配置的常驻入口时，CLI 复用其异步任务；否则使用相同论文锁和存储独立执行，不启动服务。显式 `--store /absolute/path/checks.sqlite3` 选择独立存储。已提交的远端任务遇到连接中断不会偷偷再执行一次，错误保留可查询的运行 ID。

检查 CLI 的退出码独立于旧 `latex-review`：

| 退出码 | 意义 |
| --- | --- |
| 0 | 所请求范围完成；发现规则提示默认仍为 0 |
| 1 | 配置、读写或执行失败 |
| 2 | 部分完成、覆盖不足、输入过期或取消 |
| 3 | 只有显式 `--fail-on info\|warning\|error` 才启用的规则阈值 |

多个条件同时出现时优先级为 1、2、3、0。AI 候选不参与失败阈值。查询有总数和下一页信息；默认省略完整原文证据，使用 `--evidence` 显式读取。

## 结果、证据与覆盖

每次报告另写冻结的 `checks.json` 并嵌入同一份数据；它不进入旧 `diff.json`，不增加主要变更数，也不改变旧 CLI 的退出协议。检查条目数和命中位置数不同：一个名称/标签问题可关联多个位置。

规则结果区分来源、断言类型、人工状态、当前有效性与基线差异。作者确认只表示作者判断，不表示工具证明论文正确。局部拒绝或例外必须说明理由；例外绑定证据、作用域和规则版本，相关输入改变后重新确认。

证据抽屉展示原始 LaTeX，而非渲染文本反推的位置。偏移按 Unicode 字符、左闭右开计算，保留原文件换行；文件哈希、入口与 include 实例共同校验来源。宏、条件展开、缺少依赖或不可靠映射会明确列入覆盖限制，不猜测位置。渲染不支持一个展示命令，不必然意味着原始引用扫描不可靠；对应覆盖分别报告。

「已检查范围内未发现规则问题」不是论文正确性承诺。未启用、无适用对象、不支持、缺少先决条件、失败、取消和过期都不能冒充完整通过。

## 知识与作者决定

项目知识默认使用 `.latex-review-knowledge.json`；只读项目使用外部状态目录。配置可显式指定 project 或 external；若两处都有不同知识，不会无提示合并。知识面板显示实际来源，切换后应核对再提交。修订号、内容哈希和来源前置条件防止覆盖其他窗口的修改；409 表示需要重新读取并人工核对，不要盲目重试。

可以手动创建概念、规范名、允许名、禁用名、代码标识符和作用域。使用当前文档的不可变单元选择证据，再确认定义/使用角色、符号绑定或依赖关系；不会根据首字母、自然语言或数学字母擅自建立已确认关系。较复杂的记录可通过面板中的显式 JSON 编辑保留其对象 ID。

知识操作有理由和审计历史，可撤销本地决定。导入先预览 ID/证据冲突，再显式选择；外部导入的证据需当前作者重新确认。导入的历史是来源记录，不允许通过撤销旧历史恢复外部已确认事实。

文件是唯一知识源，SQLite 是运行/候选/审计索引；不要同时手改数据库和知识文件。手工修改知识文件后先重新读取；损坏文件不会被静默覆盖，恢复操作显式使用备份。升级前按现有流程停止自己的旧服务并备份状态；新命名空间迁移不会改写旧差异或翻译数据库。回退前停止新版服务，保留新数据库与知识备份，不把未知格式强行降级。

## 语义审查：候选，不是结论

默认不联网、不在保存时调用模型、不执行论文中的指令。面板先选择任务及可精确定位的文档单元，准备本地请求并预览范围/预算；随后才可显式确认配置的模型调用。也可导出给外部模型，再手工导入返回 JSON。

```sh
latex-review-check semantic export --run RUN_ID --output request.json
latex-review-check semantic import --request request.json --response candidates.json
# 只在明确允许真实调用、且连接和预算已配置时执行：
latex-review-check run main.tex --all --semantic
```

本地保存请求权威副本；导入文件只用于选择它，不能靠修改请求文件授权新证据。候选必须逐条通过结构、任务、请求身份、原始引用和偏移验证；部分无效候选会被隔离，不因 JSON 可以解析就接受。验证仅证明引文来源正确，不证明推理、假设、矛盾或问题属实。

模型只能提供候选，不得修改源文件或确认知识。确认「潜在问题」与确认「知识关系」是两个独立动作，后者需在知识面板显式复核后提交。拒绝只抑制相同输入/证据的候选；输入改变后仍可能需要复核。取消、过期、超时或重启不会自动重试付费调用。

## 配置

添加到现有项目或外部项目配置中；命令行/项目/全局配置继续遵守既有优先级：

```toml
[checks]
enabled = true
on_save = true
debounce_ms = 800
rules = ["references", "terminology", "introductions", "symbols"]

[knowledge]
storage = "project"
path = ".latex-review-knowledge.json"

[semantic]
enabled = false
provider = "openai-compatible" # or "deepseek" for DeepSeek-specific options
thinking = "default" # default, enabled or disabled; explicit modes require deepseek
base_url = "https://your-provider.example/v1"
model = "your-model"
api_key_env = "LATEX_REVIEW_LLM_KEY"
max_requests = 20
max_input_tokens = 60000
max_output_tokens = 12000
concurrency = 2
timeout = 60
```

预算是每次运行上限，并非费用估计。`provider = "deepseek"` 时，`thinking = "enabled"` 或 `"disabled"` 会发送 DeepSeek 的 `thinking.type`；通用 `openai-compatible` 不会收到该私有字段，`thinking = "default"` 也不发送它。语义任务状态会显示生效的 provider、thinking 模式和预算。每次 provider 响应都会保留原始 `finish_reason`；截断、拒绝或异常完成仍保留可用的 `prompt_tokens`、`completion_tokens`、`total_tokens`，缺失字段明确为 `null`/`unknown`，不会因失败自动追加请求。密钥只从环境变量读取，不放在论文、报告或候选 JSON 中。真实调用前需确认服务商对论文/代码的隐私要求；HTTPS 是默认要求，仅本地测试允许 loopback HTTP。

## 离线、历史与导出

静态与历史页面只显示冻结结果；没有当前论文写权限，也不会为运行按钮隐式启动常驻入口。直接访问独立 worker 且入口不可用时，仍可阅读、筛选和查看冻结证据。

结果导出明确询问是否带原文证据及 AI 候选；知识导出是另一项显式动作。给外部模型的请求必然包含选择的原文，不应误当作脱敏统计。分享前检查内容权限；不要把私有论文或凭据提交到公共 PR。
