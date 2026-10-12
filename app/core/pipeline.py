"""编排层 — 解析 → 音频 → 富化 → 质检 → 打包 的完整流程。

原 vocab_anki_generator.py 的 main_async() 流程封装为 `run(config, callbacks)`：
GUI 与 CLI 共用同一入口。回调接口 on_progress / on_stage_changed / on_log /
on_finished；批次边界检查 `callbacks.cancel_requested`（取消后保留缓存可续跑）。
（原文搬运 + 回调埋点；CLI 不传取消回调时行为与单文件版完全一致）
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.core import anki_packager, audio, cache, config, enricher, llm, parser, prompts, quality
from app.core.models import WordEntry

logger = logging.getLogger(__name__)

try:
    from tqdm.asyncio import tqdm as async_tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    print("[WARNING] tqdm 未安装；进度条将降级为简单输出。fix: pip install tqdm")


# ===========================================================================
# 运行配置与回调
# ===========================================================================
@dataclass
class RunConfig:
    """一次生成运行的参数（与原 CLI 参数一一对应）。

    注意：AUDIO_ENABLED / QUALITY_CHECKS_ENABLED / QUALITY_MAX_ATTEMPTS 等
    全局开关仍从 `app.core.config` 读取（由 CLI/GUI 在 run 前设置，与原
    main() 的全局覆盖语义一致）。
    """
    input: str = "words.md"
    output: str = str(config.OUTPUT_DIR / "output.apkg")
    cache: str = config.CACHE_FILE
    force: bool = False
    max_concurrency: int = config.MAX_CONCURRENCY
    rate_limit: int = config.RATE_LIMIT_PER_MIN
    limit: int = 0
    no_audio: bool = False
    tts_provider: str = config.TTS_PROVIDER
    refresh_audio: bool = False
    no_quality_checks: bool = False
    quality_max_attempts: int = config.QUALITY_MAX_ATTEMPTS
    word_list: bool = False
    deck_name: str = ""


def _noop(*_args, **_kwargs) -> None:
    pass


@dataclass
class PipelineCallbacks:
    """pipeline 对外回调接口。

    cancel_requested 为 None 时不支持取消（CLI 默认，保持 tqdm 并发路径）；
    GUI 传入返回 bool 的可调用对象后，批处理退化为顺序执行并在批次边界检查。
    """
    on_progress: Callable = _noop            # (done, total, word)
    on_stage_changed: Callable = _noop       # (stage,)  parse/audio/enrich/package/sentence_gen
    on_log: Callable = _noop                 # (line,)
    on_finished: Callable = _noop            # (summary,)
    on_failures: Callable = _noop            # ([(word, reason), ...]，汇总时回传结构化失败清单
    cancel_requested: Optional[Callable[[], bool]] = None


class _ForwardLogHandler(logging.Handler):
    """把 app.* 日志转发给 on_log 回调（GUI 日志面板用）。"""

    def __init__(self, cb: PipelineCallbacks):
        super().__init__()
        self._cb = cb
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._cb.on_log(self.format(record))
        except Exception:
            self.handleError(record)


def run(args: RunConfig, callbacks: Optional[PipelineCallbacks] = None) -> Optional[str]:
    """
    同步入口：执行完整生成流程，返回结束汇总文本（无条目时返回 None）。

    异常不在此捕获 — 由 CLI / GUI 负责 KeyboardInterrupt / 异常处理。
    """
    cb = callbacks if callbacks is not None else PipelineCallbacks()
    handler = _ForwardLogHandler(cb)
    app_logger = logging.getLogger("app")
    app_logger.addHandler(handler)
    try:
        return asyncio.run(_run_async(args, cb))
    finally:
        app_logger.removeHandler(handler)


def _cancelled(cb: PipelineCallbacks) -> bool:
    return bool(cb.cancel_requested and cb.cancel_requested())


# ===========================================================================
# 纯单词模式与 AI 格式规范化（Task 4）
# ===========================================================================
def _intermediate_dir() -> Path:
    d = config.DATA_DIR / "intermediate"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def generate_word_list_sentences(
    run_args: RunConfig,
    words: list[WordEntry],
    cb: Optional[PipelineCallbacks] = None,
) -> tuple[str, list[str]]:
    """
    纯单词模式第一步：用 prompts/sentence_gen.md 为每词造雅思语境例句，
    生成**标准两行式中间文件**存 data/intermediate/<输入名>-sentences.md。

    words: parse_preview 解析纯单词列表得到的条目（sentence 字段为可选中文提示）。
    返回: (中间文件路径, 失败词列表)。失败词跳过、不阻断整体；
    中间文件可手动修改后重新运行（无需 --word-list）。
    """
    cb = cb or PipelineCallbacks()
    cb.on_stage_changed("sentence_gen")
    successful: list[tuple[str, str]] = []
    failures: list[str] = []

    batch_size = max(1, config.BATCH_SIZE)
    groups = [words[i : i + batch_size] for i in range(0, len(words), batch_size)]
    total_batches = len(groups)

    for batch_idx, group in enumerate(groups):
        if _cancelled(cb):
            logger.info("检测到取消请求，剩余造句批次跳过（已造句的词保留在中间文件）")
            break
        lines = []
        for e in group:
            if e.sentence:
                lines.append(f"- [{e.index}] {e.word}（中文提示: {e.sentence}）")
            else:
                lines.append(f"- [{e.index}] {e.word}")
        prompt = prompts.get_prompt("sentence_gen").replace("{word_list}", "\n".join(lines))
        messages = [
            {"role": "system", "content": "你是一名雅思备考教练。请只输出 JSON，不要有其他内容。"},
            {"role": "user", "content": prompt},
        ]
        result = await llm.llm_chat(messages, temperature=0.5, max_tokens=config.BATCH_MAX_TOKENS)
        if result is None:
            logger.error(f"造句批次 {batch_idx + 1}/{total_batches} 失败（API/JSON），本批 {len(group)} 词跳过")
            failures.extend(e.word for e in group)
            cb.on_progress(batch_idx + 1, total_batches, "")
            continue

        for e in group:
            data = result.get(e.word.lower())
            if not isinstance(data, dict):
                for alt in (e.word, e.word.capitalize(), e.word.upper()):
                    data = result.get(alt)
                    if isinstance(data, dict):
                        break
            sentence = str(data.get("sentence", "")).strip() if isinstance(data, dict) else ""
            if not sentence:
                logger.warning(f"造句失败（无有效例句）: {e.word}")
                failures.append(e.word)
                continue
            if e.word.lower() not in sentence.lower():
                logger.warning(f"造句失败（例句未包含该词）: {e.word} → {sentence[:60]}")
                failures.append(e.word)
                continue
            successful.append((e.word, sentence))
        cb.on_progress(batch_idx + 1, total_batches, "")

    if not successful:
        logger.error("全部单词造句失败，未生成中间文件")
        return "", failures

    out_path = _intermediate_dir() / f"{Path(run_args.input).stem or 'words'}-sentences.md"
    out_lines: list[str] = []
    for n, (word, sentence) in enumerate(successful, 1):
        out_lines.append(f"{n}. **{word}**")
        out_lines.append(f"   > {sentence}")
        out_lines.append("")
    Path(out_path).write_text("\n".join(out_lines), encoding="utf-8")
    logger.info(f"造句完成: {len(successful)} 词成功, {len(failures)} 词失败 → 中间文件: {out_path}")
    return str(out_path), failures


def normalize_any_text(text: str, target_format: str = "two_line") -> str:
    """
    用 prompts/format_assist.md 把任意笔记文本转为标准格式（Task 4）。

    target_format: single_line / two_line / colon / word_list / auto。
    返回规范化后的 Markdown 文本；失败返回空字符串。
    """
    label = parser.FORMAT_LABELS.get(target_format, target_format)
    prompt = (
        prompts.get_prompt("format_assist")
        .replace("{target_format}", f"{label}（{target_format}）")
        .replace("{text}", text)
    )
    messages = [
        {"role": "system", "content": "你是一名格式转换助手。只输出转换后的 Markdown 文本，不要解释。"},
        {"role": "user", "content": prompt},
    ]
    result = asyncio.run(
        llm.llm_chat_raw(messages, temperature=0.3, max_tokens=config.BATCH_MAX_TOKENS)
    )
    if not result:
        return ""
    out = result.strip()
    # 容错：剥离模型偶尔加上的代码块围栏
    if out.startswith("```"):
        out = re.sub(r"^```[a-zA-Z]*\r?\n?", "", out)
        out = re.sub(r"\r?\n?```$", "", out).strip()
    detected = parser.detect_format(out)
    if target_format != "auto" and detected != target_format:
        logger.warning(f"AI 规范化结果格式不符: 期望 {target_format}, 实际 {detected}")
    return out


def _cancelled_summary(cache_path: str) -> str:
    summary_lines = [
        "\n" + "=" * 60,
        "[CANCELLED] run cancelled by user.",
        "   本次已完成并写入缓存的词已保留，下次运行可续跑。",
        f"   Cache file: {cache_path}",
        "=" * 60,
    ]
    return "\n".join(summary_lines)


# ===========================================================================
# 9. 主流程
# ===========================================================================
async def _run_async(args: RunConfig, cb: PipelineCallbacks) -> Optional[str]:
    """异步主流程（原 main_async 原文搬运 + 回调埋点 + 取消检查）。"""
    if _cancelled(cb):
        summary = _cancelled_summary(args.cache)
        print(summary)
        cb.on_finished(summary)
        return summary

    # --- 9a. 解析 Markdown（含解析预检） ---
    cb.on_stage_changed("parse")
    logger.info(f"📖 解析输入文件: {args.input}")
    preview = parser.parse_preview(args.input)
    all_entries = preview.entries

    if args.limit and args.limit > 0:
        all_entries = all_entries[: args.limit]
        logger.info(f"🔢 限制处理数量为前 {args.limit} 个")

    # --- 9a-0. 纯单词模式（--word-list 或检测到纯单词列表）：先造句生成中间文件 ---
    sentence_gen_failures: list[str] = []
    if all_entries and (args.word_list or preview.detected_format == "word_list"):
        if args.word_list and preview.detected_format != "word_list":
            logger.warning("--word-list 已指定，但输入未识别为纯单词列表；仍按纯单词模式为其造句")
        interp_path, sg_failed = await generate_word_list_sentences(args, all_entries, cb)
        sentence_gen_failures.extend(sg_failed)
        if not interp_path:
            logger.error("全部单词造句失败，无法继续（可重跑重试，或手写例句后用标准格式输入）")
        else:
            logger.info(f"📝 造句中间文件已生成: {interp_path}（可手动修改后重新运行）")
            preview = parser.parse_preview(interp_path)
            all_entries = preview.entries

    if not all_entries:
        logger.warning("没有可处理的条目，退出")
        if sentence_gen_failures:
            cb.on_failures([(w, "造句失败") for w in sentence_gen_failures])
            summary_lines = [
                "\n" + "=" * 60,
                "[DONE] generation complete (no entries).",
                f"   造句失败词（未生成卡片）: {len(sentence_gen_failures)}",
            ]
            for w in sentence_gen_failures:
                summary_lines.append(f"     - {w}")
            summary_lines.append("=" * 60)
            summary = "\n".join(summary_lines)
            try:
                print(summary)
            except UnicodeEncodeError:
                print(summary.encode('ascii', errors='replace').decode('ascii'))
            cb.on_finished(summary)
            return summary
        return None

    # --- 9b. 增量过滤 ---
    enrichment_cache = cache.load_enrichment_cache(args.cache)
    processed = cache.migrate_processed_keys(cache.load_processed(config.PROCESSED_FILE))

    entries = cache.filter_new_entries(
        all_entries, enrichment_cache, processed, force=args.force
    )

    if _cancelled(cb):
        summary = _cancelled_summary(args.cache)
        print(summary)
        cb.on_finished(summary)
        return summary

    # --- 9c. 生成音频 ---
    cb.on_stage_changed("audio")
    # 先做 TTS provider 预检：gTTS 不可达时可自动退到 edge-tts（若已安装）。
    audio_health = audio.select_audio_provider(args.tts_provider)
    audio._active_tts_provider = audio_health.provider
    audio_reachable = config.AUDIO_ENABLED and audio_health.available
    if not audio_reachable:
        if audio_health.provider == "none" and audio_health.error == "audio disabled":
            logger.info("音频已禁用，本轮不生成发音")
        else:
            audio.warn_audio_unreachable()
            logger.warning(f"无可用 TTS provider: {audio_health.error}")
    else:
        logger.info(f"本轮使用 TTS provider: {audio._active_tts_provider}")

    # 对全部条目生成音频（已存在的非空文件会跳过，幂等且开销小）。
    # 这样缓存命中、本轮未重新富化的词也有正确的 audio_map 条目，
    # 保证卡片字段 [sound:...] 与 media/ 目录中的实际文件一致。
    if audio_reachable:
        logger.info(f"🎵 生成 {len(all_entries)} 个单词的发音音频（已存在的将跳过）...")
        audio_map = await audio.batch_generate_audio(
            all_entries,
            max_concurrency=args.max_concurrency,
            provider=audio._active_tts_provider,
            force=args.refresh_audio,
        )
    else:
        # 连不上：仍为已有音频文件建立映射，新词不再尝试生成
        audio_map = {
            e.word.lower(): audio.sanitize_filename(e.word) + ".mp3" for e in all_entries
        }

    # --- 9d. LLM 批量富化 ---
    cb.on_stage_changed("enrich")
    logger.info(f"LLM enriching {len(entries)} words (batch size={config.BATCH_SIZE})...")

    sem = asyncio.Semaphore(args.max_concurrency)
    enrichment_data: dict[str, dict] = {}
    now_ts = datetime.now(timezone.utc).isoformat()

    if not entries:
        logger.info("✅ 所有单词已完整处理过；仍会检查缓存并重新打包，便于补全音频/输出文件")

    # 把待处理单词分为：有缓存直接用的 + 需调 API 的
    cached_batch: list[WordEntry] = []
    fresh_batch: list[tuple[WordEntry, str]] = []

    for entry in entries:
        word_key = entry.word.lower()
        sent_hash = cache.compute_sentence_hash(entry.sentence)
        audio_fname = audio_map.get(word_key, f"{audio.sanitize_filename(entry.word)}.mp3")

        cache_key = cache.build_enrichment_cache_key(entry)
        cached, cached_key, is_legacy_cache = cache.get_cached_enrichment(entry, enrichment_cache)
        if not args.force and cached is not None:
            cached_hash = cached.get("_sentence_hash", "")
            cache_version_ok = is_legacy_cache or (
                cached.get("_schema_version") == config.ENRICHMENT_SCHEMA_VERSION
                and cached.get("_prompt_version") == config.ENRICHMENT_PROMPT_VERSION
            )
            if cached_hash == sent_hash and cache_version_ok and not quality.has_placeholder_or_low_quality_content(cached) and quality.quality_metadata_ok(cached):
                cached["audio_filename"] = f"[sound:{audio_fname}]"
                enrichment_data[word_key] = cached
                if cached_key != cache_key:
                    enrichment_cache[cache_key] = cached
                cached_batch.append(entry)
                continue

        fresh_batch.append((entry, audio_fname))

    if cached_batch:
        logger.info(f"Cache hit: {len(cached_batch)} words, remaining: {len(fresh_batch)}")

    # 把 fresh 单词分组成批次（每批 BATCH_SIZE 个）
    groups = [
        fresh_batch[i : i + config.BATCH_SIZE]
        for i in range(0, len(fresh_batch), config.BATCH_SIZE)
    ]

    # 并发处理各批次
    total_batches = len(groups)
    enrichment_failures: list[tuple[str, str]] = []
    spelling_warnings: list[tuple[str, str]] = []

    async def process_one_group(
        batch: list, batch_idx: int
    ) -> tuple[dict[str, dict], list[tuple[str, str]], list[tuple[str, str]]]:
        try:
            result, fails, warns = await enricher.process_batch(batch, sem)
            entry_by_word = {entry.word.lower(): entry for entry, _ in batch}
            for k, v in result.items():
                source_entry = entry_by_word.get(k)
                if source_entry is None:
                    source_entry = next((e for e in entries if e.word.lower() == k), None)
                if source_entry is None:
                    continue
                v["_sentence_hash"] = cache.compute_sentence_hash(source_entry.sentence)
                v["_schema_version"] = config.ENRICHMENT_SCHEMA_VERSION
                v["_prompt_version"] = config.ENRICHMENT_PROMPT_VERSION
                v["_model"] = config.LLM_MODEL
                v["_cache_key"] = cache.build_enrichment_cache_key(source_entry)
                v["_generated_at"] = now_ts
            return result, fails, warns
        except Exception as e:
            logger.error(f"Batch {batch_idx+1}/{total_batches} failed: {e}")
            return {}, [(entry.word, f"批处理异常: {e}") for entry, _ in batch], []

    def _save_partial_cache() -> None:
        """把已完成词写回富化缓存（取消时保留进度，下次续跑）。"""
        for word_key, data in enrichment_data.items():
            cache_key = data.get("_cache_key")
            if cache_key:
                enrichment_cache[cache_key] = data
            else:
                enrichment_cache[word_key] = data
        cache.save_enrichment_cache(args.cache, enrichment_cache)
        logger.info(f"💾 富化缓存已保存至: {args.cache}")

    cancelled = False
    if TQDM_AVAILABLE and total_batches > 0 and cb.cancel_requested is None:
        tasks = [process_one_group(b, i) for i, b in enumerate(groups)]
        results = await async_tqdm.gather(
            *tasks, desc=f"LLM batches ({total_batches})", total=total_batches
        )
        done = 0
        for r in (results or []):
            done += 1
            cb.on_progress(done, total_batches, "")
            if isinstance(r, tuple):
                data, fails, warns = r
                enrichment_data.update(data)
                enrichment_failures.extend(fails)
                spelling_warnings.extend(warns)
    else:
        for i, batch in enumerate(groups):
            if _cancelled(cb):
                logger.info("检测到取消请求，剩余批次跳过（已完成词保留缓存，可续跑）")
                cancelled = True
                break
            logger.info(f"Batch {i+1}/{total_batches}: {len(batch)} words")
            data, fails, warns = await process_one_group(batch, i)
            enrichment_data.update(data)
            enrichment_failures.extend(fails)
            spelling_warnings.extend(warns)
            cb.on_progress(i + 1, total_batches, "")

    if cancelled:
        _save_partial_cache()
        summary = _cancelled_summary(args.cache)
        try:
            print(summary)
        except UnicodeEncodeError:
            print(summary.encode('ascii', errors='replace').decode('ascii'))
        cb.on_finished(summary)
        return summary

    successful = len(enrichment_data)
    # 按唯一词去重计算应处理的词数（entries 按条目计，重复词会让计数虚高），
    # 避免出现 "50 success, 1 failed" 这类与实际失败列表矛盾的虚假失败计数。
    unique_words = {e.word.lower() for e in entries}
    failed = max(0, len(unique_words) - successful)
    logger.info(f"LLM enrichment done: {successful} success, {failed} failed")

    # --- 更新缓存 ---
    _save_partial_cache()

    # --- 9e. 生成 Anki 卡组 ---
    cb.on_stage_changed("package")
    logger.info("📦 生成 Anki 卡组包...")

    # 合并所有数据：新富化 + 缓存中的旧数据（用于已被处理但不需重生成的卡片）
    all_enrichment = {}
    missing_enrichment: list[tuple[str, str]] = []
    for entry in all_entries:
        key = entry.word.lower()
        cached, _cache_key, _is_legacy = cache.get_cached_enrichment(entry, enrichment_cache)
        # 优先用最新富化数据
        if key in enrichment_data:
            all_enrichment[key] = enrichment_data[key]
        elif isinstance(cached, dict):
            all_enrichment[key] = cached
        else:
            # 既无新富化也无缓存 → 该词不会生成卡片
            missing_enrichment.append((entry.word, "无富化数据，未生成卡片"))

    # 尝试读取之前的模型 ID 以保持兼容
    model_id = None
    if processed:
        # 无特定模型 ID 存储 ==> 随机生成
        pass

    # --- 为词条原形（lemma）生成发音 ---
    # 背面卡片展示的是还原后的词条原形（如 initiate），
    # 发音也应使用原形发音而非句子中的变形（如 initiated）发音。
    # 去重：跳过已有非空音频文件的 lemma（多数 lemma 与原词形相同，
    # 在前一步 batch_generate_audio 中已生成），避免重复一轮 gTTS 调用。
    lemma_audio_needed: set[str] = set()
    for key, data in all_enrichment.items():
        lemma = data.get("word", key)
        lemma_fname = audio.sanitize_filename(lemma) + ".mp3"
        lemma_path = config.MEDIA_DIR / lemma_fname
        if lemma_path.exists() and lemma_path.stat().st_size > 0:
            continue  # 已有音频，无需重复生成
        lemma_audio_needed.add(lemma)

    if lemma_audio_needed and audio_reachable:
        lemma_sem = asyncio.Semaphore(args.max_concurrency)
        async def _gen_lemma(lemma: str):
            async with lemma_sem:
                return await audio.generate_audio(lemma, str(config.MEDIA_DIR), audio._active_tts_provider, args.refresh_audio)

        logger.info(
            f"Generating audio for {len(lemma_audio_needed)} lemma forms"
            f" (back card pronunciation)..."
        )
        lemma_tasks = [_gen_lemma(lemma) for lemma in lemma_audio_needed]
        if TQDM_AVAILABLE:
            for task in async_tqdm.as_completed(
                lemma_tasks, desc="Lemma audio", total=len(lemma_tasks)
            ):
                await task
        else:
            await asyncio.gather(*lemma_tasks)

    # 持久化音频失败缓存（冷却期内的词下次直接跳过，不再卡超时）
    audio.save_audio_failures()

    # 更新所有富化数据的 audio_filename 为词条原形发音；缺失/空文件不写入声频引用。
    for key, data in all_enrichment.items():
        lemma = data.get("word", key)
        lemma_fname = audio.sanitize_filename(lemma) + ".mp3"
        if config.AUDIO_ENABLED and cache.audio_file_is_valid(lemma_fname, config.MEDIA_DIR):
            data["audio_filename"] = f"[sound:{lemma_fname}]"
        else:
            data["audio_filename"] = ""

    # 卡组名：用户自定义 + 占位符解析（留空用默认）
    deck_label = (args.deck_name or "").strip()
    if deck_label:
        deck_label = (
            deck_label
            .replace("{日期}", datetime.now().strftime("%Y-%m-%d"))
            .replace("{词数}", str(len(all_entries)))
        )
    package = anki_packager.create_anki_package(
        all_entries, all_enrichment, audio_map, deck_name=deck_label or None
    )

    # 写入文件
    package.write_to_file(args.output)
    logger.info(f"✅ Anki 卡组已生成: {args.output}")

    # --- 9f. 更新 processed.json ---
    # 只有最终卡片具备可用富化，且音频有效或明确禁用时，才标记为可跳过。
    for entry in all_entries:
        word_key = entry.word.lower()
        sent_hash = cache.compute_sentence_hash(entry.sentence)
        rich = all_enrichment.get(word_key)
        enrichment_ok = isinstance(rich, dict) and not quality.has_placeholder_or_low_quality_content(rich)
        quality_ok = isinstance(rich, dict) and quality.quality_metadata_ok(rich)
        audio_ref = rich.get("audio_filename", "") if isinstance(rich, dict) else ""
        audio_fname = cache.extract_sound_filename(audio_ref)
        if not config.AUDIO_ENABLED:
            audio_status = "disabled"
        elif audio_fname and cache.audio_file_is_valid(audio_fname, config.MEDIA_DIR):
            audio_status = "ok"
        else:
            audio_status = "missing"

        processed[word_key] = {
            "word": entry.word,
            "index": entry.index,
            "sentence_hash": sent_hash,
            "enrichment_key": cache.build_enrichment_cache_key(entry),
            "enrichment_status": "ok" if enrichment_ok else "missing",
            "audio_status": audio_status,
            "quality_status": "ok" if quality_ok else ("disabled" if not config.QUALITY_CHECKS_ENABLED else "missing"),
            "quality_schema_version": config.QUALITY_SCHEMA_VERSION,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
    cache.save_processed(config.PROCESSED_FILE, processed)
    logger.info(f"📋 已处理记录已更新: {config.PROCESSED_FILE}")

    # --- 9g. 总结 ---
    # 汇总所有失败/跳过的词：富化失败 + 无数据未成卡
    all_failures = enrichment_failures + missing_enrichment
    # 去重（同一词可能在两处都记录），保留首个原因
    seen_words: set[str] = set()
    deduped_failures: list[tuple[str, str]] = []
    for word, reason in all_failures:
        if word.lower() not in seen_words:
            seen_words.add(word.lower())
            deduped_failures.append((word, reason))

    summary_lines = [
        "\n" + "=" * 60,
        "[DONE] generation complete!",
        f"   Total entries: {len(all_entries)}",
        f"   Processed this run: {len(entries)}",
        f"   Enrichment success: {successful}",
        f"   Enrichment failed/skipped: {len(deduped_failures)}",
        f"   Output file: {args.output}",
        f"   Media files: {len(package.media_files)} audio",
        f"   TTS provider: {audio._active_tts_provider if config.AUDIO_ENABLED else 'none'}",
        f"   Quality checks: {'enabled' if config.QUALITY_CHECKS_ENABLED else 'disabled'} (max attempts={config.QUALITY_MAX_ATTEMPTS})",
        f"   Cache file: {args.cache}",
    ]
    if deduped_failures:
        summary_lines.append("-" * 60)
        summary_lines.append("   FAILED / SKIPPED words (rerun with --force to retry):")
        for word, reason in deduped_failures:
            summary_lines.append(f"     - {word}: {reason}")
    if sentence_gen_failures:
        summary_lines.append("-" * 60)
        summary_lines.append("   [!] SENTENCE GENERATION FAILED (words skipped, not in cards):")
        for word in sentence_gen_failures:
            summary_lines.append(f"     - {word}")
    # 拼写警告去重（同一词可能出现在多个批次）
    seen_spell: set[str] = set()
    deduped_spell: list[tuple[str, str]] = []
    for word, note in spelling_warnings:
        if word.lower() not in seen_spell:
            seen_spell.add(word.lower())
            deduped_spell.append((word, note))
    if deduped_spell:
        summary_lines.append("-" * 60)
        summary_lines.append("   [?] POSSIBLE SPELLING ISSUES in source (check & fix .md, then rerun --force):")
        for word, note in deduped_spell:
            summary_lines.append(f"     - {word}: {note}")
    if config.AUDIO_ENABLED and not audio_reachable:
        summary_lines.append("-" * 60)
        summary_lines.append(f"   [!] AUDIO SKIPPED: no available TTS provider ({audio_health.error}).")
        summary_lines.append("       可尝试 --tts-provider edge 并安装 edge-tts，或配置 AUDIO_PROXY 后重跑。")
    summary_lines.append("=" * 60)
    summary = "\n".join(summary_lines)

    # 用 ASCII 避免 Windows GBK 编码问题
    try:
        print(summary)
    except UnicodeEncodeError:
        print(summary.encode('ascii', errors='replace').decode('ascii'))

    cb.on_failures(
        deduped_failures + [(w, "造句失败") for w in sentence_gen_failures]
    )
    cb.on_finished(summary)
    return summary
