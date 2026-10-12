"""音频生成 — gTTS / Edge TTS、连通预检、provider 回退、失败冷却（原文搬运）。

audio_failures 冷却缓存为本模块的进程内状态（_audio_failures），
落盘文件路径由 config.AUDIO_FAILURES_FILE 决定。
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from app.core import cache, config
from app.core.models import AudioHealth, WordEntry

logger = logging.getLogger(__name__)

try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    print("[WARNING] gTTS 未安装；音频生成将禁用。fix: pip install gTTS")

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    from tqdm.asyncio import tqdm as async_tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False


# ===========================================================================
# 2. 音频生成
# ===========================================================================
# 音频失败缓存：{word_lower: 失败时间戳(epoch秒)}。在冷却期内的词直接跳过，
# 不再反复尝试连接 gTTS。进程启动时惰性加载一次，本轮内更新后回写。
_audio_failures: dict[str, float] | None = None


def load_audio_failures() -> dict[str, float]:
    """惰性加载音频失败缓存。文件不存在或损坏时返回空 dict。"""
    global _audio_failures
    if _audio_failures is None:
        data = cache.load_json(config.AUDIO_FAILURES_FILE)
        try:
            _audio_failures = {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            _audio_failures = {}
    return _audio_failures


def save_audio_failures() -> None:
    """回写音频失败缓存（若已加载）。"""
    if _audio_failures is not None:
        try:
            cache.save_json(config.AUDIO_FAILURES_FILE, _audio_failures)
        except OSError as e:
            logger.warning(f"音频失败缓存写入失败: {e}")


def _audio_recently_failed(word: str) -> bool:
    """判断该词是否在冷却期内已知失败（跳过重试）。"""
    failures = load_audio_failures()
    ts = failures.get(word.lower())
    if ts is None:
        return False
    return (time.time() - ts) < config.AUDIO_FAIL_COOLDOWN


def check_gtts_connectivity() -> bool:
    """
    音频生成前的连通性预检：用一个短词试连 gTTS（带 AUDIO_TIMEOUT 超时）。

    返回 True 表示能连上、可正常生成音频；False 表示连不上。
    预检失败时调用方应给出醒目提示，并跳过逐词生成（避免每个词都卡超时）。
    """
    if not config.AUDIO_ENABLED or not GTTS_AVAILABLE:
        return False

    saved_proxy = {}
    if config.AUDIO_PROXY:
        for var in ("HTTP_PROXY", "HTTPS_PROXY"):
            saved_proxy[var] = os.environ.get(var)
            os.environ[var] = config.AUDIO_PROXY
    try:
        import io
        tts = gTTS(text="test", lang="en", slow=False, timeout=config.AUDIO_TIMEOUT)
        buf = io.BytesIO()
        tts.write_to_fp(buf)  # 实际发起请求，但不落盘
        return buf.tell() > 0
    except Exception as e:
        logger.warning(f"gTTS 连通性预检失败: {e}")
        return False
    finally:
        for var, val in saved_proxy.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


def warn_audio_unreachable() -> None:
    """gTTS 连不上时打印醒目提示，引导用户配置网络后重试。"""
    proxy_hint = (
        f"   当前 AUDIO_PROXY={config.AUDIO_PROXY}\n" if config.AUDIO_PROXY
        else "   提示: 可在 .env 设置 AUDIO_PROXY=http://127.0.0.1:端口 走代理\n"
    )
    banner = (
        "\n" + "!" * 60 + "\n"
        "[音频警告] 无法连接 Google TTS (gTTS)，本轮将跳过音频生成。\n"
        "   卡片仍会正常生成，但不含发音。\n"
        "   gTTS 依赖 translate.google.com，国内网络通常需要代理。\n"
        + proxy_hint +
        "   配置好网络/代理后，重新运行即可自动补全音频（已生成的卡片不受影响）。\n"
        + "!" * 60
    )
    logger.warning("无法连接 gTTS，已跳过音频生成（详见控制台提示）")
    try:
        print(banner)
    except UnicodeEncodeError:
        print(banner.encode("ascii", errors="replace").decode("ascii"))


def get_tts_provider_order(provider: str = config.TTS_PROVIDER, fallbacks: str = config.TTS_FALLBACKS) -> list[str]:
    """解析 TTS provider 顺序。auto 使用 TTS_FALLBACKS。"""
    provider = (provider or "auto").strip().lower()
    if provider in ("none", "no-audio", "off"):
        return ["none"]
    if provider != "auto":
        return [provider]

    order = [p.strip().lower() for p in fallbacks.split(",") if p.strip()]
    return order or ["gtts", "edge"]


def select_audio_provider(provider: str = config.TTS_PROVIDER) -> AudioHealth:
    """选择本轮使用的 TTS provider。默认 gTTS，gTTS 不可达时尝试 Edge TTS。"""
    if not config.AUDIO_ENABLED:
        return AudioHealth(False, "none", "audio disabled")

    last_error = ""
    for name in get_tts_provider_order(provider):
        if name == "none":
            return AudioHealth(False, "none", "audio disabled")
        if name == "gtts":
            if not GTTS_AVAILABLE:
                last_error = "gTTS not installed"
                logger.warning("gTTS 未安装，尝试下一个 TTS provider")
                continue
            if check_gtts_connectivity():
                return AudioHealth(True, "gtts")
            last_error = "gTTS connectivity failed"
            logger.warning("gTTS 不可达，尝试下一个 TTS provider")
            continue
        if name == "edge":
            if EDGE_TTS_AVAILABLE:
                return AudioHealth(True, "edge")
            last_error = "edge-tts not installed"
            logger.warning("edge-tts 未安装，无法使用 Edge TTS；可运行 pip install edge-tts")
            continue
        last_error = f"unknown TTS provider: {name}"
        logger.warning(f"未知 TTS provider: {name}")

    return AudioHealth(False, "none", last_error or "no available TTS provider")


_active_tts_provider = "gtts"


def sanitize_filename(word: str) -> str:
    """
    将单词转为安全的文件名。
    规则：小写，非字母数字字符替换为下划线，限制长度。
    关键假设：Anki 支持 mp3 文件名中的下划线和字母数字。
    """
    clean = re.sub(r'[^a-z0-9]', '_', word.lower())
    clean = re.sub(r'_+', '_', clean).strip('_')
    # 限制最大长度，避免文件名过长
    return clean[:64]


def _generate_gtts_audio(word: str, filepath: Path) -> None:
    """使用 gTTS 写入音频文件。"""
    saved_proxy = {}
    if config.AUDIO_PROXY:
        for var in ("HTTP_PROXY", "HTTPS_PROXY"):
            saved_proxy[var] = os.environ.get(var)
            os.environ[var] = config.AUDIO_PROXY
    try:
        tts = gTTS(text=word, lang="en", slow=False, timeout=config.AUDIO_TIMEOUT)
        tts.save(str(filepath))
    finally:
        for var, val in saved_proxy.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


async def _generate_edge_audio_async(word: str, filepath: Path) -> None:
    """使用 edge-tts 写入音频文件。"""
    communicate = edge_tts.Communicate(word, config.TTS_VOICE)
    await communicate.save(str(filepath))


def _generate_edge_audio(word: str, filepath: Path) -> None:
    asyncio.run(_generate_edge_audio_async(word, filepath))


def generate_audio_sync(
    word: str,
    output_dir: str = "media",
    provider: Optional[str] = None,
    force: bool = False,
) -> str:

    """
    同步生成单个单词的发音 mp3。

    使用 gTTS (Google Text-to-Speech)，语言设为 en。
    - 已存在的非空文件直接跳过（幂等）
    - 冷却期内已知失败的词直接跳过，不再卡连接超时
    - 支持 AUDIO_PROXY 走代理，AUDIO_TIMEOUT 控制单次请求超时
    成功会从失败缓存中移除该词；失败则记录时间戳但不创建 0 字节伪成功文件。

    返回值: 文件名（不含路径），如 'hello.mp3'
    """
    filename = sanitize_filename(word) + ".mp3"
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filepath = out_dir / filename

    # 已存在的有效音频直接跳过（幂等）
    if filepath.exists() and filepath.stat().st_size > 0 and not force:
        return filename
    if force and filepath.exists():
        try:
            filepath.unlink()
        except OSError:
            pass

    # 空占位文件说明上次失败了——若网络已恢复，应无视冷却期重试
    placeholder_exists = filepath.exists() and filepath.stat().st_size == 0

    provider = (provider or _active_tts_provider or "gtts").lower()
    if not config.AUDIO_ENABLED or provider == "none":
        logger.warning(f"音频生成被禁用，跳过生成: {filename}")
        return filename
    if provider == "gtts" and not GTTS_AVAILABLE:
        logger.warning(f"gTTS 不可用，跳过生成: {filename}")
        return filename
    if provider == "edge" and not EDGE_TTS_AVAILABLE:
        logger.warning(f"edge-tts 不可用，跳过生成: {filename}")
        return filename

    failure_key = f"{provider}:{word.lower()}"
    # 兼容旧版 word-only 冷却记录；新记录按 provider 隔离，避免 gTTS 失败阻止 Edge 重试。
    if _audio_recently_failed(failure_key) or (provider == "gtts" and _audio_recently_failed(word)):
        if not placeholder_exists:
            return filename
        failures = load_audio_failures()
        failures.pop(failure_key.lower(), None)
        failures.pop(word.lower(), None)
        try:
            filepath.unlink()
        except OSError:
            pass

    try:
        if provider == "edge":
            _generate_edge_audio(word, filepath)
        else:
            _generate_gtts_audio(word, filepath)
        if not cache.audio_file_is_valid(filename, out_dir):
            raise OSError("provider returned an empty or missing audio file")
        logger.info(f"音频已生成: {filename} ({provider})")
        failures = load_audio_failures()
        failures.pop(failure_key.lower(), None)
        failures.pop(word.lower(), None)
        return filename
    except Exception as e:
        logger.warning(f"{provider} 生成失败 '{word}': {e}，记录失败")
        load_audio_failures()[failure_key.lower()] = time.time()
        if provider == "gtts":
            load_audio_failures()[word.lower()] = time.time()
        if filepath.exists() and filepath.stat().st_size == 0:
            try:
                filepath.unlink()
            except OSError:
                pass
        return filename


async def generate_audio(
    word: str,
    output_dir: str = "media",
    provider: Optional[str] = None,
    force: bool = False,
) -> str:
    """
    异步包装 TTS 生成调用。
    使用 asyncio.to_thread 避免阻塞事件循环。
    """
    return await asyncio.to_thread(generate_audio_sync, word, output_dir, provider, force)


async def batch_generate_audio(
    entries: list[WordEntry],
    max_concurrency: int = 4,
    provider: Optional[str] = None,
    force: bool = False,
) -> dict[str, str]:
    """
    并发生成所有单词的音频。

    返回值: {word_lower: audio_filename}
    """
    sem = asyncio.Semaphore(max_concurrency)
    results: dict[str, str] = {}

    async def _gen_one(entry: WordEntry):
        async with sem:
            fname = await generate_audio(entry.word, str(config.MEDIA_DIR), provider, force)
            results[entry.word.lower()] = fname

    # 创建异步任务列表
    tasks = [_gen_one(e) for e in entries]

    if TQDM_AVAILABLE:
        for task in async_tqdm.as_completed(tasks, desc="生成音频", total=len(tasks)):
            await task
    else:
        await asyncio.gather(*tasks)

    return results
