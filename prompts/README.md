# prompts/ — 提示词目录（社区可改，无需碰 Python）

本目录下的每个 `.md` 文件都是程序实际使用的提示词。**改文件即改行为**，
保存后下一次生成立即生效（热加载），无需重启。

> 提示词可以改是**特性**。改坏了不要紧：删掉文件或清空内容，程序会自动
> 回退到内置默认值；GUI 内也有「↺ 恢复默认」按钮一键还原。

## 文件与占位符一览

| 文件 | 用途 | 占位符 | 说明 |
|---|---|---|---|
| enrich.md | 富化主提示词（词形还原/语境释义/双语词性/JSON 约定） | `{word_list}` | 替换为本批单词与例句列表 |
| check_accuracy.md | 准确性质检 Agent | `{items_keys}` `{payload}` | 键列表 / 待检查数据 JSON |
| check_completeness.md | 完整性质检 Agent | `{items_keys}` `{payload}` | 同上 |
| fix.md | 质检失败修复 Agent | `{word}` `{sentence}` `{issues_json}` `{fix_instructions_json}` `{data_json}` | 词条/原句/问题/指令/当前 JSON |
| sentence_gen.md | 纯单词模式造句（先造例句再富化） | `{word_list}` | 替换为单词列表 |
| format_assist.md | 任意笔记 → 标准格式 | `{target_format}` `{text}` | 目标格式名 / 原文 |
| assistant.md | GUI「AI 助手」页的系统提示词 | 无 | 限定助手身份与输出规范 |

## 占位符语法约定

- 占位符由程序用 `str.replace()` 整体替换（**不是** Python `.format()`），
  因此提示词里可以自由使用花括号（JSON 示例等），不会被误解析。
- 请勿删除占位符行——缺了占位符，对应内容就拼不进提示词，生成质量会下降。
- 修改时建议只调整指令文字，保留结构（标题、判定规则、输出格式示例）。

## 恢复默认

- **GUI**：提示词页每个文件旁的「↺ 恢复默认」按钮。
- **手动**：删除对应的 `.md` 文件（或清空内容），程序启动时回退内置默认并写警告日志。
- 内置默认值保存于 `app/core/prompts.py` 的 `DEFAULT_PROMPTS`。

## 社区示例

欢迎在 Issue / Discussion 分享你的提示词改法，例如：

- enrich.md 增加词根词缀拆解要求，让卡片背面多一节「词根拆解」。
- check_completeness.md 提高例句数量要求（每义项 3 个例句）。
- sentence_gen.md 指定例句主题（如全部用商务/职场语境）。

改动提示词后建议先 `--limit 5` 小样本验证效果，再全量生成。
