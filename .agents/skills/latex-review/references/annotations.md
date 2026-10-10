# 报告批注

用户要求在报告或持续预览中添加、回复、编辑、删除或解决批注时，优先使用 `latex-review-annotations` 高层命令。只操作已有报告，不自动修改论文或重新生成报告。

1. 确认本地报告目录。命令不接受 HTTP 地址；只有预览地址时，先从本次服务启动信息确认报告目录。用 `--content` 读取当前正文，用无参数模式读取线程及消息 ID。检查 `add --help` 确认安装版本支持高层命令；不支持时明确说明，不自行安装。
2. 按用户要求提交操作。新增或回复必须传作者名称，智能体以自身名称署名，身份固定为 `agent`。原文重复时读取候选，通过源码位置和上下文选择 `--unit-id` 或从 1 开始的 `--occurrence`，不得默认选择第一个。仅规范化 ASCII 空白，不猜测模糊原文。公式使用 `--formula`，选择完整原始 LaTeX 或公式单元 ID。报告换版后重新读取定位信息。
3. 检查退出码及 JSON 结果，再读取线程确认正文、作者、分类、回复父消息或状态。冲突时保留输入，重新读取并检查操作对象，不自动重试写入。已完成的请求不要因刷新页面或不确定显示状态而重复提交。

~~~sh
latex-review-annotations REPORT_DIR --content
latex-review-annotations REPORT_DIR
latex-review-annotations REPORT_DIR add --quote '目标原文' --body '修改建议' --author Codex
latex-review-annotations REPORT_DIR reply THREAD_ID --body '回复内容' --author Codex
latex-review-annotations REPORT_DIR reply THREAD_ID --parent-message-id MESSAGE_ID --body '嵌套回复' --author Codex
latex-review-annotations REPORT_DIR edit THREAD_ID MESSAGE_ID --body '更新内容'
latex-review-annotations REPORT_DIR delete THREAD_ID --message-id MESSAGE_ID --yes
latex-review-annotations REPORT_DIR delete THREAD_ID --yes
latex-review-annotations REPORT_DIR resolve THREAD_ID
~~~

`add` 和 `reply` 分类默认为 `note`，可用 `--category question|suggestion|note` 指定。`edit` 保留作者，未指定分类时保留原分类。`reply` 默认回复初始批注；指定父消息时必须属于同一线程且未删除。
删除必须符合用户授权范围，并显式传 `--yes`。无 `--message-id` 时删除整条批注及其回复；删除记录保留，界面没有恢复入口。`resolve` 将整条批注标记为已解决。
本机 CLI 写入论文项目共享存储；离线浏览器中的新增记录需要先导出并导入，不能把磁盘结果当作离线数据。成功结果包含 `revision` 和 `result`；创建返回线程与消息 ID，回复返回消息 ID。操作失败返回 JSON 和退出码 4，参数解析失败返回 2。现有 `--request` JSON 接口继续用于导入、重新关联和重新打开等操作。
