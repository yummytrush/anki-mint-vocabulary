"""缓存与增量更新 — enrichment_cache / processed.json / 键迁移 / 句子哈希。

同时承载媒体文件校验辅助（audio_file_is_valid 等），供音频与打包模块共用。
（原文搬运；可覆盖开关一律经 `config.X` 读取）
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from app.core import config
from app.core.models import WordEntry

logger = logging.getLogger(__name__)


# ===========================================================================
# 4. 缓存管理与增量更新
# ===========================================================================
def compute_sentence_hash(sentence: str) -> str:
    """计算例句的短哈希值，用于变更检测。"""
    return hashlib.md5(sentence.encode("utf-8")).hexdigest()[:8]


def _cache_part(value: str) -> str:
    """将 cache key 片段规整为稳定、可读的低风险字符串。"""
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value).strip().lower())
    return clean.strip("-") or "unknown"


def build_enrichment_cache_key(
    entry: WordEntry,
    model: str = config.LLM_MODEL,
    prompt_version: str = config.ENRICHMENT_PROMPT_VERSION,
    schema_version: str = config.ENRICHMENT_SCHEMA_VERSION,
) -> str:
    """构造上下文相关的富化缓存 key，避免同词不同句或 prompt 升级互相污染。"""
    return ":".join([
        f"v{_cache_part(schema_version)}",
        _cache_part(model),
        _cache_part(prompt_version),
        _cache_part(entry.word),
        compute_sentence_hash(entry.sentence),
    ])


def get_cached_enrichment(entry: WordEntry, enrichment_cache: dict) -> tuple[Optional[dict], str, bool]:
    """
    读取富化缓存。

    返回: (cache_value, cache_key, is_legacy_key)。优先使用 v2 上下文 key；
    兼容旧版 word.lower() key，但旧值仍需调用方按句子 hash 校验。
    """
    cache_key = build_enrichment_cache_key(entry)
    cached = enrichment_cache.get(cache_key)
    if isinstance(cached, dict):
        return cached, cache_key, False

    legacy_key = entry.word.lower()
    cached = enrichment_cache.get(legacy_key)
    if isinstance(cached, dict):
        return cached, legacy_key, True
    return None, cache_key, False


def audio_file_is_valid(filename: str, media_dir: Path = config.MEDIA_DIR) -> bool:
    """判断 Anki 音频文件是否真实存在且非空。"""
    if not filename:
        return False
    candidates = [media_dir / filename]
    if media_dir == config.MEDIA_DIR and config.LEGACY_MEDIA_DIR != config.MEDIA_DIR:
        candidates.append(config.LEGACY_MEDIA_DIR / filename)
    return any(path.exists() and path.is_file() and path.stat().st_size > 0 for path in candidates)


def resolve_audio_file(filename: str, media_dir: Path = config.MEDIA_DIR) -> Optional[Path]:
    """返回实际可用音频路径，优先新 media 目录，兼容旧 root/media。"""
    if not filename:
        return None
    candidates = [media_dir / filename]
    if media_dir == config.MEDIA_DIR and config.LEGACY_MEDIA_DIR != config.MEDIA_DIR:
        candidates.append(config.LEGACY_MEDIA_DIR / filename)
    for path in candidates:
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def extract_sound_filename(audio_ref: str) -> str:
    """从 [sound:file.mp3] 中取出文件名；格式不符时返回空字符串。"""
    if not isinstance(audio_ref, str):
        return ""
    match = re.fullmatch(r"\[sound:([^\]]+)\]", audio_ref.strip())
    return match.group(1) if match else ""


def processed_record_complete(record: dict, sentence_hash: str) -> bool:
    """判断 processed 记录是否代表可直接跳过的完整卡片。"""
    if not isinstance(record, dict) or record.get("sentence_hash") != sentence_hash:
        return False

    # 旧 processed.json 没有状态字段；开启质量检查后必须重查，避免旧错漏永久跳过。
    if "enrichment_status" not in record and "audio_status" not in record:
        return not config.QUALITY_CHECKS_ENABLED

    enrichment_ok = record.get("enrichment_status") == "ok"
    audio_status = record.get("audio_status")
    audio_ok = audio_status in ("ok", "disabled")
    quality_ok = True
    if config.QUALITY_CHECKS_ENABLED:
        quality_ok = (
            record.get("quality_status") == "ok"
            and record.get("quality_schema_version") == config.QUALITY_SCHEMA_VERSION
        )
    return enrichment_ok and audio_ok and quality_ok


def _legacy_path_for(file_path: str) -> Optional[str]:
    path = Path(file_path)
    legacy_map = {
        Path(config.CACHE_FILE): config.LEGACY_CACHE_FILE,
        Path(config.PROCESSED_FILE): config.LEGACY_PROCESSED_FILE,
        Path(config.AUDIO_FAILURES_FILE): config.LEGACY_AUDIO_FAILURES_FILE,
    }
    legacy = legacy_map.get(path)
    if legacy and Path(legacy) != path and Path(legacy).exists():
        return legacy
    return None


def load_json(file_path: str) -> dict:
    """安全加载 JSON 文件；新 data/cache 不存在时兼容读取根目录旧文件。"""
    source_path = file_path
    if not os.path.exists(source_path):
        legacy = _legacy_path_for(file_path)
        if legacy:
            logger.info(f"使用旧位置 JSON: {legacy}；后续会写入新位置 {file_path}")
            source_path = legacy
        else:
            return {}
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"加载 JSON 文件失败 {source_path}: {e}，重置为空")
        return {}


def save_json(file_path: str, data: dict):
    """原子写入 JSON 文件 — 先写临时文件再重命名。"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_enrichment_cache(cache_path: str) -> dict:
    """加载富化缓存。键为单词小写形式。"""
    return load_json(cache_path)


