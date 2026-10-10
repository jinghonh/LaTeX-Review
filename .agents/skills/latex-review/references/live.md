# 当前论文持续预览

使用 **latex-review-live** 展示当前磁盘论文的 HTML 内容。此分支不读取 Git，不生成差异报告，也不运行整篇 TeX 编译。**--compile-new-only** 仍属于差异审阅，不能代替此分支。

1. 确认论文入口和项目根目录。根目录默认为入口文件所在目录；入口位于子目录时，用 **--root** 指向包含依赖的项目根目录。用 **command -v latex-review-live** 检查命令；命令缺失时，可在已安装本项目的 Python 环境运行 **python -m latex_review.live**。旧安装缺少该模块时，说明需要更新安装，并提供下列命令。
2. 启动服务，保存进程或终端会话标识，读取标准输出中的实际 HTTP 地址。用户要求打开浏览器时，添加 **--open-browser**。用允许访问本机网页的浏览器工具检查正文、公式、图片和预览诊断。验证自动刷新时，使用用户已授权的论文修改，或独立临时样例。
3. 用户要求持续预览时，保留服务，并交代地址、产物路径和停止方式。仅为验收启动时，完成检查后停止本次服务并确认退出。服务停止后，说明临时地址已失效。

~~~sh
latex-review-live main.tex --open-browser
# 入口位于子目录时：
latex-review-live tex/main.tex --root . --open-browser
~~~

默认产物位于项目根目录内的 **.latex-review/live/**。可用 **--output** 指定专用目录。启动会写入 **report.html** 和 **live-status.json**；选择不含用户文件的目录。服务仅监听 **127.0.0.1**，保存依赖文件后自动更新页面。更新失败时，页面显示错误并保留上次预览。

检查 **live-status.json** 中的 **revision** 和 **message**，并读取页面中的预览诊断。此分支不生成审阅清单或差异数据，无需执行审阅查询和验收记录命令。只读取本次产物，避免把目录中的旧报告当作当前结果。

HTML 内容预览不保证与 PDF 排版一致。公式使用在线 MathJax；PDF 图片缺少转换工具时显示资源链接及诊断。当前命令不读取 **.latex-review.toml**，也不提供报告变更翻译。参数以 **latex-review-live --help** 为准。
