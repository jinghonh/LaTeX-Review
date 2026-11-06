# 42af9d9 首轮完整测试记录

本记录固定首个实现检查点；追加复验及 PR 当前交付状态见 [PR #58](https://github.com/jinghonh/LaTeX-Review/pull/58)。每轮原始结果均保留，失败样本不被后来的通过覆盖。

- Before：`702896fd22bc25c9ef016badde6265eedda68114`，原始测试与产品。
- After：`42af9d9dd0966d1fdd2aa341a94a56d9637d210c`，测试资源复用与新增验证；产品 `src` tree 两侧均为 `cb034b28b3917b6196111bfe0e668fbb682dab89`。
- 两侧同一 [measure-tests.py](https://github.com/jinghonh/LaTeX-Review/blob/42af9d9dd0966d1fdd2aa341a94a56d9637d210c/scripts/measure-tests.py)，SHA-256 `ebf6125295af97b5c73afbafe7ccecdea61ffca1643252a439bcf620b8f8cbe9`。
- 每组在同一主机、同一 Python/依赖环境中顺序运行 before、after。未同时启动其他重型测试或基准；没有额外 warm-up、丢弃首样本或选取最快样本。不同主机的数值不互相比降幅。

## 完整配对结果

墙钟包含完整 pytest、原有五项 CLI smoke 与进程审计；不含安装依赖/浏览器。pytest 自身计时及每例 setup/call/teardown 另存。浏览器均为 Playwright 1.64.1、Chromium 156.0.8078.4（revision 1248）；两组 Linux 使用 Python 3.11.17，macOS 配对使用 Python 3.11.9。

| 同机配对环境 | Before 结果 | After 结果 | Before 整体墙钟 | After 整体墙钟 | 有效降幅 |
| --- | --- | --- | ---: | ---: | ---: |
| 本机 Linux 6.8 x86_64 / 2 CPU | 1562 passed / 19 skipped | 1593 passed / 19 skipped | 709.088342s | 630.915962s | **11.024350%** |
| 托管 Linux 7.0 Azure x86_64 / 4 CPU | 1556 passed / 25 skipped | 1587 passed / 25 skipped | 537.971140s | 490.128718s | **8.893120%** |
| 托管 macOS 26.6.2 arm64 / 3 CPU，attempt 1 | **1 failed** / 1556 passed / 24 skipped | 1588 passed / 24 skipped | 612.411648s | 584.495871s | **无有效比较** |

本机 pytest wall 为 707.039431s → 628.393917s；托管 Linux 为 536.265293s → 488.453806s。macOS attempt 1 为 610.868807s → 582.942888s，但 baseline 失败，因此比较工具返回非零、`comparison_valid=false`、`reduction_percent=null`。不能将其原始墙钟差称为提速。

两组有效 Linux 比较均有完整 **1581 → 1612 collected**，原测试 nodeid 及重数全部保留，新增 31 项；`missing=[]`、`regressions=[]`、`environment_mismatches={}`。新增 2 项真实浏览器资源测试验证 module/function 两种生命周期、隐式独立上下文、显式多标签共享，以及准备失败和执行失败后的清理；新增 29 项测量工具测试验证缺项、扩大跳过、环境不一致、失败和重复 nodeid 等情况不会产生有效提速声明。原测试只调整 fixture，断言和工作量未缩减。

## 主要模块与阶段

模块时间均为同组全部用例 setup + call + teardown；完整所有模块与逐例 phase 见原始 JSON，不只保留改善项。

| 模块 | 本机 Before | 本机 After | 托管 Linux Before | 托管 Linux After |
| --- | ---: | ---: | ---: | ---: |
| publication_browser | 54.941s | 31.314s | 39.514s | 24.077s |
| report_annotations_browser | 50.864s | 33.505s | 37.464s | 28.269s |
| annotation_delivery_browser | 38.656s | 29.333s | 28.282s | 20.731s |
| report_reading | 24.378s | 14.696s | 16.968s | 9.406s |
| local_service_acceptance | 96.672s | 92.965s | 73.084s | 70.989s |
| translation | 44.619s | 43.007s | 38.022s | 38.253s |
| file_events | 26.864s | 27.541s | 26.937s | 26.835s |

| 阶段总和 | 本机 Before | 本机 After | 托管 Linux Before | 托管 Linux After |
| --- | ---: | ---: | ---: | ---: |
| setup | 123.242069s | 60.883642s | 83.778725s | 42.331893s |
| call | 518.859825s | 510.624176s | 392.423587s | 389.267164s |
| teardown | 57.088549s | 53.797824s | 53.971598s | 51.475255s |

主要收益来自准备阶段。文件监听与服务生命周期仍占较大 call 时间；本改动没有缩短其观察/防抖窗口，也不将其他模块的自然变化归因于浏览器复用。

## macOS 失败与独立功能验证

[成对工作流 attempt 1](https://github.com/jinghonh/LaTeX-Review/actions/runs/42009625331/attempts/1) 的原始 baseline 在 `test_resize_burst_retains_character_and_control_focus[native-burst]` 失败：原 150ms 窗口后同一字符误差 **769px**，原阈值 **<3px**。这是未优化版本的实际失败；没有据此断言它是 flake、浏览器不可用或新 fixture 所致，也没有改产品脚本、观察窗口或阈值。完整失败 stdout、JUnit、phase JSON 和 after 通过均保留在首轮原始包中；后续完整复验记录单列在 PR，不抹去此失败。

原 macOS 功能入口另外执行两次，均 1588 passed / 24 skipped、五项 CLI smoke 成功、functional-only 明确未运行性能基准。两次 checkout 来源分别为：

- [push 42009625173](https://github.com/jinghonh/LaTeX-Review/actions/runs/42009625173)：精确 After `42af9d9`，pytest **601.64s**。
- [PR 42009750605](https://github.com/jinghonh/LaTeX-Review/actions/runs/42009750605)：合并验证 checkout `4db58b4db94262a560f8790989e4e33ef5393ff7`，将 `42af9d9` 与 base `93c2503fa74cc26ccb289c6ac1e3d44677360da2` 合并；pytest **711.81s**。base 的新增改动只有独立 UI 探针的临时路径 canonicalize 与相关文档，这次执行提供其兼容性验证，不冒称精确 `42af9d9` checkout。

这是两个不同 runner，110s 差异说明不能拿跨主机的最快结果作为收益。它们证明功能执行通过，不覆盖首轮 baseline 失败，也不能代替一组有效同机配对。

## 隔离、进程与限制

本机两侧分别观察 1824 / 1265 个后代，托管 Linux 为 1751 / 1183，首轮 macOS 为 1414 / 1013。各轮均无被观察到的存活应用后代，`audit_errors=[]`；CPython resource tracker 按精确命令单列并保留 PID、创建时间与 argv。0.2s 观察粒度的限制见[测量说明](../test-runtime.md)。没有扫描或停止用户常驻服务，也没有在其他人的工作副本运行测试。

本机 Linux 原 19 项 skip、托管 Linux 原 25 项 skip、macOS 原 24 项 skip 均保持原条件。托管环境无完整 TeX/Poppler，因此不能声称覆盖物理 Mac 配置齐备时的全部编译/沙箱场景。浏览器和服务生命周期用例未因本优化新增跳过。请求人可按[固定检查点与复验命令](../test-runtime.md)在本机继续执行完整适用验收；不需要私人论文或付费模型。

## 原始材料与命令

- [最初选瓶颈用的完整 baseline 与相关回归原始包](evidence/linux-exploratory.zip)：保留最初 pytest 700.31s / 外部 701.68s 及原插件，未混作后来的完整验收墙钟。
- [同机 Linux 完整配对原始包](evidence/linux-paired-42af9d9.zip)：before/after console、JUnit、timings JSON、comparison、依赖冻结与驱动日志。
- [托管首轮 Linux/macOS 原始包（含失败）](evidence/ci-attempt1-42af9d9.zip)：完整下载的 artifacts、依赖冻结与功能/成对 CI 日志。
- [托管首轮源工作流](https://github.com/jinghonh/LaTeX-Review/actions/runs/42009625331/attempts/1)。后续 attempt 的 artifact 名带 run id/attempt，不复用首轮输出目录。

同一个 runner 脚本执行的命令均为：

```sh
# cwd = 固定 Before 工作副本，使用与 After 共用的虚拟环境。
LATEX_REVIEW_REQUIRE_BROWSER=1 LATEX_REVIEW_FUNCTIONAL_ONLY=1 \
  python3 /absolute/after/scripts/measure-tests.py --output /absolute/evidence/before
# cwd = 固定 After 工作副本；默认无 pytest 过滤参数。
LATEX_REVIEW_REQUIRE_BROWSER=1 LATEX_REVIEW_FUNCTIONAL_ONLY=1 \
  python3 scripts/measure-tests.py --output /absolute/evidence/after
python3 scripts/compare-test-runs.py \
  /absolute/evidence/before/timings.json /absolute/evidence/after/timings.json
```

JSON 同时保存实际展开的完整命令、解释器路径、SHA、pytest 参数、平台、工具、插件环境、依赖和全部 nodeid 清单。所有样本都保留；新增样本不替换这里的数值。本票未恢复或测量 save→verified-current，原 **≤2s 未达标**记录不变。
