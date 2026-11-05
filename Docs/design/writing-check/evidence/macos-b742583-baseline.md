# b742583 最后已完成基线：请求人决定不再优化，当前目标未达标

## 当前有效决定：停止性能改进

请求人在 **2026-11-05T09:18:35Z** 的[明确指示](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6654282319>)为“不继续改进性能问题了”，覆盖此前全部持续性能优化要求。性能项的准确状态为：**“请求人决定不再优化，当前目标未达标”**。

不再启动性能代码、设计、剖析或为达到 2s 追加基准；只有请求人未来明确解除此停止指示才可恢复，CI 线索、状态问题或一般继续工作不构成解除。保留已提交代码与全部证据，不回滚、不删除、不放宽 ≤2s，不记录 pass/waiver。剩余 UI 问题及现有改动的必要回归仍可继续，不合并、不部署。父代理已确认停止性能工作并收集/清理其拥有的任务和服务；本次仅整理停止前已经完成的数据，不进行新性能分析或测量。

## 固定源码与完整功能结果

应用 `b742583254a69dd7103be07b4e5f110f3c4c2c5e`，测量说明 HEAD `becf726dafeba0700ad41db1f0d342b636357e9d`，120 文件 package/helper 起止 aggregate 为 `818b02326ecffdf402ac0387541de6ad921e1a051daad0f0e9a9d26ca3994a8e`。[原检查点说明](<../checkpoint-b742583.md>)保留为历史复现资料，其新运行/继续性能请求受上述停止指示覆盖，不能照旧启动。pin 前联合相关选择 440 passed / 2 report-only skipped / 92.08s / 18 模块，不与全套重复相加。

| 平台 / 来源 | 完整功能结果 | 结论边界 |
| --- | --- | --- |
| Linux，父代理已收集 stdout/JUnit，本台账核对 | **1555 passed / 19 skipped / 0 errors / 0 failures，1574 total，679.28s** | 强制原生浏览器；17 项 macOS 专用编译/沙箱与 2 项 report-only skip。XML suite time 678.945s，与 stdout wall time 不混写。 |
| [托管 macOS PR 41998696314](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41998696314>) | **1550 passed / 24 skipped，1574 total，576.98s** | verify 通过，不代表保存门槛通过或其他 runner 的失败被豁免。 |
| [托管 macOS push 41998689097](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41998689097>) | **1549 passed / 24 skipped / 1 failed，1574 total，732.67s** | live 第一个 resize 1040×780，字符误差 **407.008938547486px vs <3px**，原等待 150ms 后实际失败；不是只有性能门禁失败。 |
| [请求人本机公开反馈](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6654121949>)，HUMAN-REPORTED | **1572 passed / 2 report-only skipped / 0 failed，1574 total，exit 0，591.34s** | macOS 27.2 arm64 / Python 3.11.14 / Playwright 1.64.1 / Chromium156；完整 Git bundle 独立副本、已跟踪源码无改动，CLI/core 门槛通过，不代表保存 p95 通过。 |

托管 24 skip 沿用 22 项可选 TeX/PDF 工具依赖与 2 项 report-only 比较，不称零跳过。物理机通过不证明托管失败原因或 flake；旧 d2 standalone **9743.559203655352px vs <3px** 也仍未解决。产品 JS/CSS 未变，不声称生产 UI 已修复或新配对截图已产生。

