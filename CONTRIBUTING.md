# 贡献指南

感谢关注 AnkiMint！欢迎以各种方式参与：报告问题、改进文档、修改提示词/卡片模板、修复缺陷或添加功能。

## 开发环境搭建

```bash
git clone https://github.com/yummytrush/anki-mint-vocabulary.git
cd anki-mint-vocabulary

# 安装（含开发/测试依赖）
pip install -e .[dev]

# （可选）配置真实 API Key，仅用于 --limit 小样本验证；单测一律 mock，不需要 Key
cp .env.example .env
```

要求：Python ≥ 3.10。Windows / macOS / Linux 均可开发。

## 项目结构速览

```
app/
├── cli.py              # 命令行入口（argparse）
├── core/               # 全部业务逻辑（无 GUI 依赖）
│   ├── models.py       #   数据结构
│   ├── config.py       #   配置（CLI > config.json > env/.env > 默认）
│   ├── parser.py       #   Markdown 解析 + 预检
│   ├── enricher.py     #   LLM 批量富化
│   ├── quality.py      #   双 AI 质检 + 修复
│   ├── llm.py          #   API 客户端 / JSON 修复 / 限速
│   ├── audio.py        #   gTTS / Edge TTS 音频
│   ├── anki_packager.py#   genanki 打包
│   ├── prompts.py      #   提示词加载（缺失回退内置默认）
│   └── templates.py    #   卡片模板加载（同上）
└── gui/                # PySide6 五页签界面
prompts/                # 提示词外置文件（改文件即改行为）
templates/              # 卡片模板外置文件
tests/                  # pytest 测试（全部离线，无网络依赖）
```

## 跑测试

```bash
python -m pytest tests/ -v
```

- 全部测试离线运行（LLM/网络均 mock），不需要 API Key。
- Linux/macOS 无显示器环境下需先 `export QT_QPA_PLATFORM=offscreen`（GUI 冒烟测试）。
- CI 在 ubuntu + windows × Python 3.10/3.12 矩阵上运行同样的测试。

## 修改提示词（prompts/）

提示词是普通文本文件，**改文件即改行为，删除文件自动回退到内置默认**（并打 warning 日志）：

1. `prompts/enrich.md`（富化）、`check_accuracy.md` / `check_completeness.md`（双质检）、`fix.md`（修复）、`sentence_gen.md`（纯单词模式造句）、`format_assist.md`（AI 格式助手）、`assistant.md`（GUI AI 助手）。
2. 占位符清单见 [prompts/README.md](prompts/README.md)，占位符用 `str.replace()` 注入，不要引入 Python `.format()` 语法。
3. 改完跑一次小样本验证热路径：`python vocab_anki_generator.py --input examples/example-two-line.md --limit 2 --no-audio`。
4. 若改动影响输出 schema 或质检行为，请同步更新 [prompts/README.md](prompts/README.md) 与 CLAUDE.md。

## 修改卡片模板（templates/）

- `templates/card_front.html` / `card_back.html` / `card.css` 三个文件，缺失时回退内置默认。
- 可用字段清单见 [templates/README.md](templates/README.md)（`序号` / `正面` / `背面` / `提示` / `原句翻译`）。
- 改样式不影响数据；Anki 模板语法（`{{cloze:正面}}` 等）改动后建议导入 Anki 实测一遍。

## 提交 Pull Request

1. Fork → 新建分支（`git checkout -b fix/xxx` 或 `feat/xxx`）。
2. 改动后确保 `python -m pytest tests/ -v` 全绿；涉及生成流程的改动，用 `--limit 2` 小样本跑通一次。
3. 新功能请补充对应单测（mock LLM，不要发起真实网络请求）。
4. 提交信息：中文一句话说清目的，如 `修复: xxx` / `新功能: xxx`。
5. 使用 PR 模板填写变更说明与测试方式。

**注意**：

- 提交前自查不含任何真实 API Key：`grep -rn "sk-" --exclude-dir=data .`（`.env` 已被 .gitignore 排除，不要 `git add -f`）。
- 不要提交 `data/` 下的任何个人学习数据、缓存或产物。
- 富化输出 schema、卡片字段、质检行为属核心约定，变更请先开 Issue 讨论。

## 报告问题

- 提 Issue 前先看 [docs/known-issues.md](docs/known-issues.md) 与 README 的 FAQ。
- Bug 报告请附上 `data/logs/generator.log` 末尾若干行（**务必脱敏 API Key**）。
