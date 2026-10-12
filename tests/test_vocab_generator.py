"""
vocab_anki_generator 的自动化测试套件。

覆盖**不依赖网络/API** 的纯逻辑：
  - Markdown 解析（格式 A/B/C + 边界）
  - JSON 稳健修复（截断/缺首括号/markdown 包裹/尾逗号）
  - 填空正面构建
  - 富化字段兜底
  - 增量过滤 + processed.json 键迁移
  - 文件名清洗、重要性等级、process_batch 的多层 key 查找（mock LLM）

运行: pytest tests/ -v

Task 1 拆包后：测试逻辑不变，仅 import 路径改为 app.core 各模块；
monkeypatch 目标相应落到符号的**所属模块**（llm/config/audio/cache/quality）。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

# 让测试能 import 项目根目录下的 app 包与兼容 shim
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import anki_packager, audio, cache, config, enricher, llm, models, parser, quality  # noqa: E402


# placeholder-tests


# ===========================================================================
# repair_and_parse_json — JSON 稳健修复
# ===========================================================================
class TestRepairAndParseJson:
    def test_plain_json(self):
        assert llm.repair_and_parse_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced_json(self):
        raw = '```json\n{"word": "test"}\n```'
        assert llm.repair_and_parse_json(raw) == {"word": "test"}

    def test_bare_fence(self):
        raw = '```\n{"x": 2}\n```'
        assert llm.repair_and_parse_json(raw) == {"x": 2}

    def test_leading_explanation_text(self):
        raw = '好的，这是结果：\n{"word": "ok"}'
        assert llm.repair_and_parse_json(raw) == {"word": "ok"}

    def test_bom_and_zero_width(self):
        raw = '﻿​{"k": "v"}'
        assert llm.repair_and_parse_json(raw) == {"k": "v"}

    def test_trailing_text_after_object(self):
        raw = '{"a": 1} 以上就是结果'
        assert llm.repair_and_parse_json(raw) == {"a": 1}

    def test_truncated_unclosed_object(self):
        # 模拟 max_tokens 耗尽：对象未闭合
        raw = '{"word": "tenure", "phonetic": "/x/", "core": ['
        result = llm.repair_and_parse_json(raw)
        assert result["word"] == "tenure"
        assert result["phonetic"] == "/x/"

    def test_truncated_with_trailing_comma(self):
        raw = '{"a": 1, "b": 2,'
        result = llm.repair_and_parse_json(raw)
        assert result == {"a": 1, "b": 2}

    def test_truncated_nested(self):
        # 类似日志里 Tenure 那批：嵌套结构被截断
        raw = ('{"tenure": {"word": "Tenure", "core_meanings": '
               '[{"usage": "任期", "definition_en": "the period during which')
        result = llm.repair_and_parse_json(raw)
        assert "tenure" in result
        assert result["tenure"]["word"] == "Tenure"

    def test_missing_outer_braces(self):
        raw = '"a": 1, "b": 2'
        result = llm.repair_and_parse_json(raw)
        assert result == {"a": 1, "b": 2}

    def test_none_raises(self):
        with pytest.raises(json.JSONDecodeError):
            llm.repair_and_parse_json(None)

    def test_unrecoverable_raises(self):
        with pytest.raises(json.JSONDecodeError):
            llm.repair_and_parse_json("这完全不是 JSON 也没有花括号内容 ::: !!!")


# ===========================================================================
# sanitize_filename
# ===========================================================================
class TestSanitizeFilename:
    def test_lowercase_and_underscore(self):
        assert audio.sanitize_filename("In the case of") == "in_the_case_of"

    def test_strip_punctuation(self):
        assert audio.sanitize_filename("peer-reviewed!") == "peer_reviewed"

    def test_collapse_multiple_underscores(self):
        assert audio.sanitize_filename("a   b") == "a_b"

    def test_length_cap(self):
        long_word = "a" * 100
        assert len(audio.sanitize_filename(long_word)) <= 64


class TestAudioFileValidation:
    def test_audio_file_is_valid_rejects_missing_and_zero_byte(self, tmp_path):
        missing = tmp_path / "missing.mp3"
        zero = tmp_path / "zero.mp3"
        zero.write_bytes(b"")
        assert cache.audio_file_is_valid(missing.name, tmp_path) is False
        assert cache.audio_file_is_valid(zero.name, tmp_path) is False

    def test_audio_file_is_valid_accepts_non_empty(self, tmp_path):
        audio_ = tmp_path / "word.mp3"
        audio_.write_bytes(b"audio")
        assert cache.audio_file_is_valid(audio_.name, tmp_path) is True

    def test_extract_sound_filename(self):
        assert cache.extract_sound_filename("[sound:word.mp3]") == "word.mp3"
        assert cache.extract_sound_filename("word.mp3") == ""


class TestTTSProviderSelection:
    def test_provider_order_auto_uses_fallbacks(self):
        assert audio.get_tts_provider_order("auto", "gtts,edge") == ["gtts", "edge"]

    def test_provider_order_none_disables_audio(self):
        assert audio.get_tts_provider_order("none", "gtts,edge") == ["none"]

    def test_select_provider_falls_back_to_edge(self, monkeypatch):
        monkeypatch.setattr(config, "AUDIO_ENABLED", True)
        monkeypatch.setattr(audio, "GTTS_AVAILABLE", True)
        monkeypatch.setattr(audio, "EDGE_TTS_AVAILABLE", True)
        monkeypatch.setattr(audio, "check_gtts_connectivity", lambda: False)
        health = audio.select_audio_provider("auto")
        assert health.available is True
        assert health.provider == "edge"

    def test_select_provider_reports_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "AUDIO_ENABLED", False)
        health = audio.select_audio_provider("auto")
        assert health.available is False
        assert health.provider == "none"

    def test_generate_audio_force_rewrites_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "AUDIO_ENABLED", True)
        monkeypatch.setattr(audio, "GTTS_AVAILABLE", True)
        monkeypatch.setattr(audio, "load_audio_failures", lambda: {})
        audio_ = tmp_path / "word.mp3"
        audio_.write_bytes(b"old")

        def fake_gtts(word, filepath):
            filepath.write_bytes(b"new")

        monkeypatch.setattr(audio, "_generate_gtts_audio", fake_gtts)
        filename = audio.generate_audio_sync("word", str(tmp_path), provider="gtts", force=True)
        assert filename == "word.mp3"
        assert audio_.read_bytes() == b"new"


class TestPathMigration:
    def test_save_json_creates_parent_directory(self, tmp_path):
        target = tmp_path / "data" / "cache" / "x.json"
        cache.save_json(str(target), {"ok": True})
        assert target.exists()
        assert cache.load_json(str(target)) == {"ok": True}

    def test_audio_file_resolves_legacy_media(self, tmp_path, monkeypatch):
        legacy = tmp_path / "media"
        new_media = tmp_path / "data" / "media"
        legacy.mkdir()
        new_media.mkdir(parents=True)
        (legacy / "word.mp3").write_bytes(b"audio")
        monkeypatch.setattr(config, "LEGACY_MEDIA_DIR", legacy)
        monkeypatch.setattr(config, "MEDIA_DIR", new_media)
        assert cache.audio_file_is_valid("word.mp3", new_media) is True
        assert cache.resolve_audio_file("word.mp3", new_media) == legacy / "word.mp3"


# ===========================================================================
# Importance
# ===========================================================================
class TestImportance:
    def test_from_str_known(self):
        assert models.Importance.from_str("极高") == models.Importance.EXTREME_HIGH

    def test_from_str_unknown_defaults_medium(self):
        assert models.Importance.from_str("不存在") == models.Importance.MEDIUM

    def test_level_ordering(self):
        assert models.Importance.EXTREME_HIGH.to_level() > models.Importance.LOW.to_level()

    def test_meets_minimum(self):
        assert models.Importance.HIGH.meets_minimum("中") is True
        assert models.Importance.LOW.meets_minimum("高") is False


# ===========================================================================
# parse_markdown — 三种格式 + 边界
# ===========================================================================
class TestParseMarkdown:
    def _write(self, tmp_path, content):
        f = tmp_path / "words.md"
        f.write_text(content, encoding="utf-8")
        return str(f)

    def test_format_a_single_line(self, tmp_path):
        content = "1. analyze We need to analyze the data carefully.\n"
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0].word == "analyze"
        assert "analyze the data" in entries[0].sentence

    def test_format_b_two_line(self, tmp_path):
        content = (
            "1. **assessment**\n"
            "   > The government set up a Research Assessment Exercise.\n"
        )
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0].word == "assessment"
        assert entries[0].sentence.startswith("The government")

    def test_format_b_strips_inner_bold(self, tmp_path):
        content = (
            "1. **inspects**\n"
            "   > It inspects research to determine the rate of **27** ______.\n"
        )
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert "**27**" not in entries[0].sentence
        assert "27" in entries[0].sentence

    def test_format_c_colon(self, tmp_path):
        content = "1. **sector**: research within public sector.\n"
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0].word == "sector"

    def test_skips_comments_and_separators(self, tmp_path):
        content = (
            "# Title\n"
            "> 来源：xxx\n"
            "---\n"
            "1. **severe**\n"
            "   > consequences are severe.\n"
        )
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert len(entries) == 1
        assert entries[0].word == "severe"

    def test_format_b_missing_sentence_at_eof(self, tmp_path):
        content = "1. **lonely**\n"
        entries = parser.parse_markdown(self._write(tmp_path, content))
        # 缺例句行应被跳过，不抛异常
        assert entries == []

    def test_index_preserved(self, tmp_path):
        content = (
            "5. **severe**\n"
            "   > consequences are severe.\n"
            "6. **novel**\n"
            "   > novel projects.\n"
        )
        entries = parser.parse_markdown(self._write(tmp_path, content))
        assert [e.index for e in entries] == [5, 6]


# ===========================================================================
# build_cloze_front
# ===========================================================================
class TestBuildClozeFront:
    def test_first_occurrence_only(self):
        out = anki_packager.build_cloze_front("test", "a test and another test", replace_all=False)
        assert out.count("{{c1::") == 1
        assert "{{c1::test}}" in out

    def test_replace_all(self):
        out = anki_packager.build_cloze_front("test", "a test and another test", replace_all=True)
        assert out.count("{{c1::") == 2

    def test_case_insensitive_match_preserves_original(self):
        out = anki_packager.build_cloze_front("assessment", "The Assessment Exercise.", replace_all=False)
        assert "{{c1::Assessment}}" in out

    def test_word_not_in_sentence(self):
        out = anki_packager.build_cloze_front("missing", "this sentence lacks it.", replace_all=False)
        assert out.startswith("[缺词: missing]")


# ===========================================================================
# validate_and_fix_enrichment — 字段兜底
# ===========================================================================
class TestValidateAndFixEnrichment:
    def test_fills_all_defaults(self):
        data = quality.validate_and_fix_enrichment(
            {}, "word", models.Importance.HIGH, "word.mp3"
        )
        assert data["word"] == "word"
        assert data["audio_filename"] == "[sound:word.mp3]"
        assert data["core_meanings"]  # 至少一项占位
        assert data["importance"] == "高"

    def test_core_meaning_subfields(self):
        data = quality.validate_and_fix_enrichment(
            {"core_meanings": [{"usage": "x"}]},
            "w", models.Importance.MEDIUM, "w.mp3",
        )
        cm = data["core_meanings"][0]
        for key in ("usage", "logic", "definition_en", "grammar_note", "examples"):
            assert key in cm

    def test_preserves_existing_values(self):
        data = quality.validate_and_fix_enrichment(
            {"context_meaning": "发起", "phonetic": "/x/"},
            "initiate", models.Importance.EXTREME_HIGH, "initiate.mp3",
        )
        assert data["context_meaning"] == "发起"
        assert data["phonetic"] == "/x/"

    def test_deduplicates_and_drops_placeholder_meanings(self):
        data = quality.validate_and_fix_enrichment(
            {
                "core_meanings": [
                    {"usage": "暂无详细释义", "definition_en": "placeholder"},
                    {"usage": "关键的", "definition_en": "important"},
                    {"usage": "关键的", "definition_en": "duplicate"},
                ]
            },
            "critical", models.Importance.HIGH, "critical.mp3",
        )
        assert [cm["usage"] for cm in data["core_meanings"]] == ["关键的"]

    def test_invalid_phonetic_and_lemma_are_sanitized(self):
        data = quality.validate_and_fix_enrichment(
            {"word": "错误词条", "phonetic": "这是音标", "core_meanings": [{"usage": "发起"}]},
            "initiated", models.Importance.HIGH, "initiated.mp3",
        )
        assert data["word"] == "initiated"
        assert data["phonetic"] == ""

    def test_low_quality_content_detected(self):
        data = quality.validate_and_fix_enrichment(
            {"core_meanings": [{"usage": "待补充"}]},
            "word", models.Importance.HIGH, "word.mp3",
        )
        assert quality.has_placeholder_or_low_quality_content(data) is True


# ===========================================================================
# 增量过滤 + processed.json 键迁移
# ===========================================================================
class TestEnrichmentCacheKey:
    def test_cache_key_changes_with_sentence(self):
        a = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        b = models.WordEntry(index=2, word="novel", sentence="a novel solution")
        assert cache.build_enrichment_cache_key(a) != cache.build_enrichment_cache_key(b)

    def test_get_cached_enrichment_prefers_versioned_key(self):
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        versioned_key = cache.build_enrichment_cache_key(entry)
        cache_dict = {
            "novel": {"word": "old"},
            versioned_key: {"word": "new"},
        }
        cached, key, is_legacy = cache.get_cached_enrichment(entry, cache_dict)
        assert cached["word"] == "new"
        assert key == versioned_key
        assert is_legacy is False

    def test_get_cached_enrichment_falls_back_to_legacy_key(self):
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        cached, key, is_legacy = cache.get_cached_enrichment(entry, {"novel": {"word": "old"}})
        assert cached["word"] == "old"
        assert key == "novel"
        assert is_legacy is True


class TestIncrementalFilter:
    def test_new_word_not_skipped(self):
        entries = [models.WordEntry(index=1, word="novel", sentence="a novel idea")]
        new = cache.filter_new_entries(entries, {}, {}, force=False)
        assert len(new) == 1

    def test_processed_word_same_hash_skipped(self, monkeypatch):
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        h = cache.compute_sentence_hash(entry.sentence)
        processed = {"novel": {"word": "novel", "sentence_hash": h, "processed_at": "x"}}
        new = cache.filter_new_entries([entry], {}, processed, force=False)
        assert new == []

    def test_processed_word_changed_sentence_reprocessed(self):
        entry = models.WordEntry(index=1, word="novel", sentence="a brand new sentence")
        processed = {"novel": {"word": "novel", "sentence_hash": "stale", "processed_at": "x"}}
        new = cache.filter_new_entries([entry], {}, processed, force=False)
        assert len(new) == 1

    def test_force_ignores_processed(self):
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        h = cache.compute_sentence_hash(entry.sentence)
        processed = {"novel": {"word": "novel", "sentence_hash": h, "processed_at": "x"}}
        new = cache.filter_new_entries([entry], {}, processed, force=True)
        assert len(new) == 1

    def test_reordering_does_not_trigger_reprocess(self, monkeypatch):
        """核心回归：改变序号不应导致重跑（旧版 bug）。"""
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        entry = models.WordEntry(index=99, word="novel", sentence="a novel idea")
        h = cache.compute_sentence_hash(entry.sentence)
        # processed 里该词的旧序号是 1，现在变成 99
        processed = {"novel": {"word": "novel", "sentence_hash": h, "processed_at": "x"}}
        new = cache.filter_new_entries([entry], {}, processed, force=False)
        assert new == []

    def test_incomplete_new_processed_record_reprocesses(self):
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        h = cache.compute_sentence_hash(entry.sentence)
        processed = {
            "novel": {
                "word": "novel",
                "sentence_hash": h,
                "enrichment_status": "ok",
                "audio_status": "missing",
                "processed_at": "x",
            }
        }
        new = cache.filter_new_entries([entry], {}, processed, force=False)
        assert len(new) == 1

    def test_complete_new_processed_record_skips(self, monkeypatch):
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        entry = models.WordEntry(index=1, word="novel", sentence="a novel idea")
        h = cache.compute_sentence_hash(entry.sentence)
        processed = {
            "novel": {
                "word": "novel",
                "sentence_hash": h,
                "enrichment_status": "ok",
                "audio_status": "ok",
                "processed_at": "x",
            }
        }
        new = cache.filter_new_entries([entry], {}, processed, force=False)
        assert new == []


class TestMigrateProcessedKeys:
    def test_migrates_numeric_keys_to_word(self):
        old = {
            "1": {"word": "Assessment", "sentence_hash": "abc", "processed_at": "x"},
            "2": {"word": "sector", "sentence_hash": "def", "processed_at": "y"},
        }
        migrated = cache.migrate_processed_keys(old)
        assert "assessment" in migrated
        assert "sector" in migrated
        assert migrated["assessment"]["sentence_hash"] == "abc"

    def test_idempotent_on_word_keys(self):
        already = {"novel": {"word": "novel", "sentence_hash": "h", "processed_at": "x"}}
        migrated = cache.migrate_processed_keys(already)
        assert migrated == already

    def test_empty(self):
        assert cache.migrate_processed_keys({}) == {}


# ===========================================================================
# quality checker helpers — accuracy / completeness 子代理报告
# ===========================================================================
class TestQualityHelpers:
    def test_merge_quality_reports_pass(self):
        report = {"items": {"novel": {"passed": True, "severity": "pass", "issues": [], "fix_instructions": []}}}
        merged = quality.merge_quality_reports(report, report, "novel")
        assert quality.quality_report_passed(merged)

    def test_merge_quality_reports_blocking_issue(self):
        ok = {"items": {"novel": {"passed": True, "severity": "pass", "issues": []}}}
        bad = {"items": {"novel": {"passed": False, "severity": "blocking", "issues": [{"field": "context_meaning", "problem": "不贴合", "expected": "改正"}]}}}
        merged = quality.merge_quality_reports(ok, bad, "novel")
        assert not quality.quality_report_passed(merged)
        assert merged["issues"][0]["checker"] == "completeness"

    def test_missing_checker_output_fails_closed(self):
        ok = {"items": {"novel": {"passed": True, "severity": "pass", "issues": []}}}
        merged = quality.merge_quality_reports(ok, None, "novel")
        assert not merged["passed"]
        assert "checker" in quality.summarize_quality_issues(merged)

    def test_local_quality_allows_phrase_without_phonetic(self):
        entry = models.WordEntry(index=1, word="raises ethical concerns", sentence="The idea raises ethical concerns.")
        data = {"context_meaning": "引发伦理担忧", "sentence_translation": "这个想法引发伦理担忧。", "phonetic": "", "core_meanings": [{"usage": "引发伦理担忧", "definition_en": "cause moral worries"}]}
        report = quality.local_quality_report(entry, data)
        assert report["passed"]

    def test_processed_record_requires_quality_metadata_when_enabled(self, monkeypatch):
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", True)
        h = "abc"
        legacy = {"word": "novel", "sentence_hash": h, "processed_at": "x"}
        assert not cache.processed_record_complete(legacy, h)
        complete = {"word": "novel", "sentence_hash": h, "enrichment_status": "ok", "audio_status": "ok", "quality_status": "ok", "quality_schema_version": config.QUALITY_SCHEMA_VERSION}
        assert cache.processed_record_complete(complete, h)


# ===========================================================================
# process_batch — mock LLM，验证三层 key 查找 + 失败收集
# ===========================================================================
class TestProcessBatch:
    def _entry(self, word, sentence):
        return models.WordEntry(index=1, word=word, sentence=sentence)

    def _run(self, coro):
        return asyncio.run(coro)

    def test_exact_key_match(self, monkeypatch):
        async def fake_llm(*a, **k):
            return {"novel": {"word": "novel", "importance": "高",
                              "context_meaning": "新颖的", "phonetic": "/ˈnɒvəl/",
                              "sentence_translation": "一个新颖的想法",
                              "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}}
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        batch = [(self._entry("novel", "a novel idea"), "novel.mp3")]
        sem = asyncio.Semaphore(1)
        enriched, failures, warns = self._run(enricher.process_batch(batch, sem))
        assert "novel" in enriched
        assert failures == []
        assert warns == []

    def test_inflected_form_fallback(self, monkeypatch):
        # LLM 用还原后的词形 initiate 作键，但原词是 initiated
        async def fake_llm(*a, **k):
            return {"initiate": {"word": "initiate", "inflected_form": "initiated",
                                 "importance": "高", "context_meaning": "发起", "phonetic": "/ɪˈnɪʃieɪt/",
                                 "sentence_translation": "他们发起项目。",
                                 "core_meanings": [{"usage": "发起", "definition_en": "begin"}]}}
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        batch = [(self._entry("initiated", "they initiated projects"), "initiated.mp3")]
        sem = asyncio.Semaphore(1)
        enriched, failures, warns = self._run(enricher.process_batch(batch, sem))
        assert "initiated" in enriched
        assert failures == []
        assert warns == []

    def test_missing_word_recorded_as_failure(self, monkeypatch):
        async def fake_llm(*a, **k):
            return {"other": {"word": "other", "importance": "高",
                              "core_meanings": [{"definition_en": "x"}]}}
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        batch = [(self._entry("novel", "a novel idea"), "novel.mp3")]
        sem = asyncio.Semaphore(1)
        enriched, failures, warns = self._run(enricher.process_batch(batch, sem))
        assert "novel" not in enriched
        assert len(failures) == 1
        assert failures[0][0] == "novel"
        assert warns == []

    def test_whole_batch_failure(self, monkeypatch):
        async def fake_llm(*a, **k):
            return None  # 整批 JSON 失败
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        batch = [
            (self._entry("a", "word a here"), "a.mp3"),
            (self._entry("b", "word b here"), "b.mp3"),
        ]
        sem = asyncio.Semaphore(1)
        enriched, failures, warns = self._run(enricher.process_batch(batch, sem))
        assert enriched == {}
        assert len(failures) == 2
        assert warns == []

    def test_quality_checker_retry_fixes_completeness(self, monkeypatch):
        calls = {"n": 0}
        async def fake_llm(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"novel": {"word": "novel", "importance": "高",
                                  "context_meaning": "新颖的", "phonetic": "",
                                  "sentence_translation": "一个新颖的想法",
                                  "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}}
            if calls["n"] in (2, 3):
                return {"items": {"novel": {"passed": True, "severity": "pass", "issues": [], "fix_instructions": []}}}
            if calls["n"] == 4:
                return {"word": "novel", "importance": "高", "context_meaning": "新颖的",
                        "phonetic": "/ˈnɒvəl/", "sentence_translation": "一个新颖的想法",
                        "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}
            return {"items": {"novel": {"passed": True, "severity": "pass", "issues": [], "fix_instructions": []}}}
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", True)
        monkeypatch.setattr(config, "QUALITY_MAX_ATTEMPTS", 1)
        batch = [(self._entry("novel", "a novel idea"), "novel.mp3")]
        enriched, failures, warns = self._run(enricher.process_batch(batch, asyncio.Semaphore(3)))
        assert failures == []
        assert enriched["novel"]["phonetic"] == "/ˈnɒvəl/"
        assert enriched["novel"]["_quality_status"] == "passed"

    def test_quality_checker_fails_after_max_attempts(self, monkeypatch):
        async def fake_llm(*a, **k):
            content = a[0][1]["content"] if a and isinstance(a[0], list) and len(a[0]) > 1 else ""
            if "待检查数据" in content:
                return {"items": {"novel": {"passed": False, "severity": "blocking", "issues": [{"field": "context_meaning", "problem": "不准确", "expected": "修正"}], "fix_instructions": ["修正释义"]}}}
            return {"novel": {"word": "novel", "importance": "高", "context_meaning": "小说",
                              "phonetic": "/ˈnɒvəl/", "sentence_translation": "一个小说想法",
                              "core_meanings": [{"usage": "小说", "definition_en": "book"}]}}
        monkeypatch.setattr(llm, "llm_chat", fake_llm)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", True)
        monkeypatch.setattr(config, "QUALITY_MAX_ATTEMPTS", 0)
        batch = [(self._entry("novel", "a novel idea"), "novel.mp3")]
        enriched, failures, warns = self._run(enricher.process_batch(batch, asyncio.Semaphore(3)))
        assert enriched == {}
        assert failures and "质量检查未通过" in failures[0][1]


class TestCreateAnkiPackageMedia:
    def test_package_ignores_unreferenced_and_zero_byte_media(self, tmp_path):
        valid = tmp_path / "valid.mp3"
        zero = tmp_path / "zero.mp3"
        stale = tmp_path / "stale.mp3"
        valid.write_bytes(b"audio")
        zero.write_bytes(b"")
        stale.write_bytes(b"unused")

        entry_valid = models.WordEntry(index=1, word="valid", sentence="a valid idea")
        entry_zero = models.WordEntry(index=2, word="zero", sentence="a zero idea")
        enrichment = {
            "valid": {
                "word": "valid",
                "context_meaning": "有效的",
                "sentence_translation": "一个有效的想法",
                "audio_filename": "[sound:valid.mp3]",
                "core_meanings": [{"usage": "有效的", "definition_en": "valid"}],
            },
            "zero": {
                "word": "zero",
                "context_meaning": "零",
                "sentence_translation": "一个零想法",
                "audio_filename": "[sound:zero.mp3]",
                "core_meanings": [{"usage": "零", "definition_en": "zero"}],
            },
        }
        package = anki_packager.create_anki_package(
            [entry_valid, entry_zero], enrichment, {}, force_model_id=1234567890, media_dir=tmp_path
        )
        media_names = {Path(p).name for p in package.media_files}
        assert media_names == {"valid.mp3"}
