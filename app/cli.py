"""命令行入口 — 参数与行为和原 vocab_anki_generator.py 完全一致。

main() 只做三件事：把 CLI 参数同步到 config 全局（原全局覆盖语义）、
检查必填配置、调用 pipeline.run()。流程本体在 app/core/pipeline.py。
"""

import argparse
import logging
import sys

from app.core import anki_packager, config, llm, pipeline
from app.core.pipeline import PipelineCallbacks, RunConfig

logger = logging.getLogger(__name__)


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="AnkiMint — 从 Markdown 词库生成 Anki .apkg 填空卡组",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础使用
  python vocab_anki_generator.py --input data/input/words.md --output data/output/output.apkg

  # 强制重新生成所有卡片
  python vocab_anki_generator.py --input data/input/words.md --output data/output/output.apkg --force

  # 仅处理前10个单词（测试）
  python vocab_anki_generator.py --input data/input/words.md --limit 10

  # 自定义并发数与速率
  python vocab_anki_generator.py --max-concurrency 5 --rate-limit 30
        """,
    )
    parser.add_argument(
        "--input", "-i",
        default="words.md",
        help="Markdown 源文件路径 (默认: words.md)",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(config.OUTPUT_DIR / "output.apkg"),
        help=f"输出的 .apkg 文件路径 (默认: {config.OUTPUT_DIR / 'output.apkg'})",
    )
    parser.add_argument(
        "--cache", "-c",
        default=config.CACHE_FILE,
        help="富化缓存 JSON 文件路径 (默认: enrichment_cache.json)",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制重新生成所有单词的卡片 (忽略 processed.json 和缓存)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help=f"最大并发请求数 (默认: {config.MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help=f"每分钟最大请求数 (默认: {config.RATE_LIMIT_PER_MIN})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="限制处理的单词数量 (默认: 0 = 全部)",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="跳过音频生成（网络连不上 gTTS 时快速出卡，后续可补音频）",
    )
    parser.add_argument(
        "--tts-provider",
        choices=["auto", "gtts", "edge", "none"],
        default=None,
        help=f"TTS provider: auto/gtts/edge/none (默认: {config.TTS_PROVIDER})",
    )
    parser.add_argument(
        "--refresh-audio",
        action="store_true",
        help="强制重新检查/补全音频；即使全部词已 processed 也会重新打包",
    )
    parser.add_argument(
        "--no-quality-checks",
        action="store_true",
        help="跳过 accuracy/completeness 质量子代理检查（紧急快速出卡用）",
    )
    parser.add_argument(
        "--quality-max-attempts",
        type=int,
        default=None,
        help=f"质量检查失败后的最大修复重试次数 (默认: {config.QUALITY_MAX_ATTEMPTS})",
    )
    parser.add_argument(
        "--save-config",
        action="store_true",
        help=f"把当前生效的配置（含 .env 的 Key）写入 {config.CONFIG_FILE} 后退出",
    )
    parser.add_argument(
        "--word-list",
        action="store_true",
        help="纯单词模式：先让 AI 为每词造雅思语境例句（生成两行式中间文件），再走正常富化流程",
    )
    parser.add_argument(
        "--format-input",
        default=None,
        metavar="PATH",
        help="把任意笔记文本交给 AI 转换为标准格式并另存到 data/intermediate/（只转换，不生成卡片）",
    )
    parser.add_argument(
        "--target-format",
        choices=["two_line", "single_line", "colon", "word_list", "auto"],
        default="two_line",
        help="--format-input 的目标格式 (默认: two_line)",
    )
    parser.add_argument(
        "--deck-name",
        default="",
        help=f"Anki 卡组名称，支持 {{日期}}/{{词数}} 占位符 (默认: {anki_packager.DEFAULT_DECK_NAME})",
    )
    return parser.parse_args()


def main():
    """入口函数。"""
    args = parse_args()

    # --- 配置优先级合并：CLI 参数 > data/config.json > 环境变量/.env > 代码默认 ---
    # argparse 的 default=None 用于区分"用户显式给出"与"未给出"，
    # 未给出的参数不能反向覆盖 config.json。
    cli_overrides = {}
    if args.max_concurrency is not None:
        cli_overrides["max_concurrency"] = args.max_concurrency
    if args.rate_limit is not None:
        cli_overrides["rate_limit_per_min"] = args.rate_limit
    if args.quality_max_attempts is not None:
        cli_overrides["quality_max_attempts"] = args.quality_max_attempts
    if args.tts_provider is not None:
        cli_overrides["tts_provider"] = args.tts_provider
    app_cfg = config.resolve_config(cli_overrides)
    app_cfg.quality_max_attempts = max(0, app_cfg.quality_max_attempts)
    app_cfg.apply_to_config()

    if args.save_config:
        for path in (config.CACHE_DIR, config.MEDIA_DIR, config.OUTPUT_DIR, config.LOG_DIR, config.BACKUP_DIR):
            path.mkdir(parents=True, exist_ok=True)
        app_cfg.save()
        masked = app_cfg.masked()
        logger.info(f"当前生效配置（Key 已打码）: api_key={masked.api_key} base_url={masked.base_url} model={masked.model}")
        return

    # 运行期开关（--no-* 只影响本次运行，不写入配置）
    if args.no_quality_checks:
        config.QUALITY_CHECKS_ENABLED = False
        logger.info("已禁用质量检查子代理 (--no-quality-checks)")
    if args.no_audio:
        config.AUDIO_ENABLED = False
        logger.info("已禁用音频生成 (--no-audio)")

    llm.rate_limiter = llm.RateLimiter(config.RATE_LIMIT_PER_MIN)

    # --- AI 格式规范化：只转换文本，不生成卡片 ---
    if args.format_input:
        from pathlib import Path as _Path

        src = _Path(args.format_input)
        if not src.exists():
            logger.error(f"输入文件不存在: {src}")
            sys.exit(1)
        text = src.read_text(encoding="utf-8")
        normalized = pipeline.normalize_any_text(text, args.target_format)
        if not normalized:
            logger.error("AI 规范化失败（API 调用失败），未生成文件")
            sys.exit(1)
        out_dir = config.DATA_DIR / "intermediate"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{src.stem or 'formatted'}-formatted.md"
        out_path.write_text(normalized + "\n", encoding="utf-8")
        logger.info(f"✅ 规范化完成（{args.target_format}）: {out_path}")
        print(str(out_path))
        return

    # 首次运行提示：把 .env 的 Key 一键导入 data/config.json
    config.hint_env_import()

    # 检查必填配置
    if not config.LLM_API_KEY:
        logger.error(
            "LLM_API_KEY 未设置！请在 .env 文件中配置或导出环境变量。\n"
            "  复制: cp .env.example .env\n"
            "  编辑: 填入你的 API Key"
        )
        sys.exit(1)

    # 创建运行目录
    for path in (config.CACHE_DIR, config.MEDIA_DIR, config.OUTPUT_DIR, config.LOG_DIR, config.BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)

    run_config = RunConfig(
        input=args.input,
        output=args.output,
        cache=args.cache,
        force=args.force,
        max_concurrency=config.MAX_CONCURRENCY,
        rate_limit=config.RATE_LIMIT_PER_MIN,
        limit=args.limit,
        no_audio=args.no_audio,
        tts_provider=args.tts_provider or config.TTS_PROVIDER,
        refresh_audio=args.refresh_audio,
        no_quality_checks=args.no_quality_checks,
        quality_max_attempts=config.QUALITY_MAX_ATTEMPTS,
        word_list=args.word_list,
        deck_name=args.deck_name,
    )

    # 运行主流程（异常处理与原 main() 一致）
    try:
        pipeline.run(run_config, PipelineCallbacks())
    except KeyboardInterrupt:
        logger.info("用户中断操作")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"未预期的错误: {e}")
        sys.exit(1)
