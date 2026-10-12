# 开源改造实施计划

日期：2026-10-10
依据：[开源改造设计方案](../specs/2026-10-10-open-source-optimization-design.md)
状态：待执行（任务按序号推进；每个任务独立提交、独立验收）

---

## 任务总览

| # | 任务 | 前置 | 主要交付物 |
|---|---|---|---|
| 1 | 仓库骨架与包化拆分 | — | `app/` 包 + 兼容入口，72 测试全绿 |
| 2 | prompts/ 与 templates/ 外置 | 1 | 8 个提示词文件 + 3 个模板文件 + 回退机制 |
| 3 | 配置体系与连通测试 | 1 | `config.json` 优先级链 + 错误分类的连通测试 |
| 4 | 纯单词模式、解析预检、AI 格式规范化 | 2 | core 层新能力（GUI 尚未接入） |
| 5 | **GUI 前端设计（独立任务）** | 1（可与 2–4 并行） | `docs/designs/gui-frontend-design.md`，经用户审阅 |
| 6 | GUI 实现 | 3, 4, 5 | PySide6 五页签应用 + 冒烟测试 |
| 7 | 开源基建与发布 | 全部 | git 仓库、CI、双语 README、exe、v0.1.0 |

**全局工作约定**（适用于所有任务）：

- 每完成一步运行 `python -m pytest tests/ -v`，必须全绿再进下一步；每步单独提交，提交信息注明任务号。
- 全程**纯搬运不重构**：搬运中发现的缺陷记入 `docs/known-issues.md`，不顺手修。
- 富化输出 schema、卡片字段、质检行为一字不改。
- 涉及真实 API 的验证只做 `--limit 2` 小样本；单元验证一律用 mock。

---

## Task 1：仓库骨架与包化拆分

**目标**：3025 行单文件拆为 `app/core/` 模块 + `app/cli.py`，对外行为完全不变。

**步骤**（按依赖顺序，每步后跑测试）：

1. 建立 `pyproject.toml`（包名 `anki-mint-vocabulary`，原定 `ielts-anki-maker`，2026-10-12 更名、Python ≥3.10、依赖从 requirements.txt 迁入）；`app/__init__.py` 定义 `__version__ = "0.1.0"`。
2. `app/core/models.py` — 迁入 `Importance` 枚举、`WordEntry` dataclass、其它纯数据结构。
3. `app/core/config.py` — 迁入全部 `.env`/路径常量（`DATA_DIR`、`CACHE_DIR`、`MEDIA_DIR`…）与加载逻辑。
4. `app/core/llm.py` — 迁入 `llm_chat`、`repair_and_parse_json`、`_balance_braces`、`RateLimiter`。
5. `app/core/cache.py` — 迁入 `enrichment_cache`、`processed.json` 读写、`migrate_processed_keys`、句子哈希变更检测。
6. `app/core/parser.py` — 迁入 `parse_markdown` 及 3 格式自动检测。
7. `app/core/enricher.py` — 迁入 `process_batch`（含 3 级键查找）、批失败收集。
8. `app/core/quality.py` — 迁入 `ensure_quality_for_entry`、两个 checker、`fix_enrichment_with_quality_issues`、本地硬检查、质检元数据/版本门控。
9. `app/core/audio.py` — 迁入 `check_gtts_connectivity`、`batch_generate_audio`、TTS 供应商选择/回退、`audio_failures` 冷却。
10. `app/core/anki_packager.py` — 迁入 `create_anki_package`、genanki 模型与字段拼装（模板仍内置于本文件，Task 2 才外置）。
11. `app/core/pipeline.py` — 新建编排层：把原 `main()` 流程封装为 `run(config, callbacks)`；回调接口 `on_progress / on_stage_changed / on_log / on_finished`；批次边界检查 `cancel_requested`。
12. `app/cli.py` — 迁入 argparse 与 `main()`，改为调用 `pipeline.run`；全部现有参数原样保留（含 `--refresh-audio`、`--quality-max-attempts` 等）。
13. `vocab_anki_generator.py` 改为 5 行兼容 shim：`from app.cli import main; sys.exit(main())` —— CLAUDE.md 里记录的所有旧命令继续可用（该 shim 保留至 v0.2.0）。
14. `tests/` 仅改 import 路径；新增 1 个 shim 冒烟测试（模块可 import、`--help` 可解析）。

