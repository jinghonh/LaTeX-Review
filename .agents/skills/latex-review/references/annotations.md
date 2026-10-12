# 报告批注

使用 `latex-review-annotations` 操作已有报告。批注写入不等于论文修改。

1. 确认本地报告目录。只有端口时，读取 `http://127.0.0.1:PORT/api/report` 的 `report_directory`。旧服务没有接口时查找启动记录，确认目录后再操作。
2. 查看 `--help`。先用摘要、字段、筛选和分页读取必要信息，再展开目标线程或正文。过滤 JSON 后展示结果。定位时保留完整原文；分页不是全文复核完成的证据。
3. 新增或回复时以自身名称署名。含行内公式的 quote 可以使用 `$...$` 或 `\(...\)`，公式内容必须相等。歧义时根据源码位置和上下文消歧；相近候选只供核对。完整公式使用 `--formula`。
4. 全文复核使用 `batch`。给批次和每项设置唯一 ID，保存开始清单及修订号。预检后提交，核对逐条结果及 `snapshot_file`。相同请求 ID 的重试只返回原收据；修改载荷要用新 ID。
5. 每条复核回复包含“判断、依据、剩余问题”。判断使用“已落实、采纳、部分采纳、不采纳、证据不足”。依据指向实际源码或检查结果。回复完成与问题解决分开；证据不足时保留未解决状态。
6. 核对退出码、消息、作者和父消息。批量结束检查自动 `audit`，必要时再次运行 `audit SNAPSHOT_FILE`。报告遗漏、重复回复和外部新增批注。冲突后重新读取；已成功项只核对，不重复提交。

```mermaid
flowchart LR
 A[确认目录并精简读取] --> B[核对原文与依据]
 B --> C[预检与提交批次]
 C --> D[核对收据和快照]
 D --> E[检查重复和新增]
 E --> F[有充分证据才解决]
```

~~~sh
latex-review-annotations REPORT_DIR --content --full --unit-id UNIT_ID
latex-review-annotations REPORT_DIR --summary --limit 20
latex-review-annotations REPORT_DIR --thread-id THREAD_ID --fields id,messages,association
latex-review-annotations REPORT_DIR add --quote '目标原文' --body '修改建议' --author Codex
latex-review-annotations REPORT_DIR reply THREAD_ID --body '回复内容' --author Codex
latex-review-annotations REPORT_DIR reply THREAD_ID --parent-message-id MESSAGE_ID --body '嵌套回复' --author Codex
latex-review-annotations REPORT_DIR edit THREAD_ID MESSAGE_ID --body '更新内容'
latex-review-annotations REPORT_DIR delete THREAD_ID --message-id MESSAGE_ID --yes
latex-review-annotations REPORT_DIR delete THREAD_ID --yes
latex-review-annotations REPORT_DIR resolve THREAD_ID
latex-review-annotations REPORT_DIR batch batch.json --request-id UNIQUE-ID --author Codex --dry-run
latex-review-annotations REPORT_DIR batch batch.json --request-id UNIQUE-ID --author Codex
latex-review-annotations REPORT_DIR audit SNAPSHOT_FILE
~~~

`add` 和 `reply` 分类默认为 `note`，可用 `--category question|suggestion|note` 指定。`edit` 保留作者，未指定分类时保留原分类。`reply` 默认回复初始批注；指定父消息时必须属于同一线程且未删除。
删除必须符合用户授权范围，并显式传 `--yes`。无 `--message-id` 时删除整条批注及其回复；删除记录保留，界面没有恢复入口。`resolve` 将整条批注标记为已解决。
本机 CLI 写入论文项目共享存储；离线新增记录要先导出并导入，不能把磁盘结果当作离线数据。
高层写入默认返回 ID、修订号和成功状态；`--full` 返回完整结果。读取选项放在子命令之前。批量默认提交成功项；任何失败退出码为 4，仍须检查已经成功的项。`--atomic` 要求全部成功，`--dry-run` 只预检。
批量 JSON 格式及读取参数见下文。结构化替换见 [源码建议](suggestions.md)。现有 `--request` 接口继续用于导入、重新关联和重新打开。


## 摘要与历史

默认读取返回最多 50 条摘要，包含作者、消息预览、定位状态和源码位置。
使用 `--limit`、`--offset` 分页。`total_matched` 表示筛选后的总数。
`--author` 默认忽略大小写。
使用 `--thread-id ID --full` 展开目标线程；`--history` 展开历史。
正文坐标需要 `--content --full --unit-id ID`。保持完整原文，使用 UTF-16 坐标。
原选区删除后，批注可能保留所属段落或表格。容器导航不代表选区已经重新关联。

## 批量载荷与预检

批量文件接受项数组，或包含 `items`、`request_id`、`expected_revision`、`atomic` 的对象。
每项必须提供唯一 `id`。新增项示例：

```json
{"id":"review-1","operation":"add","quote":"目标原文","body":"判断、依据、剩余问题"}
```

回复项使用 `operation=reply`、`thread_id`、`body`，可用 `parent_message_id` 指定父消息。
作者可在每项提供，或使用命令的 `--author`。
预检保存 15 分钟的缓存，不提交批注，也不消耗请求 ID。
提交默认复用相同请求 ID 的缓存，也可传 `--preflight-token TOKEN`。
正文版本、批注修订号或载荷变化后，重新预检。提交拒绝过期缓存。
CLI 进度写入 stderr；stdout 保持单个 JSON。使用 `--quiet-progress` 关闭进度。
提交返回 `results`、`revision`、`preflight_reused`、`audit`、`snapshot_file`。

## HTTP 后台任务

读取 `/api/annotations/status` 获取摘要及会话令牌。
页面数据使用 `?view=page`；完整数据使用 `?view=full`；修订检查使用 `?view=revision`。
将版本检查信封提交到 `/api/annotations/update?async=1`。
POST 需要 JSON 和 `X-LaTeX-Review-Token` 请求头。
响应为 HTTP 202，包含 `task_id` 和 `status_url`。
轮询 `status_url`，读取 `phase`、`completed`、`total` 和 `state`。
完成状态为 `complete`，结果保存在 `result`。失败状态为 `failed`，保留错误原因。
批量预检与提交沿用 `operation=batch`、`expected_revision`、`request_id`、`items`。
预检增加 `dry_run=true`；提交可以提供返回的 `preflight_token`。
