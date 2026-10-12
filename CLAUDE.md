# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AnkiMint** (`anki-mint-vocabulary`) — AI-powered Anki vocabulary deck generator for IELTS prep. Parses a Markdown word list (3 formats + pure word-list mode), generates audio pronunciations (gTTS/Edge TTS), enriches each word via any OpenAI-compatible API (lemma reduction + context-aware meaning + etymology/meanings/derivatives/memory-tips + sentence translation JSON), and outputs a `.apkg` Anki deck with **type-in cloze-deletion cards**, Chinese context hints, and embedded audio. Ships as a PySide6 GUI (five tabs) plus a full CLI, packaged for Windows as a single-file exe.

## Repository layout

```
app/                    # 安装包（pip install -e .）
├── cli.py              #   argparse + main()，调用 pipeline.run
├── core/               #   全部业务逻辑，无 GUI 依赖
│   ├── models.py       #     Importance 枚举、WordEntry 等数据结构
│   ├── config.py       #     AppConfig + 路径常量 + 优先级合并 (CLI > config.json > env/.env > 默认)
│   ├── parser.py       #     parse_markdown / detect_format / parse_preview（预检）
│   ├── enricher.py     #     process_batch（含 3 级键查找）
│   ├── quality.py      #     ensure_quality_for_entry + 双 checker + fixer + 本地硬检查
│   ├── llm.py          #     llm_chat / repair_and_parse_json / _balance_braces / RateLimiter / test_llm_connection
│   ├── cache.py        #     enrichment_cache + processed.json 读写、迁移、句子哈希
│   ├── audio.py        #     check_gtts_connectivity / batch_generate_audio / TTS 回退 / 失败冷却
│   ├── anki_packager.py#     create_anki_package（genanki 模型）
│   ├── pipeline.py     #     run(config, callbacks) 编排 + generate_word_list_sentences + normalize_any_text
│   ├── prompts.py      #     提示词加载器（缺失/为空 → 回退内置默认 + warning）
│   └── templates.py    #     卡片模板加载器（同上）
└── gui/                # PySide6 五页签（__main__.py 入口；workers.py 线程；strings.py 唯一文案来源）
prompts/                # 提示词外置文件（改文件即改行为；占位符用 str.replace）
templates/              # 卡片模板外置文件（card_front.html / card_back.html / card.css）
scripts/build_exe.py    # PyInstaller onefile 打包（prompts/templates/style.qss 经 --add-data 进 _MEIPASS）
examples/               # 5 个示例输入（3 格式 + 纯单词 + 脏文件）
docs/                   # 设计文档（specs/plans/designs）+ known-issues.md
vocab_anki_generator.py # 5 行兼容 shim → app.cli:main（保留至 v0.2.0）
vocab_anki/             # 早期拆分兼容包（遗留，勿再扩展）
tests/                  # pytest 全离线（LLM/网络全部 mock）
```

## Commands

```bash
# Install (editable, with dev/test deps)
pip install -e .[dev]

# Configure API key
cp .env.example .env

# Full generation (legacy entry — keep working; thin shim over app.cli)
python vocab_anki_generator.py \
  --input "data/input/words.md" \
  --output data/output/output.apkg

# Same thing via package entry points
python -m app.cli --input data/input/words.md
anki-mint --limit 5            # installed console script
anki-mint-gui                  # GUI console script

# GUI
python -m app.gui

# Test with N words only / force full regeneration
python vocab_anki_generator.py --limit 5
python vocab_anki_generator.py --force

# Pure word-list mode (AI writes example sentences first → intermediate file → normal enrichment)
python vocab_anki_generator.py --input words.txt --word-list

# Convert messy notes to a standard format only (no card generation)
python vocab_anki_generator.py --format-input messy.md --target-format two_line

# Quality knobs (checks are ON by default — disable only for emergency fast runs)
python vocab_anki_generator.py --no-quality-checks
python vocab_anki_generator.py --quality-max-attempts 1

# Audio knobs
python vocab_anki_generator.py --no-audio          # skip audio this run
python vocab_anki_generator.py --refresh-audio     # force regenerate all audio

# Tests (no network/API needed — pure-logic + mocked LLM)
python -m pytest tests/ -v
python -m pytest tests/test_vocab_generator.py::TestRepairAndParseJson -v

# Syntax check / packaging
python -m py_compile app/cli.py
python scripts/build_exe.py            # → dist/AnkiMint.exe (--console for visible logs)

# GUI smoke tests need a platform; headless Linux/macOS:
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -v
```

