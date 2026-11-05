# macOS f563199 完整保存链路基线：未通过

固定源码 `f563199ded69bddf58ea39501cdf796d60fa5fe6`，完整保存链路包含原生监听、完整预览发布、默认 800ms 静默期限、异步通知、队列/检查、本机 HTTP 状态与导出验证；prose 还包含实际可见文本和发布 5000 单元证明。每组 40 文件、5000 单元、10 次保存、观察间隔 0.5s。全部首个及慢样本保留；最近秩 p95 为十次中的最大值。

## GitHub 托管 macOS

平台 macOS 26.6.2 arm64、Python 3.11.9、3 个逻辑 CPU。保存基准在独立 runner 中运行，没有同 runner 的浏览器/完整测试任务；不声称物理硬件独占。四组源码起止 120 文件聚合均为 `9b6ac042d72f1111213495d55abf9b70d0e273e2d0c1ffa5ef39a023a0f1e20c`，无测量中源码变化。

| 工作流/组 | 原始数据 | median (s) | p95 (s) | ≤2s |
| --- | --- | ---: | ---: | --- |
| [push 41979231225](https://github.com/jinghonh/LaTeX-Review/actions/runs/41979231225), comment | [完整 JSON](<macos-saved-f563199-push-comment.json>) | 3.097566 | 3.349016 | FAIL |
| push, prose | [完整 JSON](<macos-saved-f563199-push-prose.json>) | 3.019405 | 3.357771 | FAIL |
| [PR 41979237493](https://github.com/jinghonh/LaTeX-Review/actions/runs/41979237493), comment | [完整 JSON](<macos-saved-f563199-pr-comment.json>) | 3.243968 | 4.793702 | FAIL |
| PR, prose | [完整 JSON](<macos-saved-f563199-pr-prose.json>) | 3.471149 | 3.767862 | FAIL |

四组均 complete=true、10/10 authoritative match、源码边界一致、owned entry/worker 退出且隔离 endpoint 移除；两组 prose 均 10/10 证明实际可见文本改变及 5000 单元。PR comment 的最大值不是首个样本，不得删除或归为启动。成功运行没有启动诊断字段。

同固定源码的完整功能 job 均通过：push **1276 passed / 24 skipped / 648.64s**，PR **1276 passed / 24 skipped / 657.23s**，总数均 1300；跳过是原有 TeX/PDF 工具依赖和 report-only 比较场景，不是浏览器缺失。工作流整体失败仅因保存性能门禁；功能通过不能替代性能通过。

此前 f3 的两组 macOS 入口启动失败记录仍保留为 [push N=0](<macos-saved-f3dd5ad-push-incomplete.json>)、[PR N=0](<macos-saved-f3dd5ad-pr-incomplete.json>)，不伪造其 p95。私有夹具物理路径规范化后本轮四组均实际完成，但旧失败没有保留 stderr，不能倒填旧日志或断言排除其他因素。

## 请求人本机 macOS

来源：[请求人公开验收反馈](https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6651636068)。完整 Git 历史隔离副本、macOS 27.2 arm64、Python 3.11.14、Playwright 1.64.1 / Chromium 156；完整验证退出 0，**1298 passed / 2 skipped / 0 failed，741.09s**。两项跳过仅为 report-only 比较场景。旧报告侧栏遮挡用例包含在这个集合内。

| 组 | 所有十次保存 → verified-current 秒数（公开反馈的舍入值） | median，原始值计算 (s) | p95，原始值 (s) | ≤2s |
| --- | --- | ---: | ---: | --- |
| comment | 2.3826, 2.2624, 2.0115, 2.6461, 3.1459, 2.2754, 4.5054, 3.6596, 5.7120, 6.1119 | 2.896022 | 6.111938 | FAIL |
| prose | 3.0598, 35.0290, 22.3828, 6.8779, 4.6374, 5.5156, 2.9792, 4.2250, 5.1650, 8.4846 | 5.340305 | 35.028981 | FAIL |

本机两组均 complete=true、10/10 权威匹配、prose 10/10 可见文本改变与 5000 单元证明、源码边界相同且全部拥有的服务清理成功。先结束完整回归，再独立顺序运行两组；没有并行启动本次测试或基准，其他用户负载未控制。不能仅凭总时长把慢样本归因于外部负载或某个内部阶段。

请求人私下提供的八份产物逐项 SHA-256 核对通过，JUnit 独立核对为 1300 总数、1298 通过、2 跳过、0 失败/错误；两组原始 JSON 重新核算 p95、全部权威/来源/清理及可见文本证明。私下原始 JUnit、日志、JSON 和本机路径未复制到公开仓库；此处仅列公开反馈事实与经核对的汇总，不公开凭证或私人路径。

所有 macOS 数据与原 [Linux 历史测量](<saved-check-latency.md>) 分开保留，不跨机器拼接样本、不丢弃慢样本。**完整保存 p95 ≤2s 的目标在上述所有组上仍未通过。** 下一次固定代码检查点与复验步骤见[7744314 复验说明](<../checkpoint-7744314.md>)，不表示新版本已达到目标。
