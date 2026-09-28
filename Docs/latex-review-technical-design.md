# LaTeX Review：面向 Codex 的无编译三栏预览与结构化差异报告系统设计

**Technical Design Specification · Version 1.0**

日期：2026-09-29

> 目标：在不依赖 LaTeX PDF 编译的日常审阅路径中，自动生成“修改前 / 修改后 / 修改对比”同页 HTML 报告，并同时输出机器可读差异数据。

# 目录

本文档按“需求 → 架构 → 数据模型 → 渲染 → 差异 → UI → Git/Codex 集成 → 测试与路线图”的顺序组织，可直接作为实现规格与任务拆分依据。

| 章节 | 主题 |
| --- | --- |
| 0 | 执行摘要 |
| 1 | 背景、问题与设计目标 |
| 2 | 总体架构 |
| 3 | 端到端处理流程 |
| 4 | 中间表示（IR）与稳定节点标识 |
| 5 | LaTeX 项目预处理与 Source Map |
| 6 | HTML 预览渲染层 |
| 7 | 差异引擎设计 |
| 8 | report.html：三栏审阅界面 |
| 9 | diff.json：面向 Codex 的机器可读接口 |
| 10 | Git 版本解析与工作区集成 |
| 11 | Codex / Agent 集成设计 |
| 12 | CLI、配置与目录约定 |
| 13 | 推荐代码结构 |
| 14 | 容错、降级与诊断 |
| 15 | 性能、缓存与安全 |
| 16 | 测试策略 |
| 17 | V1 / V1.5 / V2 路线图 |
| 18 | V1 验收标准 |
| 19 | 推荐实施顺序 |
| 20 | 主要风险与应对 |
| 21 | 最终技术决策清单 |
| 附录 A–C | 变更文案、README 示例、Definition of Done |

---

# 0. 执行摘要

本设计提出一个面向 Codex/AI 高频修改 LaTeX 文档的审阅工具，暂定名为 LaTeX Review。其核心不是替代 LaTeX 编译器，而是在“每次改稿后立即检查”这一高频场景中，用结构化解析和浏览器渲染提供低成本、可定位、可交互的修改审阅。

系统每次运行接收一个“修改前版本”和一个“修改后版本”，解析多文件 LaTeX 项目，构建带源码位置的中间表示（IR），分别渲染为 HTML，再对 IR 执行结构级与行内级差异分析，最终生成单一 report.html。报告在同一个页面中并排展示 Before、After、Changes 三个区域，同时生成 diff.json 供 Codex 或其他自动化工具消费。

> **核心判断：** plasTeX 可以作为 LaTeX 解析/HTML 渲染后端，但不能独立承担完整方案。真正的系统还必须具备多文件展开、稳定节点标识、Source Map、结构化 diff、公式/表格/引用专用比较、Git 版本解析、三栏交互 UI 和机器可读差异输出。

| 输出 | 面向对象 | 用途 |
| --- | --- | --- |
| report.html | 人类作者 / 审阅者 | 快速阅读修改前、修改后与语义差异；支持同步定位与筛选。 |
| diff.json | Codex / CI / 自动化 | 让 AI 精确知道修改了哪些结构、对应哪些源文件与行范围。 |
| diagnostics.json（可选） | 开发/CI | 记录解析降级、未知宏、资源缺失、Source Map 置信度等。 |

## 0.1 V1 的一句话定义

V1 = Git HEAD 与工作区之间的多文件 LaTeX 快速审阅器：不默认运行 pdfLaTeX/XeLaTeX/LuaLaTeX，使用 plasTeX + 浏览器 MathJax 渲染正文与数学，用结构化 IR 比较 paragraph/equation/figure/table/citation 等节点，并输出三栏 report.html 与 diff.json。

## 0.2 V1 明确不做的事情

- 不承诺像最终 PDF 一样 100% 复现版式；最终投稿前仍必须运行真实 LaTeX 编译。
- 不尝试完整解释任意 TeX 宏编程；无法解析时采用可见降级，而不是静默丢失内容。
- 不把 latexdiff 生成的 \DIFadd/\DIFdel 文档作为主数据流；latexdiff 只作为可选兼容/交叉验证工具。
- V1 不实现复杂语义移动检测、逐单元格公式 AST 等高级能力，先保证稳定、可定位、可扩展。

# 1. 背景、问题与设计目标

## 1.1 目标场景

在 AI 辅助论文写作中，Codex 可能在一次任务里同时修改多个 .tex 文件、公式、表格、图注和引用。传统工作流通常有两个极端：一是只看 Git/VS Code 的源码 diff，信息准确但阅读成本高；二是每次完整编译 PDF，再人工比较视觉结果，反馈慢且难以定位结构差异。LaTeX Review 填补中间层：快速生成“足够像论文”的 HTML 阅读视图，同时给出结构化修改说明。

## 1.2 主要用户故事

