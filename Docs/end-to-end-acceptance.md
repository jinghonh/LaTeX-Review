# 端到端验收

公共合成论文在 `tests/fixtures/e2e/`：旧版与新版均含多文件正文、宏文件、公式、表格、图片和文献；新版另有插入段落、未知宏与缺图。`expectations.json` 逐项记录主变更类别、类型和双侧原文件行号。测试会追加 150 段两侧相同的正文，验证插入不会造成级联误报；并建立本地 Git 历史，单独验证文献元数据改动不产生首版语义主变更。

```sh
python -m pip install '.[test]'
python -m pytest -q tests/test_e2e.py
```

对于有授权的本地论文，在**仓库之外**建立私有验收目录：

```sh
python scripts/verify-e2e.py \
  --project-root '/path/to/paper-repository' \
  --entry path/to/main.tex \
  --old-revision '<已核实的旧版提交>' \
  --scope path/to \
  --private-dir '/private/path/to/new-acceptance-run'
```

`--scope` 默认取入口所在目录，须覆盖入口实际依赖。脚本从指定提交导出旧版，在当前磁盘工作区只读收集已跟踪和未忽略的未跟踪文件；暂存区不会代替磁盘内容，磁盘已删除文件不会从索引回填。结果目录必须事先不存在。脚本对原仓库的状态与索引前后哈希进行核对，并以哨兵确认没有调用 TeX 引擎。它核对报告页的主变更数量与 `diff.json` 一致、独立诊断与差异数据一致，并逐一检查确定的原文件位置；不确定位置必须有理由。

私有目录内的 `old/`、`new/` 是固定副本，`report/` 是完整报告，`run.json` 保存完整旧版提交、两个副本和依赖文件的 SHA-256 指纹、退出码、统计、诊断代码与耗时，`stdout.log` 和 `stderr.log` 保存运行输出。脚本还会逐卡核对页面与数据中的类型、类别和双侧位置。报告返回 0 或 2 都可接受，具体降级须根据诊断人工评估；其他退出码为验收失败。真实论文的逐项人工预期清单应放在该私有目录，不要把原稿、路径、指纹或验收副本提交到公共仓库。
