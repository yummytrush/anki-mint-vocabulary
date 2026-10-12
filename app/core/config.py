"""配置与运行环境 — 全部 .env / 环境变量常量、目录与日志初始化。

自 vocab_anki_generator.py 原文搬运（Task 1 拆包），行为不变。
其它模块通过 `from app.core import config` 后以 `config.X` 读取可被
CLI / GUI 在运行期覆盖的开关（如 QUALITY_CHECKS_ENABLED、AUDIO_ENABLED），
保证 main() 的全局覆盖语义与单文件版一致。
"""

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 第三方库导入 — 初学者的依赖检查
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARNING] python-dotenv 未安装；跳过 .env 加载。fix: pip install python-dotenv")


# ===========================================================================
# 运行目录与日志配置
# ===========================================================================
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(DATA_DIR / "cache")))
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", str(DATA_DIR / "media")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DATA_DIR / "output")))
LOG_DIR = Path(os.getenv("LOG_DIR", str(DATA_DIR / "logs")))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DATA_DIR / "backups")))
LEGACY_MEDIA_DIR = Path("media")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "generator.log"
# 强制 UTF-8 输出，避免 Windows 中文终端 GBK 编码错误
_stream_handler = logging.StreamHandler(sys.stdout)
try:
    # Python 3.7+ 支持 encoding 参数强制输出编码
    _stream_handler = logging.StreamHandler(
        open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace',
             closefd=False)
    )
except Exception:
    pass  # 回退到默认 handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        _stream_handler,
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ===========================================================================
# 配置
# ===========================================================================
# --- LLM API ---
# 关键假设：API 兼容 OpenAI SDK 的 chat/completions 接口
# 默认使用 DeepSeek；可改为 OpenAI / Azure / vLLM / Ollama 等任意兼容端点
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

# 富化缓存版本：prompt/schema/model/sentence 任一变化都应避免复用旧低质量结果。
ENRICHMENT_SCHEMA_VERSION = "2"
ENRICHMENT_PROMPT_VERSION = "2026-07-quality-v1"

# --- 并发控制 ---
# 默认值针对 DeepSeek 实测调优：并发是缩短总耗时的最大杠杆（单次调用 6-15s）；
# 速率上限设为 60 以避免触发不必要的本地限流等待（DeepSeek 实际配额远高于此）。
MAX_CONCURRENCY: int = int(os.getenv("MAX_CONCURRENCY", "8"))
RATE_LIMIT_PER_MIN: int = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))

# --- 行为开关 ---
REPLACE_ALL: bool = os.getenv("REPLACE_ALL", "false").lower() == "true"
AUDIO_ENABLED: bool = os.getenv("AUDIO_ENABLED", "true").lower() == "true"
IMPORTANCE_MIN_LEVEL: str = os.getenv("IMPORTANCE_MIN_LEVEL", "低")

# --- 富化质量检查 ---
# 每轮富化后用两个独立 LLM checker role 复核：accuracy / completeness。
QUALITY_CHECKS_ENABLED: bool = os.getenv("QUALITY_CHECKS_ENABLED", "true").lower() == "true"
QUALITY_MAX_ATTEMPTS: int = int(os.getenv("QUALITY_MAX_ATTEMPTS", "2"))
QUALITY_SCHEMA_VERSION = "2026-08-quality-agents-v1"

# --- 音频生成（gTTS）---
# gTTS 依赖 translate.google.com，国内网络常需代理。若设置 AUDIO_PROXY，
# 会在调用 gTTS 前注入 HTTP(S)_PROXY 环境变量（gTTS 底层 requests 会读取）。
AUDIO_PROXY: str = os.getenv("AUDIO_PROXY", "").strip()
# 单次 gTTS 请求超时（秒）。原先无显式超时，连不上时靠库默认超时，失败检测慢。
AUDIO_TIMEOUT: int = int(os.getenv("AUDIO_TIMEOUT", "8"))
# 音频失败短期内不重试的冷却时间（秒，默认 24h）。失败的词记入 audio_failures.json，
# 冷却期内直接跳过，避免每次重跑都对连不上的词反复卡超时。
AUDIO_FAIL_COOLDOWN: int = int(os.getenv("AUDIO_FAIL_COOLDOWN", str(24 * 3600)))
TTS_PROVIDER: str = os.getenv("TTS_PROVIDER", "auto").strip().lower()
TTS_FALLBACKS: str = os.getenv("TTS_FALLBACKS", "gtts,edge").strip().lower()
TTS_VOICE: str = os.getenv("TTS_VOICE", "en-GB-SoniaNeural").strip()

