# 固定复验检查点：b742583（未完成交付）

固定应用源码：`b742583254a69dd7103be07b4e5f110f3c4c2c5e`。累计 Draft PR #56 的局部性能修复检查点；**不是 ≤2s 通过或完整交付声明**。

## 已证实的工作与本次改动

前一固定源码的真实双进程轻量诊断中，Linux 预览构建线程 CPU 为 2.130–2.357s，包含渲染 0.400–0.424s、翻译清单 0.269–0.380s；入口执行另耗 0.775–0.870s，主要是独立捕获/解析。它们包含观察开销、嵌套阶段不能相加，**不是完整保存 p95**。详见[保留的诊断](<evidence/linux-saved-pipeline-light7b-analysis.json>)与[前一版本基线](<evidence/macos-466a321-baseline.md>)。

本次保持完整生成与检查，仅减少两处可证明等价的工作：

- 普通正文的翻译单元不再重复导入未使用的预览、标题/图注辅助模块；非普通文本保留原渲染器绑定、上下文、错误与诊断，渲染仍获得新列表和即时来源。
- 段落/图注的空白比较对原生字符串使用等价的 split/join，非标准输入保留旧正则路径。完整 canonical 渲染、比较、输出 HTML、句子锚点和诊断均保留，不跳过生成、不新增数据缓存。
- 手动代理 header 测试显式关闭无关的自动保存任务，保留真实点击、代理、全部绑定/拒绝断言及 20s 期限。受控真实代理实验验证自动任务可替代手动任务的机制，但不声称复原了历史 CI 的精确排序。
- 翻译清单测试仅在状态文件暂缺时于原 8s 期限内重试；实际目录晋升及状态随后写入的确定性交错验证了两个暂缺窗口，格式错误仍抛出。不是修改产品发布或缩短边界。
- 阅读恢复测试只增加失败后的几何/滚动/恢复状态诊断，原 150/100ms 等待、保存字符、视口序列和严格 <3px 不变。旧托管 standalone 9743.559px 失败仍未查明，**不因本机通过或新增诊断而宣称修复/flake**。

联合验证：**440 passed / 2 report-only skipped / 92.08s，18 个模块**。包含 117 项新翻译导入/独立旧实现 oracle/回调上下文场景、32 项全 Unicode/随机/原生与差异 HTML 字节等价场景、真实代理、原生阅读、来源/版本、批注、完整回执和确定性晋升回归。独立代码审阅无已知发现；不同选择的重叠测试数不相加。

固定包和两份原基准脚本共 120 文件聚合 SHA-256：`818b02326ecffdf402ac0387541de6ad921e1a051daad0f0e9a9d26ca3994a8e`。

新版本完整回归、原始边界 comment/prose N10 及托管 CI **待执行/收集**。小组件 CPU 与相同输出证明不代替验收；仍继续独立分析和修复，不等待再次确认。

## 隔离复验

使用含完整 Git 历史的独立副本，不覆盖现有工作树。激活独立 Python 3.11 环境并重新安装固定源码；无需模型、密钥或私人论文。

```sh
git fetch origin
git switch --detach b742583254a69dd7103be07b4e5f110f3c4c2c5e
git diff --exit-code HEAD
python -m pip install '.[test]'
python -m playwright install chromium
evidence=$(mktemp -d "${TMPDIR:-/tmp}/writing-check-b742583.XXXXXX")
python --version > "$evidence/python.txt"
python -m pip freeze > "$evidence/dependencies.txt"
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-macos.sh > "$evidence/verify.log" 2>&1
printf '%s\n' "$?" > "$evidence/verify.exit-code"
```

完整回归与其临时进程停止后，独立顺序执行两组原工作量和原计时边界：

```sh
for mode in comment prose; do
  python Docs/design/writing-check/evidence/benchmark_saved_checks.py \
    --files 40 --paragraphs 5000 --saves 10 --edit-kind "$mode" \
    --poll-seconds 0.5 --settle-timeout 90 \
    --concurrent-workload 'Full regression stopped; no concurrent test/benchmark intentionally started. Other user load is not controlled.' \
    --output "$evidence/saved-$mode.json" \
    > "$evidence/saved-$mode.log" 2>&1
  printf '%s\n' "$?" > "$evidence/saved-$mode.exit-code"
done
git diff --exit-code HEAD
printf 'Evidence directory: %s\n' "$evidence"
```

若一组未完成或清理失败，先检查并停止它自己拥有的服务，再开始另一组。保留全部首个/慢样本；正文仍需实际可见改词与 5000 单元发布证明。不要重跑择绿或改变默认 800ms、防抖/观察间隔、完整保存起止。私人原始文件可继续私下回传，不公开私人路径、密钥或论文内容。

前一请求人 [466a321 本机完整结果](https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6653186690)为 **1420 passed / 2 report-only skipped / 0 failed，695.61s**；comment/prose N10 p95 **2.02685012499569s / 2.019279333006125s**，均未达 ≤2s，不能四舍五入。不同负载下跨轮变化不证明单项优化因果收益，旧 Linux/CI/本机与失败样本全部保留。

真实论文、少量明确授权模型调用和作者质量评价仍独立待办，不阻塞其他实施；未授权不调用付费服务。整项 P0/P1、M0–M5、24 项验收边界不变。
