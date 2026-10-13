# 批注改稿闭环

用于同一回合中查看批注、修改论文、回复依据并解决批注。
命令参数先核对本机 `latex-review-annotations --help`。

1. 确认论文身份和报告目录。持续预览用 `latex-review-local list` 发现已有服务，保留复用服务。
2. 运行 `latex-review-annotations REPORT_DIR --status unresolved`，按 `total_matched` 和分页读取目标清单。
3. 运行 `latex-review-annotations REPORT_DIR show THREAD_ID`。核对完整消息、原文、当前位置和显示版本。
4. 按用户授权修改源码。重复原文通过文件、源码位置和单元编号消歧；换版后重新读取位置。
5. 核对实际源码和检查结果，将“判断、依据、剩余问题”保存到 UTF-8 文件。证据充分时使用下面的组合命令。
6. 再次 `show THREAD_ID`，确认回复正文、作者和解决状态。批量操作继续检查收据与快照审计。

```sh
latex-review-annotations REPORT_DIR reply THREAD_ID --body-file evidence.txt --author Codex --resolve
```

```mermaid
flowchart LR
 A[确认目录并读取清单] --> B[show 核对批注与位置]
 B --> C[授权修改源码]
 C --> D[检查并保存依据]
 D --> E[reply --resolve]
 E --> F[show 核对结果]
```

`get` 与 `show` 同义，返回单个 `thread` 和报告上下文；不存在或已删除时返回错误。
普通 `reply` 保持原解决状态。`reply --resolve` 在同一事务中追加回复并解决，只增加一次 revision。
失败时两项均不写入。并发修订冲突仍须重新读取，已成功操作只核对，不重复提交。

`--author` 可放在子命令前或后。两处都有值时，子命令后的值优先。
其他查询参数放在子命令前；没有子命令时可放在报告目录前后。
默认查询返回批注 `threads`；`--content` 返回正文 `units`，不能与 `show/get` 或 `--detail` 合用。

`add`、`reply` 和 `edit` 支持 `--body-file`；`add` 支持 `--quote-file`。
文件采用 UTF-8，保留换行和数学公式文本。文件参数与对应的 `--body` 或 `--quote` 互斥。
路径 `-` 表示标准输入；一次调用只允许一个参数读取标准输入。

```sh
latex-review-annotations REPORT_DIR add --quote-file quote.txt --body-file note.txt --author Codex
latex-review-annotations REPORT_DIR reply THREAD_ID --author Codex --body-file - <<'TEXT'
判断：已落实。
依据：源码中的 $h(\omega)$ 与 \hat{r} 已更新。
剩余问题：无。
TEXT
```

新增全文审查的范围与意见格式见 [editorial.md](editorial.md)。
源码替换建议的预览与授权应用见 [suggestions.md](suggestions.md)。
批量预检、收据和快照审计见 [annotations.md](annotations.md)。
持续预览与服务管理见 [live.md](live.md)。
