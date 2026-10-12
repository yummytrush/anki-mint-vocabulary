# 开源改造设计方案 — AnkiMint（anki-mint-vocabulary）

日期：2026-10-10
状态：已与用户确认的设计基线（实施前的唯一依据文档）

---

## 1. 背景与目标

本项目目前是一个 3025 行的单文件脚本（`vocab_anki_generator.py`），通过命令行驱动，依赖 `.env` 配置。功能成熟：多格式 Markdown 解析、gTTS/Edge TTS 音频、DeepSeek 富化（词形还原 + 语境释义 + 词源/核心义项/派生词/记忆技巧 + 例句翻译）、双 Agent 质检、增量缓存、genanki 填空卡片打包，并带有 72 个 pytest 测试。

**开源目标**：让任何雅思考生（非程序员）能通过图形界面 3 步内用上这个工具，同时让社区能方便地改提示词、卡片模板甚至核心代码。

**硬约束**：

- 现有富化卡片内容与行为**一字不改**（用户明确要求），只做外置与搬运。
- 现有 CLI 全部参数继续可用，老用户不受影响。
- 重构期间，每阶段结束 72 个测试必须全绿。

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| GUI 框架 | **PySide6 (Qt)**：原生拖拽、现代观感、可打包单文件 exe |
| 界面/文档语言 | 界面纯中文；README 中英双语 |
| 发行形态 | GitHub Releases 附 Windows exe（CI 自动构建）+ pip 源码双轨 |
| 提示词外置 | `prompts/` 目录多文件 + GUI 内可直接编辑、热加载、一键恢复默认 |
| 卡片模板外置 | `templates/` 目录（HTML/CSS），与提示词同等地位 |
| AI 助手 | 新增「AI 助手」页签，复用用户配置的 API，可流式对话并一键采纳提示词修改 |

## 3. 目标仓库结构

```
anki-mint-vocabulary/
├── README.md / README_EN.md       # 双语；3 步快速开始 + 截图/GIF + FAQ
├── LICENSE (MIT) / CONTRIBUTING.md
├── pyproject.toml                 # 包元数据 + 依赖声明
├── .gitignore                     # data/、.env、__pycache__/、dist/、build/
├── app/
│   ├── core/                      # 纯逻辑层，不 import 任何 GUI 库
│   │   ├── models.py              # WordEntry、Importance 等数据结构
│   │   ├── parser.py              # Markdown 多格式解析 + 纯单词模式
│   │   ├── enricher.py            # LLM 富化（process_batch 迁入）
│   │   ├── quality.py             # 双质检 Agent + 修复循环
│   │   ├── audio.py               # TTS（gTTS / Edge / 连通预检 / 失败冷却）
│   │   ├── anki_packager.py       # genanki 打包（读取 templates/）
│   │   ├── cache.py               # enrichment_cache / processed / 键迁移
│   │   ├── config.py              # 配置加载与保存（见 §8）
│   │   ├── llm.py                 # llm_chat、repair_and_parse_json、连通测试
│   │   └── pipeline.py            # 编排：解析→音频→富化→质检→打包
│   ├── gui/
│   │   ├── main_window.py         # QTabWidget 主窗口
│   │   ├── pages/                  # 工作台 / AI 设置 / 提示词 / 高级设置 / AI 助手
│   │   └── workers.py              # QThread 后台任务 + 信号（进度/日志/完成）
│   └── cli.py                      # 现有命令行入口，参数原样保留
├── prompts/                        # ★ 社区可改，无需碰 Python（见 §7）
│   ├── README.md
│   ├── enrich.md
│   ├── sentence_gen.md
│   ├── check_accuracy.md
│   ├── check_completeness.md
│   ├── fix.md
│   ├── format_assist.md
│   └── assistant.md
├── templates/                      # ★ Anki 卡片模板（见 §7）
│   ├── card_front.html / card_back.html / card.css
│   └── README.md
├── examples/                       # 每种输入格式一个示例 .md
├── tests/                          # 现有 72 个测试全保留 + GUI 冒烟测试
├── scripts/build_exe.py            # PyInstaller 打包脚本
└── data/                           # 运行时产物（gitignore）：config.json、cache、media、logs、output
```