1. Codex 修改论文后自动运行 latex-review main.tex，作者打开 report.html 即可浏览三栏结果。
2. 作者点击 Changes 中某个修改项，Before/After 两栏同步滚动到对应段落或公式。
3. Codex 读取 diff.json，检查自己是否只改了目标章节、是否误改了其他公式/引用。
4. CI 在 PR 中运行工具，输出静态 HTML 审阅产物和结构化变更摘要。
5. 需要最终版式验证时，再显式使用 --compile 调用 latexmk；日常审阅不默认触发真实编译。

## 1.3 设计目标

| 目标 | 要求 |
| --- | --- |
| 快速 | 典型中型论文在缓存命中后应达到秒级；不以真实 PDF 编译为默认前置条件。 |
| 人类可读 | Before/After 是连续文档视图，Changes 是语义化变更，而非 HTML 标签或 TeX 噪声。 |
| 机器可读 | 所有差异都可序列化为稳定 JSON，包含源文件、行范围、节点类型和置信度。 |
| 可定位 | 任一变更尽可能映射回原始 .tex 文件而不是 flattened.tex。 |
| 多文件友好 | 原生处理 \input/\include 与常见论文目录结构。 |
| 降级可见 | 遇到未知宏/环境、资源缺失、解析失败时必须显式标记。 |
| 可扩展 | 后续可加入更强的 LaTeX AST、BibTeX 解析、公式 AST、视觉 diff 或 PDF 编译。 |

## 1.4 非目标

- 不是 WYSIWYG LaTeX 编辑器。
- 不是 TeX 引擎替代品。
- 不是通用网页 diff 工具。
- 不是仅对单个 main.tex 的简单文本比较器。
- 不是依赖浏览器截图做像素级差异的视觉回归系统。

# 2. 总体架构

```text
            ┌────────────────────┐          ┌────────────────────┐
            │  OLD revision      │          │  NEW revision      │
            │  Git/dir/file      │          │  worktree/commit   │
            └─────────┬──────────┘          └─────────┬──────────┘
                      │                               │
                      ▼                               ▼
            ┌──────────────────────────────────────────────────┐
            │  Project Resolver / Preprocessor                 │
            │  entrypoint · input/include · assets · macros    │
            │  source map · dependency graph                   │
            └───────────────┬─────────────────┬────────────────┘
                            │                 │
                            ▼                 ▼
                     ┌────────────┐     ┌────────────┐
                     │  OLD IR    │     │  NEW IR    │
                     └─────┬──────┘     └─────┬──────┘
                           │                  │
              ┌────────────┘                  └────────────┐
              ▼                                           ▼
      HTML Renderer                                 HTML Renderer
  plasTeX + MathJax                            plasTeX + MathJax
              │                                           │
              └──────────────┐             ┌──────────────┘
                             ▼             ▼
                         Structural Differ
                         + Inline Differ
                               │
                               ▼
                     Diff Model / diff.json
                               │
               ┌───────────────┼────────────────┐
               ▼               ▼                ▼
          old HTML         new HTML        change model
               └───────────────┼────────────────┘
                               ▼
                        report.html
                 BEFORE | AFTER | CHANGES
```

## 2.1 关键架构决策

| 决策 | 结论 | 原因 |
| --- | --- | --- |
| 比较层 | 比较 IR，不比较最终 HTML 字符串 | 避免 DOM 包装、属性顺序、渲染模板变化制造无意义差异。 |
| 预览层 | plasTeX 负责文档结构；MathJax/KaTeX 在浏览器渲染数学 | 减少对本地 TeX 引擎的依赖，数学公式保持可读。 |
| 版本层 | Git 优先；同时支持显式 old/new 路径 | 最符合 Codex 工作区修改模式。 |
| 输出层 | report.html + diff.json 双输出 | 人和 AI 分别消费最适合自己的格式。 |
| latexdiff | 可选，不作为主架构 | 其 DIF 宏会污染 AST；公式、表格、多文件 Source Map 与交互定位都不理想。 |

# 3. 端到端处理流程

## 3.1 Step A：确定修改前与修改后版本

默认命令 `latex-review main.tex` 将 OLD 解析为当前 Git HEAD 中的项目状态，将 NEW 解析为工作区状态。若项目不在 Git 中，则要求显式提供 `--old` 与 `--new`。

```text
# 默认：HEAD vs working tree
latex-review main.tex

# 两个提交
latex-review main.tex --old HEAD~1 --new HEAD

# 两个目录
latex-review --old-dir ./paper-old --new-dir ./paper-new --entry main.tex

# 两个独立文件（单文件模式）
latex-review old.tex new.tex
```

## 3.2 Step B：解析项目依赖

解析器从 entrypoint 开始递归收集 \input、\include、常见 bibliography、graphic resources 和局部宏定义，形成 dependency graph。V1 可以利用 latexpand 辅助展开，但必须自己维护 origin 信息，不能只留下一个失去来源的 flattened.tex。