**验收**：72+1 测试全绿；`python vocab_anki_generator.py --help` 与改造前逐项一致；`py_compile` 全部模块通过；`--limit 2` 真实小样本跑通（有 Key 时）。

---

## Task 2：prompts/ 与 templates/ 外置

**目标**：提示词与卡片模板成为仓库内普通文本文件，改文件即改行为，删文件自动回退。

**步骤**：

1. `app/core/prompts.py`（加载器）：
   - `DEFAULT_PROMPTS` 内置字典：**现有 `BATCH_PROMPT`、两个质检、修复提示词逐字录入**，作为回退默认值。
   - `load_prompt(name)`：读 `prompts/<name>.md` → 失败/为空回退默认并写 warning 日志；启动时全量加载，`reload_prompt(name)` 供热加载。
   - 占位符继续用 `str.replace()`；文档化占位符清单：`{WORDS}` `{BATCH_JSON_SPEC}`（以实际现有占位符为准，搬运时核对）。
2. 建立文件（`prompts/`）：
   - `enrich.md`、`check_accuracy.md`、`check_completeness.md`、`fix.md` — 从代码**原文导出**。
   - `sentence_gen.md`（新写）：输入单词列表（可带中文提示），要求逐词产出雅思语境例句，输出为可直接进入富化流程的标准格式（词 + 例句 + 例句译文），约束：学术/考试高频语境、句长适中、不得滥用超纲词。
   - `format_assist.md`（新写）：任意笔记文本 → 4 种支持格式之一的规范 Markdown。
   - `assistant.md`（新写）：GUI AI 助手系统提示词——限定身份（本工具的提示词/格式/用法顾问）、限定不读文件不上网、输出代码块时给出完整可整体替换的文件内容。
   - `README.md`：占位符表、修改即热加载说明、恢复默认方法、社区示例。
3. `app/core/templates.py`：`load_card_templates()` 读取 `templates/card_front.html`、`card_back.html`、`card.css`，缺失回退内置默认（现 qfmt/afmt/CSS **逐字录入**）并警告；`anki_packager.py` 改为调用它。
4. 建立 `templates/` 四文件 + `templates/README.md`（可用字段清单：序号/正面/背面/提示/原句翻译；改样式不影响数据）。
5. 测试新增：外置文件缺失→回退默认且生成结果与内置一致；外置文件被修改→加载到修改后内容；热加载函数生效。

**验收**：测试全绿；删掉任一外置文件仍能完整生成；临时改 `enrich.md` 加一行标记词，`--limit 2` 生成的请求体中可见（验证热路径）。

---

## Task 3：配置体系与连通测试

**目标**：GUI 可用的配置存储 + 一键连通测试（core 层，无 GUI 依赖）。

**步骤**：

1. `app/core/config.py` 扩展：
   - `AppConfig` dataclass：供应商预设、`base_url`、`api_key`、`model`、并发/限速/批大小/批 tokens、全部高级旋钮。
   - 优先级合并：CLI 参数 > `data/config.json` > 环境变量/.env > 代码默认值；`save()` 写 config.json；Key 读取后提供 `masked()` 用于界面回显。
   - 首次运行检测到旧 `.env` 含 Key 时，CLI 提示可一键导入 config.json（打印指引；GUI 版在 Task 6）。
2. `app/core/llm.py` 新增 `test_llm_connection(config) -> ConnectionResult`：
   - 发一次最小对话（「回复 OK」，max_tokens 极小、非流式、8s 超时），返回 `ok / latency_ms / error_class / message`。
   - 错误分类映射：网络不通（`APIConnectionError`/超时）、鉴权失败（`AuthenticationError` 401/403）、模型不存在（`NotFoundError`/400 model）、其它（原文+建议）。
3. 测试：mock 三类异常各一例 + 成功例 + 超时例；优先级合并的穷举用例。

**验收**：测试全绿；用真实 Key 跑 `test_llm_connection` 得到正确延迟；故意填错 Key/模型名分别得到两类错误。

---

## Task 4：纯单词模式、解析预检、AI 格式规范化

