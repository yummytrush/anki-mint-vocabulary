# GUI 前端设计文档 — AnkiMint（anki-mint-vocabulary）

日期：2026-10-10
依据：[开源改造设计方案 §5](../specs/2026-10-10-open-source-optimization-design.md)、[实施计划 Task 5](../plans/2026-10-10-implementation-plan.md)
状态：**待用户审阅**（审阅通过后 Task 6 才开工）
技术栈：PySide6 (Qt 6.6+)，纯中文界面，无网络/无 Key 时全页可走查

---

## 0. 设计目标与边界

- **目标**：非程序员雅思考生 3 步内用上工具（装 exe → 填 Key → 拖文件生成）。
- **GUI 是 core 的薄壳**：不承载业务逻辑；全部生成/质检/缓存行为与 CLI 完全一致（复用 Task 1–4 的 `pipeline.run` / `parse_preview` / `AppConfig` / `test_llm_connection` / `prompts` / `normalize_any_text`）。
- **明确边界**：
  - 五页签，**无卡片模板编辑页**——`templates/` 保持文件级编辑，提示词页提供「打开 templates/ 目录」入口（见 §3.3 与 §11 决策点 D1）。
  - AI 助手对话历史仅存于内存，不落盘（§11 决策点 D2）。
  - 数据目录覆盖仍是环境变量专属，高级设置页只读展示（见 §3.4）。

---

## 1. 窗口与导航

### 1.1 主窗口

| 项 | 值 |
|---|---|
| 默认尺寸 | 1024 × 720 |
| 最小尺寸 | 860 × 600 |
| 标题栏 | `AnkiMint — AI 词汇卡片生成器 v0.1.0`（版本号读 `app.__version__`） |
| 菜单栏 | 无（操作全部在页内按钮；全局快捷键见 §8） |
| 状态栏 | 显示当前悬停/焦点控件的说明（= Tooltip 文案，见 §8.3）+ 右侧常驻当前供应商/模型摘要 |

### 1.2 页签顺序（QTabWidget，顶部）

| 序号 | 页签 | 快捷键 | 职责 |
|---|---|---|---|
| 1 | 工作台 | Ctrl+1 | 拖入文件 → 预检 → 输出设置 → 运行/取消 → 汇总 |
| 2 | AI 设置 | Ctrl+2 | 供应商预设、Key、模型、并发/限速/批参数、连通测试 |
| 3 | 提示词 | Ctrl+3 | prompts/ 文件列表 + 编辑 + 恢复默认 + 跳转 AI 助手 |
| 4 | 高级设置 | Ctrl+4 | 质检/重要性/音频/TTS 等其余旋钮（白话悬停） |
| 5 | AI 助手 | Ctrl+5 | 流式对话、代码块采纳、提示词优化联动 |

### 1.3 页签间数据流

```
                    ┌────────────────────────────────────────────┐
                    │            MainWindow（全局状态）            │
                    │  app_cfg: AppConfig（内存单例）              │
                    └───────┬───────────────┬───────────────┬────┘
                            │               │               │
        AI 设置页 ──保存──► │save()→ data/config.json        │
                            │apply_to_config()（保存后立即）  │
                            ▼               │               │
                    config 模块全局 ◄────────┘               │
                            │  llm.rate_limiter 重建         │
        工作台页 ──RunConfig 组装──► pipeline.run(args, cb) ──► 生成 .apkg
        高级设置页 ──保存──► （同 AI 设置页路径，同一 AppConfig 单例）
        提示词页 ──写文件──► prompts/*.md ──reload_prompt──► prompts 缓存（下次生成生效）
        AI 助手页 ◄──带上下文跳转── 提示词页（传参约定见 §4.5）
```

要点：

- `AppConfig` 单例由 MainWindow 持有；AI 设置页与高级设置页编辑**同一份**；任一保存 → `save()` 落盘 + `apply_to_config()` 写入 core 全局 + 重建 `llm.rate_limiter`。
- 工作台「开始」前再次 `apply_to_config()` 兜底（保证未保存的临时改动不影响运行——运行只用已保存配置）。
- 各页只读引用 `app_cfg` 做展示（Key 一律经 `masked()` 回显）。

---

## 2. 每页线框图与控件清单

图例：`[按钮]`、`(单选/下拉)`、`[__输入框__]`、`▓进度`、`▤多行文本`、`(√)` 复选。未标注单位均为 px。

### 2.1 工作台（workbench_page.py）

