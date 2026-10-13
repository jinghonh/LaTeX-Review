# 常驻入口实施状态

日期：2026-10-13。状态：T1–T5已实现，必要回归通过。Issue尚未发布。
用户已授权直接实现；Issue发布不构成本轮前置条件。

## 公共命令与协议

```sh
latex-review-local start
latex-review-local open
latex-review-local list
latex-review-live path/to/main.tex --open-browser
latex-review-preview path/to/report --open-browser
latex-review path/to/main.tex --open-browser
latex-review-local stop-paper PAPER_ID --kind live
latex-review-local stop
```

上述命令以当前项目安装为前提。本轮已更新仓库`.venv`中的console入口。
直接运行可使用`.venv/bin/latex-review-local`，全局安装需重新安装项目以取得新命令。

`--wait`附着状态。Ctrl+C只断开客户端。命令正常返回后，受管服务继续运行。
`--startup-timeout`默认120秒。超时返回4；后台操作可能仍运行，诊断显示操作ID。
`latex-review-local run --port PORT`提供前台诊断入口。入口默认固定端口8765。
`--port`在preview命令中仍指工作端口，不指入口端口。

入口协议为版本1。GET health/papers/operations保持只读。
POST register/ensure/stop/shutdown要求JSON和入口token，并校验Host与Origin。
ensure接受请求ID，返回202及操作ID；操作状态为queued/starting/ready/failed/cancelled。
稳定链接为`/papers/<paper_id>/live`和`/papers/<paper_id>/diff`。

测试隔离可设置`LATEX_REVIEW_STATE_HOME`和`LATEX_REVIEW_LOCAL_PORT`。
前者为最终状态目录，不自动添加子目录；后者设置固定入口端口，不触发随机回退。
这些设置不影响上传配置或远程slug。

## 实现接入点

| 模块 | 职责 |
| --- | --- |
| identity.py | 兼容旧project_id哈希与规范服务身份 |
| local_paths.py、local_state.py | 私有状态路径、终身内核锁、原子JSON |
| output_leases.py | 持久输出租约、祖先路径重叠检查、生成冻结边界 |
| local_reports.py | 完整报告副本、资源及身份核验 |
| report_directory.py | 候选目录生命周期、目录替换、失败回退和备份策略 |
| local_protocol.py、local_client.py | 客户端发现、并发启动、请求ID、等待与显式停止 |
| local_worker.py、local_tasks.py | 槽位终身锁、所有者EOF、独立任务组取消 |
| local_service.py | FIFO、候选替换、状态恢复、固定地址报告页面、管理命令 |
| local_dashboard.html | 论文列表、两类槽位、打开/停止/恢复与诊断 |
| live.py、serve.py、cli.py | 全部公开CLI受管接入；保留内部create_server/build API |
| report_rebuild.py、delivery.py、upload.py | 输出事务及发布冻结；SSH阶段释放生成锁 |

持续预览和显式重建通过`report_directory`上下文更新报告目录。
调用方持有现有锁，并在候选目录中生成和验证内容。
正常退出提交，异常退出清理候选目录。
持续预览删除旧目录；显式重建保留`previous_report`。
替换失败时恢复旧目录；回退失败时保留备份，并报告恢复路径。
翻译会话刷新和状态更新仍由调用方执行。

## 有依据的实施调整

1. live更新使用同文件系统候选目录，并在完成后替换输出。
2. 所有工作进程子任务由local_tasks登记专属进程组，覆盖原有独立TeX session。
3. 输出租约区分持续占有和临时任务；持续占有冲突立即返回4，避免离线命令等待120秒。
4. CLI先冻结浏览器副本，再执行上传；副本失败不会跳过上传。
5. 注册表记录不存在的论文仍可列出；显式恢复时验证真实根目录，避免一个移动项目破坏全部注册。
6. 入口关闭所有者管道后给工作进程5秒清理；必要时只终止自己保留Popen句柄的进程。
7. 重建子进程继承同一所有者管道读端，自己取消独立TeX任务组；任何子进程不继承写端。
8. 工作进程cwd使用真实论文根目录，避免编译安全路径把文件系统根目录视为禁止范围。
9. 隔离编译不复制项目`.latex-review`工具产物，避免缓存标记和共享批注进入TeX副本。