Performance/audio/quality knobs live in `.env`; CLI flags (`--max-concurrency`, `--rate-limit`, `--no-audio`, `--no-quality-checks`, `--quality-max-attempts`) override them per-run. python-dotenv does **not** override pre-existing env vars, so an explicit `MAX_CONCURRENCY=3` in `.env` wins over the code default of 8.

## Architecture

Pipeline stages (orchestrated by `app/core/pipeline.py` → `run(config, callbacks)`; callbacks `on_progress / on_stage_changed / on_log / on_finished`, batch-boundary `cancel_requested` checks; the CLI and the GUI `PipelineWorker` both drive it):

1. **Markdown parsing** (`parser.parse_markdown`): Auto-detects 4 formats — single-line (`1. word sentence`), two-line Obsidian-style (`1. **word**` / `> sentence`), colon-delimited (`1. **word**: sentence`), and pure word list (≤3 tokens per line, no sentence punctuation). `parse_preview(path) -> PreviewResult` returns `entries / detected_format / problem_lines / skipped_count` for GUI pre-checks and `main` reporting. Pure word lists go through `pipeline.generate_word_list_sentences` (prompt `prompts/sentence_gen.md`) which writes a standard two-line intermediate file to `data/intermediate/` (user-editable before re-run); failed words are listed and skipped, never fatal.

2. **Audio generation** (`audio.batch_generate_audio`): gTTS per word → `media/word.mp3`. Concurrent via `asyncio.to_thread` + `Semaphore`. **Connectivity precheck** (`check_gtts_connectivity`) runs once before the per-word loop; if gTTS is unreachable, the run skips audio entirely (no per-word timeout stalls) and the summary repeats an `AUDIO SKIPPED` line. `TTS_PROVIDER=auto` falls back to Edge TTS when installed. Failed words are recorded in `audio_failures.json` and skipped during a `AUDIO_FAIL_COOLDOWN` window (default 24h). gTTS calls use `AUDIO_TIMEOUT` (default 8s) and honor `AUDIO_PROXY`. Lemma-form audio for back cards is deduped against existing files. Existing non-empty files are skipped (idempotent) unless `--refresh-audio`.

3. **LLM enrichment** (`enricher.process_batch`): Words grouped into batches of `BATCH_SIZE` (default 5). Each batch → one API call with `max_tokens=BATCH_MAX_TOKENS` (default 8192, prevents JSON truncation on rich words). Returns `(enriched, failures, spelling_warnings)` — failures `[(word, reason)]` feed the end-of-run summary. The prompt (`prompts/enrich.md`, fallback `DEFAULT_PROMPTS` in `prompts.py`) instructs the LLM to:
   - **Lemmatize** inflected forms to base form (`initiated` → `initiate`); original kept in `inflected_form`
   - Provide **context-aware Chinese meaning** (`context_meaning`) tied to the sentence, not a dictionary gloss
   - Use **bilingual POS format** everywhere: `vt. 及物动词`, `n. 名词`, `adj. 形容词`, etc.
   - Return `sentence_translation` (Chinese translation of the example sentence)
   - JSON keys must use the **original word form** (lowercase), not the lemma — with fallback matching by `inflected_form` if the LLM misbehaves

4. **Quality gate** (`quality.ensure_quality_for_entry`): Enabled by default. After structural validation, each enriched word is checked by two independent LLM checker roles via `llm_chat`:
   - **Accuracy checker** (`check_accuracy.md`) verifies lemma/inflection, context-aware meaning, core meaning ordering, sentence translation, definitions, examples, and obvious phonetic mismatches against the source sentence.
   - **Completeness checker** (`check_completeness.md`) verifies required fields/depth and blocks placeholder/empty content.
   - Local hard checks run first for missing `context_meaning`, `sentence_translation`, first `definition_en`, placeholder core meaning, and missing IPA on single-word entries. Multi-word phrases may have empty `phonetic`; allowed only when checker agents pass and no wrong word-level IPA is inserted.
   - Blocking issues trigger `fix_enrichment_with_quality_issues` (`fix.md`) and re-check up to `QUALITY_MAX_ATTEMPTS` (default 2). Results that still fail are omitted and listed in the final summary. Checker failures fail closed: missing/malformed checker output is treated as a blocking issue.