```
┌──────────────────────────────────────────────────────────────────┐
│ 拖入文件区 (DropZone, 高 120, 虚线边框)                            │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │   ⬇  拖入 .md / .txt 文件到这里，或点击选择文件                │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│ 已选文件: C:\…\words.md                          [重新选择]       │
│ ┌─ 解析预检 ──────────────────────────────────────────────────┐ │
│ │ 识别到 120 条 · 格式：两行式 · 3 行无法识别        [详情 ▾]   │ │
│ │ ┌(详情展开)──────────────────────────────────────────────┐  │ │
│ │ │ [行 5 ] 非词条行（应为 '数字. **单词**'）: 这是散乱的…    │  │ │
│ │ │ [行 9 ] 缺少例句行: 6. **orphan**                       │  │ │
│ │ │ 无法识别的行将被自动忽略。               [去 AI 助手修正] │  │ │
│ │ └────────────────────────────────────────────────────────┘  │ │
│ │ ⓘ 上次已完成 87/120，本次只处理新增/变更词                   │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│ 格式: (自动检测 ▾)    输出: [data\output\__________] [浏览…]      │
│       (两行式)         文件名: [IELTS-{日期}.apkg_] → 预览: IELTS-2026-10-10.apkg │
│       (单行式)                                                    │
│       (冒号式)        [✨ AI 规范化…]   (∎) 全部重新生成(忽略缓存) │
│       (纯单词列表)                                                 │
│ 卡组名称: [____________________________]（占位: AI 词汇强化训练）   │
│                                                                  │
│ ┌─ 运行 ───────────────────────────────────────────────────────┐ │
│ │  解析 ──● 造句 ──○ 音频 ──○ 富化(含质检) ──○ 打包 ──○         │ │
│ │  ▓▓▓▓▓▓▓▓▓▓░░░░░░░░  富化 3/12 批        [开始生成] [取消]   │ │
│ │ ┌─ 日志 ▤ ────────────────────────────────────────────────┐ │ │
│ │ │ 21:07:44 [INFO] 造句完成: 3 词成功 → 中间文件: …          │ │ │
│ │ │                                                          │ │ │
│ │ └──────────────────────────────────────────────────────────┘ │ │
│ │ ── 失败 / 跳过的词 (2) ─────────────────── [复制] ──          │ │
│ │  · tenure: 质量检查未通过: 缺少音标                            │ │
│ │  ⓘ 重新运行时会自动重试这些词                                 │ │
│ └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

控件清单：

| ID | 类型 | 默认值 | 说明 / 启用条件 |
|---|---|---|---|
| wb_drop | DropZone(QFrame) | — | 接受 `.md`/`.txt` 本地文件 URL；点击进入 `QFileDialog`；运行中禁用 |
| wb_file_label | QLabel | 空 | 显示已选文件完整路径；无文件时隐藏整行 |
| wb_repick_btn | QPushButton `重新选择` | — | 同 wb_drop 的点击行为；无文件时隐藏 |
| wb_preview_group | QGroupBox `解析预检` | 隐藏 | 有文件后显示 |
| wb_preview_summary | QLabel | — | `识别到 {n} 条 · 格式：{fmt} · {p} 行无法识别` |
| wb_problems_toggle | QToolButton `详情 ▾` | 收起 | 仅当 problem_lines 非空时可用 |
| wb_problems_list | QListWidget | — | 每行：`[行 {no}] {reason}: {text…}`，等宽字体 |
| wb_goto_assistant_btn | QPushButton `去 AI 助手修正` | — | 仅当问题行非空时显示；传参约定见 §4.5 |
| wb_resume_hint | QLabel | 隐藏 | 续跑提示（出现条件见 §4.2） |
| wb_format_combo | QComboBox | `自动检测` | 选项：自动检测/单行式/两行式/冒号式/纯单词列表；手动选择后按该格式重新预检 |
| wb_output_dir_edit | QLineEdit | `data\output` | 输出目录；运行中禁用 |
| wb_output_dir_btn | QPushButton `浏览…` | — | `QFileDialog.getExistingDirectory` |
| wb_filename_edit | QLineEdit | `IELTS-{日期}.apkg` | 支持 `{日期}` `{词数}` 占位符 |
| wb_filename_preview | QLabel | — | 实时显示替换占位符后的最终文件名 |
| wb_normalize_btn | QPushButton `✨ AI 规范化…` | — | 打开规范化对话框（§4.6）；运行中禁用 |
| wb_force_chk | QCheckBox `全部重新生成(忽略缓存)` | 关 | 映射 `RunConfig.force`；勾选时隐藏续跑提示（§4.2）；运行中禁用 |
| wb_deck_name_edit | QLineEdit | 空（占位=默认名） | 映射 `RunConfig.deck_name`；支持 {日期}/{词数} 与 `::` 子卡组；留空用默认「AI 词汇强化训练」；运行中禁用 |
| wb_stage_widget | ProgressStages | 全灰 | 5 段：解析/造句/音频/富化(含质检)/打包；造句段仅纯单词模式点亮 |
| wb_progress_bar | QProgressBar | 0 | 富化阶段按批次确定进度；其余阶段忙等样式 |
| wb_start_btn | QPushButton `开始生成` | 禁用 | 启用条件：预检完成 且 已配置 Key 且 非运行中（§4.1 状态机） |
| wb_cancel_btn | QPushButton `取消` | 隐藏 | 仅运行中显示 |
| wb_log_panel | QPlainTextEdit | 只读 | 等宽字体；空状态文案占位 |
| wb_log_clear_btn | QPushButton `清空` | — | 清空日志面板 |
| wb_failure_group | QGroupBox | 隐藏 | 运行结束且有失败/跳过词时显示 |
| wb_failure_list | QListWidget | — | `· {word}: {reason}` |
| wb_failure_copy_btn | QPushButton `复制` | — | 复制失败词列表到剪贴板 |

### 2.2 AI 设置（ai_settings_page.py）

```
┌──────────────────────────────────────────────────────────────────┐
│ ┌─ 引导横幅(仅首次/无 config.json 但有 .env Key 时显示) ────────┐ │
│ │ ⓘ 检测到 .env 中已配置 API Key            [一键导入到本工具]  │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ 当前生效: DeepSeek · deepseek-chat · Key: sk-1****2b86            │
│                                                                  │
│ 供应商:   (DeepSeek ▾)   ← DeepSeek / OpenAI 兼容 / SiliconFlow /│
│                            Ollama / 自定义                       │
│ Base URL: [https://api.deepseek.com____________________]          │
│ API Key:  [sk-•••••••••••••••••••••••••] [👁]                   │
│ 模型:     [deepseek-chat____]                                     │
│                                                                  │
│ 并发数:       [8]   每分钟限速: [60]                              │
│ 批大小:       [5]   批最大 tokens: [8192]                         │
│                                                                  │
│ [测试连通]  ✅ 连接成功 · 延迟 1794ms · 模型回复正常               │
│                                                                  │
│                          [↺ 恢复默认]    [保存配置] ●未保存       │
└──────────────────────────────────────────────────────────────────┘
```

控件清单：

| ID | 类型 | 默认值 | 说明 / 启用条件 |
|---|---|---|---|
| ai_import_banner | QFrame(琥珀底) | 隐藏 | `config.json` 不存在且 env 有 Key 时显示（= Task 3 `hint_env_import` 的 GUI 版） |
| ai_import_btn | QPushButton `一键导入到本工具` | — | 以当前生效值 `save()`，写 `data/config.json` 后隐藏横幅 |
| ai_summary_label | QLabel | `未配置` | `当前生效: {label} · {model} · Key: {masked}`，无 Key 显示 `未配置` |
| ai_provider_combo | QComboBox | DeepSeek | 选预设自动填充 Base URL 与推荐模型（可再手改）；手改 URL/模型后预设自动跳到「自定义」 |
| ai_base_url_edit | QLineEdit | 预设值 | 失焦校验非空 |
| ai_api_key_edit | QLineEdit(Password) | 空 | 密码打码显示 |
| ai_key_show_btn | QToolButton `👁` | 关 | 切换 EchoMode Normal/Password |
| ai_model_edit | QLineEdit | 预设值 | — |
| ai_concurrency_spin | QSpinBox 1–32 | 8 | 悬停文案见 §5.3 |
| ai_rate_limit_spin | QSpinBox 1–600 | 60 | 同上 |
| ai_batch_size_spin | QSpinBox 1–20 | 5 | 同上 |
| ai_batch_tokens_spin | QSpinBox 1024–16384 | 8192 | 同上 |
| ai_test_btn | QPushButton `测试连通` | — | 禁用条件：URL/Key/模型任一为空，或正在测试 |
| ai_test_result | QLabel | — | 成功/失败分类渲染（§4.3） |
| ai_reset_btn | QPushButton `↺ 恢复默认` | — | 恢复为「当前供应商预设 + 代码默认参数」，Key 清空；弹确认 |
| ai_save_btn | QPushButton `保存配置` | — | 禁用条件：无改动；保存后 `●未保存` 徽标消失 |
| ai_dirty_badge | QLabel `●未保存` | 隐藏 | 任一字段改动后出现（琥珀色） |

### 2.3 提示词（prompts_page.py）

```
┌──────────────────────────────────────────────────────────────────┐
│ ⓘ 提示词占位符说明见 prompts/README.md，保存即热加载              │
│ ┌────────────┬─────────────────────────────────────────────────┐ │
│ │ 文件列表    │  富化（enrich.md）          ●未保存              │ │
│ │ ▶富化       │ ┌─ 编辑器(等宽) ▤ ────────────────────────────┐ │ │
│ │  造句       │ │ 你是一名英语词汇学专家。请为以下每个单词/词组… │ │ │
│ │  准确性质检  │ │                                              │ │ │
│ │  完整性质检  │ │                                              │ │ │
│ │  质检修复    │ └──────────────────────────────────────────────┘ │ │
│ │  格式规范化  │ [✨ 让 AI 优化此提示词]                          │ │
│ │  AI 助手    │          [↺ 恢复默认] [放弃修改] [保存并热加载]   │ │
│ ├────────────┴─────────────────────────────────────────────────┤ │
│ │ [打开 prompts/ 目录]   [打开 templates/ 目录]                  │ │
│ └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

控件清单：

| ID | 类型 | 默认值 | 说明 / 启用条件 |
|---|---|---|---|
| pr_note_label | QLabel(浅色) | 见文案表 | 顶部常显说明 |
| pr_file_list | QListWidget | — | 7 个提示词文件（README.md 除外），显示名 `中文名（文件名）`；当前项加粗 |
| pr_title_label | QLabel | 空 | 当前文件名 + `●未保存` 徽标 |
| pr_editor | QPlainTextEdit(等宽) | 空 | 启用条件：已选文件；空状态见 §6 |
| pr_assist_btn | QPushButton `✨ 让 AI 优化此提示词` | — | 带当前文件全文跳转 AI 助手页（§4.5）；未配置 Key 时禁用并提示 |
| pr_restore_btn | QPushButton `↺ 恢复默认` | — | 弹确认后写回内置默认（`prompts.restore_default`）并热加载 |
| pr_revert_btn | QPushButton `放弃修改` | 禁用 | 有未保存改动时启用；重读磁盘内容 |
| pr_save_btn | QPushButton `保存并热加载` | 禁用 | 有未保存改动时启用；写文件 + `reload_prompt(name)` |
| pr_open_prompts_btn | QPushButton `打开 prompts/ 目录` | — | `QDesktopServices.openUrl` |
| pr_open_templates_btn | QPushButton `打开 templates/ 目录` | — | 卡片模板入口（见 §0 边界） |

### 2.4 高级设置（advanced_page.py）

```
┌──────────────────────────────────────────────────────────────────┐
│ ⓘ 这些参数影响每次生成；鼠标悬停(或键盘聚焦)查看白话说明           │
│                                                                  │
│ 富化质量                                                          │
│  (√) 启用质检                    质检重试次数: [2]      [↺]        │
│  重要性阈值: (低 ▾)              (∎) 例句中替换全部出现  [↺]        │
│                                                                  │
│ 音频与发音                                                        │
│  (√) 生成发音                    发音引擎: (auto ▾)     [↺]        │
│  回退链:    [gtts,edge____]      Edge 音色: [en-GB-SoniaNeural]   │
│  音频代理:  [________________]   音频超时: [8] 秒                 │
│  音频失败冷却: [24] 小时                            [↺]           │
│                                                                  │
│ 数据目录（只读，环境变量修改）                                      │
│  数据: data\   缓存: data\cache\   音频: data\media\              │
│                                                                  │
│                     [↺ 全部恢复默认]        [保存配置] ●未保存     │
└──────────────────────────────────────────────────────────────────┘
```

控件清单（每行 = ParamRow：标签 + 控件 + ↺ 单项重置 + 悬停气泡）：

| ID | 类型 | 默认值 | 说明 / 启用条件 |
|---|---|---|---|
| adv_quality_checks | ParamRow(QCheckBox) | 开 | 映射 `quality_checks_enabled` |
| adv_quality_attempts | ParamRow(QSpinBox 0–5) | 2 | 映射 `quality_max_attempts` |
| adv_importance_min | ParamRow(QComboBox 极高/高/中/低) | 低 | 映射 `importance_min_level` |
| adv_replace_all | ParamRow(QCheckBox) | 关 | 映射 `replace_all` |
| adv_audio_enabled | ParamRow(QCheckBox) | 开 | 映射 `audio_enabled` |
| adv_tts_provider | ParamRow(QComboBox auto/gtts/edge/none) | auto | 映射 `tts_provider` |
| adv_tts_fallbacks | ParamRow(QLineEdit) | `gtts,edge` | 映射 `tts_fallbacks`；`provider=auto` 时启用 |
| adv_tts_voice | ParamRow(QLineEdit) | `en-GB-SoniaNeural` | 映射 `tts_voice` |
| adv_audio_proxy | ParamRow(QLineEdit) | 空 | 映射 `audio_proxy`；占位 `http://127.0.0.1:7890` |
| adv_audio_timeout | ParamRow(QSpinBox 2–60) | 8 | 映射 `audio_timeout`（秒） |
| adv_audio_cooldown | ParamRow(QSpinBox 0–168) | 24 | 映射 `audio_fail_cooldown`（界面小时 ↔ 存储秒，换算在 page 层） |
| adv_dirs_info | QLabel(只读) | 当前目录 | 数据目录仅环境变量修改（§0 边界），不参与保存 |
| adv_save_btn / adv_reset_all_btn | QPushButton | — | 与 AI 设置页同一 `AppConfig` 单例；「全部恢复默认」弹确认 |

### 2.5 AI 助手（assistant_page.py）

```
┌──────────────────────────────────────────────────────────────────┐
│ ⓘ 助手只回答本工具相关问题；对话消耗你配置的 API 额度              │
│ ┌─ 上下文卡片(从提示词页跳转时出现) ────────────────────────────┐ │
│ │ 📎 prompts/enrich.md（全文已附）                    [移除]    │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌─ 对话区(QScrollArea) ────────────────────────────────────────┐ │
│ │ 🧑 帮我优化这个提示词，要求：例句更贴近雅思写作语境            │ │
│ │                                                              │ │
│ │ 🤖 好的，建议这样改（流式渲染中…▌）                           │ │
│ │   ```                                                         │ │
│ │   你是一名英语词汇学专家…                        [应用到编辑器]│ │
│ │   ```                                                         │ │
│ │                                                              │ │
│ │   （空状态：欢迎卡片 + 3 个可点击示例问题，见 §6.5）           │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌─ 输入 ▤ ───────────────────────────────┐ [发送] [停止]        │
│ │ 输入问题，Enter 发送，Shift+Enter 换行   │                     │
│ └──────────────────────────────────────────┘                    │
│ [新建对话]  [清空对话]                                           │
└──────────────────────────────────────────────────────────────────┘
```

控件清单：

| ID | 类型 | 默认值 | 说明 / 启用条件 |
|---|---|---|---|
| as_note_label | QLabel(浅色) | 见文案表 | 定位与费用提示（§5.5） |
| as_context_card | QFrame | 隐藏 | 从提示词页跳转时显示；`📎 {file}（全文已附）` + 移除按钮 |
| as_chat_scroll | QScrollArea | — | 消息气泡列表（自定义 MessageBubble 控件） |
| as_input | QPlainTextEdit | 空 | 3 行高；Enter 发送 / Shift+Enter 换行；流式输出期间禁用 |
| as_send_btn | QPushButton `发送` | 禁用 | 启用：输入非空 且 已配置 Key 且 非流式中 |
| as_stop_btn | QPushButton `停止` | 隐藏 | 仅流式输出期间显示 |
| as_new_btn | QPushButton `新建对话` | — | 清空历史与上下文卡片 |
| as_clear_btn | QPushButton `清空对话` | — | 仅清空消息（保留上下文卡片）；弹确认 |
| as_apply_btn | QPushButton `应用到编辑器` | — | 每个完整代码块下出现（规则见 §4.4）；跳回提示词页写入编辑框（不自动保存） |

---

## 3. 交互规格

### 3.1 工作台状态机

```
                 拖入/选择文件成功                      拖入失败(格式不符)
        ┌────────────────────────────────┐         ┌──────────┐
        ▼                                │         ▼          │
   ┌────────┐  预检成功   ┌──────────┐  开始  ┌──────────┐    │
   │  空闲  │ ─────────► │ 预检完成 │ ─────► │  运行中  │    │
   └────────┘            └──────────┘       └────┬─────┘    │
        ▲   ▲                ▲   ▲                │          │
        │   │ 换文件          │   │     ┌─────────┼─────────┐│
        │   └────────────────┘   │     ▼         ▼         ▼│
        │                        │  ┌───────┐ ┌───────┐ ┌───────┐
        └────────────────────────┴─│ 已取消 │ │  失败 │ │  成功 │
             重跑/换文件            └───────┘ └───────┘ └───────┘
```

- **空闲**：无有效文件。预检区隐藏，开始禁用。
- **预检完成**：`parse_preview` 成功（条目数可含 0 问题行）。改格式下拉 → 立即重新预检。
- **运行中**：开始后锁定输入区（DropZone/格式/输出/规范化全禁用），显示取消按钮。
- **已取消**：取消在下一 LLM 批次边界生效；横幅「已取消 — 缓存已保留，重新开始将从未完成的词继续」。
- **失败**：Worker 抛致命异常（非单词级失败）。横幅显示错误摘要 + 日志末段。
- **成功**：pipeline 正常返回汇总（可含单词级失败词清单）。横幅显示输出文件路径（可点击打开所在目录）。

各状态下控件可用性矩阵：

| 控件 | 空闲 | 预检完成 | 运行中 | 已取消 | 失败 | 成功 |
|---|---|---|---|---|---|---|
| wb_drop / wb_repick_btn | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| wb_format_combo | ✗ | ✓ | ✗ | ✓ | ✓ | ✓ |
| 输出目录/文件名 | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| wb_normalize_btn | ✓ | ✓ | ✗ | ✓ | ✓ | ✓ |
| wb_start_btn | ✗ | ✓ | ✗ | ✓(重新生成) | ✓(重新生成) | ✓(重新生成) |
| wb_cancel_btn | 隐藏 | 隐藏 | ✓ | 隐藏 | 隐藏 | 隐藏 |
| wb_failure_group | 隐藏 | 隐藏 | 隐藏 | 有则显示 | 有则显示 | 有则显示 |

（另：wb_start_btn 在无 Key 时始终禁用，Tooltip = 全局错误文案 `err_no_api_key`。）

### 3.2 取消逻辑与续跑提示

- **取消路径**：点「取消」→ 按钮变「正在取消…」并禁用 → 主线程 `cancel_event.set()` → worker 在 LLM 批次边界检测到 → 已完成词写入缓存 → 返回取消汇总 → 状态机转「已取消」。**不弹确认框**（续跑无损，见 §11 决策点 D4）。
- **续跑提示出现条件**：预检完成后，读 `processed.json`（经 `migrate_processed_keys`），统计当前文件中 `processed_record_complete` 命中的词条数 `done`；`done > 0` 时显示 `ⓘ 上次已完成 {done}/{total}，本次只处理新增/变更词`。`--force` 等价物 = 输出设置行的 `wb_force_chk` 复选框（默认关；勾选后提示隐藏）。

### 3.3 失败词列表交互

- 来源：pipeline 汇总中的 `deduped_failures`（由 worker 解析汇总文本或（更优）pipeline 返回值——实现备注：Task 6 给 `pipeline.run` 增加 `on_failures(list)` 回调或在 RunConfig 上回传结构化结果，避免解析文本）。
- 每行 `· {word}: {reason}`，只读，可选中；「复制」按钮复制全部行为文本。
- 列表下常驻提示 `ⓘ 重新运行时会自动重试这些词`（增量机制天然重试，无需「仅重跑失败词」按钮——YAGNI）。

### 3.4 AI 助手：流式渲染与代码块

**流式渲染规则**：

1. 发送 → 立即插入用户气泡 + 空助手气泡（光标指示 `▌`）。
2. `ChatWorker` 每收到一个 chunk → `chunk(str)` 信号 → 追加到当前助手气泡（纯文本追加，每 ~15 chunk 或收到换行边界时重排一次富文本，避免每 chunk 全量 markdown 重渲染）。
3. 流式期间：输入框禁用、「停止」显示；点「停止」→ worker 关闭 stream → 保留已输出部分，气泡尾追加 `（已停止）`。
4. 完成信号 `done(full_text)` → 全量渲染：``` 围栏块渲染为等宽底纹代码块；行内 `code` 加底纹；其余按纯文本段落显示（**不做**完整 Markdown 渲染——助手输出以说明文为主，YAGNI）。
5. 出错 → 助手气泡显示 `❌ 对话失败：{msg}`（含白话建议，复用连通测试的错误分类文案）。

**代码块「应用到编辑器」按钮规则**：

- 仅对**完整闭合**的 ``` 围栏块出现按钮（流式中未闭合的块不出按钮）。
- 每个完整块一个按钮，无论内容语言。
- 点击 → 切到「提示词」页 → 若编辑器有未保存改动则弹确认（`当前修改将丢失，继续？`）→ 将代码块全文写入编辑框 → 显示 `●未保存` 徽标。**不自动保存**；用户审阅后点「保存并热加载」生效。

**系统提示词**：`prompts/assistant.md`（经 `prompts.get_prompt("assistant")`，用户可改）。历史只保留最近 20 条消息（控制 token 消耗）。

### 3.5 提示词页 → AI 助手 跳转传参约定

- 入口：提示词页 `pr_assist_btn`；工作台问题行 `wb_goto_assistant_btn`。
- 传参结构（MainWindow 信号 `assistantRequested(payload: dict)`）：
  ```python
  {
    "file": "prompts/enrich.md",        # 关联文件（可空）
    "content": "<文件全文>",             # 附入上下文（可空）
    "prefill": "帮我优化这个提示词，要求：",  # 预填到输入框（不自动发送）
  }
  ```
- 助手页收到后：显示上下文卡片（`📎 {file}（全文已附）`），输入框置 `prefill` 并将光标移到末尾，切页签聚焦输入框。
- 发送时若上下文卡片存在：用户消息包装为 `【{file} 当前内容】\n{content}\n\n【我的要求】\n{用户输入}`。
- 上下文卡片在「移除」「新建对话」或成功应用一次后清除。
- 工作台问题行跳转：`file` 为空，`content` 为问题行清单，`prefill = 请帮我把这几行笔记改成两行式格式（数字. **单词** + > 例句）：`。

### 3.6 AI 规范化对话框（wb_normalize_btn）

```
┌─ AI 规范化 ──────────────────────────────────────────┐
│ 粘贴你的笔记文本:                                     │
│ ┌ ▤ ───────────────────────────────────────────────┐│
│ │                                                   ││
│ └───────────────────────────────────────────────────┘│
│ 目标格式: (两行式 ▾)              [开始转换]           │
│ 转换结果:                                            │
│ ┌ ▤(只读) ─────────────────────────────────────────┐│
│ └───────────────────────────────────────────────────┘│
│        [保存为新文件]   [直接使用此结果]   [关闭]      │
└──────────────────────────────────────────────────────┘
```

- 「开始转换」：调 `pipeline.normalize_any_text(text, target_format)`（QThread），结果进只读区；失败显示错误。
- 「保存为新文件」：`QFileDialog.getSaveFileName`（默认 `data\intermediate\<原文件名>-formatted.md`）→ 写文件 → Toast 路径。
- 「直接使用此结果」：写入 `data/intermediate/normalized-{时间戳}.md` → 作为工作台当前文件并立即预检 → 关闭对话框。

---

## 4. 设计 tokens

### 4.1 色板（`app/gui/style.qss` 用 QSS 变量注释维护）

| 角色 | 浅色 | 暗色 |
|---|---|---|
| 主色 primary | `#2980b9` | `#5b9bd5` |
| 主色悬停 | `#1a5276` | `#7db3e0` |
| 成功 success | `#27ae60` | `#4ac07c` |
| 警告 warning | `#e67e22` | `#f0a54a` |
| 错误 error | `#c0392b` | `#e06c5d` |
| 文本主 text | `#2c3e50` | `#dddddd` |
| 文本次 text-muted | `#7f8c8d` | `#9a9a9a` |
| 背景 base | `#ffffff` | `#1e1e1e` |
| 背景面板 surface | `#f0f3f5` | `#2a2a2a` |
| 边框 border | `#d5dbdb` | `#444444` |
| 提示底 hint-bg | `#fef9e7` | `#3a3520` |
| 选中 selection | `#d6eaf8` | `#26425c` |

- 暗色模式跟随系统（`QStyleHints.colorScheme` 监听变化动态切换 QSS 变量；Anki 用户多用暗色）。
- 主色与卡片 CSS（templates/card.css）同源，视觉一致。

### 4.2 字体与排版

| 项 | 值 |
|---|---|
| 界面字体 | `Microsoft YaHei UI, Segoe UI, PingFang SC, sans-serif` |
| 等宽字体（日志/编辑器/代码块/问题行） | `Cascadia Mono, Consolas, monospace` |
| 基准字号 | 14px；页内标题 16px/600；说明小字 12px muted |
| 间距基数 | 4px（组件内 8px、区块间 16px、页边距 20px） |
| 圆角 | 按钮/输入框 6px；卡片/横幅 8px |
| 控件高度 | 输入框/按钮 32px；主按钮（开始生成/保存配置）36px 加粗 |

### 4.3 图标方案

- **功能图标用 Qt 内置 `QStyle.StandardPixmap`**（跨平台、零资源文件）：
  `SP_DialogOpenButton`(打开/浏览)、`SP_DialogSaveButton`(保存)、`SP_MediaPlay`(开始)、`SP_MediaStop`(取消/停止)、`SP_BrowserReload`(恢复默认/重试)、`SP_MessageBoxInformation/Warning/Critical`(状态)。
- **装饰符号用文字字符**：✨(AI 相关)、↺(恢复默认)、👁(显示 Key)、📎(上下文)、●(未保存徽标)。Windows 下渲染稳定，与纯中文界面一致。
- 页签**无图标**（纯文字，保持清爽）。

---

## 5. 文案表（Task 6 `app/gui/strings.py` 的唯一文案来源）

### 5.1 全局

| key | 文案 |
|---|---|
| app_title | AnkiMint — AI 词汇卡片生成器 |
| tab_workbench / tab_ai / tab_prompts / tab_advanced / tab_assistant | 工作台 / AI 设置 / 提示词 / 高级设置 / AI 助手 |
| btn_save / btn_cancel / btn_close / btn_copy / btn_browse | 保存 / 取消 / 关闭 / 复制 / 浏览… |
| btn_reset | ↺ 恢复默认 |
| dirty_badge | ●未保存 |
| tip_saved | 已保存 |
| err_no_api_key | 请先在「AI 设置」配置 API Key |
| confirm_discard | 当前修改将丢失，继续？ |

### 5.2 工作台

| key | 文案 |
|---|---|
| drop_hint | 拖入 .md / .txt 文件到这里，或点击选择文件 |
| drop_reject | 仅支持 .md / .txt 文件 |
| btn_repick | 重新选择 |
| preview_title | 解析预检 |
| preview_summary | 识别到 {n} 条 · 格式：{fmt} · {p} 行无法识别 |
| problems_toggle | 详情 ▾ |
| problems_ignore_note | 无法识别的行将被自动忽略，不影响其余词条 |
| btn_goto_assistant | 去 AI 助手修正 |
| resume_hint | ⓘ 上次已完成 {done}/{total}，本次只处理新增/变更词 |
| format_label | 格式 |
| format_auto / format_single / format_two / format_colon / format_words | 自动检测 / 单行式 / 两行式 / 冒号式 / 纯单词列表 |
| force_chk | 全部重新生成（忽略缓存） |
| output_dir_label | 输出位置 |
| filename_label | 文件名 |
| filename_note | 支持占位符 {日期} {词数} |
| deck_name_label / tip_deck_name | 卡组名称 / 导入 Anki 后显示的卡组名。支持占位符 {日期} {词数}；用 :: 可建子卡组（如 雅思::词汇）。留空使用默认名。 |
| btn_normalize | ✨ AI 规范化… |
| tip_normalize | 把任意笔记文本交给 AI 转成支持的格式 |
| btn_start / btn_start_again | 开始生成 / 重新生成 |
| btn_cancel_run / cancelling | 取消 / 正在取消… |
| stage_parse / stage_sentgen / stage_audio / stage_enrich / stage_package | 解析 / 造句 / 音频 / 富化(含质检) / 打包 |
| progress_enrich | 富化 {done}/{total} 批 |
| log_placeholder | 运行日志将显示在这里 |
| btn_log_clear | 清空 |
| failure_title | 失败 / 跳过的词 ({n}) |
| failure_note | ⓘ 重新运行时会自动重试这些词 |
| banner_cancelled | 已取消 — 缓存已保留，重新开始将从未完成的词继续 |
| banner_failed | 生成失败：{err} |
| banner_success | 生成完成：{output}（点击打开所在目录） |

### 5.3 AI 设置（含悬停白话）

| key | 文案 |
|---|---|
| ai_banner_import | ⓘ 检测到 .env 中已配置 API Key |
| btn_import_env | 一键导入到本工具 |
| tip_imported | 已导入，后续以 config.json 为准 |
| ai_summary_prefix | 当前生效： |
| ai_summary_empty | 未配置 |
| label_provider / label_base_url / label_api_key / label_model | 供应商 / Base URL / API Key / 模型 |
| label_concurrency | 并发数 |
| tip_concurrency | 一次同时问 AI 几个批次。调大更快，但更容易触发供应商限流报错（默认 8 够用）。 |
| label_rate | 每分钟限速 |
| tip_rate | 一分钟内最多发多少次请求，防止账号被供应商临时封禁。 |
| label_batch_size | 批大小 |
| tip_batch_size | 一次 API 调用处理几个单词。太大容易导致 AI 输出被截断。 |
| label_batch_tokens | 批最大 tokens |
| tip_batch_tokens | 单批 AI 回答的最大长度。太小会导致富化结果被截断失败。 |
| btn_test | 测试连通 |
| testing | 测试中… |
| test_ok | ✅ 连接成功 · 延迟 {ms}ms · 模型回复正常 |
| test_fail_network | ❌ 网络不通 — 无法连接 API 服务器，请检查网络/代理 |
| test_fail_auth | ❌ 鉴权失败 — API Key 无效或没有权限 |
| test_fail_model | ❌ 模型名不存在 — 请核对模型名 |
| test_fail_other | ❌ 连接失败 — {msg} |
| btn_save_config | 保存配置 |
| tip_config_saved | 已保存到 data/config.json |

### 5.4 提示词页

| key | 文案 |
|---|---|
| pr_note | ⓘ 提示词占位符说明见 prompts/README.md，保存即热加载 |
| pr_name_enrich / pr_name_sentence_gen / pr_name_check_accuracy / pr_name_check_completeness / pr_name_fix / pr_name_format_assist / pr_name_assistant | 富化（enrich.md） / 造句（sentence_gen.md） / 准确性质检（check_accuracy.md） / 完整性质检（check_completeness.md） / 质检修复（fix.md） / 格式规范化（format_assist.md） / AI 助手（assistant.md） |
| pr_empty_editor | 从左侧选择一个提示词文件开始编辑 |
| btn_pr_assist | ✨ 让 AI 优化此提示词 |
| btn_pr_revert | 放弃修改 |
| btn_pr_save | 保存并热加载 |
| tip_pr_saved | 已保存并热加载，下次生成生效 |
| confirm_pr_restore | 将 {file} 恢复为内置默认内容？当前修改将丢失。 |
| btn_open_prompts / btn_open_templates | 打开 prompts/ 目录 / 打开 templates/ 目录 |

### 5.5 高级设置（标签 + 悬停白话，逐项）

| key | 标签 | 悬停文案 |
|---|---|---|
| label_quality_checks | 启用质检 | 每个词生成后，让两个 AI 检查员复核内容准确性与完整性。开启更准，但每个词多 2 次 API 调用。 |
| label_quality_attempts | 质检重试次数 | AI 检查出卡片有错时，自动让 AI 重修几遍再放弃。越大越准、越慢。 |
| label_importance_min | 重要性阈值 | 低于此重要性等级的词直接跳过不生成卡片。「低」= 全部生成。 |
| label_replace_all | 例句中替换全部出现 | 例句中单词出现多次时，是否把所有出现位置都挖成填空。默认只挖第一处。 |
| label_audio_enabled | 生成发音 | 为每个词生成 mp3 发音（需网络）。关闭可快速出卡，之后重开可补音频。 |
| label_tts_provider | 发音引擎 | auto = gTTS 优先，连不上自动换 Edge TTS；none = 不生成。 |
| label_tts_fallbacks | 回退链 | auto 模式下按此顺序尝试发音引擎，逗号分隔。 |
| label_tts_voice | Edge 音色 | Edge TTS 使用的音色名，默认 en-GB-SoniaNeural（英式女声）。 |
| label_audio_proxy | 音频代理 | gTTS 走代理时填 http://127.0.0.1:端口。gTTS 依赖 translate.google.com，国内通常需要代理。 |
| label_audio_timeout | 音频超时(秒) | 单个发音请求最长等待时间。连不上时多久放弃。 |
| label_audio_cooldown | 音频失败冷却(小时) | 一个词发音下载失败后，多久之内不再重试它，避免每次运行都卡在连不上的网络上。 |
| adv_note | ⓘ 这些参数影响每次生成；鼠标悬停(或键盘聚焦)查看白话说明 | — |
| adv_dirs_note | 数据目录（只读，通过环境变量修改） | — |
| btn_adv_reset_all | ↺ 全部恢复默认 | — |
| confirm_adv_reset | 将全部高级参数恢复为默认值？ | — |

### 5.6 AI 助手

| key | 文案 |
|---|---|
| as_note | ⓘ 助手只回答本工具相关问题；对话消耗你配置的 API 额度 |
| as_welcome | 你好！我是本工具的 AI 助手。可以帮你：优化提示词、把笔记转成支持的格式、解答用法问题。 |
| as_example_1 | 帮我优化 enrich.md，让例句更贴近雅思写作语境 |
| as_example_2 | 把这段笔记转成两行式格式：… |
| as_example_3 | 纯单词模式和普通词库文件有什么区别？ |
| as_input_ph | 输入问题，Enter 发送，Shift+Enter 换行 |
| btn_send / btn_stop | 发送 / 停止 |
| btn_new_chat / btn_clear_chat | 新建对话 / 清空对话 |
| as_stopped | （已停止） |
| btn_apply | 应用到编辑器 |
| tip_applied | 已写入编辑器（未保存），请审阅后在「提示词」页保存 |
| as_context_file | 📎 {file}（全文已附） |
| btn_context_remove | 移除 |
| as_stream_error | ❌ 对话失败：{msg} |
| confirm_clear_chat | 清空当前对话记录？ |

### 5.7 AI 规范化对话框

| key | 文案 |
|---|---|
| norm_title | AI 规范化 |
| norm_input_label | 粘贴你的笔记文本 |
| norm_format_label | 目标格式 |
| btn_norm_run / norm_running | 开始转换 / 转换中… |
| norm_result_label | 转换结果 |
| btn_norm_save / btn_norm_use / btn_norm_close | 保存为新文件 / 直接使用此结果 / 关闭 |
| tip_norm_saved | 已保存到 {path} |

---

## 6. 无 API 引导态与各页空状态

### 6.1 首次启动（无 config.json）

判定：`data/config.json` 不存在。

- **AI 设置页**：顶部琥珀横幅二选一——
  - env/.env 有 Key：`ⓘ 检测到 .env 中已配置 API Key  [一键导入到本工具]`；
  - 无任何 Key：`ⓘ 三步开始：① 选择供应商 ② 粘贴 API Key ③ 点「测试连通」后保存`。
- **工作台**：开始按钮禁用（Tooltip=`err_no_api_key`）；其余功能（预检/规范化对话框）可用。
- **AI 助手**：发送按钮禁用（同上 Tooltip）。
- **提示词/高级设置**：不受影响（改文件/参数不依赖 Key）。

### 6.2 各页空状态（无插图资源，用 Qt 标准图标 48px + 文案）

| 页 | 空状态 |
|---|---|
| 工作台 | 仅 DropZone 大图 + `drop_hint` + 步骤行 `① 配置 Key → ② 拖入词库 → ③ 开始生成` |
| AI 设置 | 无空状态（表单即内容）；未保存 Key 时摘要行显示 `未配置` |
| 提示词 | 未选文件时编辑区占位 `pr_empty_editor` |
| 高级设置 | 无空状态 |
| AI 助手 | 欢迎卡片 + 3 个示例问题（点击填入输入框） |
| 日志面板 | `log_placeholder` |
| 失败词区 | 隐藏（无失败不显示占位） |

---

## 7. 信号/槽与线程模型

```
主线程(UI)                                    worker 线程
─────────────                                ─────────────────────────
[开始] → 组 RunConfig
       → PipelineWorker.start() ──────────► QThread.run():
                                              cancel_event = threading.Event()（主线程持有引用）
                                              pipeline.run(RunConfig, PipelineCallbacks(
                                                on_stage_changed = sig_stage.emit,
                                                on_progress      = sig_progress.emit,
                                                on_log           = sig_log.emit,
                                                on_finished      = sig_finished.emit,
                                                cancel_requested = cancel_event.is_set))
[取消] → cancel_event.set()  ─── 批次边界检查 ─► 保存部分缓存 → 取消汇总 → sig_finished
sig_stage/progress/log ──(QueuedConnection)──► 更新阶段指示/进度条/日志面板
sig_finished(summary) ──────────────────────► 状态机→成功/已取消；失败词列表
worker 异常 → sig_error(msg) ───────────────► 状态机→失败

[测试连通] → ConnectionTester(QThread) ─────► llm.test_llm_connection(AppConfig 快照)
                                              → sig_result(ConnectionResult) → 渲染分类结果

[AI 助手 发送] → ChatWorker(QThread) ───────► llm.llm_chat_stream(AppConfig 快照, messages)
                │                              (Task 6 在 core 新增的同步流式函数)
                ├─ sig_chunk(str)  → 追加当前气泡（主线程）
                ├─ sig_done(full)  → 全量渲染 + 代码块按钮
                ├─ sig_error(msg)  → 失败气泡
                └─ [停止] → worker.request_stop() → 关闭 stream → sig_done(部分文本)
```

线程安全约定：

- 所有 worker→UI 的数据一律经 Qt 信号（跨线程自动排队），**禁止** worker 直接触碰 QWidget。
- `cancel_event` 用 `threading.Event`（主线程 set、worker 读，线程安全）。
- 传给 worker 的 `AppConfig` 是 `dataclasses.replace()` 出的快照，避免运行中被编辑。
- 同一时间只允许一个 PipelineWorker 与一个 ChatWorker；连通测试与生成可并行（互不共享状态）。
- QThread 生命周期：`finished` 后 `deleteLater()`；窗口关闭时先 `cancel_event.set()` 并 `wait(3000)`，超时 `terminate()` 兜底。

---

## 8. 可访问性

### 8.1 全键盘操作路径

| 场景 | 键盘路径 |
|---|---|
| 切页签 | Ctrl+1…Ctrl+5 |
| 选文件（工作台） | Tab 到 DropZone → Enter（开文件对话框） |
| 开始/取消 | Tab 到「开始生成」→ Enter；运行中 Esc = 取消 |
| 提示词编辑 | Ctrl+3 → Tab 文件列表 → ↑↓ 选文件 → Tab 进编辑器 → Ctrl+S 保存并热加载 |
| AI 助手发送 | Ctrl+5 → 输入 → Enter（Shift+Enter 换行）；流式中 Esc = 停止 |
| 规范化对话框 | 工作台 Tab 到「AI 规范化…」→ Enter；对话框内 Ctrl+Enter = 开始转换 |

### 8.2 Tab 顺序（每页从上到下、从左到右）

- 工作台：DropZone → 重新选择 → 格式下拉 → 输出目录 → 浏览 → 文件名 → AI 规范化 → 全部重新生成 → 开始/取消 → 日志 → 失败词列表 → 复制
- AI 设置：供应商 → Base URL → API Key → 👁 → 模型 → 并发数 → 限速 → 批大小 → 批 tokens → 测试连通 → 恢复默认 → 保存配置
- 提示词：文件列表 → 编辑器 → AI 优化 → 恢复默认 → 放弃修改 → 保存并热加载
- 高级设置：逐 ParamRow（勾选/输入 → ↺）→ 全部恢复默认 → 保存配置
- AI 助手：对话区（只读，可焦点滚动）→ 输入框 → 发送/停止 → 新建对话 → 清空对话

### 8.3 悬停信息的键盘替代

- 所有带 Tooltip 的控件同时设置 `statusTip`（同文案）——键盘聚焦即显示在状态栏。
- 高级设置每个 ParamRow 额外提供 `whatsThis`（Shift+F1 进入「这是什么」模式后点击控件显示完整白话说明）。
- 图标按钮（👁/↺/✨）均设置 `accessibleName`。

---

## 9. Task 6 需要的 core 层小扩展（设计依赖，先报备）

| # | 扩展 | 原因 |
|---|---|---|
| 1 | `llm.llm_chat_stream(cfg, messages) -> Iterator[str]` | AI 助手流式输出；同步生成器，worker 线程内逐 chunk 迭代；不做 JSON 解析；直接用传入的 AppConfig 快照（不读模块全局） |
| 2 | `parser.parse_preview(path, force_format: Optional[str] = None)` | 工作台「格式」手动覆盖；默认 None 时行为与现状完全一致 |
| 3 | `pipeline.run` 增加结构化失败词回传（`PipelineCallbacks.on_failures(list)` 或返回值扩展） | 工作台失败词列表避免解析汇总文本 |
| 4 | `RunConfig` 增 `force: bool`（已有）映射「全部重新生成」复选框 | 无需改动，仅确认 |

以上均为**新增可选参数/函数**，不改变既有行为与测试（Task 6 实现时补对应单测）。

---

## 10. 文件结构（Task 6 实现映射，与实施计划一致）

```
app/gui/
├── main_window.py      # QTabWidget、AppConfig 单例、页间信号路由、无 Key 引导
├── style.qss           # §4 tokens（浅色+暗色，QSS 变量注释）
├── strings.py          # §5 文案表（唯一文案来源）
├── param_docs.py       # 高级设置逐参数白话说明（同时可生成 README 表格）
├── workers.py          # PipelineWorker / ConnectionTester / ChatWorker(QThread)
├── widgets.py          # DropZone、LogPanel、ParamRow、ProgressStages、MessageBubble
└── pages/
    ├── workbench_page.py
    ├── ai_settings_page.py
    ├── prompts_page.py
    ├── advanced_page.py
    └── assistant_page.py
```

依赖新增：`PySide6>=6.6`、`pytest-qt`（开发）。

---

## 11. 待用户确认的决策点

| # | 决策 | 默认值（设计建议） |
|---|---|---|
| D1 | 卡片模板不进 GUI 编辑（保持文件级 + 提示词页「打开 templates/ 目录」按钮） | 采纳 |
| D2 | AI 助手对话历史仅内存、不落盘 | 采纳 |
| D3 | 图标 = Qt 内置 StandardPixmap + 文字符号（✨↺👁📎●），零资源文件 | 采纳 |
| D4 | 取消不弹确认框（缓存保留、续跑无损） | 采纳 |
| D5 | 窗口 1024×720，最小 860×600 | 采纳 |
| D6 | 工作台失败词只读 + 复制，不做「仅重跑失败词」按钮（增量机制天然重试） | 采纳 |

---

## 12. 验收对照（实施计划 Task 5 验收标准）

| 要求 | 本文档位置 |
|---|---|
| 窗口与导航（尺寸/页签/数据流） | §1 |
| 每页线框图 + 控件清单（类型/ID/默认值/启用条件） | §2.1–§2.5 |
| 工作台状态机 + 取消/失败词/续跑 | §3.1–§3.3 |
| AI 助手流式/代码块/跳转传参 | §3.4–§3.5 |
| 设计 tokens（色板含暗色/字体/间距/圆角/图标） | §4 |
| 文案表（五页 + 悬停 + 空状态 + 错误） | §5 |
| 无 API 引导态 + 空状态 | §6 |
| 信号/槽与线程模型图 + 取消标志路径 | §7 |
| 可访问性（键盘/Tab/悬停替代） | §8 |
