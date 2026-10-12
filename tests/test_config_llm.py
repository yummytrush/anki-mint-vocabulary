"""
Task 3 — 配置体系与连通测试。

覆盖：
  - AppConfig 优先级合并（CLI 参数 > data/config.json > env/.env > 代码默认）穷举
  - save/load 往返、损坏文件回退、未知键忽略、bool/int 宽松转换
  - masked() 打码回显
  - apply_to_config 写回模块全局
  - hint_env_import 首次运行提示
  - test_llm_connection：成功 / 鉴权失败 / 网络不通 / 超时 / 模型不存在(404, 400) / 其它

全部用 monkeypatch 隔离（CONFIG_FILE 指向临时目录、config 全局自动还原、
openai 客户端打桩），不触网。
"""
import json
from types import SimpleNamespace

import httpx
import pytest

from app.core import config, llm


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """CONFIG_FILE 指向临时路径（不存在 = 未建配置）。"""
    p = tmp_path / "data" / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", str(p))
    return p


def _write_cfg(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ===========================================================================
# AppConfig — 优先级合并
# ===========================================================================
class TestAppConfigPriority:
    def test_defaults_no_file(self, cfg_file):
        cfg = config.AppConfig.load()
        assert cfg.model == config.LLM_MODEL
        assert cfg.max_concurrency == config.MAX_CONCURRENCY
        assert cfg.quality_checks_enabled == config.QUALITY_CHECKS_ENABLED

    def test_file_overrides_env_defaults(self, cfg_file):
        _write_cfg(cfg_file, {"model": "my-model", "max_concurrency": 3, "batch_size": 2})
        cfg = config.AppConfig.load()
        assert cfg.model == "my-model"
        assert cfg.max_concurrency == 3
        assert cfg.batch_size == 2
        # 未覆盖的字段保持 env 派生默认
        assert cfg.base_url == config.LLM_BASE_URL

    def test_overrides_win_over_file(self, cfg_file):
        _write_cfg(cfg_file, {"model": "file-model", "max_concurrency": 3})
        cfg = config.resolve_config({"model": "cli-model", "max_concurrency": 9})
        assert cfg.model == "cli-model"
        assert cfg.max_concurrency == 9

    def test_none_overrides_do_not_mask_file(self, cfg_file):
        _write_cfg(cfg_file, {"model": "file-model"})
        cfg = config.resolve_config({"model": None, "max_concurrency": None})
        assert cfg.model == "file-model"

    def test_corrupt_file_falls_back(self, cfg_file):
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text("{not json", encoding="utf-8")
        cfg = config.AppConfig.load()
        assert cfg.model == config.LLM_MODEL

    def test_non_dict_file_falls_back(self, cfg_file):
        _write_cfg(cfg_file, [1, 2, 3])
        assert config.AppConfig.load().model == config.LLM_MODEL

    def test_unknown_keys_ignored(self, cfg_file):
        _write_cfg(cfg_file, {"model": "m", "no_such_field": 1})
        assert config.AppConfig.load().model == "m"

    def test_bool_string_coercion(self, cfg_file):
        _write_cfg(cfg_file, {"audio_enabled": "false", "replace_all": "true"})
        cfg = config.AppConfig.load()
        assert cfg.audio_enabled is False
        assert cfg.replace_all is True

    def test_int_coercion_and_invalid_ignored(self, cfg_file):
        _write_cfg(cfg_file, {"batch_size": "7", "audio_timeout": "abc"})
        cfg = config.AppConfig.load()
        assert cfg.batch_size == 7
        assert cfg.audio_timeout == config.AUDIO_TIMEOUT  # 非法值忽略

    def test_null_values_ignored(self, cfg_file):
        _write_cfg(cfg_file, {"model": None})
        assert config.AppConfig.load().model == config.LLM_MODEL

    def test_save_load_roundtrip(self, cfg_file):
        cfg = config.AppConfig(base_url="https://x.example", api_key="sk-abc",
                               model="m", max_concurrency=2, replace_all=True)
        cfg.save()
        loaded = config.AppConfig.load()
        assert loaded == cfg


# ===========================================================================
# masked / apply_to_config / hint_env_import
# ===========================================================================
class TestAppConfigHelpers:
    def test_masked_hides_key_keeps_rest(self):
        cfg = config.AppConfig(api_key="sk-1234567890abcdef", model="m", max_concurrency=4)
        m = cfg.masked()
        assert m.api_key.startswith("sk-1")
        assert m.api_key.endswith("cdef")
        assert "2345678" not in m.api_key
        assert m.model == "m"

    def test_masked_empty_and_short_keys(self):
        assert config.AppConfig(api_key="").masked().api_key == ""
        assert config.AppConfig(api_key="short").masked().api_key == "****"

    def test_apply_to_config_writes_globals(self, monkeypatch):
        # 预置哨兵值，monkeypatch 保证测试后自动还原
        for name, val in [("LLM_API_KEY", "OLD"), ("LLM_MODEL", "OLD"), ("MAX_CONCURRENCY", 0),
                          ("BATCH_SIZE", 0), ("REPLACE_ALL", False), ("AUDIO_ENABLED", True),
                          ("IMPORTANCE_MIN_LEVEL", "OLD"), ("QUALITY_CHECKS_ENABLED", True),
                          ("QUALITY_MAX_ATTEMPTS", 0), ("AUDIO_PROXY", "OLD"), ("AUDIO_TIMEOUT", 0),
                          ("AUDIO_FAIL_COOLDOWN", 0), ("TTS_PROVIDER", "OLD"), ("TTS_FALLBACKS", "OLD"),
                          ("TTS_VOICE", "OLD"), ("RATE_LIMIT_PER_MIN", 0), ("BATCH_MAX_TOKENS", 0),
                          ("LLM_BASE_URL", "OLD")]:
            monkeypatch.setattr(config, name, val)
        cfg = config.AppConfig(api_key="NEW", base_url="https://n", model="NEW-M",
                               max_concurrency=2, rate_limit_per_min=10, batch_size=3,
                               batch_max_tokens=99, replace_all=True, audio_enabled=False,
                               importance_min_level="高", quality_checks_enabled=False,
                               quality_max_attempts=1, audio_proxy="http://p", audio_timeout=5,
                               audio_fail_cooldown=60, tts_provider="edge",
                               tts_fallbacks="edge,gtts", tts_voice="v1")
        cfg.apply_to_config()
        assert config.LLM_API_KEY == "NEW"
        assert config.LLM_BASE_URL == "https://n"
        assert config.LLM_MODEL == "NEW-M"
        assert config.MAX_CONCURRENCY == 2
        assert config.RATE_LIMIT_PER_MIN == 10
        assert config.BATCH_SIZE == 3
        assert config.BATCH_MAX_TOKENS == 99
        assert config.REPLACE_ALL is True
        assert config.AUDIO_ENABLED is False
        assert config.IMPORTANCE_MIN_LEVEL == "高"
        assert config.QUALITY_CHECKS_ENABLED is False
        assert config.QUALITY_MAX_ATTEMPTS == 1
        assert config.AUDIO_PROXY == "http://p"
        assert config.AUDIO_TIMEOUT == 5
        assert config.AUDIO_FAIL_COOLDOWN == 60
        assert config.TTS_PROVIDER == "edge"
        assert config.TTS_FALLBACKS == "edge,gtts"
        assert config.TTS_VOICE == "v1"

    def test_hint_env_import_prints_when_no_config_and_key(self, cfg_file, monkeypatch, capsys):
        monkeypatch.setattr(config, "LLM_API_KEY", "sk-env-key")
        config.hint_env_import()
        out = capsys.readouterr().out
        assert "--save-config" in out
        assert "config.json" in out

    def test_hint_env_import_silent_when_config_exists(self, cfg_file, monkeypatch, capsys):
        monkeypatch.setattr(config, "LLM_API_KEY", "sk-env-key")
        _write_cfg(cfg_file, {"model": "m"})
        config.hint_env_import()
        assert capsys.readouterr().out == ""

    def test_hint_env_import_silent_without_key(self, cfg_file, monkeypatch, capsys):
        monkeypatch.setattr(config, "LLM_API_KEY", "")
        config.hint_env_import()
        assert capsys.readouterr().out == ""

    def test_provider_presets_cover_design(self):
        labels = {p["label"] for p in config.PROVIDER_PRESETS.values()}
        assert {"DeepSeek", "OpenAI 兼容", "SiliconFlow", "Ollama", "自定义"} <= labels


# ===========================================================================
# test_llm_connection — 错误分类（mock openai 客户端）
# ===========================================================================
def _status_exc(cls, status: int):
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return cls("mock error", response=resp, body=None)


class _FakeCompletions:
    def __init__(self, result, capture=None):
        self._result = result
        self._capture = capture

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.update(kwargs)
        result = self._result
        if isinstance(result, Exception):
            raise result
        return result


def _patch_client(monkeypatch, result):
    capture = {}
    holder = {}

    def factory(**kwargs):
        holder["kwargs"] = kwargs
        return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(result, capture)))

    monkeypatch.setattr(llm, "OpenAI", factory)
    return capture, holder


