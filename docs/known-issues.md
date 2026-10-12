# 已知问题（搬运期发现、未顺手修复）

按《开源改造实施计划》全局约定：拆包/外置期间**纯搬运不重构**。以下问题在
搬运中发现但未修改，避免混入行为变更；确需修复时单独开任务、单独提交。

| # | 位置 | 问题 | 发现于 | 影响 |
|---|---|---|---|---|
| 1 | `pipeline.py`（原 `main_async` 9e 段） | 死代码：`model_id = None; if processed: pass` 是无效的"模型 ID 复用"残留，每次运行都随机生成新的 Model ID | Task 1 | 无功能影响；Model ID 每次随机与原版一致 |
| 2 | `cli.py --cache` 帮助文本 | `--cache` 的 help 写着 "(默认: enrichment_cache.json)"，实际默认已是 `data/cache/enrichment_cache.json` | Task 1 | 仅帮助文本不准确；不改会破坏 `--help` 与改造前逐字节一致的验收 |
| 3 | `pipeline.py` / `batch_generate_audio` | 取消仅在 LLM 批次边界生效（按设计）；音频生成循环内部没有取消检查点，大批量词表的音频阶段无法即时取消 | Task 1 | GUI（Task 6）如需音频阶段即时取消，需另行扩展 `batch_generate_audio` |
| 4 | `cache.filter_new_entries` | 参数 `enrichment_cache` 未使用（仅 processed.json 参与跳过判断） | Task 1 | 无功能影响，签名保留以免破坏调用方 |
| 5 | `enricher.process_batch` | 局部变量 `word_keys` 计算后未使用 | Task 1 | 无功能影响 |
| 6 | 原单文件末尾"附录 A/B" | 附录 A（.env.example 内容说明）与附录 B（TTS 引擎替换指南）是纯文档字符串，无代码作用。已移至 `docs/legacy-appendices.md` 保存 | Task 1 | 无 |
