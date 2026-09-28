# 领域文档

说明工程技能在探索代码时，如何读取本仓库的领域文档。

## 探索前读取

- 根目录的 **`CONTEXT.md`**；或者
- 如果根目录存在 **`CONTEXT-MAP.md`**，则读取它所指向的、与当前主题相关的各个上下文 `CONTEXT.md`；
- **`docs/adr/`** 中与当前工作领域相关的 ADR。多上下文仓库还需检查 `src/<context>/docs/adr/` 中的上下文级决策。

如果这些文件或目录尚不存在，**静默继续**。不要专门指出它们缺失，也不要提前建议创建；`/domain-modeling` 技能会在术语或决策实际确定时按需创建。

## 文件布局

单一上下文仓库（大多数仓库）：

```text
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录存在 `CONTEXT-MAP.md`）：

```text
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← 全系统决策
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← 上下文专属决策
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## 使用术语表中的词汇

在 issue 标题、重构提案、假设或测试名称中提及领域概念时，使用 `CONTEXT.md` 中定义的术语。不要改用术语表明确避免的近义词。

如果需要的概念尚未出现在术语表中，说明项目可能尚未定义该术语：要么重新考虑是否引入新说法，要么将这个缺口记录下来，交由 `/domain-modeling` 处理。

## 标出与 ADR 的冲突

如果输出与现有 ADR 冲突，应明确指出，而不是默默覆盖：

> _这与 ADR-0007（事件溯源订单）相冲突，但值得重新讨论，因为……_