class TestLLMConnection:
    CFG = SimpleNamespace(api_key="sk-test", base_url="https://api.example.com", model="test-model")

    def test_success(self, monkeypatch):
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
        capture, holder = _patch_client(monkeypatch, resp)
        r = llm.test_llm_connection(self.CFG)
        assert r.ok is True
        assert r.error_class == ""
        assert "OK" in r.message
        assert r.latency_ms >= 0
        # 最小请求参数
        assert capture["model"] == "test-model"
        assert capture["max_tokens"] == 8
        assert holder["kwargs"]["timeout"] == 8.0
        assert holder["kwargs"]["max_retries"] == 0
        assert holder["kwargs"]["api_key"] == "sk-test"

    def test_empty_reply_is_failure(self, monkeypatch):
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))])
        _patch_client(monkeypatch, resp)
        r = llm.test_llm_connection(self.CFG)
        assert r.ok is False
        assert r.error_class == "other"

    def test_auth_error(self, monkeypatch):
        import openai
        _patch_client(monkeypatch, _status_exc(openai.AuthenticationError, 401))
        r = llm.test_llm_connection(self.CFG)
        assert r.ok is False
        assert r.error_class == "auth"
        assert "401" in r.message or "鉴权" in r.message

    def test_403_counts_as_auth(self, monkeypatch):
        import openai
        _patch_client(monkeypatch, _status_exc(openai.APIStatusError, 403))
        r = llm.test_llm_connection(self.CFG)
        assert r.error_class == "auth"

    def test_connection_error(self, monkeypatch):
        import openai
        req = httpx.Request("POST", "https://api.example.com")
        _patch_client(monkeypatch, openai.APIConnectionError(request=req))
        r = llm.test_llm_connection(self.CFG)
        assert r.ok is False
        assert r.error_class == "network"

    def test_timeout_error(self, monkeypatch):
        import openai
        req = httpx.Request("POST", "https://api.example.com")
        _patch_client(monkeypatch, openai.APITimeoutError(request=req))
        r = llm.test_llm_connection(self.CFG)
        assert r.error_class == "network"
        assert "超时" in r.message

    def test_model_not_found_404(self, monkeypatch):
        import openai
        _patch_client(monkeypatch, _status_exc(openai.NotFoundError, 404))
        r = llm.test_llm_connection(self.CFG)
        assert r.error_class == "model_not_found"

    def test_bad_request_400_counts_as_model(self, monkeypatch):
        import openai
        _patch_client(monkeypatch, _status_exc(openai.APIStatusError, 400))
        r = llm.test_llm_connection(self.CFG)
        assert r.error_class == "model_not_found"

    def test_rate_limit_is_other_with_hint(self, monkeypatch):
        import openai
        _patch_client(monkeypatch, _status_exc(openai.APIStatusError, 429))
        r = llm.test_llm_connection(self.CFG)
        assert r.error_class == "other"
        assert "429" in r.message

    def test_unknown_exception_is_other(self, monkeypatch):
        _patch_client(monkeypatch, RuntimeError("boom"))
        r = llm.test_llm_connection(self.CFG)
        assert r.ok is False
        assert r.error_class == "other"
        assert "RuntimeError" in r.message