## 3.3 Step C：构建 Source Map

Source Map 是该工具的基础设施。每个可比较节点必须尽量保留 `source_file`、`start_line`、`end_line`、`source_span`，必要时还记录 flattened 范围和置信度。这样报告中的“Method / paragraph 4”才能回到 `sections/method.tex:143–158`。

## 3.4 Step D：构建结构化 IR

将文档归一化为稳定的逻辑节点，而不是把 LaTeX 当作纯文本。节点树保留章节层级、正文块、公式、图、表、列表、定理环境、引用等；同时保留原始 LaTeX 与可比较的规范化表示。

## 3.5 Step E：分别渲染 OLD/NEW HTML

IR/DOM 经 plasTeX 及后处理生成阅读视图。数学内容优先保留 TeX 源，在浏览器中由 MathJax 或 KaTeX 排版。图片与静态资源复制到报告目录，无法渲染的对象展示带源码位置的占位卡片。

## 3.6 Step F：执行结构化 diff

先做文档块匹配，再对“已匹配但发生修改”的块进行更细粒度 diff。节点匹配基于稳定 ID、章节路径、label、节点类型、文本指纹和邻域信息，而不是仅靠绝对序号。

## 3.7 Step G：生成三栏报告与 JSON

最终模板把 old HTML、new HTML 和 change model 聚合为单一静态 report.html。浏览器端 JavaScript 只负责交互、同步滚动、过滤与跳转，不重新计算核心 diff。

# 4. 中间表示（IR）与稳定节点标识

## 4.1 文档节点模型

```text
Document
├── Section
│   ├── Paragraph
│   ├── Paragraph
│   ├── Equation
│   ├── Figure
│   ├── Table
│   └── Subsection
│       ├── Paragraph
│       └── Theorem
└── Bibliography
```

## 4.2 建议 Node Schema

```text
Node {
  id: "sec-method/p-0004",
  stable_key: "label:sec:method|paragraph|fingerprint:...",
  type: "paragraph",
  section_path: ["3 Method", "3.2 Spectral Operator"],
  source_file: "sections/method.tex",
  start_line: 143,
  end_line: 158,
  source_span: [4210, 4791],

  raw_latex: "...",
  normalized_latex: "...",
  plain_text: "...",
  html_fragment: "...",

  labels: [],
  citations: ["li2024"],
  assets: [],
  parse_warnings: [],
  source_map_confidence: 0.99
}
```

## 4.3 稳定 ID 策略

纯粹使用“第几个 paragraph”会因插入段落而导致后续全部错位。稳定节点标识应按优先级组合：显式 \label > section path + node type + 内容指纹 > 上下文邻域 + 顺序。

| 信号 | 权重/优先级 | 说明 |
| --- | --- | --- |
| 显式 label | 最高 | 公式、章节、图表如果有 label，可直接作为强匹配键。 |
| 章节路径 | 高 | 限定候选匹配范围，避免跨章节误匹配。 |
| 节点类型 | 高 | paragraph 与 equation 不应互相匹配。 |
| 规范化文本指纹 | 高 | 去掉空白、注释和部分无关宏后计算 hash/similarity。 |
| 前后邻域 | 中 | 帮助识别内容变化较大的同一段。 |
| 绝对顺序 | 低 | 作为最后的 tie-breaker。 |

> **V1 约束：** 先实现 robust matching，不必过早追求复杂的跨章节 MOVE 检测。无法高置信匹配时，将其标记为 remove + add，并在 diff.json 中保留 confidence。

# 5. LaTeX 项目预处理与 Source Map

## 5.1 多文件项目

系统必须把多文件论文视为默认场景。入口文件常见结构为 `main.tex -> sections/*.tex -> figures/* + refs.bib`。预处理器负责递归依赖收集、循环检测、相对路径解析、宏/资源作用域和源位置跟踪。

## 5.2 latexpand 的角色

latexpand 可以作为 V1 的辅助展开工具，但其产物只能用于解析便利，不能成为最终定位依据。实现上建议同时生成“展开流”和“origin span 表”，每段展开文本都记录来自哪个原始文件的哪个范围。

## 5.3 Source Map 数据结构

```text
SourceSpan {
  virtual_start: 18032,
  virtual_end: 18617,
  file: "sections/method.tex",
  start_line: 143,
  end_line: 158,
  start_col: 1,
  end_col: 1,
  confidence: 1.0
}
```

## 5.4 注释与空白归一化

- 普通 `%` 注释默认不参与语义 diff，但应保留“注释变化”开关供需要时启用。
- 连续空白与换行在 paragraph 语义比较中归一化；verb/verbatim/代码环境例外。
- 宏参数中的空白按 LaTeX 语义谨慎处理，不做激进全局 rewrite。
- 在 raw_latex 中始终保留原始内容，以支持审计和回退。

# 6. HTML 预览渲染层