## 4. 架构分层

- **core** 是唯一业务逻辑层；**GUI 与 CLI 都是它的薄壳**。GUI 通过 QThread 调用 `pipeline`，通过信号接收进度与日志；CLI 直接调用同一 `pipeline`。
- 拆分自现有单文件：**纯搬运**（函数体尽量不动），不做顺手重构；搬运中发现的缺陷单独记录、单独修。
- `pipeline` 对外暴露：`parse_preview(path)`（预检用）、`run(config, callbacks)`（完整生成）、`test_llm_connection(config)`（连通测试）。回调接口：`on_progress(done, total, word)`、`on_log(line)`、`on_stage_changed(stage)`、`on_finished(summary)`、`on_cancel()`。
- GUI 必须支持**中途取消**：pipeline 内在批次边界检查取消标志，取消后保留缓存（下次续跑）。

## 5. GUI 设计（PySide6，单窗口五页签）

### 5.1 工作台（首页）

- **拖拽区**：拖入或点击选择输入文件（.md / .txt）。
- **解析预检**：拖入后立即解析并显示——「识别到 120 条 · 格式：单行式 · 3 行无法识别」。无法识别的行可展开查看、选择「忽略」或「跳转去 AI 助手修正」。格式检测结果（单行/两行/冒号式/纯单词）自动带出，允许手动覆盖。
- **输出设置**：保存位置（浏览选择）+ 文件名，默认命名模板 `IELTS-{日期}.apkg`，可自定义（支持 `{日期}`、`{词数}` 占位符）。
- **运行区**：开始按钮、进度条（按阶段显示：解析/音频/富化/质检/打包）、实时日志面板（同时写 `data/logs/`）、结束后显示成功/失败/跳过汇总及失败词清单。
- **续跑提示**：检测到缓存中有已完成词时显示「上次已完成 87/N，本次只处理新增/变更词」。

### 5.2 AI 设置

- **供应商预设下拉**：DeepSeek / OpenAI 兼容 / SiliconFlow / Ollama / 自定义。选中预设自动填充 Base URL 与推荐模型名，均可手改。
- 表单：Base URL、API Key（密码框打码，可点眼睛查看）、模型名、并发数、每分钟限速、批大小、批最大 tokens。
- **[测试连通]** 按钮 → 发送一次最小请求（如「回复 OK」），显示：✅ 连接成功 · 延迟 812ms · 模型回复正常；或 ❌ 具体分类错误——网络不通 / 鉴权失败（Key 无效）/ 模型名不存在。不通过时允许继续配置但开始生成前再次提醒。
- 「保存配置」写入 `data/config.json`；页面顶部显示当前生效配置摘要。

### 5.3 提示词编辑

- 左侧文件列表（prompts/ 下全部文件）+ 右侧编辑器。
- 保存即**热加载**（下一次生成立即生效，无需重启）。
- 每个文件有「↺ 恢复默认」按钮——默认值内置在程序中，文件被删/改坏均可一键还原。
- 工具栏「✨ 让 AI 优化此提示词」按钮 → 携带当前文件全文跳转 AI 助手页（见 5.5）。
- 顶部常显一行浅色说明：「提示词占位符说明见 prompts/README.md」。

### 5.4 高级设置

