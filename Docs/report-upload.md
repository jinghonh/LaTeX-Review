# 私有报告发布

仅显式 `--upload` 会上传报告。普通生成和本地预览不连接发布服务器。

在论文目录执行：

```sh
latex-review main.tex --open-browser --upload
```

命令先生成本地报告，再上传，然后启动本地预览。预览持续运行；按 Ctrl+C 停止。
上传失败时，本地报告仍可读取，本地预览仍会启动，最终退出码为 `16`。
报告诊断原有退出码仍适用。成功发布不表示论文没有诊断。

## 配置

用户级配置为 `~/.config/latex-review/upload.toml`。
设置 `XDG_CONFIG_HOME` 时，配置位于 `$XDG_CONFIG_HOME/latex-review/upload.toml`。
论文入口以绝对路径匹配。未注册的论文不能上传。
配置不保存网站密码。SSH 使用专用密钥和配置别名。

```toml
host = "ali-review"
base_url = "https://reports.gpare.top"

[[papers]]
slug = "bue-br"
entry = "/absolute/path/BUE-BR/main.tex"

[[papers]]
slug = "reachable-set-fno"
entry = "/absolute/path/Reachable-Set-FNO/neural_networks/main.tex"
```

`base_url` 只支持 HTTPS 站点根地址。`host` 只能是 SSH 别名。
同一入口必须唯一注册。slug 使用小写字母、数字和连字符。
服务器的 `/srv/latex-review/papers.json` 同时注册 slug 和主页名称。
新增论文时，需要同时更新本地入口配置和服务器论文注册表。

## 发布内容

发布内容包括报告页面及页面引用的 `assets/`、`previews/`、`pages/` 资源。发布页面不再内嵌资源的 Base64 副本。
页面携带完整批注备份。当前正文通过 `annotation-current-version` 引用备份中的版本，不再重复内嵌正文清单。备份使用 `annotation-backup-gzip` 内嵌 gzip + Base64，保留全部历史正文和批注。浏览器先显示正文，再在空闲时解码；打开批注或操作正文可提前触发加载。解码后的 JSON 契约不变。已有译文按下述规则处理。在线发布页不提供独立 HTML 导出；批注 JSON 导入与导出继续可用。本机预览和本机独立导出保持原有能力。
这些资源允许受控图片及论文图中的 PDF；它们是阅读资源，不是独立原始论文下载。
发布不会复制源码、`diff.json`、`diagnostics.json`、编译目录或翻译凭据。
报告本身含论文正文，因此报告也需要访问控制。
有翻译配置时，发布页面只内嵌当前配置验证通过的缓存译文，不合并报告中原有译文。
没有翻译配置时，发布保留报告中已有译文。
翻译清单过旧或缓存验证失败时，发布跳过不兼容译文，在页面显示原因，并保留原文。
资源缺失、路径不安全或体积超限时，交付失败，不替换服务器现有报告。
上传不会请求翻译服务。上传后在本地新生成的译文，需要下一次 `--upload` 才会出现在服务器。
在线 MathJax 仍由报告加载。服务器不提供在线按需翻译接口。

## 服务器结构

- 宝塔 Nginx 配置：`/www/server/panel/vhost/nginx/reports.gpare.top.conf`。
- 独立发布程序：`/opt/latex-review/publish.py` 和 `portal.py`，由 root 管理。
- 论文注册表、发布清单、发布锁：`/srv/latex-review/`，不直接暴露给网页。
- 网站：`/srv/latex-review/site/`，每篇有 `releases/` 和原子切换的 `latest` 链接。
- 网站登录凭据摘要：`/etc/latex-review/login.json`，仅 root 和网页服务账号可读取。
- HTTPS 证书：`/etc/letsencrypt/live/reports.gpare.top/`。
- 发布账号：`latex-review`。专用密钥使用 forced command，只能执行 `publish`。

网站主页列出所有注册论文。每篇保留最近 10 次发布。
历史列表标注北京时间和比较基准，默认入口打开最新报告。
未发布的论文显示“尚未发布报告”。
服务器串行处理发布，验证文件类型、路径、版本标识和大小后才切换最新版本。
每次发布最多 200 MiB、4096 个文件。非法包不会替换现有报告。

HTTP 仅提供证书验证及 HTTPS 跳转。网页登录保护报告和全部资源。
登录会话有效期为 24 小时。Cookie 使用 Secure、HttpOnly 和 SameSite=Lax。
主页提供退出登录。修改密码摘要后，所有原有会话立即失效。
撤销某位合作者时，须更换共用账号密码并通知其他合作者。
不使用服务器 root 密钥进行日常发布。

## 运维

验证配置并重载宝塔 Nginx：

```sh
ssh ali '/www/server/nginx/sbin/nginx -t && /www/server/nginx/sbin/nginx -s reload'
```

检查证书自动续期：

```sh
ssh ali 'systemctl status certbot.timer --no-pager'
ssh ali 'certbot renew --dry-run'
```

首次部署使用 `deploy/setup.sh`、两个 Nginx 配置和受限公钥。
证书签发后，使用 `deploy/setup-login.sh` 安装网页登录服务，再启用 HTTPS 配置。
`setup.sh` 的 HTTP 配置只用于证书签发前引导；运行它后必须恢复 HTTPS 配置。
不要在已启用 HTTPS 的站点反复运行引导脚本。
备份 `/srv/latex-review/`、`/etc/latex-review/`、证书和站点配置。
网页登录由 `latex-review-web.service` 管理，Gunicorn 仅监听 `127.0.0.1:8768`。
登录账号摘要位于 `/etc/latex-review/login.json`，会话密钥位于 `/etc/latex-review/session.key`。
两个文件仅 root 和独立网页服务账号可读取。旧 htpasswd 只保留用于回退。
`deploy/setup-login.sh` 安装网页服务并验证健康状态，然后切换 Nginx 配置。
登录接口有 Nginx 请求速率限制。服务停止时，报告不会绕过验证。
修改密码时，更新 login.json 的 salt 和 hash。hash 使用 PBKDF2-HMAC-SHA256，迭代 600000 次。
本地上传密钥无需改变。

