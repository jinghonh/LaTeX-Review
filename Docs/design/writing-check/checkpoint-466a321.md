# 固定复验检查点：466a321（未完成交付）

固定应用源码：`466a3213230dc59fc6405871eeb3a84c47a2d5bd`。累计 Draft PR #56 的性能修复检查点；**不是 ≤2s 通过或完整交付声明**。

## 本次变化与证据边界

前一固定源码的实际双进程诊断确认：保存通知重复读取并完整校验同一份 5000 单元 manifest 两次，新版本状态读取、导出还重复校验。诊断有插桩开销，不是验收 p95，也不能相加嵌套阶段。

本次只复用“这些完全相同的字节已通过纯结构校验”的有限内存回执：

- 每次仍读取实际文件字节并计算 SHA-256，仍新解析完整 JSON 和全部嵌套对象；不缓存可变正文对象、SourceMap、权限、当前状态或来源事实。
- 仅原版纯谓词、解析器及辅助函数命名空间可命中；不同/被修改的谓词或解析器完整执行，失败不进入回执，LRU 上限 128。
- 生产者保留实际写出的同一 JSON 文本，在原来的页面 metadata 校验位置验证其确切 UTF-8 字节；不是用可变对象的旧校验结果预热。公开 `validate_content` / `content_meta` 函数体不变，原 HTML metadata 字节一致。
- 原始来源、页面、配置、知识、引擎版本、所有权、代次和写入权限围栏不变。没有提早发布、移除检查、缩短默认 800ms 静默期限、改动 0.5s 观察参数或测量起止边界。

**273 passed / 46.42s / 0 skipped**，覆盖 18 个相关模块：72 项新回执场景、原状态身份测试、真实 HTTP/来源/页面/批注写入/原生阅读/生命周期和夹具。包含无源码变更的独立审阅、真实 Unicode/公式构建的实际写入字节与 metadata 一致性、嵌套对象隔离、同尺寸/同 mtime 篡改、未知或修复型谓词、解析器自恢复以及完整验证回退。不同阶段的重叠测试数不相加。

固定包和两份基准脚本共 120 文件聚合 SHA-256：`de00f087600364452c93faee440e9ece9435fb19b99c1d3fd91799e6877d560b`。

此版本完整全套和新的完整保存 N10 尚待执行；回执次数/旧插桩时间不代替验收结果。

## 复验步骤

使用完整 Git 历史的隔离副本，不覆盖现有工作树改动。激活独立 Python 3.11 环境，并重新安装这个固定源码。无需模型、密钥或私有论文。

```sh
git fetch origin
git switch --detach 466a3213230dc59fc6405871eeb3a84c47a2d5bd
git diff --exit-code HEAD
python -m pip install '.[test]'
python -m playwright install chromium
evidence=$(mktemp -d "${TMPDIR:-/tmp}/writing-check-466a321.XXXXXX")
python --version > "$evidence/python.txt"
python -m pip freeze > "$evidence/dependencies.txt"
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-macos.sh > "$evidence/verify.log" 2>&1
printf '%s\n' "$?" > "$evidence/verify.exit-code"
```

完整回归结束、其拥有的临时进程停止后，独立顺序执行原两组：

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

若一组未完成或清理失败，先检查并停止它自己拥有的服务，再开始另一组。保留全部首个/慢样本和负载、HTTP、阶段观测；正文仍需实际可见文本改变与发布 5000 单元证明。不要为了得到绿色结果重跑筛选样本或缩短边界。原始产物可继续私下保留/回传，不公开密钥、私人路径或论文原文。

## 前一检查点与未完成项

请求人 [7744314 本机结果](https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6652141178)：**1348 passed / 2 report-only skipped / 0 failed，790.51s**；完整 comment N10 p95 **2.053136208s**（仍大于 2，不能四舍五入为通过），prose **5.056157625s**，均未通过。其他用户负载未控制，不从跨轮变化推断单项优化的因果收益。完整独立记录见[7744314 基线](<evidence/macos-7744314-baseline.md>)；旧 Linux/CI/本机数据不改写。

下一步仍是完整保存链路验收，不等待真实论文或付费模型资源；后者及作者质量评价只作为独立待办，绝不默认调用付费服务。本说明不改变完整 P0/P1、M0–M5 与 24 项验收的总交付边界。
