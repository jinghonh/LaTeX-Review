# macOS 7744314 / 6310822 复验基线：功能通过，保存性能仍未通过

应用固定源码 `7744314e36634cdba55a306d9386b01107005336`；托管 CI 使用其 docs-only 子提交 `63108221c0f1f691fa98197fb74868b2eff8cfb6`。已核对这两个提交的应用与测量 helper 无差异。120 文件 package/helper 起止 aggregate 均为 `42a8e701d215c4d937320986af55a1a987451083233d608b660a7775c1b92f31`。不把这些结果归给其后正在实施的 validation receipt。

计时仍含原生监听、完整渲染/发布、默认 800ms 静默期限、异步通知、队列/检查、本机 HTTP 状态与导出验证；prose 另含实际发布可见文本和 5000 text units 证明。每组 40 文件 / 5000 单元 / N=10，observer sleep 0.5s 加实际 HTTP 工作；没有浏览器 paint。所有首个及慢样本保留，最近秩 p95 是该组十个样本的最大值；不跨机器合并，不挑选重跑的绿结果。

## GitHub 托管 macOS：四组完整 N=10，全部 FAIL ≤2s

macOS 26.6.2 arm64 / Python 3.11.9。verify 与 saved_current 在独立 runner jobs 运行；不声称底层物理硬件独占。

| 工作流 / 模式 | 原始公开数据 | median (s) | p95 (s) | 第一保存 (s) | ≤2s |
| --- | --- | ---: | ---: | ---: | --- |
| [push 41983158922](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41983158922>) / comment | [JSON](<macos-saved-6310822-push-comment.json>) | 3.3012312085 | 3.599518708 | 3.599519 | FAIL |
| push / prose | [JSON](<macos-saved-6310822-push-prose.json>) | 2.771450542 | 3.720795292 | 2.456123 | FAIL |
| [PR 41983165340](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41983165340>) / comment | [JSON](<macos-saved-6310822-pr-comment.json>) | 2.809923354 | 3.275192416 | 3.275192 | FAIL |
| PR / prose | [JSON](<macos-saved-6310822-pr-prose.json>) | 3.163168167 | 4.973879875 | 2.666886 | FAIL |

四组 complete=true，40/40 保存均 authoritative/completed/non-stale；每组 source paths changed 为空、120 文件边界一致、entry exit 0 / entry exited / worker exited / endpoint removed。两组 prose 各 10 次真实已发布文本变化及 5000 units 证明，证明成本包含在 timer 内。comment 最大值均是第一次；push prose 最大值在第 8 次、PR prose 在第 9 次，不能全部归为启动或删点。

两个完整功能 verify job 都 success：push **1326 passed / 24 skipped / 519.32s**，PR **1326 passed / 24 skipped / 676.05s**，各 1350 总数。24 个 skip 为 22 项可选 TeX/PDF 工具依赖及 2 项 report-only 比较参数，没有浏览器或历史对象缺失跳过。CLI smoke 与同步核心门禁通过；同步核心不代替真实保存。两个 workflow 整体 failure **仅因 saved_current 性能门禁失败**，不再是 F3 的 N=0 启动失败。

## 请求人本机 macOS：HUMAN-REPORTED，已完成本轮复验

来源：[本轮请求人公开反馈](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6652141178>)。实际应用固定 `7744314`，macOS 27.2 arm64 / Python 3.11.14，完整历史独立副本，`LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-macos.sh` 退出 0；**1348 passed / 2 skipped / 0 failed，1350 total**，命令总耗时约 790.51s（父代理核对原值 790.507655859s）。两项 skip 仅为 compare-mode report-only；没有浏览器/历史缺失，已跟踪源码无变化。CLI/core-only 门禁通过不意味着保存达标。

| 模式 | 全部十次秒数（公开反馈舍入值，原顺序） | median (s，父代理原始复核汇总) | p95 (s，父代理原始复核汇总) | ≤2s |
| --- | --- | ---: | ---: | --- |
| comment | 1.937551, 1.912142, 1.961303, 1.955446, 1.986149, 2.053136, 1.912870, 1.941398, 2.028944, 1.977638 | 1.95837414599 | **2.053136207978241** | FAIL |
| prose | 2.097751, 2.214932, 3.035211, 2.870439, 2.803131, 4.730836, 2.385093, 2.901257, 2.376655, 5.056158 | 2.836784916988 | **5.056157624989282** | FAIL |

**comment p95 2.053136207978241s >2s，不能四舍五入成达标。** 第一保存 1.937551s / 2.097751s 均保留，最大值分别在第 6 / 10 次。完整回归结束后再独立顺序跑两组，没有本轮测试/基准的主动并发，但其他用户负载未控制。

公开反馈确认两组 complete=true、全部 20 样本 authoritative、prose 10/10 真实已发布变化与 5000 units 证明、起止来源一致/无测量中源码变化、拥有的 entry/worker 退出及私有 endpoint 移除。父代理另外独立复核了十份私下产物的 SHA-256、JUnit 1350 总数/1348 通过/2 跳过/0 失败错误及两组原始身份/文本/清理证明。本台账代理只读取公开反馈和公开 CI/基准数据；私下原始文件、日志、JUnit、JSON、逐文件 hash 和私人目录均未复制或链接到公开仓库，此处仅保留可公开事实与中性汇总。

本轮物理 Mac 测得值较[上一轮 f563199 基线](<macos-f563199-baseline.md>)低，但源码及共享负载条件并非受控因果实验，不能把 prose 35.028981→5.056158s 全归给某一优化或外部负载。旧 SHA、全部旧样本及 F3 N=0 记录不变；本轮复验请求已履行，不应继续等待同一个 Mac 资源或真实论文/预算才开展可行工作。请求人要求继续修复并给出下一个验证后的固定应用 SHA。

## 同应用的 Linux 参考测量

[完整保存台账](<saved-check-latency.md>)另列 [comment N=10](<linux-saved-performance-optimized6-comment.json>) median 4.3285965164s / p95 **7.5897699429s**、[prose N=10](<linux-saved-performance-optimized6-prose.json>) median 4.2752680921s / p95 **5.301649058s**，均 FAIL ≤2s。首个 comment 是最大值，prose 最大值是第二次，第一 prose 4.766394s 也保留。六组公开 JSON 与本机汇总不能拼成单一分布；所有结果只覆盖该固定源，尚未验证或重测的新 receipt 实现不享有这些通过数或性能结论。