## 6.1 plasTeX 的职责边界

plasTeX 适合把 LaTeX 文档结构转为 DOM/HTML，因此应承担章节、段落、列表、定理、图注、基础交叉引用等内容的阅读视图生成。但它不是完整系统：它不会自动提供高质量版本匹配、Source Map、三栏同步交互或针对公式/表格/引用的差异语义。

## 6.2 数学公式：保留 TeX，在浏览器中排版

公式节点应尽可能保留原始 TeX 内容，并在 HTML 中输出标准 inline/display math delimiter 或 data 属性，由 MathJax 3（默认）或 KaTeX（可选）渲染。这样日常预览完全不需要本地 TeX 引擎，同时避免把数学结构提前栅格化。

```text
<div class="math-block" data-node-id="eq-017">
  \[
  h_{K_t}(\omega)=\sum_{\ell,m} c_{\ell m}(t)Y_{\ell m}(\omega)
  \]
</div>
```

## 6.3 图片和 PDF 图资源

常见 PNG/JPG/SVG 可直接复制并引用。对于论文中作为图资源的 PDF，V1 可优先生成缩略预览（若系统具备转换能力）；无法转换时显示文件名、尺寸、caption 与 source location。预览失败不能阻止整份报告生成。

## 6.4 CSS 与阅读模式

- Before/After 两栏采用一致 typography，避免排版差异干扰内容判断。
- 正文宽度按论文阅读而非网页 dashboard 设计；每栏内部可独立滚动。
- 每个结构节点包裹 `data-node-id`，用于差异高亮和同步跳转。
- 未知宏/环境使用醒目的 fallback card，而不是直接吞掉内容。

# 7. 差异引擎设计

## 7.1 两阶段 diff

```text
Stage 1: Block / Structure Matching
  Section → Paragraph → Equation → Figure → Table → ...
  输出：UNCHANGED / ADDED / REMOVED / MODIFIED / (MOVED, later)

Stage 2: Fine-grained Diff for MODIFIED nodes
  paragraph  → token/word diff
  equation   → TeX token diff + before/after math
  figure     → asset/caption/label diff
  table      → structural cell diff when possible
  citation   → citation-key set diff
```

## 7.2 段落 diff

段落先转换为“可显示 token 流”，保护 LaTeX 命令、引用、交叉引用和数学片段的边界，再使用 Myers 或 diff-match-patch 进行 word/token diff。右侧 Changes 应优先呈现自然语言差异，而不是暴露大量 TeX 控制序列。

## 7.3 公式 diff

公式不适合简单按字符标红。V1 的推荐交互是“Before 公式 + After 公式 + 规范化 TeX token 变化摘要”。浏览器中两份公式都用 MathJax 正常排版；Changes 面板只在需要时展开 token-level 细节。

| 公式变化 | 展示方式 |
| --- | --- |
| 轻微变量/下标变化 | Before/After 两行渲染公式；下方显示关键 TeX token 变化。 |
| 整段公式替换 | 标记 Equation replaced；默认不尝试把复杂公式内部逐字符染色。 |
| 仅 label/tag 变化 | 单独报告 label/tag，不当成数学内容变化。 |
| 无法解析 | 显示 raw LaTeX before/after，并把 parse_warning 写入 diff.json。 |

## 7.4 引用 diff

对 `\cite{a,b}` 之类结构先解析 citation key 集合，Changes 中报告“added citation: wang2026”“removed citation: li2023”。这比比较原始字符串更符合审阅目的，并可在未来连接 BibTeX 元数据。

## 7.5 图与图注 diff

- 资源路径变化：`result-v1.pdf → result-v2.pdf`。
- caption 文本变化：使用普通 token diff。
- label 变化：单独列出。
- 图片文件内容变化但路径不变：可选计算文件 hash 并标记 asset changed。

## 7.6 表格 diff

V1 对常见 tabular/array 尝试解析行列；结构一致时按单元格定位变化，如 “row 3, col 2: 0.87 → 0.91”。若结构变化较大或包含复杂宏，退化为表格级 Before/After + raw LaTeX 摘要。

## 7.7 变更分类与置信度

```text
Change {
  id: "chg-0017",
  kind: "modified",
  node_type: "paragraph",
  old_node_id: "sec-method/p-004",
  new_node_id: "sec-method/p-004",
  confidence: 0.96,
  summary: "Method 3.2 paragraph modified",
  source_old: {...},
  source_new: {...},
  inline_diff: [...],
  warnings: []
}
```

# 8. report.html：三栏审阅界面

## 8.1 页面布局

```text
┌─────────────────────────────────────────────────────────────────────┐
│ LaTeX Review  | 17 changes | +423 -181 | 3 eq | 1 fig | filters   │
├──────────────────────┬──────────────────────┬───────────────────────┤
│ BEFORE               │ AFTER                │ CHANGES               │
│                      │                      │                       │
│ 3 Method             │ 3 Method             │ #12 Modified paragraph│
│ ...                  │ ...                  │ source: method.tex:143│
│                      │                      │ [before] → [after]    │
│ [node highlight]     │ [node highlight]     │ [jump] [open source] │
└──────────────────────┴──────────────────────┴───────────────────────┘
```

