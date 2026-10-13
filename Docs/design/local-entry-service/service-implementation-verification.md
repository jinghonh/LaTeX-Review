# 常驻入口实施独立验收

日期：2026-10-13。状态：macOS 独立验收通过。
最终仓库必要验证同一轮 **663 passed in 412.50s**，失败 0，跳过 0，退出码 0。
其中服务独立验收 22 项，上传独立验收 21 项。没有拼接不同代码状态的结果。
T1–T5 实施负责人的历史结果未代替本轮独立运行。

## 前置证据与边界

先通过 wait_threads 确认实施负责人本轮 completed，并明确交付 T1–T5。
随后读取 implementation-status.md、implementation-validation.json 和当前生产接口。
只修改独占测试、README、迁移文档及仓库技能参考。未修改生产源码或其他负责人测试。
所有论文、配置、状态根及端口均隔离。未读取真实论文、凭据或发布服务器。
只关闭本次 Popen 所有者和受控编译器记录的进程。未扫描全系统进程或停止未知进程。

## 实际命令与结果

工作目录为 `/Users/jinghong/Documents/Code/LaTeX Review`。
无空格运行别名指向同一仓库 `.venv`，不会创建第二套安装。
HTTP 检查绕过系统代理，避免伪造 Host 被代理拦截。

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
 NO_PROXY=127.0.0.1,localhost LATEX_REVIEW_REQUIRE_BROWSER=1 \
 LATEX_REVIEW_ACCEPTANCE_EVIDENCE=/private/tmp/latex-review-local-entry-20261013/process-browser \
 /private/tmp/latex-review-local-entry-20261013/process-runtime-rg6_rnxo/venv/bin/python \
 -m pytest -q tests/test_local_service_acceptance.py \
 --basetemp=/private/tmp/latex-review-local-entry-20261013/process-acceptance-tests \
 --junitxml=/private/tmp/latex-review-local-entry-20261013/process-acceptance.xml
```

结果：22 passed in 51.16s。失败、错误、跳过均为 0。

```sh
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
 NO_PROXY=127.0.0.1,localhost \
 PATH=/private/tmp/latex-review-local-entry-20261013/process-runtime-rg6_rnxo/venv/bin:$PATH \
 LATEX_REVIEW_REQUIRE_BROWSER=1 \
 LATEX_REVIEW_ACCEPTANCE_EVIDENCE=/private/tmp/latex-review-local-entry-20261013/process-full-browser \
 PYTEST_ADDOPTS='--basetemp=/private/tmp/latex-review-local-entry-20261013/process-full-tests --junitxml=/private/tmp/latex-review-local-entry-20261013/process-full.xml' \
 ./scripts/verify-macos.sh
```

结果：663 passed in 412.50s。脚本随后完成 CLI help/version，退出码 0。
JUnit 同时包含 tests.test_local_service_acceptance 的 22 项及 tests.test_local_upload_compatibility 的 21 项。
未设置 ignore、未排除翻译缓存检查、未编辑上传负责人的文件。
`git diff --check` 通过。

## 服务验收矩阵

| 标准 | 本轮真实证据 | 结论 |
|---|---|---|
| A1 | 24 个公开 live 模块客户端同时启动；同一稳定 URL；日志仅一个 live 工作进程；槽位 flock 忙 | 通过 |
| A2 | 同论文 live/diff 及另一论文并行就绪；两个服务均可读 | 通过 |
| A3 | 相同别名复用相同代次；不同 output 替换代次；两个 baseline 操作按接受顺序最终更新 | 通过 |
| A4 | root、主 tex、checkout 身份分离；真实目录和入口符号链接合并 | 通过 |
| A5 | 越界符号链接及缺资源候选拒绝，当前报告保持可读 | 通过 |
| A6 | 实际批注建议触发 replay，再启动受控 latexmk 及其子进程；stop/SIGTERM/SIGKILL 均退出编译与子进程，释放槽位锁 | 通过 |
| A7 | SIGKILL 后立即恢复；损坏注册表隔离；伪造诊断 PID 不授权终止；遗留槽位锁阻止第二服务 | 通过 |
| A8 | 入口端口占用、工作端口占用、活动工作端口变更、协议不兼容分别失败；不随机回退 | 通过 |
| A9 | baseline/output 替换及所有者重启后稳定 URL 不变，新点击跳到当前工作端口 | 通过 |
| A10 | 两种服务根 API、批注状态/内容/导出；真实 HTTP 翻译提供端、全部译文、导出、停止恢复缓存且无新增请求；浏览器批注和搜索 | 通过 |
| A11 | 精确 Host/Origin、缺 token、错误 token、路径边界；公共状态不含假密钥 | 通过 |
| A17 | console、模块、目录、report.html、tex 快捷、生成+browser 全部受管；--wait 客户端退出后服务可读；help 无公开 standalone | 通过 |
| A18 | 真实子进程持输出租约，入口离线时同级/父/子冲突拒绝；公开离线生成不覆盖活动 live；独立输出成功 | 通过 |
| A19 | 完整副本逐文件字节核对，含 .rebuild 和旧来源；原输出改写不影响服务；缺资源/符号链接拒绝；缓存位置不变 | 通过；中途强杀复制未测 |
| A20 | macOS 原生及 poll 保存刷新、完整仓库和实际 Chromium 交互 | macOS 通过；Linux 未执行 |

上传 A12–A16 的独立详细结论见 [上传验收](upload-implementation-verification.md)。
本轮完整回归包含上传独立文件和既有发布、交付、静态浏览器检查。

```mermaid
flowchart LR
 A[T1–T5完整交付] --> B[公开入口与真实锁验收]
 B --> C[编译退出与候选恢复]
 C --> D[真实浏览器与翻译]
 D --> E[两份独立文件及全量同轮通过]
 E --> F[核对自身锁释放并交付]
