"""LLM 批量富化 — 批量 prompt 构造、process_batch（原文搬运）。

核心优化：每批 5 个词合并一次 API 调用，37 词只需 5-6 次请求，
从 60-180s 降至 15-40s，且大幅降低触发频率限制的概率。
富化提示词外置于 prompts/enrich.md（默认副本内置在 app/core/prompts.py）。
"""

import asyncio
import logging

from app.core import config, llm, prompts, quality
from app.core.models import Importance, WordEntry

logger = logging.getLogger(__name__)


# ===========================================================================
# 3a. 批量富化生成（多个单词合并为一次 API 调用）
# ===========================================================================
def build_batch_prompt(batch: list[tuple["WordEntry", str]]) -> str:
    """构造批量单词列表文本。每个单词带序号、词条、例句、音频文件名。"""
    lines = []
    for entry, audio_filename in batch:
        lines.append(
            f"- [{entry.index}] {entry.word}\n"
            f"  例句: {entry.sentence}\n"
            f"  音频: [sound:{audio_filename}]"
        )
    return prompts.get_prompt("enrich").replace("{word_list}", "\n".join(lines))


async def process_batch(
    batch: list[tuple["WordEntry", str]],
    sem: asyncio.Semaphore,
) -> tuple[dict[str, dict], list[tuple[str, str]], list[tuple[str, str]]]:
    """
    一次 API 调用处理一批单词。

    返回: (enriched, failures, spelling_warnings)
      enriched:  {word_lower: enrichment_dict} — 成功解析的词
      failures:  [(word, reason)] — 失败或被跳过的词及原因，供末尾汇总报告
      spelling_warnings: [(word, note)] — 拼写疑问，供末尾汇总报告
    """
    word_keys = [entry.word.lower() for entry, _ in batch]
    prompt = build_batch_prompt(batch)

    messages = [
        {"role": "system", "content": "你是一名英语词汇学专家。请只输出 JSON，不要有其他内容。"},
        {"role": "user", "content": prompt},
    ]

    result = await llm.llm_chat(messages, temperature=0.5, max_tokens=config.BATCH_MAX_TOKENS, sem=sem)

    failures: list[tuple[str, str]] = []
    spelling_warnings: list[tuple[str, str]] = []

    if result is None:
        logger.error(f"批量富化失败: 共 {len(batch)} 词，全部跳过（API 调用或 JSON 解析失败）")
        for entry, _ in batch:
            failures.append((entry.word, "整批 API/JSON 失败"))
        return {}, failures, spelling_warnings

    # 解析子词结果
    enriched: dict[str, dict] = {}
    for entry, audio_filename in batch:
        key = entry.word.lower()
        word_data = result.get(key)
        if not isinstance(word_data, dict):
            # 尝试首字母大写 / 原形
            for alt in (entry.word, entry.word.capitalize(), entry.word.upper()):
                word_data = result.get(alt)
                if isinstance(word_data, dict):
                    break
        if not isinstance(word_data, dict):
            # LLM 可能用还原后的词形作键，遍历匹配 inflected_form
            for _rk, _rv in result.items():
                if isinstance(_rv, dict):
                    if _rv.get("inflected_form", "").lower() == key:
                        word_data = _rv
                        break
        if not isinstance(word_data, dict):
            logger.warning(f"批量结果中未找到 '{entry.word}' 的数据")
            failures.append((entry.word, "批量结果中未找到该词的键"))
            continue

        word_data.setdefault("word", entry.word)
        importance_str = word_data.get("importance", "中")
        importance = Importance.from_str(importance_str)

        if not importance.meets_minimum(config.IMPORTANCE_MIN_LEVEL):
            logger.info(f"跳过 '{entry.word}'：重要性 {importance.value} 低于阈值")
            failures.append((entry.word, f"重要性 {importance.value} 低于阈值 {config.IMPORTANCE_MIN_LEVEL}"))
            continue

        # 标准修复 + accuracy/completeness checker agents 复核
        word_data = quality.validate_and_fix_enrichment(
            word_data, entry.word, importance, audio_filename
        )
        if quality.has_placeholder_or_low_quality_content(word_data):
            logger.warning(f"单词 '{entry.word}' 富化结果质量不足，未写入成功结果")
            failures.append((entry.word, "富化结果缺少有效核心释义"))
            continue

        checked_data, quality_reason, _quality_attempts, _quality_report = await quality.ensure_quality_for_entry(
            entry, word_data, audio_filename, sem
        )
        if checked_data is None:
            logger.warning(f"单词 '{entry.word}' 质量检查未通过: {quality_reason}")
            failures.append((entry.word, f"质量检查未通过: {quality_reason}"))
            continue
        word_data = checked_data
        enriched[key] = word_data

        # 检查拼写提示
        spelling_note = word_data.get("_spelling_note", "")
        if spelling_note:
            logger.warning(f"⚠ 拼写疑问 [{entry.word}]: {spelling_note}")
            spelling_warnings.append((entry.word, spelling_note))

        logger.info(f"富化完成: {entry.word} → {importance.value}；质量检查={word_data.get('_quality_status', 'unknown')}")

    return enriched, failures, spelling_warnings
