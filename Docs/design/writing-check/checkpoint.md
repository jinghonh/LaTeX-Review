# 可复现检查点：本机 macOS 与服务器继续实施

本检查点是 **未完成的开发版本**，不是 M5 或 AC-01–AC-24 全部通过的交付。实际提交 SHA 以 Issue #55 的检查点评论为准。服务器继续修复性能与界面，不等待真实论文或模型预算。最终源码改变后须另做必要的最终 macOS 验证。

## 隔离与依赖

- 在新的临时目录克隆仓库，不复用请求人的论文目录、常驻服务或已有虚拟环境；不需要 SSH。
- Python >=3.11（服务器及 CI 使用 3.11）、pip、Git、Chromium；浏览器由 Playwright 安装。Node.js 用于可选 JavaScript 语法检查。
- `.[test]` 安装 jsonschema、plasTeX、watchfiles、pytest、Playwright 和 psutil。合成检查、浏览器、离线语义协议测试不需要 TeX 编译器或模型凭据。
- 保留 Git 历史：旧报告兼容测试需要固定基线 `fe82620af8bbbb028809d59ceeaaad7085427880`，不可使用仅当前提交的浅克隆后把相关 skip 算作通过。
- 不上传私有论文，不设置付费模型凭据，不启动或替换常驻用户服务。测试及性能脚本使用自己的临时资源。

```sh
checkpoint_root=$(mktemp -d "${TMPDIR:-/tmp}/latex-review-writing-check.XXXXXX")
git clone --branch agent/writing-check-delivery-6277b05259b6 https://github.com/jinghonh/LaTeX-Review.git "${checkpoint_root:?}/repo"
cd "${checkpoint_root:?}/repo"
# 将下列占位符替换为 Issue #55 检查点评论中的完整 SHA。
git checkout --detach CHECKPOINT_SHA
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m playwright install chromium
```

## 执行命令与预期

先记录 `git rev-parse HEAD`、`sw_vers`、`python --version` 和浏览器版本。输出保存在临时目录；回传 Issue 时仅提供合成测试结果，不贴令牌、凭据或私有内容。

```sh
# 安装后 CLI 冒烟、全仓库测试及核心性能断言。
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-macos.sh

# 分开保存完整回归的 JUnit 与跳过原因；上条已跑全套时无需重复。
LATEX_REVIEW_REQUIRE_BROWSER=1 python -m pytest -q -ra --junitxml=/tmp/writing-check-macos.xml

# 写作专项；上条已包含时可仅用来定位失败。
LATEX_REVIEW_REQUIRE_BROWSER=1 python -m pytest -q tests/test_check*.py

# 真实保存→当前报告：独立执行，其他测试/编译任务结束后再测。
python Docs/design/writing-check/evidence/benchmark_saved_checks.py --files 40 --paragraphs 5000 --saves 10 --settle-timeout 90 --output /tmp/writing-check-saved-macos.json

# 可选静态语法检查。
node --check src/latex_review/review_workspace.js
```

预期：确定性核心冷检查 <=10 秒；**真实保存链路热检查 p95 <=2 秒**。核心性能脚本通过不代表保存链路通过。[完整测量边界](<evidence/saved-check-latency.md>)包括原生监听、预览重建与发布、默认防抖、异步协调器及实际 HTTP 当前结果验证；只排除 TeX 编译和外部模型网络调用。十次顺序保存采用最近秩 p95；若中断或超时，保留 `complete: false` 和实际样本，不能宣称完成十次或达标。

离线语义测试验证请求/响应 schema、精确引文、过期/伪造拒绝、候选状态、拒绝抑制及预算/取消边界。它们不发出真实付费模型调用，也不代替真实模型质量或作者评价。

## 本检查点已知状态

- 先前完整 Linux 回归为 **1098 passed / 14 failed / 19 skipped**；其后 13 项功能回归修复的 59 项专项、另 5 项导航/阅读恢复测试通过，不能据此把完整最终回归写成通过。
- 翻译停止后的端口重绑定是原基线的异步清理竞态；测试夹具只为 `EADDRINUSE` 增加有界等待，保留原 CLI 1.5 秒退出和共享服务继续可用断言，修改后 8/8 通过。
- 最新共享导航预留高度实现已写入，但新增真实布局测试尚有夹具错误待修复：diff 的批注关闭应点实际可见的“关闭”按钮，live 的临时输出目录须先创建。该轮 2 个导航测试通过、3 个布局失败、3 个 setup error；不应称为布局验收完成。
- 先前实际 diff 捕获存在侧栏遮挡标签；当前 After 仍是明确标注的部分/混合证据，不是最终配对截图。修复后会重新捕获。
- 首个真实保存样本 **77.208 秒，N=1**，显著不满足 <=2 秒目标；未声称 p95。当前继续剖析/修复完整渲染与 HTTP 验证开销。[原始诊断](<evidence/linux-saved-performance.json>)明确不完整。
- 真实论文、获准模型配置/预算及真实作者质量评价单独待资源；**不阻塞**上述实施、Linux 测试或本机 macOS 测试。

测试结果、源码版本和局限继续维护在[验收台账](<verification.md>)与[总体验收记录](<acceptance.md>)。此检查点的本机结果仅覆盖该 SHA；后续源码变动需要最终复验。