**目标**：core 层具备「宽松输入」全部能力（Task 6 才接进 GUI）。

**步骤**：

1. `parser.py` 新增：
   - `detect_format(text)`：在现有 3 格式外识别「纯单词列表」（每行 ≤3 个 token 且无句号）。
   - `parse_preview(path) -> PreviewResult`：`entries / detected_format / problem_lines[(行号, 原文, 原因)] / skipped_count`，供 GUI 预检；`main` 流程改用它。
2. `pipeline.py` 新增 `generate_word_list_sentences(config, words)`：
   - 用 `prompts/sentence_gen.md` 调 LLM 为每词造句 → 校验/修复 JSON（复用 `repair_and_parse_json`）→ 生成**标准两行式中间文件**存 `data/intermediate/<输入名>-sentences.md`，日志明示路径（用户可手改再跑）。
   - 失败词列出并跳过，不阻断整体。
3. `pipeline.py` 新增 `normalize_any_text(config, text, target_format)`：用 `format_assist.md` 把任意文本转为标准格式，GUI/CLI 均可调用（CLI 加 `--format-input` 参数）。
4. `cli.py`：新增 `--word-list`（纯单词模式开关）与 `--format-input`；纯单词模式 = 造句中间文件 → 复用现有富化流程。
5. `examples/`：4 个示例文件（3 现有格式 + 纯单词 + 1 个"脏"文件演示预检报错）。
6. 测试：纯单词检测与误判（含普通句子不得误判）、造句流程（monkeypatch `llm_chat`）、中间文件格式正确、`parse_preview` 问题行定位、`normalize_any_text`（mock）。

**验收**：测试全绿；`--word-list` + `--limit 3` 真实小样本端到端跑出 .apkg；脏文件预检输出合理问题行。

---

## Task 5：GUI 前端设计（独立任务）

**目标**：先设计、后编码。产出 GUI 设计文档，**用户审阅通过后** Task 6 才开工。

**交付物**：`docs/designs/gui-frontend-design.md`，必须包含：

1. **窗口与导航**：主窗口尺寸/最小尺寸、页签顺序、页签间数据流示意（如：AI 设置保存 → 全局配置对象 → 各页只读引用）。
2. **每页线框图**（ASCII）与控件清单：五页逐一给出——布局分区、每个控件的类型（QLineEdit/QComboBox/…）、ID、默认值、启用条件（如未配置 Key 时哪些按钮置灰）。
3. **交互规格**：
   - 工作台：拖入→预检结果→开始→进度/日志→汇总的完整状态机（空闲/预检完成/运行中/已取消/失败/成功），每个状态下每个控件的状态。
   - 取消逻辑、失败词列表交互、续跑提示出现条件。
   - AI 助手：流式渲染规则、消息中代码块识别与「应用到编辑器」按钮出现规则、从提示词页带上下文跳转的传参约定。
4. **设计 tokens**：色板（主色/成功/警告/错误/中性，含暗色模式对应值——Anki 用户多用暗色，Qt 用 QSS 实现 `app/gui/style.qss`）、字体栈、间距基数、圆角、图标方案（Qt 内置主题图标 or 内嵌 SVG）。
5. **文案表**：全部界面字符串集中成表（含五页所有按钮、空状态、错误提示、高级设置每个参数的白话悬停文案），作为实现时的唯一文案来源，方便未来提取 i18n。
6. **无 API 引导态**：首次启动（无 config.json）的引导流程——提示先去 AI 设置页；各页空状态文案与插图。
7. **信号/槽与线程模型图**：`PipelineWorker(QThread)` 与 `ChatWorker`（流式）如何发信号、UI 如何线程安全更新；取消标志传递路径。
8. **可访问性**：全键盘操作路径、Tab 顺序、悬停信息在无鼠标设备上的替代展示（`whatsThis`/状态栏）。

**验收**：文档完整覆盖上述 8 点；用户审阅通过（有修改先改再过）。

---

## Task 6：GUI 实现

**目标**：按 Task 5 设计文档实现完整 GUI。

**文件结构**：