push 的 after-error context 被 pytest dict safe-repr 截断，不等于原数据缺失。后续未提交的[失败专用 JSON 输出](<../../../../tests/test_reading_position.py#L106-L125>)保留原误差与之后独立 RPC 的不同时间边界，不虚构原 browser clock；完整 JSON string 与 BEGIN/END stdout 标记经刻意失败验证，可保留嵌套尾部。原 <3px、150/100ms、Range、四个浏览器表达式及通过路径的一次 RPC 均不改。父代理报告该诊断选择 **4 passed / 2 skipped / 16.96s**；它不是原根因修复。UI 初始 RAF 调查仍为非性能工作，未获新证据前不报告修复。

## 停止前已完成的六组公开 N=10：全部 FAIL ≤2s

| 平台 / 模式 | 原始公开 JSON | median (s) | p95 (s) | 第一保存 (s) | 最大值次序 |
| --- | --- | ---: | ---: | ---: | ---: |
| Linux / comment | [JSON](<linux-saved-performance-optimized8-comment.json>) | 3.902824978926219 | **4.367912306915969** | 3.898366932 | 3 |
| Linux / prose | [JSON](<linux-saved-performance-optimized8-prose.json>) | 3.9108151890104637 | **4.080010120058432** | 3.756733019 | 7 |
| 托管 push / comment | [JSON](<macos-saved-becf726-push-comment.json>) | 3.802007146 | **4.470315208** | 3.800719667 | 10 |
| 托管 push / prose | [JSON](<macos-saved-becf726-push-prose.json>) | 3.6145699795 | **4.210134625** | 3.533413834 | 10 |
| 托管 PR / comment | [JSON](<macos-saved-becf726-pr-comment.json>) | 3.130443396 | **3.678342750** | 3.144769209 | 9 |
| 托管 PR / prose | [JSON](<macos-saved-becf726-pr-prose.json>) | 3.367913541 | **3.969962709** | 2.785689250 | 9 |

这些运行在停止指示前已完成，父代理已独立核对：每组 complete、10/10 authoritative/completed/non-stale、起止 120 文件 aggregate 一致/source paths changed 空；entry exit 0 / entry exited / worker exited / endpoint removed，prose 每组 10/10 真实普通单词发布与 5000 units 证明。Linux prose 证明耗时 min/median/max **0.056988237 / 0.065557529 / 0.102588256s** 全含在原 timer 内。本次只登记已有结果，不追加性能实验、内部 CPU 归因或选择性重跑。

原边界为 40 文件 / 5000 单元 / 1,479,521 初始字节，默认 800ms 静默期限、observer 0.5s 加真实 HTTP 工作，含原生监听、完整预览发布、通知/队列/检查、current/status/export；没有浏览器 paint。所有首个和慢样本保留，不把不同机器拼成 p95，不改 timer，不以 core/profile 替代失败的完整链路。

## 最后物理 Mac 数据：两组仍严格失败，不再优化

以下全部样本来自[停止前的公开本机反馈](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6654121949>)，中性原始证明复核由父代理完成，不访问私下反馈目录。

| 模式 | 公开十样本（秒，展示舍入 6 位，原顺序） | median (s，公开原值) | p95 (s，公开原值) | ≤2s |
| --- | --- | ---: | ---: | --- |
| comment | 1.909726, 1.952816, 1.936067, 1.931296, 1.968395, 1.884414, 1.898841, 2.013340, 1.924585, 1.909506 | 1.9279404584958684 | **2.013339917000849** | FAIL |
| prose | 1.940915, 1.889012, 1.913921, 1.942224, 1.942035, 2.005655, 1.915428, 1.960617, 1.973828, 1.964268 | 1.9421296460204758 | **2.0056546250125393** | FAIL |

最大值位于第 8 / 6 次；第一保存 **1.909726292011328 / 1.940915000042878s** 均保留。两组超标约 13.34ms / 5.65ms 仍失败，不四舍五入、豁免或放宽目标。每组只跑一次，回归结束后顺序独立运行；其他用户负载未控制，不从旧/新值差异推断因果提速。

公开反馈与父代理中性复核确认：全部 20 authoritative、两组 complete、source120 起止 818… 一致且无已跟踪源码变化、prose 10/10 真实已发布正文/5000 units、owned entry exit 0 与服务/endpoint 清理通过。父代理独立核对十二份私下产物 SHA 清单、1574 个唯一 JUnit 用例及完整证明。**本台账代理不读、复制、链接或公开私下文件、私人路径、raw JSON/JUnit 或逐文件 hash 清单**；此处仅公开反馈事实和中性汇总。

[466](<macos-466a321-baseline.md>)、[774](<macos-7744314-baseline.md>)、[f563](<macos-f563199-baseline.md>)、F3 及全部原始慢样本保持不变。此前公开反馈中的“继续剖析/下一固定 SHA”性能要求已被后来的停止指示覆盖。当前性能结论固定为 **“请求人决定不再优化，当前目标未达标”**；不阻塞获准 UI/必要回归，也不因论文/少量明确获准模型调用/作者质量独立待资源而恢复性能工作。不得合并或部署。