网页登录的 Referrer-Policy 使用 same-origin，保留表单提交的同源 Origin。
不能将它改为 no-referrer；浏览器会发送 Origin: null，导致登录校验失败。
浏览器回归检查：`scripts/verify-web-login.sh`。该检查使用故意错误的密码，验证页面显示密码错误而非页面过期。

服务器会为报告页添加“返回首页”按钮，返回论文选择主页。
该按钮覆盖最新报告和历史报告。服务器不会为此改写报告文件。

## 增量上传与资源共享

新客户端先通过受限 SSH 命令 `resource-query-v1` 查询缺少的资源哈希，再通过 `publish-incremental-v1` 上传页面、完整资源清单和缺少的资源。相同内容只传一次；第二次发布相同资源时，资源传输量为零。页面与批注备份每次仍完整传输。SSH 启用传输压缩，降低正文和批注 JSON 的网络传输量。命令输出本次资源文件数和未压缩字节数。

服务器按论文在 `/srv/latex-review/resources/<slug>/<sha256>` 保存资源。缓存位于网站目录外；发布目录使用硬链接并保留原有资源 URL。缓存和发布目录必须位于同一文件系统。服务器检查完整清单的路径、哈希、大小和文件数，200 MiB 与 4096 文件限制仍按完整发布内容计算。发布、资源查询、迁移和清理使用同一把锁。

服务器在最终发布时再次确认资源完整。资源在查询后被清理时，客户端按明确回执补传，最多重试两次。验证失败不会切换 `latest`。删除旧发布后，服务器回收没有发布目录引用的缓存资源。

新服务器继续接受旧 `publish` 完整包。新客户端仅在收到旧服务器的明确命令拒绝时回退到完整上传；连接失败、异常回执和资源校验失败直接报错。

升级只需替换 `/opt/latex-review/publish.py`，不修改 HTTPS、Nginx、登录服务或受限公钥。不要重跑首次部署脚本。以发布账号执行以下管理命令，可对现有历史资源去重，历史 HTML 保持原样：

```sh
python3 /opt/latex-review/publish.py /srv/latex-review --deduplicate
```

共享资源必须通过替换写入更新，不能原地修改。清理发布目录时，不能仅凭路径删除仍被其他发布引用的共享数据。


静态页面的批注读取器兼容旧报告的 `annotation-content` 与 `annotation-backup` 双标签、版本引用和压缩备份。压缩备份需要支持 `DecompressionStream` 的浏览器，例如当前 Chrome。浏览器检查 gzip 校验和、解码大小和完整数据契约；失败时显示错误，不覆盖浏览器批注。JSON 导出仍使用完整 `versions` / `threads` 契约。本机报告和独立 HTML 导出继续使用原有格式。此变化不改变上传协议或 receiver；旧客户端完整上传和新客户端回退仍可使用。历史发布不需要迁移。

压缩只用于发布交付。发布准备直接从已校验的完整备份编码，避免先生成大段 JSON 再解析。没有新增侧文件，因此资源白名单、完整上传、增量协议和旧服务器明确拒绝后的回退保持兼容。普通本机报告继续从本机接口读取；独立 HTML 和 JSON 导出继续携带完整可恢复数据。

正文历史现在使用 `shared-units-v1` 共享单元池。每个版本保留完整元数据及有序整数引用；完全相同的正文单元只保存一次。单元的源码位置、选择器、顺序或公式原子不同，就保存不同单元。版本之间没有依赖链，因此没有基线或循环依赖。读取器在契约校验和定位前恢复完整版本。缺少单元池、引用越界、非整数引用和超过 200 MiB 的恢复数据会明确失败。

发布编码为 `gzip-shared-units-v1`；读取器继续支持 `gzip-base64-v1` 和旧完整备份。IndexedDB保留旧客户端的完整备份外形，并通过结构化克隆共享完全相同的单元对象。事务读取时解除跨版本对象共享，写入时重新共享。读取器兼容旧完整记录和已有整数引用池记录；下次成功写入会使用兼容旧页面的完整外形。JSON 导入导出和独立 HTML 仍使用完整正文版本，原有 API 语义保持不变。

本地项目批注文件采用带 SHA-256 校验的共享表示。读取兼容旧完整 JSON；下一次成功批注写入会原子转换。`AnnotationStore.compact()` 可在锁内校验全部版本和锚点，再原子迁移，且不改变 revision。显式迁移真实数据前先保存外部备份，并验证导出相等和恢复能力。旧程序不识别新本地格式；回退程序前须在存储锁内用 `unpack_storage` 恢复完整文件，包括 `requests` 批量回执，再写回原格式。普通 JSON 导出仍按原契约不包含批量回执。不能直接运行旧程序覆盖新文件。

轻量版本检查与批注清单读取仍校验本地文件的 SHA-256，但不重建历史正文。完整正文、历史、备份和写入路径继续恢复完整版本。迁移后须重启仍加载旧代码的本机服务；已有标签页须重载，以取得服务的新写入令牌。
