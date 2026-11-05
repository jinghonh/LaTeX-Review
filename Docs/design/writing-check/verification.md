# 写作检查技术证据台账

## 范围与结论边界

本台账逐项核对[规格 AC-01–AC-24](<spec.md#L433-L456>)与**实际测试断言**，不以测试函数标题代替证明，也不将测试通过等同于作者验收。界面截图与人工阅读比较另见[验收记录](<acceptance.md>)，真实模型评估前置条件见[语义评估协议](<semantic-evaluation.md>)。本轮只补测试和文档，不改生产实现。

- 上一轮完整写作选择的生产代码基线：`44085641b20d99b86d56fcd0a9f6a0ccfb5a660d`，包含 entry proxy 的冻结内容标识转发修复。其后生产代码因 AC-03/24 实现与旧界面回归修复而解冻；下述 253 用例结果不能自动作为后续新源码提交的通过证据，最终新提交与整套复跑仍待固定、收集。
- AC-03/09/19/24 的新增专项结果由实施代理及父代理报告，本台账已逐条阅读当前测试断言后补记；未把“代理说功能完成”本身当作断言覆盖。AC-11/18 的 3 个异步专项由台账维护者直接执行。本台账维护者只修改测试和本文，没有修改生产代码。
- 执行环境：Linux `6.8.0-138-generic` / x86_64 / glibc 2.39，Python 3.11.17，2 个逻辑 CPU。浏览器证据为 Linux headless Chromium，不代表 macOS CI。
- **自动化支撑**：该行主要技术边界有已检查的断言；仍限于合成输入，不表示真实论文、人类判断或外部模型已获验收。
- **部分支撑**：有相关断言，但该 AC 的复合场景尚有明确缺口，列于最后一列。不得据此宣称完整验收。
- **A** = Python 合成/临时工程、真实解析器/规则/存储；**B** = Chromium 执行生产 JS，但 API 被固定响应拦截；**C** = Chromium → 实际本地 entry proxy → worker → coordinator；**D** = Chromium → 真实 HTTP worker → 测试桥直接 dispatch 真实 coordinator（替换 entry RPC，不是假结果）；**O** = 独立脚本留存的观测。A/B/C/D 的论文内容均为合成数据。

## 可重复测试与选择范围

```bash
.venv/bin/python -m pytest tests/test_check_*.py tests/test_writing_fixture_baseline.py tests/test_rule_mask_scanning.py -q
```

| 执行 | 实际结果 | 选择边界 |
| --- | --- | --- |
| 首次技术审计选择 | **250 passed，63.39s** | 当时已有的 `test_check_*.py` 加固定 fixture/masking 两个模块；包含真实 entry proxy 回归，不包含之后新增的分页模块和下述两个 AC 补测。 |
| AC-11/12 补测后的规则模块 | **13 passed，1.13s** | [当前规则测试](<../../../tests/test_check_rules.py>)，含两个新增断言链；不是另一轮完整套件，也不把与首轮重叠的 11 个用例重复计为新增覆盖。 |
| 上一基线的写作选择复跑 | **253 passed，70.23s** | 使用上方同一命令，19 个模块；包含 [500 条分页测试](<../../../tests/test_check_workspace_pagination.py>)、[真实入口代理回归](<../../../tests/test_check_entry_proxy.py>)与 AC-11/12 规则补测。 |
| AC-11/18 异步专项 | **3 passed，1.51s** | `.venv/bin/python -m pytest tests/test_check_acceptance_edges.py -q`；[新专项模块](<../../../tests/test_check_acceptance_edges.py>)中概念定义删除 1 例、两个入口违规方向各 1 例；未跑浏览器/provider，也不是新源码完整回归。 |
| AC-03 新实现相关联跑（实施代理报告） | **28 passed，33.63s** | [诊断发布/适配](<../../../tests/test_check_diagnostics.py>)2 例、[浏览器合并与 7 种拒绝误合并](<../../../tests/test_check_diagnostics_browser.py>)8 例已包含于这次相关联跑；此 28 是联跑总数，非 28 个新增诊断用例，也不是完整写作选择。 |
| AC-24 新实现相关联跑（实施代理报告） | **57 passed，约 9s** | [新批注转换模块](<../../../tests/test_check_annotation.py>)6 例 + [服务模块](<../../../tests/test_check_service.py>)21 例 + [原批注模块](<../../../tests/test_annotations.py>)30 例。独立 reviewer 对同 6 个转换用例另报 **6 passed，4.34s**、无问题；额外 Unicode/重复 include 人工技术探测不计入这 6 例或正式性能样本。 |
| AC-09/19 兼容专项（实施代理报告） | **3 passed，4.60s** | [兼容模块](<../../../tests/test_check_compatibility_edges.py>)1 个真实跨文件不连续段落测试 + 2 个冻结旧版本报告浏览器测试；与[分页模块](<../../../tests/test_check_workspace_pagination.py>)联跑为 **4 passed，7.84s**，前 3 例已包含在后 4 例中，不重复相加。 |
| 最新固定源码的完整写作选择 / 全仓库复跑 | **待收集** | 新实现专项通过不能替代旧界面回归修复后的最终复跑；也不能抹去下述已观察到的 M0 失败。 |

pytest 数量为参数化展开后的用例数，不是测试函数数。不同选择存在重叠，不将上表各行通过数相加为唯一用例数；历史写作选择与后续专项也不是同一冻结源码的最终验收。父代理全仓库结果单列如下，不将其已通过的旧用例挪用为本台账未测场景的证据。

### 已观察到的 M0 回归：不能视为已通过

父代理报告在上一生产基线、最新修复前运行全仓库测试：**14 failed，1098 passed，19 skipped，约 1049s**。已报告的失败分类为 12 个导航覆盖层/URL history/滚动功能回归，1 个 reading resize 失败（是否同根因待验证），以及 1 个 translation port 测试失败（基线归因尚不能作为豁免理由）。这不是全绿，也不能把 14 项笼统写成“已知基线问题”。

当前正在修复静态导航、初始滚动及 URL history 路径；在固定新源码后，需重跑对应失败用例及完整选择，并保留剩余失败/跳过原因。**修复进行中不等于已验证修复；专项新功能通过不等于 M0 无回归。** 最新固定提交、全套输出及这些失败的最终状态仍待父代理收集。

## AC 逐项证据

| AC | 技术状态 / 层级 | 实际断言与可核对来源 | 尚未证明的边界 |
| --- | --- | --- | --- |
| AC-01 离线全文检查、定位、人工知识 | 自动化支撑 · A/B/C | [离线 CLI](<../../../tests/test_check_cli.py#L50-L58>)在本地服务不可用时仍返回 completed；[多文件 fixture](<../../../tests/test_check_rules.py#L105-L116>)产生当前引用/文献检查；[人工概念流程](<../../../tests/test_check_knowledge_browser.py#L60-L82>)提交作者知识、条件版本且不发 AI 调用；[真实入口流程](<../../../tests/test_check_entry_proxy.py#L12-L57>)点击检查获 202 并完成，源文件不变。 | 这些证据分别覆盖离线核心、多文件和面板，不是同一份真实论文的完整人工验收；未以“本机恰好装有 TeX”推导编译依赖，测试路径本身不要求编译。 |
| AC-02 引用/文献缺失、重复标签、不可靠文献源 | 自动化支撑 · A | [规则测试](<../../../tests/test_check_rules.py#L23-L36>)核对 unresolved reference、missing bibliography key、duplicate label、精确引文及分组；[不完整源门控](<../../../tests/test_check_rules.py#L90-L103>)保留重复标签的肯定证据，却不输出无法证明的缺失键；畸形文献记录进入 coverage blind spots。 | 合成异常源，不等于任意 BibTeX 方言均受支持。 |
| AC-03 预览诊断与规则统一、主变更数不膨胀 | 自动化支撑 · A/B | [真实 live/diff 发布](<../../../tests/test_check_diagnostics.py#L28-L55>)分别保存 preview diagnostics 与冻结 checks，主变更数不变；[浏览器合并](<../../../tests/test_check_diagnostics_browser.py#L46-L103>)使用实际发布的 unresolved-reference 规则/预览诊断，同一问题只计一项，抽屉保留两种来源，独立 preview-only 行只读且 XSS 不执行，源文件及 frozen checks 不变；[7 种反例](<../../../tests/test_check_diagnostics_browser.py#L106-L133>)分别改变 code/target/file/location/producer/reliability/content，均不误合并。 | 浏览器消费的是实际发布数据，但 API 被拦截（B），不是新的 C 层端到端服务测试。相关旧 M0 导航/滚动回归尚需修复后最终复跑，不能由本行专项通过豁免。 |
| AC-04 禁用术语上下文、精确位置、无父子重复 | 自动化支撑 · A | [术语上下文测试](<../../../tests/test_check_knowledge_rules.py#L28-L48>)核对正文/图注命中并排除注释、verbatim、URL/键、数学与默认 texttt；[嵌套图环境](<../../../tests/test_check_knowledge_rules.py#L202-L211>)只产生一条、两个位置，并拒绝不匹配 include instance；[正文范围](<../../../tests/test_check_knowledge_rules.py#L214-L221>)包括图注而不包括摘要。 | 不宣称支持任意未知宏内部的正文；遇到未解释宏明确降级。 |
| AC-05 代码 ID 与合法实现说明 | 自动化支撑 · A | [代码 ID / 例外测试](<../../../tests/test_check_knowledge_rules.py#L51-L70>)先验证只有 mapping 不违规，再显式启用 body/texttt 上下文产生两处命中，登记例外后为 waived 且 lifecycle 仍 current。 | 例外依赖作者登记，不自动判断代码名是否“应当”允许。 |
| AC-06 正式使用、预告与公式后的解释 | 自动化支撑 · A | [角色/顺序测试](<../../../tests/test_check_knowledge_rules.py#L86-L105>)用实际 live 阅读顺序而非提交的 reading_order，preview 不违规；[同一 introduction group](<../../../tests/test_check_knowledge_rules.py#L108-L114>)允许相连的使用/解释，不按字符先后盲报。 | 合成角色由作者确认；不是模型自动发现所有定义的质量证明。 |
| AC-07 摘要/正文缩写、前导区声明 | 自动化支撑 · A | [缩写域测试](<../../../tests/test_check_knowledge_rules.py#L73-L83>)正文 SM 不被摘要展开或前导区声明冒充已介绍；[登记域限制](<../../../tests/test_check_knowledge_rules.py#L238-L243>)不把域外摘要用法错误算入正文规则。 | 当前仅对已登记缩写和可解释文本给出结论。 |
| AC-08 局部符号、明确冲突与不猜类型 | 自动化支撑 · A | [符号绑定测试](<../../../tests/test_check_knowledge_rules.py#L148-L166>)同域显式 incompatible_with 才报；shadows 与不相交 proof scope 均不报；去掉显式不兼容后，仅有 vector/scalar 自由文本不产生冲突。 | 未做数学类型推导，不把自然语言 meaning 字段当类型系统。 |
| AC-09 Unicode/CRLF/跨文件/重复 include 定位 | 自动化支撑 · A/B | [快照位置测试](<../../../tests/test_check_snapshot.py#L115-L131>)校验 Unicode/CRLF、字节 SHA256 与重复 include 实例；新增[真实跨文件段落](<../../../tests/test_check_compatibility_edges.py#L23-L70>)构造 main→part→main 同段落，聚合 source 无文件/行号、confidence<1 且有 uncertainty/low_confidence_source；三个 evidence fragments 分别精确映射原文、Unicode 半开区间与字节 hash，unit 保留三个片段，不伪造单一精确跨度。 | 专项证明该不连续形态明确降级，不意味着所有复杂宏展开都能得到 exact 来源；无法解释的宏仍须维持覆盖不足。 |
| AC-10 未知宏/解析失败不能推断不存在定义 | 自动化支撑 · A | [未知宏](<../../../tests/test_check_knowledge_rules.py#L169-L176>)不猜宏参数是正文，coverage 为 unsupported/incomplete；[不可靠源](<../../../tests/test_check_rules.py#L90-L103>)禁止缺失结论；[过期角色](<../../../tests/test_check_knowledge_rules.py#L117-L129>)不作为事实。 | 没有把 parser blind spot 当作规则已完整执行；任意真实 TeX 宏语义仍不在承诺范围。 |
| AC-11 跨文件删除/改名影响不变使用处 | 自动化支撑 · A | [跨文件标签改名](<../../../tests/test_check_rules.py#L39-L55>)验证全文 fallback；新增[已登记概念定义删除异步专项](<../../../tests/test_check_acceptance_edges.py#L77-L123>)经真实 coordinator 注册 author_confirmed 概念/正式使用/定义，先产生当前顺序问题，再只删除另一个 include 的定义文本。changed 请求完整执行 full fallback，usage 字节/hash 不变且 applicable_count 仍为 1；旧 finding 变 unknown，coverage 明示 stale_occurrence_evidence 与 confirmed_definition_in_scope_missing，非 missing include/parser blind spot。 | 覆盖实际队列、存储及完整重检；不把过期作者证据升级成“确定缺定义”。UI 保存通知/呈现延迟及真实论文仍不是此 A 层专项的证明范围。 |
| AC-12 新知识名称未在索引出现 | 自动化支撑 · A | 新增[知识增量测试](<../../../tests/test_check_rules.py#L58-L75>)：同一冻结 document、未改 include 文件，空知识无命中；登记此前未注册 AliasToken 后 full 扫描命中该文件，knowledge_hash_changed、delta not_comparable。 | 证明当前保守全文回退路径，不宣称已实现/测量增量依赖索引。 |
| AC-13 编造 ID/引文/位置/额外字段/歧义 | 自动化支撑 · A | [逐候选校验](<../../../tests/test_check_semantic.py#L90-L122>)分别断言 candidate_schema/evidence_id/quote_missing/quote_range/object_id/quote_ambiguous，合法候选与非法候选分离；[请求边界](<../../../tests/test_check_semantic.py#L55-L67>)公开导出不能冒充本地主机权威；[import 持久化](<../../../tests/test_check_semantic.py#L462-L479>)拒绝伪本地请求。 | 通过引用验证只证明来源，不证明语义推断为真；无效字段没有获得知识事实的写入权限。 |
| AC-14 有来源但错误推断仍候选，拒绝抑制 | **部分支撑** · A/B | [候选态](<../../../tests/test_check_semantic.py#L70-L81>)合法精确引文仍 assessment=candidate；[拒绝持久化](<../../../tests/test_check_semantic.py#L482-L505>)重复请求幂等，换一套措辞、同输入同证据的建议保持 rejected；[关系预填](<../../../tests/test_check_semantic_browser.py#L56-L79>)只发预填事件、不自动确认/写知识。 | 自动化验证状态边界和拒绝抑制；**未由真实模型产生错误推断并交给真实作者判断正确率/可接受性**。 |
| AC-15 AI 途中变更/取消不得成为 current | 自动化支撑 · A | [版本域矩阵](<../../../tests/test_check_semantic.py#L125-L131>)逐一改变 content/paper/entry/knowledge/rules/config/parser/engine/scope 得 stale；[晚到结果](<../../../tests/test_check_semantic.py#L325-L345>)cancel/change 分别 cancelled/stale，缓存为空；[检查取消/替换](<../../../tests/test_check_service.py#L58-L98>)旧任务不能成为 current。 | Provider 为受控 transport；不等于真实远程连接已验证中断回收。 |
| AC-16 论文提示词注入无工具权限 | 自动化支撑 · A/B | [注入文本测试](<../../../tests/test_check_semantic.py#L177-L182>)把执行指令当普通证据；[假 provider 请求体](<../../../tests/test_check_semantic.py#L251-L270>)无 tools/functions，凭据不出现在执行结果；[浏览器测试](<../../../tests/test_check_workspace_browser.py#L92-L99>)包含恶意片段却未执行 window.pwned，[JS 合约](<../../../tests/test_check_workspace.py#L68-L74>)无 innerHTML/eval。 | 这里验证能力隔离和请求形状；不是外部提供商提示词注入红队评测，也不声称文本提示本身能保证模型服从。 |
| AC-17 两窗口/手改 JSON 冲突 | 自动化支撑 · A/B | [同 revision 手改](<../../../tests/test_check_registry.py#L79-L87>)依 content hash 拒绝覆盖；[跨进程竞争](<../../../tests/test_check_registry.py#L110-L128>)恰一成功一冲突、保留赢家；[浏览器冲突](<../../../tests/test_check_knowledge_browser.py#L111-L119>)保留未保存表单、只 POST 一次不自动重试。 | 进程测试是真实锁竞争；GUI 用固定 409 响应，不冒称两真实浏览器窗口的完整竞态录屏。 |
| AC-18 多入口共享名称但隔离定义、只读安全 | 自动化支撑 · A | 新增[同名双定义异步专项](<../../../tests/test_check_acceptance_edges.py#L126-L176>)在同根共享 registry 中注册一个 Shared Filter 概念、两份意义不同的 confirmed definition 与各自 entry/body scope；分别以 main 或 supplement 为违规入口运行，两份实际 knowledge reports 各只计 1 个正式使用、coverage 完整，只有违规入口出现顺序 finding，证据仅来自其本入口使用/定义文件，另一份无串入 finding，源文件不变。[只读存储](<../../../tests/test_check_registry.py#L131-L140>)另实际 chmod 验证外部写入与源文件不变；[共享根服务测试](<../../../tests/test_check_service.py#L245-L331>)覆盖各入口配置验证。 | 新专项是实际 coordinator/规则/知识/队列，不是仅校验 paper_id 或表键；仍非两个真实浏览器窗口或真实论文作者验收。 |
| AC-19 静态/历史/旧报告可读、不写入/不调用 | 自动化支撑 · A/B | [旧报告 fixture](<../../../tests/test_check_compatibility_edges.py#L73-L124>)离线归档冻结 `fe82620af8bbbb028809d59ceeaaad7085427880` 的真实包，用独立子进程生成原版无 checks/workspace 的报告；[浏览器两种客户端](<../../../tests/test_check_compatibility_edges.py#L127-L192>)分别验证原资产和注入当前资产仍可阅读，API 真正返回 404、检查按钮禁用、知识/语义无写表单，无 POST/AI 请求且报告文件 hash 不变；[历史 findings](<../../../tests/test_check_service.py#L434-L456>)拒绝 resolved/stale/unknown 的作者操作。 | 该固定历史版本可重现；没有穷举每一种历史格式。若本地缺冻结 Git object，该专项明确 skip，不联网补齐，也不得把 skip 宣称为已通过；本轮代理报告是 3 passed、非 skip。静态旧导航的 M0 行为仍以最终回归结果为准。 |
| AC-20 无 Git/无基线/未跟踪依赖 | 自动化支撑 · A | [当前规则测试](<../../../tests/test_check_rules.py#L23-L36>)在无 Git 临时工程中检查可运行、首轮 delta not_comparable；[依赖新出现](<../../../tests/test_check_snapshot.py#L184-L191>)未存在 include 新建后改变 content_id 并纳入 files；[服务失效](<../../../tests/test_check_service.py#L101-L109>)依赖出现使 current stale。 | 未将“没有基线”解释为全文全部新增。 |
| AC-21 确定性与规则/知识变化区别 | 自动化支撑 · A | [确定性规则](<../../../tests/test_check_rules.py#L23-L36>)完全相同报告；[配置变化](<../../../tests/test_check_rules.py#L78-L87>)not_comparable 并记录 configuration_hash_changed；[知识变化](<../../../tests/test_check_rules.py#L58-L75>)不改文件却能触发规则命中，明确 knowledge_hash_changed，不作为正文修复；[live/diff 同身份](<../../../tests/test_check_snapshot.py#L95-L112>)排除无关渲染/编译配置。 | 不以跨软件版本的字节完全一致代替版本指纹比较；未测真实模型输出确定性。 |
| AC-22 阅读位置/抽屉/窄屏/键盘/非颜色 | **部分支撑** · B/O | [异步刷新后 Escape](<../../../tests/test_check_workspace_browser.py#L69-L99>)关闭抽屉并恢复到新列表行焦点，原文/冻结 JSON 不变；[键盘合约](<../../../tests/test_check_workspace.py#L68-L79>)包含 Escape/方向键/阅读位置代码路径；[500 条测试](<../../../tests/test_check_workspace_pagination.py#L123-L183>)翻页/筛选准确且切面板不换原文 DOM、不重解析报告、不请求 API；窄屏/滚动/截图实际观测见[界面验收](<acceptance.md>)。 | 静态源码断言不是键盘可用性证明；全链路辅助技术、跨浏览器、不同真实论文阅读位置恢复尚未人工验收。截图不能代替屏幕阅读器/完整键盘导航测试。 |
| AC-23 降级解析消失只能 unknown/stale | 自动化支撑 · A | [解析不完整后的比较](<../../../tests/test_check_rules.py#L78-L87>)原问题消失但 missing include 后 lifecycle=unknown，而非 resolved；[范围锚点消失](<../../../tests/test_check_knowledge_rules.py#L224-L235>)coverage incomplete，记录 scope_anchor_unavailable / stale_concept_knowledge；[无效角色](<../../../tests/test_check_knowledge_rules.py#L117-L129>)不冒充事实。 | 不完整不等于“已修好”；UI 的“没有展示 finding”不能独立作为 resolved 证据。 |
| AC-24 审阅/转批注不是解决 | 自动化支撑 · A/D | [作者决定](<../../../tests/test_check_service.py#L383-L431>)不改 finding 的 origin/lifecycle；新增[真实转换持久化](<../../../tests/test_check_annotation.py#L50-L94>)在重载/重启/知识重检后复用一个批注、保留 current/unreviewed、源文件不变，只有实际修正文后完整重检才 resolved；[伪造/过期拒绝](<../../../tests/test_check_annotation.py#L97-L110>)不创建批注；[重复 include 锚定](<../../../tests/test_check_annotation.py#L132-L152>)保留实例；[浏览器转换](<../../../tests/test_check_annotation.py#L155-L195>)经真实 HTTP worker 与测试 dispatch 桥，两次显式转换只生成一个线程，worker 覆盖浏览器伪造输出目录，finding 仍 current。 | D 层替换了 entry RPC，不能称为独立真实 entry proxy 的全链路；已有 C 层运行路径另行验证。转批注不等于修复或作者接受语义判断，不把 reviewed/waived/converted 混同 resolved。 |

汇总：**24 行均有已检查的相关证据，其中 22 行记为自动化支撑、2 行记为部分支撑**（AC-14/22）。AC-03/09/19/24 已补入通过的专项及直接断言；但完整新源码尚未冻结复跑，M0 全套存在已观察到、正在修复的功能失败。**这不是“24/24 已验收”，也不是“全仓库无回归”**；真实模型/论文/作者判断、完整窄屏键盘/辅助技术及最终兼容回归仍须分别验收。

## 性能与规模：已经测到什么

[Linux 合成性能记录](<evidence/linux-performance.json>)与[可重复脚本](<evidence/benchmark_checks.py#L45-L76>)的测量边界是：保存改动后同步 `capture_snapshot → build_check_document → evaluate_current`，**不含文件监听 debounce、队列等待、HTTP、页面发布、浏览器渲染或模型网络**。

| 数据 / 观测 | 实际值 | 可得结论 |
| --- | --- | --- |
| 同步合成规模 | 1,479,521 字节、40 文件、每轮 5,000 units | 处于规格 ≤2MiB/40文件/5000units 的参考上限内；不是所有真实宏/文献/知识规模的最坏情形。 |
| 首次同步 pipeline | 0.7653s，1 次 | 该 Linux 合成样本低于 10s；“cold”是脚本首轮，非清空操作系统缓存后的系统冷启动。 |
| 已保存修改后同步 pipeline | 10 次，median 0.8965s，nearest-rank p95 1.3156s | 样本内部同步耗时低于 2s；**不能据此宣称端到端 save→queue→页面结果 p95 达标**。10 次仅是小样本。 |
| 500 条 UI 规模 | 10 页 × 50，无重复/漏项；组合筛选 63 条、末页 13；confirmed 筛选 100 条 | [分页断言](<../../../tests/test_check_workspace_pagination.py#L123-L183>)与[观测数据](<evidence/pagination-500.json>)证明合成冻结列表的分页/筛选和切换面板不重复解析/请求；这是 B 层，不是 500 个真实独立语义问题。 |
| 实际浏览器服务路径 | [真实 entry proxy 回归](<../../../tests/test_check_entry_proxy.py#L12-L57>)发送冻结 content/page headers，缺失 content header 得 409 且未替换 current run | C 层验证具体安全转发和任务完成，不是保存热路径吞吐或 p95 基准。 |

## 尚未完成的验收与精确补测项

1. **真实论文与作者判断**：未获得/执行私有真实论文人工验收；没有误报率、漏报率、作者确认率或审查耗时的可信结果。合成 fixture 与截图不得充当真实论文认可。
2. **实际 provider/model**：假 transport 验证了显式授权、预算预检、重试上限、候选状态和过期结果，但没有真实模型语义质量、实际计费/限流行为；[固定案例协议](<semantic-evaluation.md>)仍待授权执行。引文有效率不等于语义判断正确率。
3. **macOS**：本轮 Linux；macOS CI/本地兼容执行仍待完成，不能用跨平台代码设计代替结果。
4. **保存端到端延迟**：需在固定机器/论文负载记录实际保存→debounce→入队→执行→当前报告→浏览器可见结果的多次样本及 p95。现有同步基准与一次 C 层检查完成不补足该项。
5. **剩余 AC 边界与 M0 回归**：AC-14 的真实模型推断/作者质量判断，AC-22 的完整窄屏阅读位置、键盘及辅助技术验收仍未完成。AC-03/09/11/18/19/24 的具体自动化缺口已有上表专项断言，不再笼统列为未测；但旧导航/URL history/滚动/reading resize 等 M0 失败必须在修复后的固定新提交上重跑，translation port 失败也须明确最终结果和归因，不能仅凭相邻新功能通过或“疑似基线”销项。

本台账没有请求新硬件、真实凭据或外部模型调用，也没有替换独立维护的[界面验收记录](<acceptance.md>)。
