"""
Task 4 — 纯单词模式、解析预检、AI 格式规范化。

覆盖：
  - detect_format：4 种格式识别；普通句子不得误判为纯单词列表
  - parse_preview：问题行定位（行号/原文/原因）、跳过计数、纯单词条目带中文提示
  - parse_markdown 与 parse_preview 条目一致（共享解析核心的回归守护）
  - generate_word_list_sentences（mock LLM）：中间文件为标准两行式、失败词列出并跳过
  - normalize_any_text（mock LLM）：围栏剥离、目标格式校验
  - pipeline.run 纯单词模式端到端（mock LLM + 无音频 + 临时目录）

全部不触网。
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.core import audio, cache, config, llm, models, parser, pipeline
from app.core.models import WordEntry
from app.core.pipeline import PipelineCallbacks, RunConfig


# ===========================================================================
# detect_format
# ===========================================================================
class TestDetectFormat:
    def test_single_line(self):
        text = "1. analyze We need to analyze the data carefully.\n"
        assert parser.detect_format(text) == "single_line"

    def test_two_line(self):
        text = "1. **assessment**\n   > The government set up a plan.\n"
        assert parser.detect_format(text) == "two_line"

    def test_colon(self):
        text = "1. **sector**: research within public sector.\n"
        assert parser.detect_format(text) == "colon"

    def test_word_list_plain(self):
        text = "initiate\nsustainable\nmitigate\n"
        assert parser.detect_format(text) == "word_list"

    def test_word_list_numbered_with_hints(self):
        text = "1. resilient\n2. leverage 杠杆\n3. discrepancy\n"
        assert parser.detect_format(text) == "word_list"

    def test_word_list_ignores_headers_and_quotes(self):
        text = "# 标题\n> 来源：xx\n---\ninitiate\nsustainable\n"
        assert parser.detect_format(text) == "word_list"

    def test_ordinary_sentence_not_word_list(self):
        # >3 个 token 的普通句子不得误判
        text = "We need to analyze the data carefully.\nAnother ordinary sentence here.\n"
        assert parser.detect_format(text) != "word_list"

    def test_sentence_with_period_not_word_list(self):
        text = "run.\nwalk.\n"
        assert parser.detect_format(text) != "word_list"

    def test_mixed_words_and_sentence_not_word_list(self):
        text = "initiate\nWe need to analyze the data carefully.\n"
        assert parser.detect_format(text) != "word_list"

    def test_numbered_sentences_are_single_line(self):
        text = "1. analyze We need to analyze.\n2. novel It was a novel idea.\n"
        assert parser.detect_format(text) == "single_line"

    def test_empty_is_unknown(self):
        assert parser.detect_format("") == "unknown"
        assert parser.detect_format("# 只有标题\n") == "unknown"

    def test_format_labels_cover_all(self):
        for key in ("single_line", "two_line", "colon", "word_list", "unknown"):
            assert key in parser.FORMAT_LABELS


# ===========================================================================
# parse_preview — 问题行定位
# ===========================================================================
class TestParsePreview:
    def _write(self, tmp_path, content, name="words.md"):
        f = tmp_path / name
        f.write_text(content, encoding="utf-8")
        return str(f)

    def test_two_line_no_problems(self, tmp_path):
        content = (
            "1. **assessment**\n"
            "   > The government set up a Research Assessment Exercise.\n"
            "2. **sector**\n"
            "   > Research within the public sector has grown.\n"
        )
        r = parser.parse_preview(self._write(tmp_path, content))
        assert r.detected_format == "two_line"
        assert len(r.entries) == 2
        assert r.problem_lines == []
        assert r.skipped_count == 0

    def test_dirty_file_problem_lines_located(self, tmp_path):
        content = (
            "1. **assessment**\n"
            "   > The government set up a Research Assessment Exercise.\n"
            "2. **sector**\n"
            "   > Research within the public sector has grown.\n"
            "这是一个没有编号格式的散乱句子\n"
            "3. **novel**\n"
            "   > The researcher proposed novel approaches.\n"
            "random text without structure\n"
            "6. **orphan**\n"
        )
        r = parser.parse_preview(self._write(tmp_path, content))
        assert r.detected_format == "two_line"
        # 成功条目
        assert [e.word for e in r.entries] == ["assessment", "sector", "novel"]
        # 问题行：第 5 行（散乱句）、第 8 行（无结构文本）、第 9 行（缺例句）
        assert [(p.lineno, p.reason) for p in r.problem_lines] == [
            (5, "非词条行（两行式词条应为 '数字. **单词**'）"),
            (8, "非词条行（两行式词条应为 '数字. **单词**'）"),
            (9, "缺少例句行"),
        ]
        assert r.problem_lines[0].text == "这是一个没有编号格式的散乱句子"
        assert r.skipped_count == 3

    def test_word_list_entries_carry_hints(self, tmp_path):
        content = "initiate\nsustainable\nmitigate 缓解\n"
        r = parser.parse_preview(self._write(tmp_path, content))
        assert r.detected_format == "word_list"
        assert [(e.word, e.sentence) for e in r.entries] == [
            ("initiate", ""),
            ("sustainable", ""),
            ("mitigate", "缓解"),
        ]
        assert [e.index for e in r.entries] == [1, 2, 3]
        assert r.problem_lines == []

    def test_numbered_word_list_preserves_index(self, tmp_path):
        content = "4. resilient\n5. leverage 杠杆\n"
        r = parser.parse_preview(self._write(tmp_path, content))
        assert [e.index for e in r.entries] == [4, 5]

    def test_single_line_file_entries_match_parse_markdown(self, tmp_path):
        content = "1. analyze We need to analyze the data carefully.\n2. novel It was a novel idea.\n"
        path = self._write(tmp_path, content)
        preview = parser.parse_preview(path)
        direct = parser.parse_markdown(path)
        assert preview.entries == direct
        assert preview.detected_format == "single_line"

    def test_single_line_unsplittable_line_is_problem(self, tmp_path):
        content = "1. analyze We need to analyze.\n2. atom\n"
        r = parser.parse_preview(self._write(tmp_path, content))
        assert len(r.entries) == 1
        assert [(p.lineno, p.reason) for p in r.problem_lines] == [(2, "无法拆分单词与例句")]

    def test_examples_files_preview_cleanly(self):
        """examples/ 的三个规范示例应零问题行；脏文件应报出问题行。"""
        root = parser.PROJECT_ROOT if hasattr(parser, "PROJECT_ROOT") else Path(__file__).resolve().parent.parent
        for name in ("example-single-line.md", "example-two-line.md", "example-colon.md"):
            r = parser.parse_preview(str(root / "examples" / name))
            assert r.problem_lines == [], f"{name} 不应有问题行"
        dirty = parser.parse_preview(str(root / "examples" / "example-dirty.md"))
        assert dirty.problem_lines, "脏文件应报出问题行"
        assert dirty.detected_format == "two_line"


# ===========================================================================
# generate_word_list_sentences（mock LLM）
# ===========================================================================
class TestGenerateWordListSentences:
    def _run_config(self, tmp_path, name="mywords"):
        src = tmp_path / f"{name}.md"
        src.write_text("initiate\nsustainable\n", encoding="utf-8")
        return RunConfig(input=str(src), output=str(tmp_path / "out.apkg"),
                         cache=str(tmp_path / "cache.json"))

    def _entries(self, *pairs):
        return [WordEntry(index=i + 1, word=w, sentence=h)
                for i, (w, h) in enumerate(pairs)]

    def test_generates_standard_two_line_file(self, tmp_path, monkeypatch):
        captured = {}

        async def fake_llm(messages, *a, **k):
            captured["content"] = messages[-1]["content"]
            return {
                "initiate": {"sentence": "They initiated the project last year.", "translation": "他们去年发起了该项目。"},
                "sustainable": {"sentence": "Sustainable development protects resources.", "translation": "可持续发展保护资源。"},
            }

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        args = self._run_config(tmp_path)
        path, failures = asyncio.run(
            pipeline.generate_word_list_sentences(args, self._entries(("initiate", ""), ("sustainable", "")))
        )
        assert failures == []
        assert path.endswith("mywords-sentences.md")
        assert Path(path).exists()
        text = Path(path).read_text(encoding="utf-8")
        # 标准两行式：可被 parse_markdown 直接解析
        entries = parser.parse_markdown(path)
        assert [e.word for e in entries] == ["initiate", "sustainable"]
        assert entries[0].sentence == "They initiated the project last year."
        assert "1. **initiate**" in text and "   > They initiated" in text
        # 提示词带上了单词列表
        assert "initiate" in captured["content"]

    def test_hint_passed_into_prompt(self, tmp_path, monkeypatch):
        captured = {}

        async def fake_llm(messages, *a, **k):
            captured["content"] = messages[-1]["content"]
            return {"leverage": {"sentence": "They leverage their strengths well.", "translation": "x"}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        args = self._run_config(tmp_path, "hints")
        path, failures = asyncio.run(
            pipeline.generate_word_list_sentences(args, self._entries(("leverage", "杠杆/充分利用")))
        )
        assert failures == []
        assert "中文提示: 杠杆/充分利用" in captured["content"]

    def test_failed_words_listed_and_skipped(self, tmp_path, monkeypatch):
        async def fake_llm(messages, *a, **k):
            # sustainable 的例句未包含该词 → 判失败
            return {
                "initiate": {"sentence": "They initiated the project.", "translation": "x"},
                "sustainable": {"sentence": "Green energy protects the environment.", "translation": "x"},
                "orphan": {"sentence": "", "translation": "x"},
            }

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        args = self._run_config(tmp_path, "partial")
        path, failures = asyncio.run(
            pipeline.generate_word_list_sentences(
                args, self._entries(("initiate", ""), ("sustainable", ""), ("orphan", ""))
            )
        )
        assert failures == ["sustainable", "orphan"]
        entries = parser.parse_markdown(path)
        assert [e.word for e in entries] == ["initiate"]

    def test_all_failed_returns_empty_path(self, tmp_path, monkeypatch):
        async def fake_llm(messages, *a, **k):
            return None  # 整批失败

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        args = self._run_config(tmp_path, "allfail")
        path, failures = asyncio.run(
            pipeline.generate_word_list_sentences(args, self._entries(("initiate", "")))
        )
        assert path == ""
        assert failures == ["initiate"]


# ===========================================================================
# normalize_any_text（mock LLM 原始输出）
# ===========================================================================
class TestNormalizeAnyText:
    def test_strips_code_fence_and_returns_text(self, monkeypatch):
        captured = {}

        async def fake_llm_raw(messages, *a, **k):
            captured["content"] = messages[-1]["content"]
            return "```markdown\n1. **novel**\n   > It was a novel idea.\n```"

        monkeypatch.setattr(llm, "llm_chat_raw", fake_llm_raw)
        out = pipeline.normalize_any_text("novel: It was a novel idea.", target_format="two_line")
        assert out == "1. **novel**\n   > It was a novel idea."
        # 提示词包含目标格式与原文
        assert "两行式" in captured["content"]
        assert "novel: It was a novel idea." in captured["content"]

    def test_plain_output_without_fence(self, monkeypatch):
        async def fake_llm_raw(messages, *a, **k):
            return "1. apple I ate an apple."

        monkeypatch.setattr(llm, "llm_chat_raw", fake_llm_raw)
        out = pipeline.normalize_any_text("apple ...", target_format="single_line")
        assert out == "1. apple I ate an apple."

    def test_failure_returns_empty(self, monkeypatch):
        async def fake_llm_raw(messages, *a, **k):
            return None

        monkeypatch.setattr(llm, "llm_chat_raw", fake_llm_raw)
        assert pipeline.normalize_any_text("text") == ""

    def test_format_mismatch_logs_warning_only(self, monkeypatch, caplog):
        async def fake_llm_raw(messages, *a, **k):
            return "1. **novel**\n   > It was a novel idea."

        monkeypatch.setattr(llm, "llm_chat_raw", fake_llm_raw)
        out = pipeline.normalize_any_text("novel ...", target_format="single_line")
        assert "novel" in out  # 仍返回结果，只告警


# ===========================================================================
# pipeline.run 纯单词模式端到端（mock LLM、无音频、隔离目录）
# ===========================================================================
class TestPipelineWordListEndToEnd:
    def test_word_list_generates_apkg(self, tmp_path, monkeypatch):
        # 输入：纯单词列表
        src = tmp_path / "e2e.md"
        src.write_text("initiate\nsustainable\n", encoding="utf-8")

        # 隔离全局路径与开关
        monkeypatch.setattr(config, "AUDIO_ENABLED", False)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        monkeypatch.setattr(config, "PROCESSED_FILE", str(tmp_path / "processed.json"))
        monkeypatch.setattr(config, "AUDIO_FAILURES_FILE", str(tmp_path / "audio_failures.json"))
        monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

        calls = {"n": 0}

        async def fake_llm(messages, *a, **k):
            calls["n"] += 1
            if "雅思备考教练" in messages[0]["content"]:
                # 造句批（两词一批）
                return {"initiate": {"sentence": "They initiated the project.", "translation": "他们发起了项目。"},
                        "sustainable": {"sentence": "Sustainable growth protects resources.", "translation": "x"}}
            # 富化批：键为中间文件的词
            return {"initiate": {"word": "initiate", "importance": "高", "context_meaning": "发起",
                                 "phonetic": "/ɪˈnɪʃieɪt/", "sentence_translation": "他们发起了项目。",
                                 "core_meanings": [{"usage": "发起", "definition_en": "begin"}]},
                    "sustainable": {"word": "sustainable", "importance": "中", "context_meaning": "可持续的",
                                    "phonetic": "/səˈsteɪnəbl/", "sentence_translation": "可持续增长保护资源。",
                                    "core_meanings": [{"usage": "可持续的", "definition_en": "able to continue"}]}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)

        out = tmp_path / "out.apkg"
        args = RunConfig(
            input=str(src), output=str(out), cache=str(tmp_path / "cache.json"),
            tts_provider="none", word_list=True,
        )
        summary = pipeline.run(args, PipelineCallbacks())
        assert summary and "[DONE]" in summary
        assert out.exists()
        # 中间文件已生成且为两行式
        interp = tmp_path / "data" / "intermediate" / "e2e-sentences.md"
        assert interp.exists()
        entries = parser.parse_markdown(str(interp))
        assert [e.word for e in entries] == ["initiate", "sustainable"]
        # 造句 1 批 + 富化 1 批
        assert calls["n"] == 2

    def test_word_list_detected_without_flag_also_triggers(self, tmp_path, monkeypatch):
        src = tmp_path / "auto.md"
        src.write_text("initiate\nsustainable\n", encoding="utf-8")

        monkeypatch.setattr(config, "AUDIO_ENABLED", False)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        monkeypatch.setattr(config, "PROCESSED_FILE", str(tmp_path / "processed.json"))
        monkeypatch.setattr(config, "AUDIO_FAILURES_FILE", str(tmp_path / "audio_failures.json"))
        monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

        async def fake_llm(messages, *a, **k):
            content = messages[-1]["content"]
            if "雅思备考教练" in messages[0]["content"]:
                return {"initiate": {"sentence": "They initiated the project.", "translation": "x"},
                        "sustainable": {"sentence": "Sustainable growth protects resources.", "translation": "x"}}
            return {"initiate": {"word": "initiate", "importance": "中", "context_meaning": "发起",
                                 "phonetic": "/ɪˈnɪʃieɪt/", "sentence_translation": "x",
                                 "core_meanings": [{"usage": "发起", "definition_en": "begin"}]},
                    "sustainable": {"word": "sustainable", "importance": "中", "context_meaning": "可持续的",
                                    "phonetic": "/səˈsteɪnəbl/", "sentence_translation": "x",
                                    "core_meanings": [{"usage": "可持续的", "definition_en": "able to continue"}]}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        out = tmp_path / "out.apkg"
        args = RunConfig(input=str(src), output=str(out), cache=str(tmp_path / "cache.json"),
                         tts_provider="none", word_list=False)
        summary = pipeline.run(args, PipelineCallbacks())
        assert summary and "[DONE]" in summary
        assert (tmp_path / "data" / "intermediate" / "auto-sentences.md").exists()
