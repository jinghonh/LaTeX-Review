# 完整测试耗时与复验

Issue [#57](https://github.com/jinghonh/LaTeX-Review/issues/57)。本改动只调整测试基础设施，不修改产品代码、断言、观察窗口、阅读位置条件或保存性能判定。当前应用基于 [PR #56](https://github.com/jinghonh/LaTeX-Review/pull/56) 的固定 `702896fd22bc25c9ef016badde6265eedda68114`；测试优化使用独立分支，以该累计分支为 PR base，不修改其活跃工作副本。

## 测量与选择

先在固定 `702896f`、Linux x86_64、2 CPU、Python 3.11.17、Playwright 1.64.1 / Chromium 156.0.8078.4 上运行原始完整测试。无其他重型测试同时运行；常驻用户服务未停止或修改。原始命令：

```sh
PATH="$PWD/.test-venv/bin:$PATH" PYTHONPATH="/tmp:$PWD/src" \
  LATEX_REVIEW_REQUIRE_BROWSER=1 \
  TEST_RUNTIME_OUTPUT=/tmp/test-runtime-57/linux-before.json \
  /usr/bin/time -o /tmp/test-runtime-57/linux-before.time \
  .test-venv/bin/python -m pytest -q -ra -p test_runtime_measure \
  --junitxml=/tmp/test-runtime-57/linux-before.xml
```

1581 collected，1562 passed / 19 skipped / 0 failed；pytest **700.31s**、外部进程墙钟 **701.68s**。setup **121.615s**、call **515.210s**、teardown **57.266s**。这是选瓶颈的首轮原始样本，不包含 CLI smoke，不能与请求人的历史 macOS 591.34s 直接比较。

| 原始主要模块 | setup + call + teardown（秒） |
| --- | ---: |
| local_service_acceptance | 93.336 |
| publication_browser | 53.509 |
| report_annotations_browser | 49.055 |
| translation | 43.299 |
| annotation_delivery_browser | 38.778 |
| file_events | 28.145 |
| report_reading | 27.214 |

仅三个可复用浏览器 fixture 所在的主要模块就有约 64.5s setup 成本。选择复用一个测试模块内的 Playwright/Chromium **进程**。每例仍创建独立上下文：隐式 `browser.new_page()` 每次独立，显式 `new_context()` 内的多标签按原语义共享。逐例 finalizer 关闭所有未显式关闭的上下文；模块结束关闭浏览器和 driver。准备或用例失败也清理上下文；批注页的上下文、服务器线程和监听套接字用 `finally`/上下文管理器收尾。

报告、源码目录、磁盘状态、服务令牌和端口保持逐例独立。未复用可变报告：其项目身份、来源、检查快照和重建配方包含真实路径，直接复制会串写来源。服务生命周期、进程所有权、文件监听与计时敏感测试保持原执行方式；HTTP 服务器轮询、防抖和所有固定观察窗口保持原值。

本轮加入显式分片入口 `scripts/run-test-shards.py`。`LATEX_REVIEW_TEST_SHARDS=auto` 在少于 4 个 CPU 的主机上保持一个完整串行进程；在至少 4 个 CPU 的主机上并行启动两个明确分组：包含浏览器、文件监听、服务生命周期、进程所有权和计时敏感场景的 serial lane，以及只包含私有 `tmp_path`、无浏览器/常驻服务/原生 watcher/固定外部产物的 pure lane。新文件默认进入 serial lane。启动前生成完整 collection manifest，合并后用 `Counter` 校验全部 nodeid 及参数重数；每个 lane 使用独立 evidence、配置、状态和端口命名空间，并保留各自进程清理审计。当前 2 CPU 本机自动回退串行；`LATEX_REVIEW_TEST_SHARDS=0` 是子进程和诊断的显式串行回退。没有引入 xdist，避免浏览器、监听器和本地服务在通用 worker 中争用资源。

## 回归与完整验收入口

先在独立虚拟环境安装本检查点与 Chromium：

```sh
python3 -m pip install -e '.[test]'
python3 -m playwright install chromium
```

开发反馈（覆盖所有采用共享浏览器 fixture 的消费者及失败清理测试，不能代替完整验收）：

```sh
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-tests.sh browser
```

Linux/macOS 完整功能验收，含完整 pytest 与原有五项 CLI smoke：

```sh
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-tests.sh full
```

macOS 保留原入口；按既有停止指令，必须使用 functional-only，不运行保存性能或其他性能基准：

```sh
LATEX_REVIEW_REQUIRE_BROWSER=1 LATEX_REVIEW_FUNCTIONAL_ONLY=1 ./scripts/verify-macos.sh
```

默认已经串行。若需要逐例重新启动浏览器，回退命令为：

```sh
LATEX_REVIEW_BROWSER_SCOPE=function LATEX_REVIEW_REQUIRE_BROWSER=1 \
  ./scripts/verify-tests.sh full
```

默认 `LATEX_REVIEW_BROWSER_SCOPE=module`；只允许 `module` / `function`，非法值直接报错。回退只改变浏览器 fixture 生命周期，不改测试选择或判断标准。

## 可比的完整测量

[测量入口](../scripts/measure-tests.py) 在 `LATEX_REVIEW_TEST_SHARDS=auto` 下调用显式分片 runner：CPU不足时为一个完整串行 lane，CPU足够时为 serial + pure 两个 lane；随后只在父 runner 运行同样的五项 CLI smoke，保留每项结果与耗时。`wall_seconds` 包含 collection、所有 lane、CLI smoke 和证据合并，`pytest_wall_seconds` 记录最长 lane 的 pytest 时间；每个 lane 的 setup/call/teardown、nodeid和进程审计单独保留。输出目录必须是新目录，以免覆盖慢样本或失败样本。设置 `LATEX_REVIEW_TEST_SHARDS=0` 可强制旧的单进程路径。

```sh
LATEX_REVIEW_REQUIRE_BROWSER=1 python3 scripts/measure-tests.py --output /absolute/new-evidence-directory
```

同一 runner/虚拟环境先测 before，随后测 after。可以从 baseline 工作目录调用 after 中的测量脚本；脚本显式把**当前工作目录**的 `src` 放在本进程与子进程的导入路径首位，不修改 baseline 测试。两个版本必须使用完全相同的测量脚本、依赖、平台、工具和浏览器设置；不要在两轮间安装新工具或启动重型任务。

```sh
# 在 before 的工作目录；after-checkpoint 是本 PR 固定检查点。
LATEX_REVIEW_REQUIRE_BROWSER=1 python3 /absolute/after-checkpoint/scripts/measure-tests.py \
  --output /absolute/evidence/before
# 然后在 after-checkpoint 工作目录，同一个 Python 环境中运行。
LATEX_REVIEW_REQUIRE_BROWSER=1 python3 scripts/measure-tests.py \
  --output /absolute/evidence/after
python3 scripts/compare-test-runs.py \
  /absolute/evidence/before/timings.json /absolute/evidence/after/timings.json \
  > /absolute/evidence/comparison.json
```

[比较入口](../scripts/compare-test-runs.py) 检查完整 nodeid **重数**、原通过项是否变跳过/失败、运行退出码，以及 Python/依赖/平台/浏览器/工具/参数/插件环境/测量脚本哈希的一致性。新增用例完整列出。失败或不可比时仍保留原始墙钟差值，但 `comparison_valid=false`、有效 `reduction_percent=null` 并返回非零，不能把提前失败称为提速。

[跨平台工作流](../.github/workflows/test-runtime.yml) 在 Linux 与 macOS 各自的同一个 runner 上串行执行 baseline 和 after，共用依赖环境，始终上传原始 console、JUnit、每例 phase JSON、清单、工具/版本与比较结果。原 macOS 工作流仍运行完整功能验证，`saved_current` 的停用状态保持不变。CI 上未安装的 TeX/Poppler/macOS 沙箱场景按原条件跳过，完整跳过原因保留；不能以托管 CI 代替配置了这些工具的物理 Mac 验收。

进程审计只观察本 runner 的后代，按 PID 与创建时间匹配，不向任何进程发信号。检测到存活应用子进程或审计错误会失败。CPython `multiprocessing.resource_tracker` 的精确启动命令作为随解释器退出的辅助进程单列，保留 PID/创建时间/argv；不将任意 Python 进程忽略。0.2s 采样可能看不到在两个采样之间立即脱离的短命进程，因此另外保留既有服务生命周期断言和真实浏览器跨例隔离/准备失败/执行失败后的清理验证，不宣称采样是完备的系统级泄漏证明。

## 交付检查点与结果

固定测试检查点：**`42af9d9dd0966d1fdd2aa341a94a56d9637d210c`**，Before 为 `702896fd22bc25c9ef016badde6265eedda68114`。[首轮完整结果与原始包](test-runtime/results-42af9d9.md)记录两组 Linux 同机完整墙钟减少 **11.02% / 8.89%**、全部用例清单、逐阶段/模块耗时，以及 macOS baseline 的真实失败和 After 通过；失败配对没有有效降幅。追加完整复验及 PR 当前交付状态见 [PR #58](https://github.com/jinghonh/LaTeX-Review/pull/58)，不以相关回归代替完整验收。请求人原有保存性能 **≤2s 未达标**记录不变，本任务没有重跑该性能基准。

macOS 可用本机 Python 3.11+ 在独立工作副本与虚拟环境复验固定检查点：

```sh
git fetch origin
git worktree add --detach ../latex-review-tests-42af9d9 42af9d9dd0966d1fdd2aa341a94a56d9637d210c
cd ../latex-review-tests-42af9d9
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m playwright install chromium
LATEX_REVIEW_REQUIRE_BROWSER=1 LATEX_REVIEW_FUNCTIONAL_ONLY=1 ./scripts/verify-macos.sh
# 或生成完整墙钟、逐例 phase、JUnit、CLI 和进程审计证据：
LATEX_REVIEW_REQUIRE_BROWSER=1 LATEX_REVIEW_FUNCTIONAL_ONLY=1 \
  python3 scripts/measure-tests.py --output /absolute/new-macos-evidence
```
