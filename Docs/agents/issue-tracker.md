# Issue 跟踪：GitHub

本仓库的问题和规格记录在 GitHub Issues 中。所有操作使用 `gh` CLI。

当前目录尚未配置 GitHub 远程地址。配置远程地址后，`gh` 可在克隆目录中自动识别仓库。

## 约定

- **创建 issue**：`gh issue create --title "..." --body "..."`。多行正文使用 heredoc。
- **读取 issue**：`gh issue view <number> --comments`；可用 `jq` 筛选评论，并一并获取标签。
- **列出 issue**：使用 `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，并按需添加 `--label` 和 `--state` 筛选条件。
- **评论**：`gh issue comment <number> --body "..."`
- **添加或移除标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <number> --comment "..."`

通过 `git remote -v` 确定仓库；在已配置远程地址的克隆目录中，`gh` 会自动识别仓库。

## 将 PR 作为 triage 请求入口

**PR 作为请求入口：否。**（如果本仓库把外部 PR 当作功能请求，可改为 `yes`；`triage` 技能会读取此设置。）

设为 `yes` 后，PR 使用与 issue 相同的标签和状态，并使用对应的 `gh pr` 命令：

- **读取 PR**：`gh pr view <number> --comments`，并用 `gh pr diff <number>` 查看差异。
- **列出供 triage 的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，之后只保留 `authorAssociation` 为 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR` 或 `NONE` 的 PR；排除 `OWNER`、`MEMBER` 和 `COLLABORATOR`。
- **评论、添加标签或关闭**：使用 `gh pr comment`、`gh pr edit --add-label` / `--remove-label`、`gh pr close`。

GitHub 的 issue 和 PR 共用编号，因此单独出现的 `#42` 可能指其中之一：先运行 `gh pr view 42`，若找不到再运行 `gh issue view 42`。

## 技能要求“发布到 issue 跟踪器”时

创建一个 GitHub issue。

## 技能要求“获取相关工单”时

运行 `gh issue view <number> --comments`。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图**是一个 issue，**子项**是作为工单的子 issue。

- **地图**：一个带有 `wayfinder:map` 标签的 issue，正文包含 Notes / Decisions-so-far / Fog。使用 `gh issue create --label wayfinder:map` 创建。
- **子工单**：使用 GitHub 子 issue API 将 issue 关联到地图。若仓库未启用子 issue，则将子工单列入地图正文的任务清单，并在子工单正文开头注明 `Part of #<map>`。标签格式为 `wayfinder:<type>`，类型为 `research`、`prototype`、`grilling` 或 `task`。认领后，将工单分配给负责推进的开发者。
- **阻塞关系**：优先使用 GitHub 原生 issue 依赖，这是 GitHub 界面可见的标准表示。可通过 `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` 添加关系。其中 `<blocker-db-id>` 必须是阻塞 issue 的数字 **database id**，可用 `gh api repos/<owner>/<repo>/issues/<n> --jq .id` 查询；不要使用 `#number` 或 `node_id`。GitHub 的 `issue_dependencies_summary.blocked_by` 表示仍未关闭的阻塞项。若原生依赖不可用，则在子工单正文开头写 `Blocked by: #<n>, #<n>`。所有阻塞项关闭后，工单才算解除阻塞。
- **可推进工单查询**：列出地图下仍开放的子项（或正文任务清单中的工单），排除存在未关闭阻塞项或已分配负责人的工单；按地图中的顺序取第一个。阻塞项可通过 `issue_dependencies_summary.blocked_by > 0` 判断，或检查 `Blocked by` 行中列出的 issue 是否仍开放。
- **认领**：运行 `gh issue edit <n> --add-assignee @me`。这是本轮会话的第一次写操作。
- **完成**：先运行 `gh issue comment <n> --body "<answer>"`，再运行 `gh issue close <n>`，然后在地图的 Decisions-so-far 中追加上下文指针（gist + 链接）。
