# macOS 466a321 / d2fc739：物理机功能通过，托管回归与全部保存性能仍未通过

应用固定源码 `466a3213230dc59fc6405871eeb3a84c47a2d5bd`；托管运行 HEAD `d2fc739b819e5c4bd8079db992b7edbe4cbcf4e2`。已核对两提交 app/helper 无差异，全部六组公开 N=10 的 120 文件起止 aggregate 为 `de00f087600364452c93faee440e9ece9435fb19b99c1d3fd91799e6877d560b`。原 receipt 实现相关回归为 273 passed / 46.42s / 18 模块，不把它与全套、后续 test-only 修正或下一轮实现累加。

## 托管 macOS：verify 与 saved_current 均有失败

| 工作流 | 完整 verify 结果 | 原始失败 |
| --- | --- | --- |
| [push 41989929700](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41989929700>) | **1397 passed / 24 skipped / 1 failed，544.06s** | [固定源 entry proxy 用例](<https://github.com/jinghonh/LaTeX-Review/blob/d2fc739b819e5c4bd8079db992b7edbe4cbcf4e2/tests/test_check_entry_proxy.py#L41>)在 202、冻结 headers、真实 proxy 路径及页面 content ID 断言之后，等待指定 manual run 成为 completed/partial current 超过原 20s。不是 header 转发断言失败。 |
| [PR 41989935454](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41989935454>) | **1397 passed / 24 skipped / 1 failed，772.47s** | [固定源 standalone resize 用例](<https://github.com/jinghonh/LaTeX-Review/blob/d2fc739b819e5c4bd8079db992b7edbe4cbcf4e2/tests/test_reading_position.py#L85>)字符位置误差 **9743.559203655352px**，要求 **<3px**。尚未解决，不能因本机通过称其 flake。 |

每轮共 1422 用例，24 skip 沿用可选 TeX/PDF 工具依赖及两个 compare report-only 参数；不等于零跳过。两个 workflow 不能再描述成“只有保存门禁失败”，verify job 也失败；物理机全绿不覆盖这些失败。

### 针对性调查的证明边界

父代理报告，受控真实 UI/proxy 探测证明同 content ID 下自动任务 B 可替换 manual A：A stale、B completed、界面正常。后续仅在[manual header 测试夹具](<../../../../tests/test_check_entry_proxy.py#L17-L45>)显式配置 `on_save=false`，隔离该测试的 manual-ID 契约；原 20s、精确 header/proxy/content ID 与 run-ID 断言均保留，相关选择 **26 passed / 15.02s**。它证明一种机制与修正后的夹具，不证明原 CI 的精确任务排序或新完整 CI 已通过；应用源 aggregate 未变。

standalone 调查仅有一次有界 Linux Chromium **155** 探测（不是 CI 156）：原三次 150ms 误差约 0.1394/0.3763/0.5656px，稳定后还是同一 Range，观察到 paper→window→paper scroll root 转换。不能据此确定托管失败原因或称已修好。当前[失败诊断断言](<../../../../tests/test_reading_position.py#L103-L137>)仅在原误差失败后发额外 RPC，保留所有原等待、Range 和 <3px；通过路径无额外 RPC。相关 **4 passed / 2 skipped / 16.94s** 不是原 macOS 失败销项，新 CI 失败时的 trace 仍待收集。父代理确认产品 JS/CSS 未改、standalone 不走三处 receipt Python 改动；没有新增生产 UI 修复或新配对截图声明。

## 托管保存性能：四组 N=10 均 FAIL ≤2s

| 工作流 / 模式 | 原始公开 JSON | median (s) | p95 (s) | 第一保存 (s) | 最大值所在次序 |
| --- | --- | ---: | ---: | ---: | ---: |
| push / comment | [JSON](<macos-saved-d2fc739-push-comment.json>) | 4.4347147915 | **5.278743875** | 4.802216 | 2 |
| push / prose | [JSON](<macos-saved-d2fc739-push-prose.json>) | 3.049801938 | **3.358164417** | 3.073136 | 8 |
| PR / comment | [JSON](<macos-saved-d2fc739-pr-comment.json>) | 3.452289687 | **4.710165584** | 4.710166 | 1 |
| PR / prose | [JSON](<macos-saved-d2fc739-pr-prose.json>) | 3.127583813 | **3.480522750** | 3.423659 | 2 |

四组 complete=true，全部 40 保存 authoritative/completed/non-stale、source paths changed 为空，120 文件边界不变，entry exit 0 / entry exited / worker exited / endpoint removed。每组 prose 10/10 真实已发布改词与 5000 units 证明，成本含在计时内。40 文件 / 5000 单元、默认 800ms、observer 0.5s 加实际工作、完整监听/渲染/发布/通知/队列/检查/HTTP/current/export 边界和全部首个/慢样本均保留。没有浏览器 paint，也不把不同平台或组拼成一个分布。

## 物理 Mac：HUMAN-REPORTED，复验已履行，两个 p95 仍失败

来源：[请求人公开反馈](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6653186690>)。固定应用 `466a321`、macOS 27.2 arm64 / Python 3.11.14 / Playwright 1.64.1 / Chromium 156，完整历史独立副本；verify exit 0，**1420 passed / 2 report-only skipped / 0 failed / 1422 total**，脚本耗时 695.61s（父代理复核原值 695.610144138s）。CLI/core 门槛通过；无 browser/history 缺失跳过。上面两个托管失败用例在本轮物理机全套通过，但不是它们的根因或 flake 证明。

| 模式 | 全部十次秒数（公开反馈，展示舍入至 6 位，原顺序） | median (s，公开原值) | 最近秩 p95 (s，公开原值) | ≤2s |
| --- | --- | ---: | ---: | --- |
| comment | 1.928245, 2.026850, 1.927823, 1.865275, 1.934771, 1.957280, 1.948012, 2.021934, 1.926940, 1.946652 | 1.9407115414796863 | **2.02685012499569** | FAIL |
| prose | 1.948669, 1.975740, 1.979535, 1.917431, 1.965994, 2.019279, 1.944244, 1.884537, 1.960362, 1.970680 | 1.963177708501462 | **2.019279333006125** | FAIL |

**2.02685012499569 与 2.019279333006125 都严格 >2s，不能舍入成通过。** comment 最大值是第二次，prose 是第六次；第一保存 1.9282454999629408 / 1.9486694170045666s 也保留。两组各仅执行一次，回归结束后独立顺序运行；其他用户负载未控制，不挑选绿结果。

公开反馈确认全部 20 保存 authoritative、两组 complete、source120 起止 de00… 一致/无改动、全部拥有的服务与 endpoint 清理成功、prose 10/10 实际可见正文及 5000 units。父代理独立核对十一份私下产物的 SHA 清单、JUnit 与完整证明。本台账代理仅读取公开反馈和公开 CI/基准数据，**不读取、复制、公开或链接任何私下原始文件、私人路径或逐文件 hash 清单**。

本轮 Mac 复验与注意力请求已履行，继续可行性能/回归修复，不再等待同一复验或真实论文/预算。较[7744314 旧基线](<macos-7744314-baseline.md>)的测得值低不证明单项优化的因果收益，也不能用本机通过覆盖托管失败。新固定 SHA 须在实际相关验证后发布；下一轮 import-laziness/whitespace 实施尚未验证或 N=10 重测，不享有当前数据结论。

## 同源 Linux 与诊断

[Linux comment N=10](<linux-saved-performance-optimized7-comment.json>) median 4.087779192952439s / p95 **4.7473551540169865s**，[prose N=10](<linux-saved-performance-optimized7-prose.json>) median 4.002629752038047s / p95 **4.444787323009223s**，均 FAIL ≤2s；最大值在第 4 / 5 次，第一保存 4.060014976 / 3.973113607s 均保留。全部证明/清理通过，正文证明成本含在 timer 内，median 0.062742495s、range 0.051231908–0.116048791s。

[light7b 分析](<linux-saved-pipeline-light7b-analysis.json#L1-L20>)、[GC 摘要](<linux-saved-pipeline-light7b-gc-summary.json>)与[原始诊断归档](<linux-saved-pipeline-light7b-raw.zip>)只用于真实链路 CPU/回执观测，不是 N=10/p95。无 cProfile/tracemalloc，嵌套 CPU/GC 时间重叠不可求和；GC 触发阶段不是对象分配来源证明，ru_maxrss 是累计峰值而非实时 heap/leak。保留[首次失败诊断 JSON](<linux-saved-pipeline-light7-comment.json>)及[原始归档](<linux-saved-pipeline-light7-attempt1-raw.zip>)：1 个权威保存后 RuntimeError 和原 180s 超时，疑似外部 GC 聚合器迭代，不当作已证明的产品根因。observer-only 修正后的 comment/prose 各 2 保存权威/清理/来源证明通过，不以这 2+2 插桩时间代替完整 N=10。
