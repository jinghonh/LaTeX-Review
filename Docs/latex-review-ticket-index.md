# LaTeX 审阅票据索引

[实施路线总览](https://github.com/jinghonh/LaTeX-Review/issues/1)

第一版9张、第一点五版4张、第二版3张，共16张开放实施主票和1张总览。另有22张已替代旧票保留历史；全部细粒度要求保留在主票内部。

| 当前编号 | 版本 | 票据 | 前置主票 |
| --- | --- | --- | --- |
| 01 | 第一版 | [#2 [第一版 01] 工程骨架与公共契约](https://github.com/jinghonh/LaTeX-Review/issues/2) | 无 |
| 02 | 第一版 | [#4 [第一版 02] 双版本来源与项目展开](https://github.com/jinghonh/LaTeX-Review/issues/4) | #2 |
| 03 | 第一版 | [#7 [第一版 03] LaTeX结构解析与HTML预览](https://github.com/jinghonh/LaTeX-Review/issues/7) | #4 |
| 04 | 第一版 | [#8 [第一版 04] 核心结构匹配与正文差异](https://github.com/jinghonh/LaTeX-Review/issues/8) | #7 |
| 05 | 第一版 | [#10 [第一版 05] 结构化内容差异](https://github.com/jinghonh/LaTeX-Review/issues/10) | #8 |
| 06 | 第一版 | [#16 [第一版 06] 三栏HTML审阅报告](https://github.com/jinghonh/LaTeX-Review/issues/16) | #10 |
| 07 | 第一版 | [#18 [第一版 07] 命令行闭环、诊断与安全加固](https://github.com/jinghonh/LaTeX-Review/issues/18) | #16 |
| 08 | 第一版 | [#21 [第一版 08] 真实论文端到端验收](https://github.com/jinghonh/LaTeX-Review/issues/21) | #18 |
| 09 | 第一版 | [#22 [第一版 09] Codex技能与使用文档](https://github.com/jinghonh/LaTeX-Review/issues/22) | #21 |
| 01 | 第一点五版 | [#23 [第一点五版 01] 缓存与图片资源变化检测](https://github.com/jinghonh/LaTeX-Review/issues/23) | #18 |
| 02 | 第一点五版 | [#24 [第一点五版 02] 阅读交互、源码跳转与单文件交付](https://github.com/jinghonh/LaTeX-Review/issues/24) | #16 |
| 03 | 第一点五版 | [#25 [第一点五版 03] 表格、移动与公式高级差异](https://github.com/jinghonh/LaTeX-Review/issues/25) | #16 |
| 04 | 第一点五版 | [#27 [第一点五版 04] 参考文献与自定义宏兼容](https://github.com/jinghonh/LaTeX-Review/issues/27) | #18 |
| 01 | 第二版 | [#33 [第二版 01] 真实编译、页面对比与沙箱渲染](https://github.com/jinghonh/LaTeX-Review/issues/33) | #18 |
| 02 | 第二版 | [#36 [第二版 02] 持续集成产物与审阅评论发布](https://github.com/jinghonh/LaTeX-Review/issues/36) | #22 |
| 03 | 第二版 | [#38 [第二版 03] 论文规则检查](https://github.com/jinghonh/LaTeX-Review/issues/38) | #27、#33 |

## 合并映射

以下“原任务”采用上一版的本地编号；远程票据编号见第三列。

| 第一版主票 | 原任务 | 原票据 |
| --- | --- | --- |
| [01 工程骨架与公共契约](https://github.com/jinghonh/LaTeX-Review/issues/2) | 01 + 02 | #2、#3 |
| [02 双版本来源与项目展开](https://github.com/jinghonh/LaTeX-Review/issues/4) | 03 + 04 + 05 + 16 | #4、#5、#6、#17 |
| [03 LaTeX结构解析与HTML预览](https://github.com/jinghonh/LaTeX-Review/issues/7) | 06 + 13；另含原18的局部解析回退 | #7、#14 |
| [04 核心结构匹配与正文差异](https://github.com/jinghonh/LaTeX-Review/issues/8) | 07 + 08 | #8、#9 |
| [05 结构化内容差异](https://github.com/jinghonh/LaTeX-Review/issues/10) | 09 + 10 + 11 + 12 | #10、#11、#12、#13 |
| [06 三栏HTML审阅报告](https://github.com/jinghonh/LaTeX-Review/issues/16) | 14 + 15 | #15、#16 |
| [07 命令行闭环、诊断与安全加固](https://github.com/jinghonh/LaTeX-Review/issues/18) | 17 + 18 + 19 | #18、#19、#20 |
| [08 真实论文端到端验收](https://github.com/jinghonh/LaTeX-Review/issues/21) | 20 + 38 | #21、#39 |
| [09 Codex技能与使用文档](https://github.com/jinghonh/LaTeX-Review/issues/22) | 21 | #22 |

已被替代的旧票：[#3](https://github.com/jinghonh/LaTeX-Review/issues/3)、[#5](https://github.com/jinghonh/LaTeX-Review/issues/5)、[#6](https://github.com/jinghonh/LaTeX-Review/issues/6)、[#9](https://github.com/jinghonh/LaTeX-Review/issues/9)、[#11](https://github.com/jinghonh/LaTeX-Review/issues/11)、[#12](https://github.com/jinghonh/LaTeX-Review/issues/12)、[#13](https://github.com/jinghonh/LaTeX-Review/issues/13)、[#14](https://github.com/jinghonh/LaTeX-Review/issues/14)、[#15](https://github.com/jinghonh/LaTeX-Review/issues/15)、[#17](https://github.com/jinghonh/LaTeX-Review/issues/17)、[#19](https://github.com/jinghonh/LaTeX-Review/issues/19)、[#20](https://github.com/jinghonh/LaTeX-Review/issues/20)、[#39](https://github.com/jinghonh/LaTeX-Review/issues/39)。关闭仅表示工作已迁移，不代表实现完成。

## 第一点五版与第二版合并映射

| 当前主票 | 原票据 |
| --- | --- |
| [第一点五版 01 缓存与图片资源变化检测](https://github.com/jinghonh/LaTeX-Review/issues/23) | #23、#31 |
| [第一点五版 02 阅读交互、源码跳转与单文件交付](https://github.com/jinghonh/LaTeX-Review/issues/24) | #24、#28、#29 |
| [第一点五版 03 表格、移动与公式高级差异](https://github.com/jinghonh/LaTeX-Review/issues/25) | #25、#26、#30 |
| [第一点五版 04 参考文献与自定义宏兼容](https://github.com/jinghonh/LaTeX-Review/issues/27) | #27、#32 |
| [第二版 01 真实编译、页面对比与沙箱渲染](https://github.com/jinghonh/LaTeX-Review/issues/33) | #33、#34、#35 |
| [第二版 02 持续集成产物与审阅评论发布](https://github.com/jinghonh/LaTeX-Review/issues/36) | #36、#37 |
| [第二版 03 论文规则检查](https://github.com/jinghonh/LaTeX-Review/issues/38) | #38 |

本轮已替代旧票：#26、#28、#29、#30、#31、#32、#34、#35、#37。关闭不表示实现完成。