```
app/gui/
├── main_window.py        # QTabWidget、全局配置对象、页间信号路由
├── style.qss             # 设计 tokens 的样式实现（含暗色模式）
├── pages/
│   ├── workbench_page.py    # 拖拽、预检、输出设置、运行区
│   ├── ai_settings_page.py   # 供应商预设、表单、连通测试
│   ├── prompts_page.py      # 文件列表、编辑器、恢复默认、跳转助手
│   ├── advanced_page.py     # 参数行组件 + 悬停注释 + 单项重置
│   └── assistant_page.py    # 流式聊天、代码块采纳
├── widgets/               # DropZone、LogPanel、ParamRow（含 tooltip/↺）、ProgressStages
├── workers.py            # PipelineWorker / ConnectionTester / ChatWorker(QThread)
└── strings.py            # Task 5 文案表 → 代码（唯一定义处）
├── param_docs.py         # 每个配置键的白话说明（同时生成 README 表格）
```

**实现要点**：

- 拖拽：`dragEnterEvent` 校验后缀 `.md/.txt` 与本地文件 URL；同一拖拽区也支持点击调用 `QFileDialog`。
- 运行中 UI 锁定：开始后禁用输入控件、显示取消按钮；取消经 `pipeline` 回调标志，结束状态区分「已取消（缓存保留，可续跑）」。
- 连通测试按 Task 3 的错误分类渲染结果（✅/❌ + 白话建议）。
- AI 助手流式：`ChatWorker` 收 chunk → 信号增量刷新；代码块解析用轻量正则，仅对 ``` 包围块给「应用到编辑器」按钮。
- 依赖加入：`PySide6>=6.6`、`pytest-qt`（开发）。

**验收**：pytest-qt 冒烟测试（五页可打开切换、无 Key 时连通按钮触发 mock、拖拽事件模拟）全绿；无网络无 Key 状态下 GUI 全页可走查；真实 Key 小样本端到端生成 .apkg；手动清单（打开→配置→测试→拖入→预检→运行→取消→续跑→完成→导出）逐项核对。

---

## Task 7：开源基建与发布

**步骤**：

1. `git init` + `.gitignore`（`data/`、`.env`、`__pycache__/`、`dist/`、`build/`、`*.spec`）；首次提交包含设计文档、本计划与全部代码；提交前全局扫描确认无任何真实 Key（`grep -r "sk-"` 等）。
2. `LICENSE`(MIT)、`CONTRIBUTING.md`（如何改提示词/模板/跑测试/提 PR）、`.github/ISSUE_TEMPLATE/`（bug/功能）、PR 模板。
3. `README.md` + `README_EN.md`：双语、顶部互链；内容 = 项目简介 + 截图/GIF（Task 6 完成后录）+ exe 用户 3 步 + 源码安装 + 输入格式表（链接 examples/）+ AI 格式助手说明 + FAQ（gTTS 网络/代理、Anki 导入、Key 安全、API 成本估算、提示词/模板自定义指引）。
4. `scripts/build_exe.py`（PyInstaller onefile + windows 隐藏控制台选项说明）。
5. `.github/workflows/ci.yml`：push/PR 跑 pytest（ubuntu+windows 矩阵，标记网络相关测试跳过）；`release.yml`：打 tag → windows runner PyInstaller → 附 exe 发 Release。
6. 更新 `CLAUDE.md` 反映新结构与命令；`requirements.txt` 保留（同步 pyproject）。
7. 打 tag `v0.1.0` 发布。

**验收**：全新目录 `git clone` → `pip install -e .` → CLI 可跑、`python -m app.gui` 或打包 exe 可开 GUI；GitHub 上 CI 全绿；Release 页可下载 exe 并在干净 Windows 上完成一次完整生成。

---

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| PyInstaller 打包 PySide6 体积大/误报 | CI 自动化构建 + Release 注明体积与杀软误报原因；同时提供 pip 路线 |
| 拆包时隐藏的全局状态耦合 | 严格按依赖顺序搬运 + 每步测试；卡住则回退该步并记录 |
| 造句/富化新提示词质量不稳 | 复用现有质检 Agent 兜底；提示词文件可热改，发布后社区可迭代 |
| GUI 开发消耗 API 费用 | 单元/冒烟一律 mock；真实验证每任务最多一次 `--limit 2` |
| gTTS 在 CI 无网络 | 音频测试早已隔离网络；CI 跳过网络标记 |
