# 固定复验检查点：7744314（未完成交付）

固定应用源码：`7744314e36634cdba55a306d9386b01107005336`。这是累计 Draft PR #56 的下一次复验检查点，不是性能通过或完整交付声明。

## 本次变更与已运行检查

- 来源区间只对已经验证的不可变、有序段采用二分定位；区间裁剪、Unicode/CRLF 坐标、重复包含和错误行为保持原实现，非标准或可变输入走原线性路径。没有结果缓存。
- 写作检查只使用原始捕获的标签事实，不把编译编号或未捕获的辅助文件当作同一内容身份下的检查依据。预览、翻译和原双版本 Diagnostic 的编译行为保持不变。
- 动态计算命令导致的缺失证明不足，沿用既有不完整覆盖机制，不报告未经证明的缺失问题。
- 检查引擎补丁版本为 `1.0.1`。旧引擎/解析器报告仍可读取，但不会保持当前写入权限；重新检查后恢复正常。知识、论文和历史不被重写。

组合验证：**430 passed / 106.76s / 0 skipped**，包含全部检查测试、独立 SourceMap 原实现 oracle、来源与结构、原生阅读恢复、旧报告兼容、保存通知、服务生命周期以及私有夹具测试。更早的分组检查存在重叠，不相加为完整回归数量。本固定版本完整 Linux/macOS 回归与新的完整保存性能仍待复验。

## 复验步骤

在已有完整 Git 历史的隔离副本中执行；不要覆盖有未提交改动的工作树。使用独立 Python 3.11 环境，或复用上次隔离环境并重新安装这个固定版本。以下命令不使用私有论文、不调用模型，不需要提供密钥。

```sh
git fetch origin
git switch --detach 7744314e36634cdba55a306d9386b01107005336
git diff --exit-code HEAD
python -m pip install '.[test]'
python -m playwright install chromium
evidence=$(mktemp -d "${TMPDIR:-/tmp}/writing-check-7744314.XXXXXX")
python --version > "$evidence/python.txt"
python -m pip freeze > "$evidence/dependencies.txt"
LATEX_REVIEW_REQUIRE_BROWSER=1 ./scripts/verify-macos.sh > "$evidence/verify.log" 2>&1
printf '%s\n' "$?" > "$evidence/verify.exit-code"
```

完整回归结束、其拥有的临时服务已停止后，顺序独立执行两组，保持与上一轮相同的默认 800ms 静默期限、0.5s 观察间隔、40 文件、5000 单元和包括第一次保存的完整计时边界：

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

若一组未完成或清理失败，先确认其拥有的进程已退出，再启动另一组；不要把不完整运行当成 p95。两组都保留所有十次保存，包括慢样本和首次保存；正文模式还必须证明实际发布的可见文本改变及 5000 个单元。原始输出可继续私下保留/回传，不要公开论文原文、密钥、请求头或本机私人路径。

## 已知未通过项

前一应用检查点 `f563199` 的本机 macOS 完整功能验证为 1298 passed / 2 skipped，完整保存 p95 为 comment **6.111938s**、prose **35.028981s**，均未达到 ≤2s；其他用户负载未控制，不能仅凭总时长归因。来源见[请求人的公开验收记录](https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6651636068)。这些旧证据不会被本次结果替换。

本次尚未宣称解决所有保存延迟，完整性能目标不变。真实论文、少量明确获准的模型调用与作者质量评价也仍独立待完成；不会默认启用付费调用。