## 8.2 必需交互

- 点击 Changes 项：Before 与 After 同步跳到对应节点并高亮。
- Before/After 可开启“同步滚动”；同步基于节点锚点而不是简单 scrollTop 比例。
- 过滤器：All / Added / Removed / Modified / Equations / Figures / Tables / Citations。
- 顶部统计：总变更数、增加/删除词数、公式变化、图变化、表格变化、引用变化。
- 每个变更显示原始源文件与行范围；若运行环境支持编辑器 URL，可提供 Open Source。
- 支持键盘 `j/k` 或 `n/p` 跳到下一/上一变更。
- 支持“仅显示变更附近上下文”和“完整文档”两种阅读模式。

## 8.3 同步滚动算法

不要用两个面板 scrollTop 百分比直接绑定，因为增加/删除段落会让文档长度不同。应记录当前视口最接近顶部的 `data-node-id`，通过 old↔new node mapping 查找对应节点，并按节点内相对位置进行近似对齐。

## 8.4 颜色与可访问性

新增/删除/修改不能只靠颜色表达。UI 同时使用图标/标签（ADD/DEL/MOD）和背景色；数学、正文和 source location 都应有足够对比度。支持 prefers-color-scheme 不是 V1 必需，但 CSS 结构应允许后续增加暗色模式。

# 9. diff.json：面向 Codex 的机器可读接口

## 9.1 为什么必须单独输出 JSON

HTML 适合人，但 Codex 如果需要验证“本轮只改 Method 章节，没有误碰 Conclusion”，不应重新解析 HTML。diff.json 应成为稳定 API，HTML 只是它的一个视图。

```text
{
  "schema_version": "1.0",
  "project": {
    "entry": "main.tex",
    "old": {"kind": "git", "rev": "HEAD"},
    "new": {"kind": "worktree"}
  },
  "summary": {
    "changes": 17,
    "added_words": 423,
    "removed_words": 181,
    "equations_changed": 3,
    "figures_changed": 1,
    "tables_changed": 0,
    "citations_added": 2,
    "citations_removed": 0
  },
  "changes": [
    {
      "id": "chg-0017",
      "kind": "modified",
      "node_type": "paragraph",
      "section_path": ["3 Method", "3.2 Spectral Operator"],
      "source_old": {
        "file": "sections/method.tex",
        "start_line": 143,
        "end_line": 156
      },
      "source_new": {
        "file": "sections/method.tex",
        "start_line": 143,
        "end_line": 158
      },
      "old_text": "...",
      "new_text": "...",
      "confidence": 0.96,
      "warnings": []
    }
  ]
}
```

## 9.2 Codex 可直接执行的检查

- 确认变更文件是否只在任务允许范围。
- 确认是否出现意外删除。
- 检查新增/删除 citation keys。
- 检查公式变化数量是否与任务预期一致。
- 检查 parse_warnings/source_map_confidence 是否出现异常。
- 在下一轮修改时直接读取具体 source_file + lines 定位问题。

# 10. Git 版本解析与工作区集成

## 10.1 默认版本语义

| 命令 | OLD | NEW |
| --- | --- | --- |
| latex-review main.tex | Git HEAD | 当前 worktree（包含未提交修改） |
| latex-review main.tex --old HEAD~1 --new HEAD | HEAD~1 | HEAD |
| latex-review --old-dir A --new-dir B --entry main.tex | 目录 A | 目录 B |
| latex-review old.tex new.tex | old.tex | new.tex |

## 10.2 为什么不能只 `git show HEAD:main.tex`

多文件项目必须解析整个旧版本文件树。如果 OLD 是 Git revision，应通过 Git object database/临时 materialization 获取该 revision 下所有依赖文件，而不是只取 main.tex；否则 `\input{sections/method}` 会错误地读取当前工作区版本。

## 10.3 临时工作树策略

实现上可选两种策略：A）`git archive`/object 读取到临时目录；B）通过 Git plumbing API 按需读取 blob。V1 推荐临时目录法，简单、可测试、与 plasTeX 文件访问模型兼容。

# 11. Codex / Agent 集成设计

## 11.1 推荐自动化时机

Codex 在完成一次“有意义的 .tex 修改批次”后运行 LaTeX Review，而不是每保存一次文件都运行。工具运行结果作为本轮修改的审阅产物。

## 11.2 推荐 Codex 行为协议

```text
1. 修改 LaTeX 文件。
2. 运行：latex-review main.tex --output .latex-review/latest
3. 读取：.latex-review/latest/diff.json
4. 检查：
   - 是否只修改目标章节/文件；
   - 是否存在意外删除；
   - 是否有解析或资源警告；
   - 公式、图、引用变化是否符合任务。
5. 向用户提供：report.html 路径 + 简洁变更摘要。
6. 只有在用户要求或最终投稿检查时，再运行真实 LaTeX compile。
```

