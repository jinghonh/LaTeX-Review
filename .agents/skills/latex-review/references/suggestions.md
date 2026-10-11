# 源码替换建议

建议保存原始 LaTeX 与替换 LaTeX。添加建议不会回写源码。

1. 读取目标单元的 `raw_latex`、`source` 和 `source_span`。根据原始 LaTeX 构造替换；预览文字偏移不是源码偏移。
2. 用 `add` 或 `reply` 的 `--original-chunk`、`--replacement-chunk` 保存建议。原文须在所属源码单元内唯一。替换块可以为空。不可靠区间允许保存说明，但不能自动应用。
3. 用 `preview THREAD_ID MESSAGE_ID` 查看差异。核对文件、原文和替换范围。源码指纹变化后重新读取并提出新建议，不绕过过期检查。
4. 用户明确授权后执行 `apply THREAD_ID MESSAGE_ID`。Web 的“接受并应用源码建议”也是显式应用。检查 `source_changed`、`rebuild` 和实际源码差异。
5. 应用成功后继续复核，再决定是否解决批注。缺少配方或重建失败时说明“源码已修改、报告待更新”。旧报告及批注仍保留。

```mermaid
flowchart LR
 A[读取源码区间] --> B[保存替换建议]
 B --> C[预览差异]
 C --> D[明确授权后应用]
 D --> E[核对源码与更新报告]
 E --> F[独立判断是否解决]
```

```sh
latex-review-annotations REPORT_DIR --content --unit-id UNIT_ID --fields id,raw_latex,source,source_span
latex-review-annotations REPORT_DIR add --quote '目标原文' --body '修改理由' --author Codex --category suggestion --original-chunk '原始 LaTeX' --replacement-chunk '替换 LaTeX'
latex-review-annotations REPORT_DIR preview THREAD_ID MESSAGE_ID
latex-review-annotations REPORT_DIR apply THREAD_ID MESSAGE_ID
```

区间须可靠、连续、单文件且可写。Git 历史版本只读。简单图注和单元格提供精确位置，复杂结构明确降级。离线报告可保存建议，回写需要本机源码服务。
新报告保存重建配方和固定旧版来源，使用生成时的配置，包括已经显式启用的编译设置。重建先写临时报告，成功后切换。旧报告路径在 `rebuild.previous_report`。重建不继承旧浏览器验收声明。
