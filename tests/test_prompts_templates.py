"""
Task 2 — prompts/ 与 templates/ 外置的测试。

覆盖：
  - 外置文件缺失/为空 → 回退内置默认（并保证生成结果与内置一致）
  - 外置文件被修改 → 加载到修改后内容（热路径：请求体可见）
  - reload_prompt / restore_default 热加载函数生效
  - 默认提示词渲染结果与原 f-string 逐字一致（行为不变的守护测试）
  - 模板三件套缺失回退 / 修改生效 / create_anki_package 实际使用外置模板

全部测试用 monkeypatch 将 PROMPTS_DIR / TEMPLATES_DIR 指向临时目录，
不触碰仓库内的真实文件。
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.core import anki_packager, audio, cache, config, enricher, llm, models, prompts, quality, templates


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch):
    """把 PROMPTS_DIR 指向空临时目录（= 全部文件缺失 → 全部回退默认）。"""
    d = tmp_path / "prompts"
    d.mkdir()
    monkeypatch.setattr(prompts, "PROMPTS_DIR", d)
    # 清空启动时已加载的缓存，模拟"全新启动"
    monkeypatch.setattr(prompts, "_loaded", {})
    return d


@pytest.fixture
def templates_dir(tmp_path, monkeypatch):
    d = tmp_path / "templates"
    d.mkdir()
    monkeypatch.setattr(templates, "TEMPLATES_DIR", d)
    return d


# ===========================================================================
# 提示词加载器 — 回退 / 修改 / 热加载
# ===========================================================================
class TestPromptLoader:
    def test_missing_file_falls_back_to_default(self, prompts_dir):
        assert prompts.get_prompt("enrich") == prompts.DEFAULT_PROMPTS["enrich"]
        assert prompts.get_prompt("check_accuracy") == prompts.DEFAULT_PROMPTS["check_accuracy"]

    def test_empty_file_falls_back_to_default(self, prompts_dir):
        (prompts_dir / "enrich.md").write_text("", encoding="utf-8")
        assert prompts.get_prompt("enrich") == prompts.DEFAULT_PROMPTS["enrich"]

    def test_whitespace_only_file_falls_back_to_default(self, prompts_dir):
        (prompts_dir / "fix.md").write_text("   \n\n  ", encoding="utf-8")
        assert prompts.get_prompt("fix") == prompts.DEFAULT_PROMPTS["fix"]

    def test_modified_file_content_loaded(self, prompts_dir):
        (prompts_dir / "enrich.md").write_text(
            prompts.DEFAULT_PROMPTS["enrich"] + "\nMARKER-WORD-123", encoding="utf-8"
        )
        assert prompts.reload_prompt("enrich").endswith("MARKER-WORD-123")
        assert prompts.get_prompt("enrich").endswith("MARKER-WORD-123")

    def test_hot_reload_replaces_cached_text(self, prompts_dir):
        assert prompts.get_prompt("fix") == prompts.DEFAULT_PROMPTS["fix"]
        (prompts_dir / "fix.md").write_text("新提示词", encoding="utf-8")
        assert prompts.get_prompt("fix") == prompts.DEFAULT_PROMPTS["fix"]  # 未重载前仍是旧缓存
        prompts.reload_prompt("fix")
        assert prompts.get_prompt("fix") == "新提示词"

    def test_restore_default_overrides_file(self, prompts_dir):
        (prompts_dir / "enrich.md").write_text("被改坏的提示词", encoding="utf-8")
        prompts.reload_prompt("enrich")
        assert prompts.get_prompt("enrich") == "被改坏的提示词"
        restored = prompts.restore_default("enrich")
        assert restored == prompts.DEFAULT_PROMPTS["enrich"]
        assert prompts.get_prompt("enrich") == prompts.DEFAULT_PROMPTS["enrich"]

    def test_real_repo_prompts_match_builtin_defaults(self, monkeypatch):
        """仓库内的 prompts/*.md 与内置默认逐字一致（改文件即改行为的前提）。"""
        for name, default in prompts.DEFAULT_PROMPTS.items():
            path = prompts.PROJECT_ROOT / "prompts" / f"{name}.md"
            assert path.exists(), f"缺少 {path}"
            assert path.read_text(encoding="utf-8").strip() == default, f"{name}.md 与内置默认不一致"

    def test_real_repo_templates_match_builtin_defaults(self):
        for fname, default in (
            ("card_front.html", templates.DEFAULT_CARD_FRONT),
            ("card_back.html", templates.DEFAULT_CARD_BACK),
            ("card.css", templates.DEFAULT_CARD_CSS),
        ):
            path = templates.PROJECT_ROOT / "templates" / fname
            assert path.exists(), f"缺少 {path}"
            # 两侧 strip：DEFAULT_CARD_CSS 逐字录入自带首行换行
            assert path.read_text(encoding="utf-8").strip() == default.strip(), f"{fname} 与内置默认不一致"


# ===========================================================================
# 默认提示词渲染结果与原 f-string 行为逐字一致（守护测试）
# ===========================================================================
class TestDefaultPromptRenderingUnchanged:
    def _batch(self):
        e1 = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        e2 = models.WordEntry(index=2, word="tenure", sentence="his tenure ended")
        return [(e1, "novel.mp3"), (e2, "tenure.mp3")]

    def test_build_batch_prompt_matches_original(self, prompts_dir):
        """无外置文件时 build_batch_prompt 输出与原 BATCH_PROMPT.replace 完全一致。"""
        batch = self._batch()
        lines = []
        for entry, audio_filename in batch:
            lines.append(
                f"- [{entry.index}] {entry.word}\n"
                f"  例句: {entry.sentence}\n"
                f"  音频: [sound:{audio_filename}]"
            )
        expected = prompts.DEFAULT_PROMPTS["enrich"].replace("{word_list}", "\n".join(lines))
        assert enricher.build_batch_prompt(batch) == expected

    def test_quality_check_prompt_accuracy_matches_original(self, prompts_dir):
        items = {"novel": {"source_word": "novel", "enrichment": {"word": "novel"}}}
        payload = json.dumps(items, ensure_ascii=False, indent=2)
        expected = (
            "你是语义准确性审校员。请只输出 JSON，不要输出解释文本。\n"
            "任务：检查 lemma/inflected_form 是否对应原词，context_meaning 与 core_meanings[0] 是否贴合原句，"
            "sentence_translation 是否忠实翻译原句，英文定义/例句是否与该词真实用法一致。\n\n"
            "对每个 items 条目返回：\n"
            "{\n"
            '  "items": {\n'
            '    "word_lower": {\n'
            '      "passed": true,\n'
            '      "severity": "pass|warning|blocking",\n'
            '      "issues": [{"field": "字段名", "problem": "问题", "expected": "应如何修正"}],\n'
            '      "fix_instructions": ["给修复代理的具体指令"]\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            "判定规则：\n"
            "- 会误导学习者或导致卡片缺失关键信息的问题，severity 必须是 blocking。\n"
            "- 轻微风格问题不要阻断。\n"
            "- 如果词条是短语，phonetic 可以为空；除非 phonetic 明显是错误单词的音标。\n"
            "- derivatives/confusable_words/memory_tips 只在明显错误或空洞占位时阻断；没有合适易混词时允许为空。\n"
            "- checker 返回的 items 键必须使用原始词条小写：novel。\n"
            "- 若不确定，请标记 blocking 并说明需要修正什么。\n\n"
            "待检查数据：\n"
            f"{payload}"
        )
        assert quality.build_quality_check_prompt("accuracy", items) == expected

    def test_quality_fix_prompt_matches_original(self, prompts_dir):
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        data = {"word": "novel", "core_meanings": [{"usage": "新颖的"}]}
        report = {"issues": [{"field": "phonetic", "problem": "缺少音标"}], "fix_instructions": ["补充 IPA"]}
        expected = (
            "你是 IELTS 词汇卡修复代理。请只输出修复后的单个词条 JSON 对象，不要 Markdown，不要解释。\n\n"
            f"原词条：{entry.word}\n"
            f"原句：{entry.sentence}\n"
            f"质量问题：{json.dumps(report.get('issues', []), ensure_ascii=False, indent=2)}\n"
            f"修复指令：{json.dumps(report.get('fix_instructions', []), ensure_ascii=False, indent=2)}\n\n"
            "要求：\n"
            "- 保留原词作为 inflected_form；word 字段应为 lemma/base form。\n"
            "- context_meaning 必须贴合原句。\n"
            "- sentence_translation 必须完整翻译原句。\n"
            "- core_meanings[0] 必须是最贴合原句的义项，且包含 usage/logic/definition_en/grammar_note/examples。\n"
            "- 单词补充英式 IPA；多词短语可 phonetic 为空，但不要填错误音标。\n"
            "- 不得使用“待补充/暂无/意思/含义”等占位内容。\n\n"
            "当前 JSON：\n"
            f"{json.dumps(data, ensure_ascii=False, indent=2)}"
        )
        assert quality.build_quality_fix_prompt(entry, data, report) == expected

    def test_modified_enrich_prompt_reaches_llm_request_body(self, prompts_dir, monkeypatch):
        """热路径验收：改 enrich.md 加标记词，构造的请求体中可见。"""
        (prompts_dir / "enrich.md").write_text(
            prompts.DEFAULT_PROMPTS["enrich"] + "\nHOTPATH-MARKER-42", encoding="utf-8"
        )
        prompts.load_all_prompts()

        captured = {}

        async def fake_llm(messages, *a, **k):
            captured["content"] = messages[-1]["content"]
            return {"novel": {"word": "novel", "importance": "高",
                              "context_meaning": "新颖的", "phonetic": "/ˈnɒvəl/",
                              "sentence_translation": "一个新颖的想法",
                              "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        batch = [(models.WordEntry(index=1, word="novel", sentence="a novel idea"), "novel.mp3")]
        asyncio.run(enricher.process_batch(batch, asyncio.Semaphore(1)))
        assert "HOTPATH-MARKER-42" in captured["content"]


# ===========================================================================
# 卡片模板 — 回退 / 修改 / 打包实际使用
# ===========================================================================
class TestCardTemplates:
    def test_missing_files_fall_back_to_defaults(self, templates_dir):
        tpl = templates.load_card_templates()
        assert tpl.qfmt == templates.DEFAULT_CARD_FRONT
        assert tpl.afmt == templates.DEFAULT_CARD_BACK
        assert tpl.css == templates.DEFAULT_CARD_CSS

    def test_modified_files_are_loaded(self, templates_dir):
        (templates_dir / "card_front.html").write_text('MARKER-FRONT {{cloze:正面}}', encoding="utf-8")
        (templates_dir / "card.css").write_text(".card { color: red; }", encoding="utf-8")
        tpl = templates.load_card_templates()
        assert tpl.qfmt.startswith("MARKER-FRONT")
        assert tpl.css == ".card { color: red; }"
        assert tpl.afmt == templates.DEFAULT_CARD_BACK  # 未改的文件仍回退默认

    def test_package_uses_external_templates(self, tmp_path, templates_dir):
        (templates_dir / "card_front.html").write_text(
            'FRONT-MARKER {{cloze:正面}}<div class="hint">{{提示}}</div>{{type:cloze:正面}}',
            encoding="utf-8",
        )
        entry = models.WordEntry(index=1, word="valid", sentence="a valid idea")
        enrichment = {
            "valid": {
                "word": "valid",
                "context_meaning": "有效的",
                "sentence_translation": "一个有效的想法",
                "audio_filename": "",
                "core_meanings": [{"usage": "有效的", "definition_en": "valid"}],
            },
        }
        package = anki_packager.create_anki_package(
            [entry], enrichment, {}, force_model_id=1234567890, media_dir=tmp_path
        )
        model = package.decks[0].notes[0].model
        assert model.templates[0]["qfmt"].startswith("FRONT-MARKER")
        assert model.templates[0]["afmt"] == templates.DEFAULT_CARD_BACK
        assert model.css == templates.DEFAULT_CARD_CSS