## 11.3 Skill 形式

若封装为 Codex skill，skill 不需要理解论文内容本身，只负责规范化触发条件、命令调用和结果检查。核心逻辑必须位于独立 CLI 中，避免把功能锁死在某个 Agent 平台。

> **设计边界：** “Codex skill”是集成层，“latex-review CLI”才是产品核心。这样同一工具可以被人类、CI、Git hook、IDE 或其他 Agent 使用。

# 12. CLI、配置与目录约定

## 12.1 CLI 草案

```text
latex-review main.tex \
  --old HEAD \
  --new worktree \
  --output .latex-review/latest \
  --math mathjax \
  --format html,json

# 可选最终验证
latex-review main.tex --compile --latexmk
```

## 12.2 项目配置 `.latex-review.toml`

```text
entry = "main.tex"
ignore = ["build/**", "generated/**"]

[render]
math = "mathjax"
copy_assets = true
show_unknown_macros = true

[diff]
comments = false
move_detection = false
citation_semantics = true
formula_token_diff = true

[git]
default_old = "HEAD"
default_new = "worktree"
```

## 12.3 输出目录

```text
.latex-review/latest/
├── report.html
├── diff.json
├── diagnostics.json
├── assets/
│   ├── old/
│   └── new/
└── cache-meta.json
```

V1 可以将 CSS/JS 直接内嵌到 report.html，从而方便单文件分享；图片等二进制资源仍放 assets/。后续可增加 `--single-file` 将小型图片转 data URI。

# 13. 推荐代码结构

```text
latex-review/
├── pyproject.toml
├── README.md
├── latex_review/
│   ├── cli.py
│   ├── config.py
│   ├── project.py          # entry/dependency resolution
│   ├── git_source.py       # revision/worktree materialization
│   ├── preprocess.py       # input/include + normalization
│   ├── source_map.py
│   ├── ir/
│   │   ├── model.py
│   │   └── normalize.py
│   ├── render/
│   │   ├── plastex_renderer.py
│   │   ├── math.py
│   │   └── assets.py
│   ├── diff/
│   │   ├── matcher.py
│   │   ├── paragraph.py
│   │   ├── equation.py
│   │   ├── table.py
│   │   ├── citation.py
│   │   └── model.py
│   ├── report/
│   │   ├── build.py
│   │   └── templates/report.html.j2
│   └── diagnostics.py
└── tests/
    ├── fixtures/
    ├── test_source_map.py
    ├── test_matching.py
    ├── test_equation_diff.py
    └── test_report_smoke.py
```

## 13.1 建议依赖

| 组件 | 建议 | 角色 |
| --- | --- | --- |
| LaTeX → DOM/HTML | plasTeX | 主要结构解析与 HTML 渲染后端。 |
| LaTeX token 辅助 | pylatexenc | 宏参数、token/节点辅助解析；不要求替代 plasTeX。 |
| 多文件辅助展开 | latexpand（可选） | V1 简化 input/include；Source Map 仍由本工具维护。 |
| 文本 diff | diff-match-patch 或自实现 Myers | paragraph/token diff。 |
| 模板 | Jinja2 | 生成 report.html。 |
| HTML 后处理 | BeautifulSoup/lxml | 插入 node ids、重写资源、清理模板。 |
| 数学 | MathJax 3 | 浏览器端公式显示。 |
| 配置 | tomllib / tomli | 读取 TOML。 |

# 14. 容错、降级与诊断

## 14.1 原则：报告必须“尽量生成”

论文项目经常含自定义 class、私有 package、宏和 TikZ。快速审阅工具不能因为一个未知宏就完全失败。对局部不可解析内容采用 fallback node，保留 raw LaTeX、源码位置和 warning。

| 问题 | 行为 |
| --- | --- |
| 未知宏 | 原样或简化显示，标记 unknown_macro；不吞正文。 |
| 未知环境 | 生成 fallback block，显示环境名与内容摘要。 |
| 图片缺失 | 显示 Missing asset 卡片并记录路径。 |
| Source Map 低置信 | 变更仍展示，但 source location 标记“approximate”。 |
| 某一节点 diff 失败 | 退化为 raw before/after，不阻止其他节点。 |
| plasTeX 整体失败 | 输出 diagnostics + 源码级 fallback report；CLI 返回非零或 degraded code。 |

## 14.2 退出码建议

| Exit code | 语义 |
| --- | --- |
| 0 | 报告完整生成，无严重警告。 |
| 2 | 报告生成，但存在 degraded rendering / source-map warnings。 |
| 4 | OLD/NEW 项目解析失败，无法生成可信报告。 |
| 8 | 内部错误。 |

# 15. 性能、缓存与安全

