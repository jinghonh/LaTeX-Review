# d120858 增量分片结果

本轮以 [0540b03](https://github.com/jinghonh/LaTeX-Review/commit/0540b03c67e752d83547bd75411a3fedb563f44c) 的完整结果为增量背景，测试实现检查点为 `d12085820e904d2fc61523b8acc89766048a0bf1`。本次只新增显式执行分片、覆盖合并和嵌套测量边界；产品代码、原测试断言、工作量、观察窗口和保存性能判定均未修改。

## 实际配对

两组均在同一 runner 上先运行 Before `702896fd22bc25c9ef016badde6265eedda68114`，再运行 After `d120858`，共享 Python、依赖和 Chromium。完整 nodeid 由独立 collection manifest 生成，serial/pure lane 合并后按 `Counter` 校验；全部原始 nodeid 与参数重数为 **1581 → 1612**，`missing=[]`、`unexpected=[]`、`regressions=[]`，五项 CLI smoke 全部成功。

| 环境 | Before → After | 完整墙钟 | pytest wall | 有效降幅 | 执行模式 |
| --- | --- | ---: | ---: | ---: | --- |
| Linux 7.0 Azure x86_64 / 4 CPU / Python 3.11.17 | 1556 passed / 25 skipped → 1587 / 25 | 512.966371s → 462.959450s | 505.616792s → 455.914889s | **9.748577%** | serial + pure 两 lane |
| macOS 26.6.2 arm64 / 3 CPU / Python 3.11.9 | 1557 passed / 24 skipped → 1588 / 24 | 694.895967s → 674.458071s | 692.785112s → 672.433626s | **2.941145%** | 自动串行回退 |

macOS 这一轮识别为 3 CPU，低于并行阈值，因此没有启动 pure worker；这保留浏览器、服务、监听和计时敏感场景的串行隔离。Linux 的 pure lane 仅使用私有 `tmp_path`、不启动浏览器/原生 watcher/常驻服务且没有固定外部产物的明确 allowlist；所有新文件默认进入 serial lane。

Linux 阶段合计为 setup **82.335284s → 41.272893s**、call **386.445502s → 382.962738s**、teardown **53.381299s → 49.807350s**。MacOS 阶段合计为 setup **100.106789s → 55.459488s**、call **528.793615s → 556.771383s**、teardown **57.585413s → 54.856857s**；Mac call 阶段波动抵消了部分准备收益，因此保留实际2.94%结果，不将它包装成更高收益。

两组 comparison 均 `comparison_valid=true`，环境差异为空，exitstatus均为0。Linux serial/pure 两个子进程均无存活应用后代、无审计错误；Mac串行进程同样无存活应用后代、无审计错误。完整 JSON、JUnit、console、collection manifest、每 lane timings 和依赖冻结保存在 workflow artifact：

- [Linux/macOS d120858 workflow](https://github.com/jinghonh/LaTeX-Review/actions/runs/42039434080)
- Linux artifact：`test-runtime-ubuntu-latest-42039434080-1`
- macOS artifact：`test-runtime-macos-latest-42039434080-1`

两项 macOS 功能验证也通过，均为 1588 passed / 24 skipped；两个 `saved_current` 按既有停止指令 skipped，不计作性能通过。完整第一轮证据与首轮 Mac baseline 失败仍保留在 [42af9d9 记录](results-42af9d9.md)，没有用本轮结果覆盖历史样本。
