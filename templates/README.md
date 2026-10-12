# templates/ — Anki 卡片模板目录

这三个文件控制卡片的样式与排布。**改样式不影响数据**：富化数据字段不变，
Anki 导入结果只在外观上变化。文件缺失/为空时程序自动回退内置默认并写警告。

## 可用字段（genanki 模型字段，共 5 个）

| 字段 | 来源 | 内容 |
|---|---|---|
| `{{序号}}` | Markdown 行号 | 词条序号（也参与 GUID 唯一化） |
| `{{正面}}` | 例句 + 填空标记 | 含 `{{c1::...}}` 填空与 `{{type:cloze:正面}}` 输入框标记 |
| `{{背面}}` | LLM 富化 JSON → HTML | 词头/音标/词源/核心释义表/衍生词/易混词/语法/记忆技巧 |
| `{{提示}}` | context_meaning | 语境中文提示（回退 core_meanings[0].usage） |
| `{{原句翻译}}` | sentence_translation | 原句中文翻译 |

## 文件说明

| 文件 | 对应模型属性 | 说明 |
|---|---|---|
| card_front.html | qfmt | 正面：填空句 + 提示 + 打字输入框。**保留 `{{cloze:正面}}` 与 `{{type:cloze:正面}}`，否则填空/输入失效** |
| card_back.html | afmt | 背面：答案对照 + 背面 HTML。**保留 `{{cloze:正面}}`、`{{type:cloze:正面}}`、`{{背面}}`** |
| card.css | css | 全部样式（桌面/移动端/Anki 夜览 nightMode 适配） |

## 注意事项

- `{{...}}` 是 Anki 模板语法（字段引用/填空/打字），不是占位符；改样式时勿动。
- 卡片内置 HTML 的 CSS 类名（`.hint`、`.sentence-section`、`.cm-table`、`.word-header`、
  `.memory-tip` 等）由 `app/core/anki_packager.py` 的 `build_back_html()` 生成——
  改 CSS 里的类名会导致对应区域失样。
- 恢复默认：删除文件即可（程序回退内置默认）；内置值在 `app/core/templates.py`。
- 修改后直接重新运行生成即可看效果；Anki 端需重新导入 .apkg（或用「检查媒体」同步）。