## 15.1 缓存

缓存粒度建议按文件 hash + 配置 hash + renderer version。工作区只改一个 section 时，不应重新解析所有未变化资源。OLD Git revision 通常可长期缓存；NEW 对已修改文件增量失效。

## 15.2 性能目标（V1）

| 项目规模 | 目标 |
| --- | --- |
| 单文件 20–30 页论文 | 冷启动 < 5–8 s；热启动 < 2–3 s（参考目标，不作为硬实时保证）。 |
| 多文件 50–80 页论文 | 冷启动 < 10–15 s；增量运行明显快于完整 TeX 编译。 |
| 前端打开 | 本地静态 HTML 应近乎即时；大文档可延迟加载 Changes 细节。 |

## 15.3 安全

- 默认不执行任意 shell escape、\write18 或 LaTeX 中的外部命令。
- 渲染层只读取项目允许目录；资源路径规范化，防止目录穿越。
- HTML 中对来自 LaTeX 的文本进行转义；仅允许受控标签进入模板。
- 若未来支持插件式宏处理，默认禁用第三方执行代码。

# 16. 测试策略

## 16.1 Fixture 矩阵

| Fixture | 必须覆盖 |
| --- | --- |
| simple-paper | section/paragraph/equation/citation 基础变化。 |
| multi-file | input/include 与 Source Map。 |
| equations | display/inline/aligned/label/tag 变化。 |
| tables | 单元格变化、行插入、复杂表退化。 |
| figures | asset/path/caption/label 变化。 |
| custom-macros | 未知宏可见降级。 |
| git-revision | OLD revision 全项目 materialize。 |
| large-paper | 性能与同步滚动稳定性。 |

## 16.2 单元测试

- Source Map：展开后每个节点回到正确原文件与行范围。
- Matcher：插入段落后，后续相同段落仍保持正确配对。
- Paragraph diff：空白和换行变化不产生语义噪声。
- Citation diff：key 集合增加/删除准确。
- Equation diff：常见下标/参数变化能显示 before/after 和 token 摘要。
- Report smoke：生成的 report.html 不依赖服务端即可打开。

## 16.3 Snapshot / Golden 测试

对 diff.json 使用 golden snapshots，避免版本升级导致无意识 schema/匹配变化。HTML 不建议整页字符串 snapshot，而应对关键 DOM 结构与变更数量做断言。

# 17. V1 / V1.5 / V2 路线图

## 17.1 V1：先把主闭环做稳

| 优先级 | 能力 | 状态要求 |
| --- | --- | --- |
| P0 | HEAD vs worktree / old-new dir | 必须 |
| P0 | \input/\include 多文件解析 | 必须 |
| P0 | Source Map 到原文件行范围 | 必须 |
| P0 | plasTeX HTML + MathJax | 必须 |
| P0 | paragraph/equation/figure/table/citation 基础结构 diff | 必须 |
| P0 | 三栏 report.html + 点击跳转 | 必须 |
| P0 | diff.json | 必须 |
| P1 | 节点锚点同步滚动 | V1 建议 |
| P1 | 缓存 | V1 建议 |
| P1 | 诊断与 fallback | 必须至少有基础版本 |

## 17.2 V1.5：提高审阅质量

- 更强的表格 cell-level diff。
- MOVE detection 与跨章节匹配。
- BibTeX 元数据展示（不仅是 key）。
- IDE deep link（VS Code/Cursor/Codex desktop 可用时）。
- 报告单文件打包。
- 更精细的公式 token/AST 差异。

## 17.3 V2：最终版式与高级验证

- 可选 `--compile` 真实 LaTeX PDF。
- PDF 页级视觉 diff 与结构 diff 联动。
- TikZ/复杂宏的沙箱渲染。
- PR 评论/CI artifact 自动发布。
- 面向论文的规则检查：引用丢失、label 重复、公式编号变化、图表未引用等。

# 18. V1 验收标准

只有满足以下条件，V1 才算形成可用闭环，而不是“plasTeX demo”。

1. 在一个包含至少 5 个 section 文件的真实论文项目中，默认命令能够正确比较 HEAD 与 worktree。
2. report.html 单文件入口可在无后端服务的浏览器中打开，并显示 Before / After / Changes 三栏。
3. 点击任一变更项可定位到两侧对应节点；至少 paragraph/equation/figure/table/citation 五类节点可区分。
4. 至少 95% 的普通正文变更能够映射回正确源文件与近似正确行范围；映射不可靠时明确标记低置信。
5. 插入一个段落不会导致同章节后续所有段落被误判为修改。
6. 公式修改以正常渲染的 before/after 形式可读，且不会因复杂 token diff 破坏整页。
7. diff.json 与页面显示的变更总数、节点类型和源位置一致。
8. 未知宏或缺图不会让整个报告生成失败；必须产生 diagnostics。
9. 默认路径不调用 pdfLaTeX/XeLaTeX/LuaLaTeX；最终编译必须通过显式选项触发。