5. **Caching** (`cache.py`): `enrichment_cache.json` and `processed.json` both keyed by **lowercase word**. Sentence-hash change detection enables incremental re-runs (reordering/inserting Markdown lines doesn't force a full re-run). `migrate_processed_keys` auto-migrates legacy numeric keys. With quality checks enabled, cached/processed records are reused only if they carry passing quality metadata for the current `QUALITY_SCHEMA_VERSION`; legacy records are rechecked.

6. **Anki packaging** (`anki_packager.create_anki_package`): genanki model `"AI 词汇强化填空"` with **5 fields**: `序号`, `正面` (sentence with `{{c1::word}}` cloze), `背面` (full enrichment HTML), `提示` (context hint from `context_meaning`, fallback `core_meanings[0].usage`), `原句翻译`. Front: `{{cloze:正面}}` + hint div + `{{type:cloze:正面}}`. Back: revealed cloze + translation → answer comparison → enrichment HTML. Templates load from `templates/` via `templates.py` (`load_card_templates()`), falling back to verbatim built-ins with a warning; deck name supports `{书名}/{日期}` placeholders (`--deck-name`).

7. **GUI** (`app/gui/`): Five tabs — 工作台 (drag-drop, pre-check, run, progress/log), AI 设置 (provider presets, connection test via `llm.test_llm_connection` → `ok/latency_ms/error_class`), 提示词 (editor, hot-reload, jump-to-assistant), 高级设置 (param rows + tooltips from `param_docs.py`), AI 助手 (streaming chat, code blocks get "apply to editor"). Workers (`workers.py`) are QThreads; UI updates only via signals. UI strings come exclusively from `strings.py`. Config edits save to `data/config.json` via `AppConfig.save()`; the key is never written anywhere else.

Key data structures: `Importance` enum (极高/高/中/低), `WordEntry` dataclass, `RateLimiter` (sliding-window token bucket), `AppConfig` dataclass.

## Configuration

Priority chain: **CLI args > `data/config.json` (saved by GUI or `--save-config`) > env vars / `.env` > code defaults**. All knobs (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `MAX_CONCURRENCY`, `RATE_LIMIT_PER_MIN`, `BATCH_SIZE`, `BATCH_MAX_TOKENS`, `REPLACE_ALL`, `AUDIO_ENABLED`, `IMPORTANCE_MIN_LEVEL`, `QUALITY_CHECKS_ENABLED`, `QUALITY_MAX_ATTEMPTS`, `AUDIO_PROXY`, `AUDIO_TIMEOUT`, `AUDIO_FAIL_COOLDOWN`, `TTS_PROVIDER`, `TTS_FALLBACKS`, `TTS_VOICE`) are `AppConfig` fields — GUI 高级设置页 renders them with plain-language tooltips from `app/gui/param_docs.py`. Defaults target DeepSeek (`https://api.deepseek.com`, `deepseek-chat`). `AppConfig.masked()` gives the safe-for-display key form.

Runtime artifacts default to `data/*`: cache/state in `data/cache`, audio in `data/media`, logs in `data/logs`, outputs in `data/output`, GUI/CLI config in `data/config.json`, AI-sentence intermediates in `data/intermediate`. Legacy root files are still read when the new locations are empty, but new writes go to `data/*` unless overridden by `DATA_DIR`, `CACHE_DIR`, `MEDIA_DIR`, `OUTPUT_DIR`, `LOG_DIR`, or `BACKUP_DIR`.

## Enrichment data schema

Each word in `enrichment_cache.json` follows this structure (setdefaults applied by `validate_and_fix_enrichment`):

```
{
  "word": "initiate",           // lemma (base form) — used for back-card display & analysis
  "inflected_form": "initiated",// original form from sentence
  "context_meaning": "发起，启动", // in-context Chinese meaning → front hint
  "phonetic": "/ɪˈnɪʃieɪt/",
  "importance": "极高",
  "reason": "学术高频动词",
  "core_meanings": [{usage, logic, definition_en, grammar_note, examples}],
  "etymology": "...",
  "derivatives": [{word, pos, meaning, example}],
  "confusable_words": [{words, difference}],
  "grammar_tips": ["..."],
  "memory_tips": "...",
  "sentence_translation": "...", // Chinese translation of example sentence
  "_quality_status": "passed",    // passed|failed|disabled
  "_quality_schema_version": "2026-08-quality-agents-v1",
  "_quality_attempts": 0,
  "_quality_checked_at": "2026-...",
  "_quality_summary": "passed",
  "_sentence_hash": "a1b2c3d4",
  "_generated_at": "2026-..."
}
```

## Known behavior

- DeepSeek does NOT support `response_format: json_object` — the client auto-detects `deepseek` in the base URL and skips that parameter.
- Prompts are loaded from `prompts/*.md`; a missing/empty file falls back to the verbatim built-in default (`DEFAULT_PROMPTS`) with a warning log. Placeholders are injected with `str.replace()` (never `.format()` — prompt text contains literal braces). Placeholder inventory lives in `prompts/README.md`. `reload_prompt(name)` hot-reloads; the GUI prompts tab saves + reloads.
- Windows GBK terminals can't print emoji; the final summary uses ASCII-only `print()` with a `UnicodeEncodeError` fallback.
- SDK retries are disabled (`max_retries=0`) in favor of the script's own 3-retry exponential backoff. JSON parsing is centralized in `llm.repair_and_parse_json`: strips markdown fences/BOM/zero-width chars, locates the first `{`, then on failure attempts **truncation repair** (cut to last `}`) and **brace balancing** (`_balance_braces` — counts unclosed `{`/`[` skipping string literals, strips trailing commas, appends closers). This recovers `max_tokens`-truncated responses.
- `process_batch` has a 3-tier key lookup: exact lowercase match → case variants → iterate all values matching by `inflected_form` field. Words that can't be matched, fall below the importance threshold, whose batch fails entirely, or fail the quality gate after retries are collected into the returned failure list and printed in the end-of-run summary.
- Multi-word phrase entries are allowed to have an empty `phonetic` when both checker roles approve; single-word entries with empty IPA are blocked by local completeness checks.
- Audio is generated for **all** entries (not just newly-enriched ones); existing non-empty files are skipped unless `--refresh-audio`. Card media only includes referenced, non-empty audio files.
- gTTS depends on `translate.google.com` (NOT `google.com` — a proxy that reaches the main site may still fail the translate endpoint). When unreachable, the run does not stall: the precheck detects it in ~2s, then falls back to Edge TTS if available or skips audio with a clear warning. To diagnose a "slow run", check `data/logs/generator.log`. Re-running with `--refresh-audio` after fixing connectivity backfills audio.
- The `context_meaning` field is the **primary** source for the front-card Chinese hint; it falls back to `core_meanings[0].usage` only when `context_meaning` is empty.
- All POS labels must use bilingual format (`adj. 形容词` etc.) — enforced via the prompt, not post-processing.
- PyInstaller-frozen exe: prompts/templates/style.qss land under `sys._MEIPASS`, and the loaders' `Path(__file__).parents[2]` resolves there automatically — no frozen-mode special-casing. The exe writes `data/` relative to its working directory.
- Tests live in `tests/` (pytest, ~170 tests across 7 files): parsing/format detection, JSON repair, cloze building, field validation, quality guards, cache key/version behavior, incremental filtering + key migration, TTS provider selection, media packaging, checker fail-closed behavior, config priority merge + `test_llm_connection` error classes (mocked), word-list mode, prompts/templates fallback & hot-reload, GUI smoke (pytest-qt), and the shim. All offline.

## Release workflow

Tag `v*` → GitHub Actions `release.yml` builds the exe on windows-latest and attaches `AnkiMint.exe` + SHA-256 to the Release. CI (`ci.yml`) runs pytest on ubuntu+windows × Python 3.10/3.12. Before committing anything: never commit `.env`, `data/`, or any real key (`grep -rn "sk-" --exclude-dir=data .`).
