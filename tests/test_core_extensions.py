"""
Task 6 前置 — core 层小扩展测试（设计文档 §9）：
  - parse_preview(force_format=...) 手动覆盖格式
  - PipelineCallbacks.on_failures 结构化失败清单回传
  - llm.llm_chat_stream 流式输出（mock OpenAI 客户端）

全部不触网。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.core import config, llm, parser, pipeline
from app.core.models import WordEntry
from app.core.pipeline import PipelineCallbacks, RunConfig


# ===========================================================================
# parse_preview(force_format)
# ===========================================================================
class TestParsePreviewForceFormat:
    def _write(self, tmp_path, content):
        f = tmp_path / "words.md"
        f.write_text(content, encoding="utf-8")
        return str(f)

    def test_force_word_list(self, tmp_path):
        path = self._write(tmp_path, "initiate\nsustainable\n")
        r = parser.parse_preview(path, force_format="word_list")
        assert r.detected_format == "word_list"
        assert [e.word for e in r.entries] == ["initiate", "sustainable"]

    def test_force_two_line_on_word_list_file_reports_problems(self, tmp_path):
        path = self._write(tmp_path, "initiate\nsustainable\n")
        r = parser.parse_preview(path, force_format="two_line")
        assert r.detected_format == "two_line"
        assert r.entries == []
        assert len(r.problem_lines) == 2

    def test_force_single_line(self, tmp_path):
        path = self._write(tmp_path, "1. analyze We need to analyze the data.\n")
        r = parser.parse_preview(path, force_format="single_line")
        assert r.detected_format == "single_line"
        assert len(r.entries) == 1

    def test_invalid_force_falls_back_to_auto(self, tmp_path):
        path = self._write(tmp_path, "1. analyze We need to analyze the data.\n")
        r = parser.parse_preview(path, force_format="bogus")
        assert r.detected_format == "single_line"

    def test_none_is_auto(self, tmp_path):
        path = self._write(tmp_path, "1. **assessment**\n   > The plan worked.\n")
        r = parser.parse_preview(path, force_format=None)
        assert r.detected_format == "two_line"


# ===========================================================================
# on_failures 结构化失败清单
# ===========================================================================
class TestPipelineOnFailures:
    def test_failures_reported_via_callback(self, tmp_path, monkeypatch):
        src = tmp_path / "f.md"
        src.write_text("1. novel It was a novel idea.\n2. tenure His tenure ended.\n", encoding="utf-8")

        monkeypatch.setattr(config, "AUDIO_ENABLED", False)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        monkeypatch.setattr(config, "PROCESSED_FILE", str(tmp_path / "processed.json"))
        monkeypatch.setattr(config, "AUDIO_FAILURES_FILE", str(tmp_path / "audio_failures.json"))
        monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

        async def fake_llm(messages, *a, **k):
            # tenure 故意缺失 → 进入失败清单
            return {"novel": {"word": "novel", "importance": "高", "context_meaning": "新颖的",
                              "phonetic": "/ˈnɒvəl/", "sentence_translation": "一个新颖的想法",
                              "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)

        failures_box = []
        cb = PipelineCallbacks(on_failures=lambda failures: failures_box.extend(failures))
        args = RunConfig(input=str(src), output=str(tmp_path / "out.apkg"),
                         cache=str(tmp_path / "cache.json"), tts_provider="none")
        summary = pipeline.run(args, cb)
        assert summary and "[DONE]" in summary
        assert len(failures_box) == 1
        assert failures_box[0][0] == "tenure"


# ===========================================================================
# llm_chat_stream
# ===========================================================================
class _FakeChunk:
    def __init__(self, text):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=text))] if text is not None else []


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __iter__(self):
        return iter(self._chunks)


class TestLLMChatStream:
    CFG = SimpleNamespace(api_key="sk-test", base_url="https://api.example.com", model="test-model")

    def _patch(self, monkeypatch, chunks_or_exc):
        holder = {}

        def factory(**kwargs):
            holder["kwargs"] = kwargs

            class _Completions:
                def create(self, **call_kwargs):
                    holder["call"] = call_kwargs
                    if isinstance(chunks_or_exc, Exception):
                        raise chunks_or_exc
                    return _FakeStream(chunks_or_exc)

            return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

        monkeypatch.setattr(llm, "OpenAI", factory)
        return holder

    def test_yields_chunks_in_order(self, monkeypatch):
        holder = self._patch(monkeypatch, [_FakeChunk("你好"), _FakeChunk("，世界"), _FakeChunk(None), _FakeChunk("！")])
        out = list(llm.llm_chat_stream(self.CFG, [{"role": "user", "content": "hi"}]))
        assert out == ["你好", "，世界", "！"]
        assert holder["call"]["stream"] is True
        assert holder["call"]["model"] == "test-model"
        assert holder["kwargs"]["api_key"] == "sk-test"

    def test_empty_stream_yields_nothing(self, monkeypatch):
        self._patch(monkeypatch, [])
        assert list(llm.llm_chat_stream(self.CFG, [])) == []

    def test_error_propagates(self, monkeypatch):
        self._patch(monkeypatch, RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            list(llm.llm_chat_stream(self.CFG, []))


# ===========================================================================
# 卡组名称自定义（deck_name）
# ===========================================================================
class TestDeckName:
    def _package(self, deck_name=None):
        from app.core import anki_packager
        entry = WordEntry(index=1, word="valid", sentence="a valid idea")
        enrichment = {
            "valid": {"word": "valid", "context_meaning": "有效的",
                      "sentence_translation": "x", "audio_filename": "",
                      "core_meanings": [{"usage": "有效的", "definition_en": "valid"}]},
        }
        import tempfile
        return anki_packager.create_anki_package(
            [entry], enrichment, {}, force_model_id=1234567890,
            media_dir=__import__("pathlib").Path(tempfile.mkdtemp()),
            deck_name=deck_name,
        )

    def test_custom_deck_name(self):
        p = self._package("雅思::核心词汇")
        assert p.decks[0].name == "雅思::核心词汇"

    def test_blank_falls_back_to_default(self):
        from app.core.anki_packager import DEFAULT_DECK_NAME
        assert self._package("").decks[0].name == DEFAULT_DECK_NAME
        assert self._package("   ").decks[0].name == DEFAULT_DECK_NAME
        assert self._package(None).decks[0].name == DEFAULT_DECK_NAME

    def test_pipeline_resolves_placeholders_and_passes_through(self, tmp_path, monkeypatch):
        from datetime import datetime
        from app.core import anki_packager

        src = tmp_path / "d.md"
        src.write_text("1. novel It was a novel idea.\n", encoding="utf-8")
        monkeypatch.setattr(config, "AUDIO_ENABLED", False)
        monkeypatch.setattr(config, "QUALITY_CHECKS_ENABLED", False)
        monkeypatch.setattr(config, "PROCESSED_FILE", str(tmp_path / "processed.json"))
        monkeypatch.setattr(config, "AUDIO_FAILURES_FILE", str(tmp_path / "audio_failures.json"))
        monkeypatch.setattr(config, "MEDIA_DIR", tmp_path / "media")
        monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")

        async def fake_llm(messages, *a, **k):
            return {"novel": {"word": "novel", "importance": "高", "context_meaning": "新颖的",
                              "phonetic": "/ˈnɒvəl/", "sentence_translation": "x",
                              "core_meanings": [{"usage": "新颖的", "definition_en": "new"}]}}

        monkeypatch.setattr(llm, "llm_chat", fake_llm)

        captured = {}
        orig = anki_packager.create_anki_package

        def spy(*a, **k):
            captured["deck_name"] = k.get("deck_name")
            return orig(*a, **k)

        monkeypatch.setattr(anki_packager, "create_anki_package", spy)

        args = RunConfig(input=str(src), output=str(tmp_path / "out.apkg"),
                         cache=str(tmp_path / "cache.json"), tts_provider="none",
                         deck_name=" {词数}·{日期} ")
        summary = pipeline.run(args, PipelineCallbacks())
        assert summary and "[DONE]" in summary
        today = datetime.now().strftime("%Y-%m-%d")
        assert captured["deck_name"] == f"1·{today}"