- 覆盖其余全部 `.env` 旋钮：质检开关、质检重试次数、IMPORTANCE 阈值、REPLACE_ALL、音频开关、TTS 供应商/回退链/代理/超时/失败冷却、数据目录覆盖。
- **每个参数配悬停气泡（QToolTip）白话注释**，例如：
  - 并发数：一次同时问 AI 几个批次。调大更快，但更容易触发供应商限流报错（默认 8 够用）。
  - 每分钟限速：一分钟内最多发多少次请求，防止账号被供应商临时封禁。
  - 质检重试次数：AI 检查出卡片有错时，自动让 AI 重修几遍再放弃。越大越准、越慢。
  - 音频失败冷却：一个词发音下载失败后，多久之内不再重试它，避免每次运行都卡在连不上的网络上。
- 每个参数旁「↺」小按钮：单独恢复该项默认值。

### 5.5 AI 助手（新增页签）

- **定位**：用用户自己配置的 API，帮助优化提示词、转换输入格式、答疑本项目的用法。**不是**通用聊天机器人（不联网搜索、不读用户电脑其它文件、不免费）——README 与页面内均明示。
- 左侧多轮对话窗，回答**流式**输出（OpenAI SDK stream）。
- 系统提示词来自 `prompts/assistant.md`（可改，见 §7）。
- **与提示词页联动**：从提示词页跳转时自动携带当前提示词全文，并预填「帮我优化这个提示词，要求：……」。
- **一键采纳**：AI 回复中的代码块旁有「应用到编辑器」按钮——只写入提示词编辑页的编辑框，**不自动保存**；用户审阅后手动保存才生效。
- **格式转换用途**：用户可把任意笔记粘贴进来要求「转成支持的格式」，结果保存为 .md 后拖入工作台——与工作台的「AI 规范化」入口（§6）形成两条通道。

## 6. 输入格式宽松化

1. **保留现有 3 格式**自动检测：单行式 / 两行 Obsidian 式 / 冒号分隔式。
2. **新增纯单词模式**：每行一个单词（可选附带中文提示，如 `initiate 发起`）。流程 = 先用 `sentence_gen.md` 提示词让 LLM 为每词造雅思语境例句 → 生成中间标准格式文件 → 走原有富化流程。中间文件自动存入 `data/` 并在日志中可见，方便用户修改后重跑。
3. **解析预检**（见 §5.1）：拖入即见结果，问题行当场暴露。
4. **AI 格式助手双通道**：
   - 文档通道：README 提供复制即用的「把你的笔记转成支持格式」提示词模板（不依赖本工具，任何 AI 都能用）。
   - 程序通道：`format_assist.md` 提示词 + 工作台「AI 规范化」按钮，用已配置的 API 把任意文本/词表转成标准格式并另存为新文件。
5. `examples/` 提供全部 4 种格式（含纯单词）的示例文件；README 用表格说明各格式并链接示例。

## 7. prompts/ 与 templates/ 设计