```mermaid
flowchart LR
 A[CLI生成租约] --> B[生成报告]
 B --> C[冻结浏览器副本及发布字节]
 C --> D[释放输出租约]
 D --> E[独立SSH发布]
 E --> F[受管ensure及稳定链接]
 G[纯上传] --> A
 F --> H[入口与工作进程终身锁]
```

纯上传不调用local_client。注册、发现、重建和恢复没有SSH调用。
上传SSH参数、增量协议、回退条件、latest地址和退出码优先级不变。
报告副本包含重建配方和旧来源快照；不会按静态发布白名单删减。

## 验证记录

已通过93项针对性测试，包括CLI、服务、持续预览、翻译、监听、上传、交付及真实replay子进程。
新增核心与浏览器测试13项通过，包括所有者SIGKILL、独立任务组取消、发布网络阶段释放租约。
实际浏览器检查通过打开、停止、恢复、根路径跳转及390px窄屏布局。
原生监听和poll-files浏览器测试3项通过；保存发生在构建期间仍保留下一次更新。
仓库必要验证入口`./scripts/verify-macos.sh`通过；结果为`620 passed in 336.35s (0:05:36)`。
该入口还通过CLI help和version检查。两位独立验收负责人保留各自验收文件责任。
独立编译退出检查的stop/SIGTERM/SIGKILL三项已通过，耗时7.25秒。
独立入口和上传检查42项通过，耗时46.57秒；该轮仅排除翻译缓存等待条件检查。
验收负责人随后修正了翻译等待条件；该项再次运行通过，耗时4.19秒。
生产负责人没有修改验收文件；产品继续逐条展示已完成译文。
最后的CLI、上传及核心测试39项通过，耗时18.10秒。

本机测试工具shebang若使用含空格的`sys.executable`路径，系统会拒绝执行。
验证使用同一虚拟环境的无空格别名`/tmp/latex-review-local-entry-20261013/implementation-venv`。
系统代理也会拦截使用伪造Host的安全测试；验证命令清除代理环境变量。
产品local_client自身已禁用代理，真实本机请求不会依赖系统代理。

## 限制

第一期仅macOS/Linux。没有登录自动启动、无停机替换或未知旧进程接管。
旧工作地址在替换后可能失效；稳定入口链接可重新定位当前服务。
每槽位最多16项等待；同槽位FIFO，不取消已接受的不同baseline任务。
状态和锁须位于本机文件系统。生产网络发布未执行。
完整技术设计仍见[设计总结](design-summary.md)；Issue草稿见[实施规格](implementation-issue-draft.md)。

## 复现必要验证

在仓库创建指向`.venv`的无空格目录别名，并把其bin目录置于PATH首位。
本轮别名为`/tmp/latex-review-local-entry-20261013/implementation-venv`。
执行验证时清除HTTP_PROXY、HTTPS_PROXY、ALL_PROXY及其小写形式。
设置`PYTEST_ADDOPTS="--ignore=tests/test_local_service_acceptance.py --ignore=tests/test_local_upload_compatibility.py"`后执行`./scripts/verify-macos.sh`。
这两个独立验收文件另外执行；42项普通检查和1项翻译缓存检查均通过。
必要检查不使用跳过项替代机制失败。完整日志保存在原临时目录。

实际浏览器截图见[窄屏入口](implementation-browser.png)。

机器可读验证摘要见[验证结果](implementation-validation.json)。

## 固定地址显示修复

用户反馈302跳转会让地址栏显示随机工作端口。入口现在返回全屏iframe页面，地址栏保持固定论文URL。
报告在原工作origin内运行，根API、批注、翻译及资源地址不变。入口CSP只允许当前工作origin内嵌。
工作代次变化时，入口页面在同一地址重新加载报告。纯上传路径及静态报告生成不参与此修改。

固定地址修复后的必需检查为663项全部通过。详情见[修复验证](stable-address-validation.md)。