# 19. 推荐实施顺序

## 19.1 Milestone A：最小垂直切片

1. 建立 CLI，支持 old-dir/new-dir + entry main.tex。
2. 实现基础项目解析与 paragraph/section IR。
3. 用 plasTeX 生成两份 HTML，嵌入同一 report.html。
4. 实现 paragraph matching + token diff。
5. 输出最小 diff.json。

## 19.2 Milestone B：让它适合真实论文

1. 加入 \input/\include 与 Source Map。
2. 加入 equation、citation、figure、table 节点。
3. 加入 MathJax、资源复制和 fallback cards。
4. 加入三栏点击跳转和变化过滤。

## 19.3 Milestone C：接入 Git/Codex

1. 实现 Git revision 全项目 materialization。
2. 默认 HEAD vs worktree。
3. Codex skill/脚本：修改后运行、读取 diff.json、报告异常。
4. 缓存与性能优化。

## 19.4 Milestone D：强化与产品化

1. 同步滚动、键盘导航、上下文折叠。
2. 表格/公式高级 diff。
3. CI artifact 与 IDE deep link。
4. 可选真实编译与 PDF visual diff。

# 20. 主要风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| TeX 宏系统过于动态 | 无法完整静态解析 | 明确 V1 不是 TeX 引擎；fallback 节点 + diagnostics；对常见论文宏优先支持。 |
| plasTeX 与某些 class/package 不兼容 | 预览缺失或结构错误 | 建立兼容层；raw LaTeX 回退；允许用户添加 macro stubs。 |
| 节点匹配错位 | Changes 噪声巨大 | 稳定 key + section scope + fingerprint + 邻域；低置信时宁可 add/remove。 |
| Source Map 在展开后丢失 | 无法回源 | 从架构第一天就把 origin span 作为一等数据，不事后补。 |
| HTML 预览被误认为最终版式 | 用户误判投稿效果 | UI 明确标记“content preview”；最终编译使用显式模式。 |
| 报告前端过度复杂 | 开发成本膨胀 | V1 使用 Jinja2 + Vanilla JS/CSS，不引入 SPA 框架。 |

# 21. 最终技术决策清单

| 问题 | 决定 |
| --- | --- |
| 只用 plasTeX 是否足够？ | 不够；plasTeX 只负责解析/HTML 渲染层的一部分。 |
| 是否以 latexdiff 为主？ | 否；可作为辅助，但主 diff 基于结构化 IR。 |
| 是否比较 HTML？ | 否；HTML 是展示层，不是语义比较层。 |
| 是否默认编译 PDF？ | 否；日常路径不编译，最终验证才显式编译。 |
| 数学如何预览？ | 保留 TeX，浏览器 MathJax/KaTeX。 |
| 多文件如何处理？ | 递归项目解析 + Source Map；必要时借助 latexpand。 |
| 版本来源？ | Git 优先，支持目录/文件模式。 |
| 核心输出？ | report.html + diff.json。 |
| Codex 集成方式？ | 独立 CLI 为核心，skill 只是调用/检查协议。 |
| V1 前端？ | 静态 HTML + CSS + Vanilla JS。 |

# 附录 A：推荐的 Change 摘要文案规范

| 节点类型 | 示例 |
| --- | --- |
| Paragraph | Modified paragraph in 3.2 Spectral Operator · method.tex:143–158 |
| Equation | Equation modified · eq:support-spectrum · method.tex:201–206 |
| Citation | Added citation: wang2026 · related.tex:88 |
| Figure | Figure asset and caption changed · fig:overview · intro.tex:120–132 |
| Table | Table cell changed: row 3, col 2 · table:ablation · exp.tex:305–340 |

# 附录 B：推荐的首版 README 示例

```text
# LaTeX Review

Fast structural review for LaTeX projects.

## Quick start

    pip install latex-review
    cd your-paper
    latex-review main.tex

Open:

    .latex-review/latest/report.html

Machine-readable changes:

    .latex-review/latest/diff.json

By default, Git HEAD is BEFORE and the current worktree is AFTER.
No PDF compilation is performed unless `--compile` is explicitly requested.
```

# 附录 C：完成定义（Definition of Done）

当工具能够在真实多文件论文上稳定执行“解析 → Source Map → IR → HTML → 结构化 diff → report.html + diff.json”，并且 Codex 可以在改稿后自动运行它、读取 JSON 自检、把 HTML 交给作者审阅时，这个项目才算完成第一个有价值版本。仅仅做到 `plasTeX old.tex`、`plasTeX new.tex` 或 `latexdiff old new` 都不满足该定义。

> **推荐的第一实施目标：** 先完成一个真实论文项目上的端到端最小闭环，不要先追求复杂数学 AST 或前端框架。优先验证 Source Map、节点匹配和三栏审阅是否真的减少人工检查成本。
