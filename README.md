<div align="center">

# 🎴 AnkiMint

**AI 驱动的 Anki 词汇填空卡组生成器**
把你的单词列表变成「拼写练习 + 语境提示 + 真人发音 + 词根词缀解析」的 Anki 卡组

[![CI](https://github.com/yummytrush/anki-mint-vocabulary/actions/workflows/ci.yml/badge.svg)](https://github.com/yummytrush/anki-mint-vocabulary/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/yummytrush/anki-mint-vocabulary)](https://github.com/yummytrush/anki-mint-vocabulary/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

[English](README_EN.md) | 简体中文

</div>

---

![工作台](docs/screenshots/gui-workbench.png)

## ✨ 特性

- **📝 宽松输入**：三种 Markdown 词表格式自动识别（单行 / 两行 Obsidian / 冒号式），还支持**纯单词列表** —— AI 先为每个词造雅思语境例句，再进入富化流程；脏文件会先预检并定位问题行
- **🧠 AI 深度富化**：语境中文词义、词源、核心词义表（含英文释义与语法标注）、派生词、易混词、语法/记忆提示 —— 兼容任何 OpenAI 接口的服务（DeepSeek、OpenAI、Azure、vLLM、Ollama…）
- **✍️ 填空 + 打字卡片**：例句挖空 cloze 卡片，正面给中文语境提示，背面完整词卡；支持自定义卡组名与卡片模板
- **🔊 双 TTS 引擎**：gTTS 优先、Edge TTS 自动回退，网络不通不卡死；美音/英音可选
- **✅ 双 AI 质检员**：准确性 + 完整性两个独立角色逐词审查，不合格自动修复重检，仍不合格的词在汇总中列出
- **💾 增量缓存**：中断可续跑；句子没变就不再花 API 钱；换设备/重跑只处理新增与改动词
- **🖥️ GUI + CLI 双入口**：PySide6 桌面应用（拖拽、进度、日志、内置 AI 助手）与全功能命令行
- **🎨 提示词/模板全外置**：`prompts/`、`templates/` 普通文本文件，改文件即改行为，删文件自动回退内置默认

## 🚀 快速开始

### 方式一：下载 exe（推荐，无需装 Python）

1. 到 [Releases](https://github.com/yummytrush/anki-mint-vocabulary/releases) 下载 `AnkiMint.exe`
2. 双击打开 → 「AI 设置」页选供应商、粘贴 API Key → 点「测试连通」→ 保存
3. 回「工作台」拖入单词 Markdown → 点「开始生成」→ 得到 `.apkg`，拖进 Anki 导入

> 💡 还没有 API Key？推荐 [DeepSeek](https://platform.deepseek.com/)，注册即送额度，百词全流程成本约几元人民币。

![AI 设置](docs/screenshots/gui-ai-settings.png)

### 方式二：pip 安装（源码）

```bash
git clone https://github.com/yummytrush/anki-mint-vocabulary.git
cd anki-mint-vocabulary
pip install -e .

anki-mint-gui        # 图形界面
# 或
anki-mint --help     # 命令行
```

### 方式三：命令行直接跑

```bash
cp .env.example .env     # 填入 LLM_API_KEY（也可在 GUI 里保存配置）

python vocab_anki_generator.py \
  --input data/input/words.md \
  --output data/output/output.apkg

# 小样本试跑（2 个词）
python vocab_anki_generator.py --input data/input/words.md --limit 2
```

## 📥 输入格式

把雅思/词汇书里摘出来的单词和例句整理成下面任意一种格式即可（参考 [examples/](examples/)）：

| 格式 | 写法 | 示例文件 |
|---|---|---|
| A · 单行式 | `1. word sentence` | [example-single-line.md](examples/example-single-line.md) |
| B · 两行式（Obsidian 友好） | `1. **word**` 换行 `> sentence` | [example-two-line.md](examples/example-two-line.md) |
| C · 冒号式 | `1. **word**: sentence` | [example-colon.md](examples/example-colon.md) |
| D · 纯单词列表 | 每行一个词，可带中文提示；AI 自动造句 | [example-word-list.md](examples/example-word-list.md) |
| 脏文件 | 混入无法识别的行 → 预检定位问题行，不中断 | [example-dirty.md](examples/example-dirty.md) |

<details>
<summary>📄 更多界面截图</summary>

| 提示词编辑（保存即热加载） | AI 助手（可优化提示词、转换格式） |
|---|---|
| ![提示词页](docs/screenshots/gui-prompts.png) | ![AI 助手](docs/screenshots/gui-assistant.png) |

</details>

## 🤖 AI 格式助手

手里的笔记是「散乱一句话 + 单词」甚至纯文本？两种解法：

- **GUI**：工作台点「✨ AI 规范化」，或直接在「AI 助手」页让 AI 把笔记转成标准格式（会给出可整体替换的完整文件内容）
- **CLI**：`python vocab_anki_generator.py --format-input messy.md --target-format two_line` —— 转换结果存到 `data/intermediate/`，可手改后再跑

## 🛠 生成流程

```
Markdown 词表 ──► 解析 + 预检 ──► (纯单词模式: AI 造句) ──► TTS 发音 ──► AI 富化 ──► 双 AI 质检+修复 ──► .apkg
     │                │                                              │                │
   .md/.txt      问题行定位，不中断                                  gTTS/Edge TTS    不合格词自动重试，
                                                                    网络不通自动降级    仍不合格则汇总列出
```

所有中间产物都在 `data/` 下：`output/`（.apkg）、`cache/`（富化缓存，增量续跑）、`media/`（发音 mp3）、`logs/`（运行日志）、`config.json`（GUI 保存的配置，**含 Key，已被 gitignore**）。

## ⚙️ 配置

配置优先级：**CLI 参数 > `data/config.json`（GUI 保存）> 环境变量 / `.env` > 代码默认值**。

常用项（完整清单见 [.env.example](.env.example) 与 GUI「高级设置」页悬停说明）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `LLM_API_KEY` | — | API Key，仅存本机 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 任意 OpenAI 兼容端点 |
| `MAX_CONCURRENCY` | 8 | 最大并发请求数（提速的最大杠杆） |
| `BATCH_SIZE` / `BATCH_MAX_TOKENS` | 5 / 8192 | 每批词数 / 单批输出上限 |
| `TTS_PROVIDER` | `auto` | `auto`/`gtts`/`edge`/`none` |
| `AUDIO_PROXY` | — | gTTS 用的代理（如 `http://127.0.0.1:7890`） |
| `QUALITY_CHECKS_ENABLED` | true | 双 AI 质检（赶时间可关） |

## ❓ FAQ

<details>
<summary><b>发音生成失败 / 全程很慢？</b></summary>

gTTS 依赖 `translate.google.com`，国内网络通常不可直连。本工具的处理：启动时 2 秒内预检连通性 → 不可达则自动回退 Edge TTS（需 `pip install edge-tts`）→ 仍不行则跳过音频并明确提示，**不会卡死**。有代理可设 `AUDIO_PROXY=http://127.0.0.1:7890`。排查看 `data/logs/generator.log`；网络恢复后 `--refresh-audio` 可补音频。

</details>

<details>
<summary><b>如何导入 Anki？</b></summary>

双击 `.apkg` 文件，或 Anki 中「文件 → 导入」。卡组名支持 `{书名}/{日期}` 占位符（CLI `--deck-name`、GUI 工作台「卡组名称」）。卡片为 cloze 填空 + 打字答题，需 Anki 2.1+（桌面/手机/网页均支持）。

</details>

<details>
<summary><b>API Key 安全吗？</b></summary>

Key 只保存在**本机**（`.env` 或 `data/config.json`，两者均已被 .gitignore 排除），请求直接从你的设备发往 API 供应商，本项目没有中间服务器。给别人的 exe 不会带上你的 Key。提 Issue/PR 时注意日志脱敏。

</details>

<details>
<summary><b>API 成本大概多少？</b></summary>

每词 1 次「批量富化」（默认 5 词一批）+ 2 次质检，必要时 1–2 次修复。100 词全流程约 250+ 次调用、1–3 百万 tokens，按 DeepSeek 价格通常在**几元人民币**量级（以供应商实际定价为准）。缓存命中后重跑几乎免费。

</details>

<details>
<summary><b>想自定义提示词 / 卡片样式？</b></summary>

改 `prompts/*.md`（保存即热加载，GUI「提示词」页可直接编辑）或 `templates/` 三件套（正面/背面 HTML + CSS）。改错了删掉文件即回退内置默认。占位符与可用字段见 [prompts/README.md](prompts/README.md) 与 [templates/README.md](templates/README.md)。exe 版使用内置默认，自定义请用源码方式。

</details>

<details>
<summary><b>exe 报毒 / 被 Defender 拦截？</b></summary>

未签名的 PyInstaller 单文件程序常见误报。可校验 Release 附带的 SHA-256、从源码自行打包（`python scripts/build_exe.py`）或走 pip 源码路线。介意误报请勿下载 exe。

</details>

<details>
<summary><b>生成中断了 / 想换台电脑继续？</b></summary>

重跑同一命令即可：已富化且通过质检的词直接复用缓存，只处理缺的部分。把 `data/` 拷到新机器可带走全部缓存与音频。

</details>

## 🧑‍💻 开发

```bash
pip install -e .[dev]                  # 含 pytest / pytest-qt
python -m pytest tests/ -v             # 全部离线测试（无 Key、无网络）
QT_QPA_PLATFORM=offscreen python -m app.gui   # 无显示器环境跑 GUI
python scripts/build_exe.py            # 打包单文件 exe（--console 可看日志）
```

- 架构与已知行为见 [CLAUDE.md](CLAUDE.md)；设计文档在 [docs/](docs/)；已知缺陷在 [docs/known-issues.md](docs/known-issues.md)
- 欢迎贡献！见 [CONTRIBUTING.md](CONTRIBUTING.md)

## 📄 License

[MIT](LICENSE) © 2026 Lee Chen (AnkiMint)