```

## 浏览器证据

使用真实 headless Chromium。服务端、入口及 worker 均使用生产实现。
已检查入口 1280px 桌面和 390px 窄屏截图。窄屏无横向溢出。
浏览器从稳定 URL 跳转后，读取正文，搜索 manuscript，写入批注，读取批注及译文导出。
入口停止和恢复 live 后仍显示原稳定 URL。

最终截图：

- [入口桌面](/private/tmp/latex-review-local-entry-20261013/process-full-browser/dashboard-desktop.png)
- [入口窄屏](/private/tmp/latex-review-local-entry-20261013/process-full-browser/dashboard-mobile.png)
- [差异阅读](/private/tmp/latex-review-local-entry-20261013/process-full-browser/diff-desktop.png)
- [持续预览](/private/tmp/latex-review-local-entry-20261013/process-full-browser/live-desktop.png)

最小样例无公式，独立截图的 math-status 为空，不能据此声称在线公式加载已验收。
全量回归包含既有公式调度、翻译、搜索和批注浏览器检查；它们不等于真实网络 MathJax 可用性承诺。

## 清理与日志

完整回归结束后，仅遍历该轮临时目录的 local.lock 和 slots/*.lock。
所有这些锁均可重新取得，未留下活动受管服务锁。
SIGKILL 场景会保留 endpoint.json；协议冲突和上传测试也故意保留假 endpoint。
这些文件是测试证据，不是活动进程。没有根据残留 PID 杀进程。

日志包含一次 HTTP 请求处理的异常提示，但没有对应失败或跳过。日志未保留完整堆栈，无法确定其具体来源。
不把该提示隐藏为全量失败，也不据此声称没有任何诊断输出。

- 完整日志：`/private/tmp/latex-review-local-entry-20261013/process-full.log`
- 完整 JUnit：`/private/tmp/latex-review-local-entry-20261013/process-full.xml`
- 独立日志：`/private/tmp/latex-review-local-entry-20261013/process-acceptance.log`
- 独立 JUnit：`/private/tmp/latex-review-local-entry-20261013/process-acceptance.xml`
- 机器摘要：`/private/tmp/latex-review-local-entry-20261013/process-final-results.json`

## 用户操作与迁移

更新安装后，使用以下命令。全局 Python 环境也须重新安装，仓库安装不会更新所有环境。

```sh
latex-review-live main.tex --open-browser
latex-review-preview .latex-review/latest --open-browser
latex-review-local list
latex-review-local open
latex-review-local stop-paper PAPER_ID --kind live
latex-review-local stop-paper PAPER_ID --kind diff
latex-review-local stop
```

默认入口为 `http://127.0.0.1:8765/`。收藏 CLI 输出的稳定论文链接。
启动成功后 CLI 返回；服务继续运行。--wait 的 Ctrl+C 只断开客户端。
stop-paper 需要 list 显示的完整论文 ID。stop 会停止全部受管槽位。
升级前手动停止旧版本服务。新入口不会接管无协议进程或批量终止未知 PID。
纯 --upload 继续独立；加 --open-browser 时，发布先完成，本地阅读随后启动。

## 剩余风险和未完成项

Linux CI 未执行。原生 Windows、网络文件系统锁、登录自动启动及无停机替换不在本轮支持承诺内。
未执行复制中途 SIGKILL、队列 16 项溢出压力、管道描述符泄漏压力、真实网络翻译挂起退出。
A6 已验证编译、HTTP 槽位和所有者退出；未强制制造真实外部翻译请求永久不返回。
A19 已验证失败候选完整性和完整复制；未模拟磁盘断电或 fsync 故障。
独立浏览器覆盖可见操作；没有穷举全部控件状态和外部编辑器协议。
旧服务、手工内部 API、恶意绕过锁及跨主机实例不属于受管唯一性保障。
未提交、推送、创建 Issue/PR 或实际远程发布。未修改真实论文或用户安装技能副本。