**prompts/**（社区改提示词不碰代码）：

| 文件 | 内容 | 来源 |
|---|---|---|
| enrich.md | 富化主提示词（词形还原/语境释义/双语词性/JSON 格式约定） | 现 `BATCH_PROMPT` **原文搬运** |
| sentence_gen.md | 纯单词模式造句提示词 | 新增 |
| check_accuracy.md | 准确性质检 | 现质检提示词原文搬运 |
| check_completeness.md | 完整性质检 | 同上 |
| fix.md | 质检失败修复 | 现修复提示词原文搬运 |
| format_assist.md | 任意文本→标准格式 | 新增 |
| assistant.md | GUI AI 助手的系统提示词 | 新增 |
| README.md | 占位符语法说明 + 修改示例 + 「改动会热加载」提示 | 新增 |

- 占位符沿用现有 `str.replace()` 机制（如 `{WORDS}`、`{SENTENCES}`），**不用 `.format()`**（JSON 示例含花括号）。`prompts/README.md` 列出全部占位符。
- 加载策略：程序启动读取一次 + 提示词页保存时刷新；文件缺失/为空 → 回退内置默认值并在日志警告。
- prompts/README.md 明示：提示词可改是**特性**，改坏了一键恢复默认即可。

**templates/**（社区改卡片样式不碰代码）：

- `card_front.html` / `card_back.html` / `card.css` = 现 genanki 模型 qfmt/afmt/CSS **原文搬运**。
- `anki_packager.py` 启动时读取这三个文件组装模型；缺失回退内置默认并警告。
- README 说明：改样式不影响数据字段；可用的 5 个字段（序号/正面/背面/提示/原句翻译）及其来源。

## 8. 配置体系与安全

- **存储**：GUI 配置写入 `data/config.json`（gitignore）。**API Key 永不入库**——`.env`、`data/` 全部 gitignore，模板文件不含任何真实 Key。
- **优先级**：CLI 参数 > GUI 配置(config.json) > 环境变量/.env > 代码默认值。
- 迁移：首次运行检测到旧 `.env` 时提示一键导入到 config.json（Key 不回显）。
- `data/` 结构沿用现状：cache / media / logs / output / backups；新增 config.json。

## 9. 错误处理与日志

- GUI 内所有失败可见：任务级失败弹窗 + 单词级失败进日志与结束汇总（沿用现有失败清单机制）。
- 日志双写：GUI 日志面板 + `data/logs/generator.log`（沿用现有 logging）。
- API 连通错误分类显示（§5.2）；音频沿用现有预检横幅 + 冷却机制，行为不变。
- 批次 JSON 修复（truncation repair / brace balancing）等现有容错全部保留。

## 10. 测试策略

- 现有 72 个 pytest 测试**全部保留**，随搬运改造 import 路径，每阶段结束全绿。
- 新增：`parser` 纯单词模式测试；`llm.test_llm_connection` 错误分类测试（mock）；config 优先级测试；提示词/模板文件缺失回退测试。
- GUI 用 pytest-qt 做最小冒烟测试（窗口可开、页签可切换、连通测试按钮触发 mock 调用），不做全量 GUI 测试。
- CI（GitHub Actions）：push/PR 跑 pytest（ubuntu + windows 矩阵）；打 tag 时自动 PyInstaller 构建 exe 并发布 Release。

## 11. 开源基建

- git init + .gitignore；LICENSE = MIT；CONTRIBUTING.md（如何改提示词/模板/跑测试）。
- Issue 模板（bug / 功能建议）+ PR 模板。
- README（中文为主 + 顶部语言切换链接）：项目截图、功能 GIF、exe 用户 3 步快速开始、源码用户安装步骤、输入格式表格、FAQ（gTTS 网络问题与代理、Anki 导入 .apkg 方法、API Key 安全说明、成本估算）。
- 版本号 `0.1.0` 起步，`app/__init__.py` 定义 `__version__`，GUI 关于页显示。

## 12. 实施阶段（每阶段结束测试全绿、可独立交付）

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| 1 拆包 | 单文件 → `app/core/` + `cli.py`，纯搬运 | CLI 行为与输出与改造前完全一致；72 测试全绿 |
| 2 外置 | prompts/、templates/ 建立并接管加载；内容原文不变 | 删除任一外置文件可回退默认；测试全绿 |
| 3 GUI | PySide6 五页签、拖拽、预检、连通测试、纯单词模式、AI 格式规范化、AI 助手、悬停注释 | 无 API/网络时 GUI 可完整走查（mock 冒烟）；真实配置下全流程可跑 |
| 4 开源 | git、CI、打包脚本、双语 README、文档、发布 0.1.0 | 从零克隆 → pip 安装 → CLI 与 GUI 均可运行；Release 附可运行 exe |

## 13. 明确不做（YAGNI）

- 界面多语言切换（中文界面已定；字符串集中存放，未来需要时再做 i18n）。
- 通用聊天机器人（AI 助手仅限本项目相关用途）。
- 卡片模板可视化编辑器（直接编辑文件已够用）。
- 数据库/Web 服务/多用户（保持单机工具）。
- 对现有富化输出 schema 与卡片字段的任何改动。