# --- 批量富化 ---
# 批量大小：每批处理的单词数。配合 max_tokens=8192，5 词富化输出可安全容纳，
# 不易触发 JSON 截断；批次数较 3 显著减少，配合并发可大幅缩短总耗时。
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))

# 单批输出 token 上限。deepseek-chat 支持 8192；过小会导致富词批次 JSON 被截断。
BATCH_MAX_TOKENS = int(os.getenv("BATCH_MAX_TOKENS", "8192"))

# --- 文件路径 ---
CACHE_FILE = str(CACHE_DIR / "enrichment_cache.json")
PROCESSED_FILE = str(CACHE_DIR / "processed.json")
AUDIO_FAILURES_FILE = str(CACHE_DIR / "audio_failures.json")
LEGACY_CACHE_FILE = "enrichment_cache.json"
LEGACY_PROCESSED_FILE = "processed.json"
LEGACY_AUDIO_FAILURES_FILE = "audio_failures.json"

# GUI/CLI 共用配置文件（Task 3）。data/ 整体 gitignore，api_key 只存在本机。
CONFIG_FILE = str(DATA_DIR / "config.json")

# --- 供应商预设（选中预设自动填充 Base URL 与推荐模型名，均可手改） ---
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "openai_compatible": {"label": "OpenAI 兼容", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "siliconflow": {"label": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "ollama": {"label": "Ollama", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    "custom": {"label": "自定义", "base_url": "", "model": ""},
}


# ===========================================================================
# 应用配置（GUI 与 CLI 共用） — 优先级：CLI 参数 > config.json > env/.env > 代码默认
# ===========================================================================
# 说明：env/.env 的值在模块导入时已读入下方常量，AppConfig 的字段默认值即取自
# 这些常量（天然满足 env > 代码默认）；load() 再用 config.json 覆盖；CLI 用
# resolve_config(overrides) 最后覆盖。
# 注意：DATA_DIR/CACHE_DIR 等目录覆盖仍是环境变量专属（导入期解析），不进 AppConfig。


@dataclass
class AppConfig:
    """应用配置快照。api_key 保存于本机 data/config.json（已 gitignore），永不入库。"""

    provider: str = "deepseek"
    base_url: str = LLM_BASE_URL
    api_key: str = LLM_API_KEY
    model: str = LLM_MODEL

    max_concurrency: int = MAX_CONCURRENCY
    rate_limit_per_min: int = RATE_LIMIT_PER_MIN
    batch_size: int = BATCH_SIZE
    batch_max_tokens: int = BATCH_MAX_TOKENS

    replace_all: bool = REPLACE_ALL
    audio_enabled: bool = AUDIO_ENABLED
    importance_min_level: str = IMPORTANCE_MIN_LEVEL

    quality_checks_enabled: bool = QUALITY_CHECKS_ENABLED
    quality_max_attempts: int = QUALITY_MAX_ATTEMPTS

    audio_proxy: str = AUDIO_PROXY
    audio_timeout: int = AUDIO_TIMEOUT
    audio_fail_cooldown: int = AUDIO_FAIL_COOLDOWN
    tts_provider: str = TTS_PROVIDER
    tts_fallbacks: str = TTS_FALLBACKS
    tts_voice: str = TTS_VOICE

    # ---- 读 ----
    @classmethod
    def load(cls) -> "AppConfig":
        """加载配置：config.json 覆盖 env/.env 派生的默认值。文件缺失/损坏回退默认。"""
        cfg = cls()
        if not os.path.exists(CONFIG_FILE):
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"加载配置文件失败 {CONFIG_FILE}: {e}，使用默认配置")
            return cfg
        if not isinstance(data, dict):
            logger.warning(f"配置文件格式异常 {CONFIG_FILE}（应为 JSON 对象），使用默认配置")
            return cfg
        known_types = {fld.name: fld.type for fld in fields(cls)}
        for key, value in data.items():
            if key not in known_types or value is None:
                continue
            coerced = _coerce_value(known_types[key], value)
            if coerced is None:
                logger.warning(f"配置项 {key}={value!r} 类型不合法，已忽略")
                continue
            setattr(cfg, key, coerced)
        return cfg

    # ---- 写 ----
    def save(self) -> None:
        """写入 data/config.json（原子写，复用 cache.save_json）。"""
        from app.core import cache  # 延迟导入避免 config <-> cache 循环

        cache.save_json(CONFIG_FILE, asdict(self))
        logger.info(f"配置已保存至: {CONFIG_FILE}")

    # ---- 回显 ----
    def masked(self) -> "AppConfig":
        """返回 api_key 打码后的副本，供界面回显（不落盘）。"""
        data = asdict(self)
        data["api_key"] = _mask_key(data["api_key"])
        return AppConfig(**data)

    # ---- 应用 ----
    def apply_to_config(self) -> None:
        """把当前值写入 config 模块全局（延续原 main() 的全局覆盖语义）。"""
        global LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
        LLM_API_KEY = self.api_key
        LLM_BASE_URL = self.base_url
        LLM_MODEL = self.model
        global MAX_CONCURRENCY, RATE_LIMIT_PER_MIN, BATCH_SIZE, BATCH_MAX_TOKENS
        MAX_CONCURRENCY = self.max_concurrency
        RATE_LIMIT_PER_MIN = self.rate_limit_per_min
        BATCH_SIZE = self.batch_size
        BATCH_MAX_TOKENS = self.batch_max_tokens
        global REPLACE_ALL, AUDIO_ENABLED, IMPORTANCE_MIN_LEVEL
        REPLACE_ALL = self.replace_all
        AUDIO_ENABLED = self.audio_enabled
        IMPORTANCE_MIN_LEVEL = self.importance_min_level
        global QUALITY_CHECKS_ENABLED, QUALITY_MAX_ATTEMPTS
        QUALITY_CHECKS_ENABLED = self.quality_checks_enabled
        QUALITY_MAX_ATTEMPTS = self.quality_max_attempts
        global AUDIO_PROXY, AUDIO_TIMEOUT, AUDIO_FAIL_COOLDOWN
        AUDIO_PROXY = self.audio_proxy
        AUDIO_TIMEOUT = self.audio_timeout
        AUDIO_FAIL_COOLDOWN = self.audio_fail_cooldown
        global TTS_PROVIDER, TTS_FALLBACKS, TTS_VOICE
        TTS_PROVIDER = self.tts_provider
        TTS_FALLBACKS = self.tts_fallbacks
        TTS_VOICE = self.tts_voice


def _mask_key(key: str) -> str:
    """API Key 打码：保留首尾各 4 字符，中间 ****；空/过短整体掩码。"""
    key = str(key or "")
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _coerce_value(annotation, value):
    """按字段类型宽松转换 JSON 值；无法转换返回 None（调用方忽略）。"""
    name = getattr(annotation, "__name__", str(annotation))
    try:
        if name == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() == "true"
            return bool(value)
        if name == "int":
            return int(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def resolve_config(cli_overrides: Optional[dict] = None) -> AppConfig:
    """优先级合并：CLI 参数 > data/config.json > 环境变量/.env > 代码默认值。

    cli_overrides 只包含 CLI 中**显式给出**的参数（argparse default=None 区分），
    值为 None 的项跳过，避免未传参数反向覆盖 config.json。
    """
    cfg = AppConfig.load()
    for key, value in (cli_overrides or {}).items():
        if value is None:
            continue
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def hint_env_import() -> None:
    """首次运行且未建 config.json 时，提示把 .env 里的 Key 一键导入 config.json。"""
    if os.path.exists(CONFIG_FILE):
        return
    if not LLM_API_KEY:
        return
    try:
        print(
            "\n[i] 检测到 .env 中已配置 API Key。\n"
            "    可执行以下命令将其导入 GUI 使用的配置文件（data/config.json）：\n"
            "      python vocab_anki_generator.py --save-config\n"
            "    之后 .env 与 config.json 并存，均会生效（config.json 优先）。\n"
        )
    except UnicodeEncodeError:
        pass