def save_enrichment_cache(cache_path: str, cache: dict):
    """保存富化缓存。"""
    save_json(cache_path, cache)


def load_processed(processed_path: str) -> dict:
    """
    加载已处理单词记录。
    键为 str(index)，值为 ProcessedRecord-as-dict。
    """
    return load_json(processed_path)


def save_processed(processed_path: str, data: dict):
    """保存已处理单词记录。"""
    save_json(processed_path, data)


def migrate_processed_keys(processed: dict) -> dict:
    """
    将旧版 processed.json（以序号字符串为键）迁移为以 word.lower() 为键。

    旧格式: {"1": {"word": "Assessment", "sentence_hash": ...}, ...}
    新格式: {"assessment": {"word": "Assessment", "index": 1, "sentence_hash": ...}, ...}

    判定依据：若存在任何纯数字键，视为旧格式并迁移。已是新格式则原样返回。
    幂等：对新格式数据重复调用不会破坏。
    """
    if not processed:
        return processed
    # 检测是否含旧式纯数字键
    has_numeric_key = any(str(k).isdigit() for k in processed)
    if not has_numeric_key:
        return processed

    migrated: dict = {}
    for key, record in processed.items():
        if not isinstance(record, dict):
            continue
        word = record.get("word", "")
        if not word:
            continue
        new_key = word.lower()
        # 保留原序号信息（若键本身是数字）
        if str(key).isdigit() and "index" not in record:
            record = {**record, "index": int(key)}
        migrated[new_key] = record
    logger.info(f"已迁移 processed.json: {len(processed)} 条旧记录 → {len(migrated)} 条新键")
    return migrated


def filter_new_entries(
    entries: list[WordEntry],
    enrichment_cache: dict,
    processed: dict,
    force: bool = False,
) -> list[WordEntry]:
    """
    增量过滤：移除已处理且例句未变的单词。

    逻辑:
      1. 计算每个条目的例句 hash
      2. 查 processed.json（以 word.lower() 为键）: 若命中且 hash 匹配 → 跳过（除非 --force）
      3. 返回需处理的条目列表

    增量键使用 word.lower() 而非序号 —— 这样修改 Markdown 行序或增删词
    不会导致后续所有词被误判为"新词"全量重跑。例句变更仍通过 sentence_hash 检测。
    """
    new_entries: list[WordEntry] = []
    skipped_count = 0

    for entry in entries:
        sent_hash = compute_sentence_hash(entry.sentence)
        word_key = entry.word.lower()

        if not force and word_key in processed:
            existing = processed[word_key]
            if processed_record_complete(existing, sent_hash):
                skipped_count += 1
                logger.debug(f"跳过已处理: [{entry.index}] {entry.word}")
                continue

        new_entries.append(entry)

    logger.info(
        f"增量过滤: {len(entries)} 总 → {len(new_entries)} 需处理 → "
        f"{skipped_count} 已跳过"
    )
    return new_entries
