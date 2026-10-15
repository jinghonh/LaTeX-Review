# 当前论文持续预览与常驻入口

`latex-review-live` 显示当前磁盘论文。保存正文或依赖后，页面自动更新。
它不比较版本，不隐式上传，也不运行整篇 TeX 编译。

## 启动与发现

```sh
latex-review-live main.tex --open-browser
latex-review-local list
latex-review-local open
```

入口默认是 `http://127.0.0.1:8765/`。入口列出注册论文和两种服务状态。
CLI 输出论文的稳定入口链接。收藏稳定入口链接，不收藏工作进程随机端口。
服务就绪后，启动 CLI 返回。服务继续运行。

## 论文身份与唯一性

真实项目根目录和根内主入口共同定义论文身份。
普通符号链接别名共用身份。不同 checkout 和不同主 tex 分别拥有身份。
每个身份至多运行一个持续预览和一个差异报告服务。
相同请求复用已有服务。参数改变时，入口串行更新对应服务。
TeX 编译和资源转换子进程不计为额外阅读服务。

默认根目录由入口推导；Git 项目使用 Git 根目录。
需要其他根目录时，显式传入 `--root`。
默认输出位于根目录的 `.latex-review/live/`。
项目配置和用户翻译配置沿用现有合并规则。
翻译由用户操作触发；启动服务不调用翻译 API。

```sh
latex-review-live tex/main.tex --root . --open-browser
latex-review-live main.tex --poll-files
```

## 停止与迁移

`--wait` 附着状态。Ctrl+C 只断开附着客户端，不停止共享服务。
使用 `latex-review-local stop` 显式停止入口及其受管服务。
只停止某篇论文时，先从 `list` 取得完整论文 ID。

```sh
latex-review-local stop-paper PAPER_ID --kind live
latex-review-local stop-paper PAPER_ID --kind diff
```

1. 升级前，手动停止旧版本启动的预览。
2. 升级后，从新 CLI 注册和启动论文。
3. 用 `latex-review-local list` 查看状态。
4. 将浏览器收藏改为稳定论文链接。

```mermaid
flowchart LR
 A[停止旧版服务] --> B[新CLI注册论文]
 B --> C[入口创建或复用槽位]
 C --> D[收藏稳定论文URL]
```

新入口不接管未知进程，也不按进程名称批量终止。
唯一性只覆盖新版本受管入口。旧随机端口链接不能自动迁移。
入口使用本机状态目录和终身内核锁。PID 和状态文件不是所有权证明。
入口崩溃后，其工作进程检测所有者退出并关闭。
重新启动入口不会自动重建全部论文；按需请求恢复。

macOS 与 Linux 是本地入口的目标平台。Linux 验收结果另行记录。
服务锁和状态目录需要本机文件系统。原生 Windows 不在本期支持范围。
固定入口端口冲突会明确失败，不会悄悄更换端口。

## 阅读边界

HTML 是内容预览，不保证 PDF 最终排版。公式仍加载在线 MathJax。

预览显示章节、图、表和公式编号。章节目录与交叉引用使用相同编号。
源码规则支持附录、计数器重置、自定义计数格式、分行公式和显式公式标签。
入口同目录或项目 `build/` 内的当前 `.aux` 文件可提供已编译标签编号；过期文件不参与编号。
任意宏或文档类计算的编号需要当前编译数据；无法确认的编号会生成预览诊断。
需要完整编译编号时，使用差异报告的 `--compile`，参见[编译与渲染](compiled-rendering.md)。

窗口宽度变化或章节目录开合时，预览保持当前文字及其相对视口位置。
原生文件事件失败时，服务使用元数据轮询，并显示降级提示。
网络盘可显式使用 `--poll-files`。修改根目录、输出或翻译配置后，需要更新服务参数。
不同论文和服务不能认领父子重叠输出目录。
生成差异报告时，请使用与活动持续预览分离的输出目录。

## 状态位置与诊断

macOS 默认状态目录为 `~/Library/Application Support/latex-review/local-service`。
Linux 默认使用 `~/.local/state/latex-review/local-service`；绝对 `XDG_STATE_HOME` 可覆盖基础目录。
`LATEX_REVIEW_STATE_HOME` 可指定独立状态根，用于隔离测试或明确配置。
状态根必须是绝对路径，并且不能包含符号链接。
使用 `latex-review-local doctor` 查看状态位置和诊断；它不会扫描或停止旧进程。

入口端口需要更改时，先显式停止入口，再指定新端口。原收藏链接需要更新。

```sh
latex-review-local start --port 8875
```

独立实施验收见[验收报告](design/local-entry-service/service-implementation-verification.md)。
当前 macOS 全量通过；Linux 尚未独立运行。
