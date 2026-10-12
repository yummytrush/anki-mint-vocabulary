"""富化质量体系 — 结构化校验兜底 + accuracy/completeness 双质检 Agent + 修复循环。

validate_and_fix_enrichment 系列（结构校验/字段兜底）也放在本模块：
process_batch（enricher）与 fix_enrichment_with_quality_issues 都依赖它。
（原文搬运；LLM 调用一律经 `llm.llm_chat`，可覆盖开关经 `config.X` 读取）
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app.core import config, llm, prompts
from app.core.models import Importance, WordEntry

logger = logging.getLogger(__name__)


# ===========================================================================
# 3b-结构. 富化 JSON 结构校验与字段兜底
# ===========================================================================
def _normalize_meaning_text(text: str) -> str:
    """归一化释义文本，用于占位/重复检测。"""
    text = str(text or "").strip().lower()
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[，。；;、,.!！?？:：()（）\[\]【】\"'“”‘’]+", "", text)
    return text


_PLACEHOLDER_MEANINGS = {
    "",
    "意思",
    "含义",
    "词义",
    "释义",
    "翻译",
    "待补充",
    "暂无详细释义",
    "无",
    "未知",
    "n/a",
    "na",
    "none",
    "todo",
}


def _is_placeholder_meaning(text: str) -> bool:
    normalized = _normalize_meaning_text(text)
    return normalized in _PLACEHOLDER_MEANINGS or normalized.startswith("暂无")


def deduplicate_core_meanings(core_meanings: list) -> list[dict]:
    """移除空释义、占位释义和简单重复释义。"""
    if not isinstance(core_meanings, list):
        return []

    cleaned: list[dict] = []
    seen: set[str] = set()
    for item in core_meanings:
        if not isinstance(item, dict):
            continue
        usage = str(item.get("usage", "")).strip()
        if _is_placeholder_meaning(usage):
            continue

        dedupe_key = _normalize_meaning_text(usage)
        if not dedupe_key or dedupe_key in seen:
            continue

        item = dict(item)
        item["usage"] = usage
        cleaned.append(item)
        seen.add(dedupe_key)
    return cleaned


def validate_phonetic(phonetic: Optional[str]) -> str:
    """基础校验 IPA 字段；明显错误时返回空字符串。"""
    value = str(phonetic or "").strip()
    if value in ("/?/", "?", ""):
        return ""
    if re.search(r"[一-鿿]", value):
        return ""
    if len(value) > 80 or " " in value.strip("/[]"):
        return ""
    if not re.search(r"[/\[\]ˈˌəɪʊɔæɑɒɜθðʃʒŋa-zA-Z]", value):
        return ""
    return value


def validate_lemma(lemma: Optional[str], original_word: str) -> str:
    """基础校验 lemma；明显不像词条时回退原词。"""
    value = str(lemma or "").strip()
    original = str(original_word or "").strip()
    if not value:
        return original
    if re.search(r"[一-鿿]", value):
        return original
    if len(value) > 80 or any(ch in value for ch in "\n\r\t{}[]"):
        return original
    if len(value.split()) > 6:
        return original
    return value


def has_placeholder_or_low_quality_content(data: dict) -> bool:
    """判断富化结果是否缺少可用的核心内容。"""
    if not isinstance(data, dict):
        return True
    core_meanings = data.get("core_meanings", [])
    if not isinstance(core_meanings, list) or not core_meanings:
        return True
    first_usage = core_meanings[0].get("usage", "") if isinstance(core_meanings[0], dict) else ""
    return _is_placeholder_meaning(first_usage)


def validate_and_fix_enrichment(
    data: dict,
    word: str,
    importance: Importance,
    audio_filename: str,
) -> dict:
    """
    验证 LLM 返回的富化 JSON 结构，修复缺失字段。

    关键假设：LLM 可能返回不完整或格式有误的 JSON，需容错。
    风险：若 word 字段缺失，无法确认这个结果属于哪个单词。
    """
    # 必填字段兜底
    data["word"] = validate_lemma(data.get("word", word), word)
    data.setdefault("inflected_form", word)
    data.setdefault("context_meaning", "")
    data["phonetic"] = validate_phonetic(data.get("phonetic", ""))
    data.setdefault("audio_filename", f"[sound:{audio_filename}]")
    data.setdefault("importance", importance.value)
    data["core_meanings"] = deduplicate_core_meanings(data.get("core_meanings", []))
    data.setdefault("etymology", None)
    data.setdefault("derivatives", [])
    data.setdefault("confusable_words", [])
    data.setdefault("grammar_tips", [])
    data.setdefault("memory_tips", None)
    data.setdefault("sentence_translation", "")

    # 确保 core_meanings 至少有一项；占位仅用于显示兜底，不应被当作高质量富化。
    if not data["core_meanings"]:
        data["core_meanings"] = [{
            "usage": "",
            "logic": "",
            "definition_en": "",
            "grammar_note": "",
            "examples": [],
        }]

    # 确保每个 core_meaning 子字段都存在
    for cm in data["core_meanings"]:
        cm.setdefault("usage", "")
        cm.setdefault("logic", "")
        cm.setdefault("definition_en", "")
        cm.setdefault("grammar_note", "")
        cm.setdefault("examples", [])

    # 确保 derivatives 子字段
    for d in data["derivatives"]:
        d.setdefault("word", "")
        d.setdefault("pos", "")
        d.setdefault("meaning", "")
        d.setdefault("example", "")

    # 确保 confusable_words 子字段
    for cw in data["confusable_words"]:
        cw.setdefault("words", "")
        cw.setdefault("difference", "")

    # 标记缺失的必填字段
    missing = []
    if data["phonetic"] in ("", None):
        missing.append("phonetic")
    if has_placeholder_or_low_quality_content(data):
        missing.append("core_meanings")
    elif not data["core_meanings"][0].get("definition_en"):
        missing.append("definition_en (核心释义第1项)")

    if missing:
        logger.warning(f"单词 '{word}' 富化结果缺失字段: {', '.join(missing)}，已用占位符")

    return data


# ===========================================================================
# 3b. 富化质量检查（accuracy / completeness checker agents）
# ===========================================================================
def is_multiword_entry(word: str) -> bool:
    """判断词条是否为短语；短语不强制要求单词级 IPA。"""
    return len(str(word or "").strip().split()) > 1


def build_quality_check_prompt(kind: str, items: dict[str, dict]) -> str:
    """构建 accuracy / completeness checker prompt（提示词外置于 prompts/，占位符 str.replace）。"""
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    name = "check_accuracy" if kind == "accuracy" else "check_completeness"
    return (
        prompts.get_prompt(name)
        .replace("{items_keys}", ", ".join(items.keys()))
        .replace("{payload}", payload)
        .strip()
    )


def _quality_item(report: Optional[dict], word_key: str) -> dict:
    if not isinstance(report, dict):
        return {
            "passed": False,
            "severity": "blocking",
            "issues": [{"field": "quality_checker", "problem": "checker 无有效 JSON 返回", "expected": "重新检查并返回合格报告"}],
            "fix_instructions": ["重新生成完整、准确的富化 JSON"],
        }
    items = report.get("items", {})
    item = items.get(word_key) if isinstance(items, dict) else None
    if not isinstance(item, dict):
        return {
            "passed": False,
            "severity": "blocking",
            "issues": [{"field": "quality_checker", "problem": f"报告缺少 {word_key} 条目", "expected": "返回该词的检查结果"}],
            "fix_instructions": ["重新检查该词并修复所有缺失字段"],
        }
    return item


def merge_quality_reports(
    accuracy_report: Optional[dict], completeness_report: Optional[dict], word_key: str
) -> dict:
    """合并两个 checker agent 的报告。"""
    merged = {"passed": True, "severity": "pass", "issues": [], "fix_instructions": []}
    for kind, report in (("accuracy", accuracy_report), ("completeness", completeness_report)):
        item = _quality_item(report, word_key)
        issues = item.get("issues", []) if isinstance(item.get("issues", []), list) else []
        for issue in issues:
            if isinstance(issue, dict):
                merged["issues"].append({"checker": kind, **issue})
            else:
                merged["issues"].append({"checker": kind, "field": "unknown", "problem": str(issue), "expected": "修复该问题"})
        instructions = item.get("fix_instructions", [])
        if isinstance(instructions, list):
            merged["fix_instructions"].extend(str(x) for x in instructions if str(x).strip())
        passed = bool(item.get("passed")) and item.get("severity") != "blocking"
        if not passed:
            merged["passed"] = False
            merged["severity"] = "blocking"
    return merged


def quality_report_passed(report: dict, word_key: str = "") -> bool:
    """判断合并后或原始 checker 报告是否通过。"""
    if not isinstance(report, dict):
        return False
    if "items" in report and word_key:
        item = _quality_item(report, word_key)
    else:
        item = report
    return bool(item.get("passed")) and item.get("severity") != "blocking" and not item.get("issues")


def summarize_quality_issues(report: dict, max_items: int = 3) -> str:
    issues = report.get("issues", []) if isinstance(report, dict) else []
    parts = []
    for issue in issues[:max_items]:
        if isinstance(issue, dict):
            field = issue.get("field", "unknown")
            problem = issue.get("problem", "质量检查失败")
            parts.append(f"{field}: {problem}")
        else:
            parts.append(str(issue))
    if len(issues) > max_items:
        parts.append(f"另有 {len(issues) - max_items} 项")
    return "; ".join(parts) or "质量检查未通过"


def local_quality_report(entry: "WordEntry", data: dict) -> dict:
    """本地硬性完整性检查；不替代 LLM checker，但能先抓明显漏项。"""
    issues: list[dict] = []
    if not str(data.get("context_meaning", "")).strip():
        issues.append({"checker": "local", "field": "context_meaning", "problem": "缺少语境中文提示", "expected": "给出贴合原句的中文含义"})
    if not str(data.get("sentence_translation", "")).strip():
        issues.append({"checker": "local", "field": "sentence_translation", "problem": "缺少原句翻译", "expected": "翻译完整原句"})
    core = data.get("core_meanings", [])
    first = core[0] if isinstance(core, list) and core and isinstance(core[0], dict) else {}
    if not first or _is_placeholder_meaning(first.get("usage", "")):
        issues.append({"checker": "local", "field": "core_meanings[0].usage", "problem": "核心释义为空或占位", "expected": "给出首个、贴合语境的中文释义"})
    if first and not str(first.get("definition_en", "")).strip():
        issues.append({"checker": "local", "field": "core_meanings[0].definition_en", "problem": "首个核心释义缺少英文定义", "expected": "给出准确英文定义"})
    if not is_multiword_entry(entry.word) and not str(data.get("phonetic", "")).strip():
        issues.append({"checker": "local", "field": "phonetic", "problem": "单词缺少英式 IPA", "expected": "补充该词英式 IPA"})
    return {"passed": not issues, "severity": "blocking" if issues else "pass", "issues": issues, "fix_instructions": [i["expected"] for i in issues]}


async def run_quality_check(kind: str, entry: "WordEntry", data: dict, sem: asyncio.Semaphore) -> Optional[dict]:
    word_key = entry.word.lower()
    payload = {
        word_key: {
            "source_word": entry.word,
            "source_sentence": entry.sentence,
            "is_multiword_entry": is_multiword_entry(entry.word),
            "enrichment": data,
        }
    }
    messages = [
        {"role": "system", "content": f"你是 IELTS 词汇卡质量检查子代理：{kind}。只输出 JSON。"},
        {"role": "user", "content": build_quality_check_prompt(kind, payload)},
    ]
    return await llm.llm_chat(messages, temperature=0.1, max_tokens=4096, sem=sem)


def build_quality_fix_prompt(entry: "WordEntry", data: dict, report: dict) -> str:
    """构建质检修复 prompt（提示词外置于 prompts/fix.md，占位符 str.replace）。"""
    return (
        prompts.get_prompt("fix")
        .replace("{word}", entry.word)
        .replace("{sentence}", entry.sentence)
        .replace("{issues_json}", json.dumps(report.get("issues", []), ensure_ascii=False, indent=2))
        .replace("{fix_instructions_json}", json.dumps(report.get("fix_instructions", []), ensure_ascii=False, indent=2))
        .replace("{data_json}", json.dumps(data, ensure_ascii=False, indent=2))
        .strip()
    )


async def fix_enrichment_with_quality_issues(
    entry: "WordEntry", data: dict, report: dict, audio_filename: str, sem: asyncio.Semaphore
) -> Optional[dict]:
    messages = [
        {"role": "system", "content": "你是英语词汇卡修复专家。请只输出一个 JSON 对象。"},
        {"role": "user", "content": build_quality_fix_prompt(entry, data, report)},
    ]
    fixed = await llm.llm_chat(messages, temperature=0.2, max_tokens=config.BATCH_MAX_TOKENS, sem=sem)
    if not isinstance(fixed, dict):
        return None
    key = entry.word.lower()
    if key in fixed and isinstance(fixed[key], dict):
        fixed = fixed[key]
    elif "item" in fixed and isinstance(fixed["item"], dict):
        fixed = fixed["item"]
    importance = Importance.from_str(fixed.get("importance", data.get("importance", "中")))
    return validate_and_fix_enrichment(fixed, entry.word, importance, audio_filename)


async def ensure_quality_for_entry(
    entry: "WordEntry", data: dict, audio_filename: str, sem: asyncio.Semaphore
) -> tuple[Optional[dict], Optional[str], int, dict]:
    """运行本地检查 + 两个 checker agents；必要时用 fixer 重试。"""
    if not config.QUALITY_CHECKS_ENABLED:
        data["_quality_status"] = "disabled"
        return data, None, 0, {"passed": True, "severity": "pass", "issues": []}

    attempts = 0
    current = data
    last_report: dict = {"passed": False, "severity": "blocking", "issues": []}
    while attempts <= max(0, config.QUALITY_MAX_ATTEMPTS):
        local_report = local_quality_report(entry, current)
        accuracy_report, completeness_report = await asyncio.gather(
            run_quality_check("accuracy", entry, current, sem),
            run_quality_check("completeness", entry, current, sem),
        )
        merged = merge_quality_reports(accuracy_report, completeness_report, entry.word.lower())
        if not local_report["passed"]:
            merged["passed"] = False
            merged["severity"] = "blocking"
            merged["issues"] = local_report.get("issues", []) + merged.get("issues", [])
            merged["fix_instructions"] = local_report.get("fix_instructions", []) + merged.get("fix_instructions", [])
        last_report = merged
        if merged.get("passed") and not merged.get("issues"):
            current["_quality_status"] = "passed"
            current["_quality_schema_version"] = config.QUALITY_SCHEMA_VERSION
            current["_quality_attempts"] = attempts
            current["_quality_checked_at"] = datetime.now(timezone.utc).isoformat()
            current["_quality_summary"] = "passed"
            return current, None, attempts, merged

        if attempts >= max(0, config.QUALITY_MAX_ATTEMPTS):
            break
        logger.warning(f"质量检查未通过，尝试修复 '{entry.word}': {summarize_quality_issues(merged)}")
        fixed = await fix_enrichment_with_quality_issues(entry, current, merged, audio_filename, sem)
        attempts += 1
        if fixed is None:
            break
        current = fixed

    reason = summarize_quality_issues(last_report)
    current["_quality_status"] = "failed"
    current["_quality_schema_version"] = config.QUALITY_SCHEMA_VERSION
    current["_quality_attempts"] = attempts
    current["_quality_summary"] = reason
    return None, reason, attempts, last_report


def quality_metadata_ok(data: dict) -> bool:
    if not config.QUALITY_CHECKS_ENABLED:
        return True
    return (
        isinstance(data, dict)
        and data.get("_quality_status") == "passed"
        and data.get("_quality_schema_version") == config.QUALITY_SCHEMA_VERSION
    )
