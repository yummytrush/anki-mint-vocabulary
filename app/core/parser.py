"""Markdown 解析 — 三种输入格式自动检测 + 纯单词模式 + 解析预检（Task 4 扩展）。

- parse_markdown：原有入口，行为与单文件版逐字一致（纯单词检测关闭）。
- detect_format(text)：检测 single_line / two_line / colon / word_list / unknown。
- parse_preview(path)：返回 PreviewResult（条目 + 格式 + 问题行定位），
  供 GUI 预检与主流程使用；支持纯单词列表（每行 ≤3 个 token 且无句号）。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from app.core.models import WordEntry

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. Markdown 解析
# ===========================================================================
# 支持三种输入格式（自动检测）：
#
#   格式 A — 单行紧凑（原始设计）:
#     1. analyze We need to analyze the data carefully.
#
#   格式 B — 双行 Markdown（Obsidian/标准）:
#     1. **assessment**
#        > The government set up a Research Assessment Exercise.
#
#   格式 C — 粗体词条 + 冒号分隔:
#     1. **assessment**: The government set up a Research Assessment Exercise.
#
#   格式 D — 纯单词列表（Task 4 新增，detect_format/parse_preview 识别）:
#     initiate
#     leverage 杠杆
#
# 关键假设：
#   - 序号后紧跟 ". " 或 ". **"
#   - 格式 B 中，例句行以 ">" 开头（去掉引用标记后作为例句正文）
#   - 例句中一定包含该单词（不区分大小写）；解析失败仅 warn 不阻塞
# 潜在风险：
#   - 格式 B 的例句引用了文中其他粗体（如 **27**），在清洗时保留原样
#   - 若词条有尾随序号如 "**27** ______"，不在此解析器处理范围

# 匹配单行: "数字. 内容" 或 "数字. **内容**"
LINE_A = re.compile(r'^(\d+)\.\s+(.+)$')
# 匹配双行词条行: "数字. **单词**"（允许词条含空格/连字符/标点）
LINE_B_WORD = re.compile(r'^(\d+)\.\s+\*\*(.+?)\*\*\s*$')
# 匹配双行例句行: "> 内容"（允许0-2个空格缩进）
LINE_B_SENTENCE = re.compile(r'^\s*>\s+(.+)$')
# 匹配粗体+冒号: "数字. **单词**: 例句"
LINE_C = re.compile(r'^(\d+)\.\s+\*\*(.+?)\*\*[:\s]+(.+)$')
# 匹配行首列表编号: "1. " / "1、" / "1)" 以及 Markdown 圆点/星号列表
LIST_NUMBER = re.compile(r'^\s*(?:\d+\s*[.、)）]|[-*])\s+')

# 纯单词判定：每行最多 token 数；出现句末标点即视为句子
WORD_LIST_MAX_TOKENS = 3
_SENTENCE_PUNCT = ("。", "．", ".", "!", "！", "?", "？")

# 格式标识 → 中文显示名（GUI 预检展示用）
FORMAT_LABELS = {
    "single_line": "单行式",
    "two_line": "两行式",
    "colon": "冒号式",
    "word_list": "纯单词列表",
    "unknown": "未知",
}


@dataclass
class ProblemLine:
    """解析预检发现的问题行。"""
    lineno: int      # 1 起始行号
    text: str        # 行原文（已 strip）
    reason: str      # 未能识别的原因


@dataclass
class PreviewResult:
    """parse_preview 的结果：条目、格式判定、问题行与跳过计数。"""
    entries: list[WordEntry]
    detected_format: str
    problem_lines: list[ProblemLine] = field(default_factory=list)
    skipped_count: int = 0


def _strip_list_number(line: str) -> str:
    """去掉行首列表编号（1. / 1、 / 1) / - / *），供纯单词判定与解析。"""
    return LIST_NUMBER.sub("", line, count=1)


def _is_word_like(line: str) -> bool:
    """纯单词行判定：非空、≤3 个 token、不含句末标点（可带中文提示）。"""
    tokens = line.split()
    if not tokens or len(tokens) > WORD_LIST_MAX_TOKENS:
        return False
    return not any(ch in line for ch in _SENTENCE_PUNCT)


def _looks_like_word_list(raw_lines: list[str]) -> bool:
    """全部内容行都是纯单词行 → 纯单词列表（普通句子行存在即排除）。"""
    content = 0
    word_like = 0
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---" or stripped.startswith(">"):
            continue
        content += 1
        if _is_word_like(_strip_list_number(stripped)):
            word_like += 1
    return content > 0 and word_like == content


def detect_format(text: str) -> str:
    """检测文本格式（不读文件、不写日志）。返回 FORMAT_LABELS 的键。"""
    return _parse_lines(text.splitlines(), allow_word_list=True, log=False)[3]


def parse_preview(file_path: str, force_format: Optional[str] = None) -> PreviewResult:
    """
    解析预检：返回条目、格式判定与问题行定位。

    与 parse_markdown 输出兼容（同一解析核心），并额外支持纯单词列表：
    条目的 sentence 字段保存行内中文提示（无则为空字符串）。
    force_format: GUI 手动覆盖格式（two_line/single_line/colon/word_list），
    None 时自动检测（行为与现状一致）。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()
    entries, skipped, problems, detected = _parse_lines(
        raw_lines, allow_word_list=True, forced_format=force_format
    )
    return PreviewResult(
        entries=entries,
        detected_format=detected,
        problem_lines=problems,
        skipped_count=skipped,
    )


def parse_markdown(file_path: str) -> list[WordEntry]:
    """
    解析 Markdown 词库文件，自动检测格式 A / B / C。

    检测逻辑：
      先逐行扫描，若发现 "数字. **单词**" 行且下一行以 ">" 开头，
      则判定为格式 B（双行）；否则回退为格式 A/C 混合解析。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()
    entries, _skipped, _problems, _detected = _parse_lines(raw_lines, allow_word_list=False)
    return entries


def _parse_lines(
    raw_lines: list[str],
    allow_word_list: bool,
    log: bool = True,
    forced_format: Optional[str] = None,
) -> tuple[list[WordEntry], int, list[ProblemLine], str]:
    """
    共享解析核心：返回 (entries, skipped, problem_lines, detected_format)。

    parse_markdown（allow_word_list=False）与 parse_preview/detect_format 共用；
    日志消息与原 parse_markdown 保持逐字一致。
    forced_format 为 None 时自动检测；给定值时强制按该格式解析（GUI 覆盖）。
    """
    entries: list[WordEntry] = []
    skipped = 0
    warnings: list[str] = []
    problems: list[ProblemLine] = []

    # ---- 检测主要格式 ----
    format_b_lines = 0
    format_a_lines = 0
    format_c_lines = 0
    content_lines = 0
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        content_lines += 1
        if LINE_B_WORD.match(stripped):
            format_b_lines += 1
        elif LINE_A.match(stripped):
            # 与原实现一致：LINE_C 行也匹配 LINE_A，计入 format_a_lines
            format_a_lines += 1
            if LINE_C.match(stripped):
                format_c_lines += 1

    use_format_b = format_b_lines > format_a_lines
    if forced_format in ("two_line", "single_line", "colon", "word_list"):
        detected = forced_format
    elif content_lines == 0:
        detected = "unknown"
    elif use_format_b:
        detected = "two_line"
    elif allow_word_list and _looks_like_word_list(raw_lines):
        detected = "word_list"
    elif format_c_lines > 0 and format_c_lines >= format_a_lines - format_c_lines:
        detected = "colon"
    else:
        detected = "single_line"

    if log:
        if forced_format in ("two_line", "single_line", "colon", "word_list"):
            logger.info(
                f"格式检测: A类行={format_a_lines}, B类行={format_b_lines} → "
                f"手动指定为 {FORMAT_LABELS[forced_format]}（{forced_format}）"
            )
        elif detected == "word_list":
            logger.info(
                f"格式检测: A类行={format_a_lines}, B类行={format_b_lines} → "
                f"使用纯单词列表（每行 ≤{WORD_LIST_MAX_TOKENS} 词且无句号）"
            )
        else:
            logger.info(
                f"格式检测: A类行={format_a_lines}, B类行={format_b_lines} → "
                f"使用{'格式B（双行）' if use_format_b else '格式A/C（单行）'}"
            )

    # ---- 格式 D: 纯单词列表 ----
    if detected == "word_list":
        seq = 0
        for lineno, raw in enumerate(raw_lines, 1):
            line = raw.strip()
            # 跳过空行/注释/分隔线/引用行
            if not line or line.startswith("#") or line == "---" or line.startswith(">"):
                continue
            m_num = re.match(r'^\s*(\d+)\s*[.、)）]', line)
            body = _strip_list_number(line)
            if not _is_word_like(body):
                logger.warning(f"[行 {lineno}] 非纯单词行，跳过: {line[:60]}")
                problems.append(ProblemLine(lineno, line, "纯单词列表中无法识别的行"))
                skipped += 1
                continue
            tokens = body.split()
            word = tokens[0]
            hint = " ".join(tokens[1:])
            index = int(m_num.group(1)) if m_num else seq + 1
            entries.append(WordEntry(index=index, word=word, sentence=hint))
            seq += 1

        if log:
            logger.info(
                f"解析完成: {len(entries)} 个条目, 跳过 {skipped} 行, "
                f"{len(warnings)} 个警告"
            )
        return entries, skipped, problems, detected

    # ---- 格式 B: 双行解析 ----
    if detected == "two_line":
        i = 0
        while i < len(raw_lines):
            line = raw_lines[i].strip()

            # 跳过空行/注释/分隔线
            if not line or line.startswith("#") or line == "---":
                i += 1
                continue

            # 匹配 "数字. **单词**"
            m_word = LINE_B_WORD.match(line)
            if not m_word:
                logger.warning(f"[行 {i + 1}] 非词条行，跳过: {line[:60]}")
                problems.append(ProblemLine(i + 1, line, "非词条行（两行式词条应为 '数字. **单词**'）"))
                i += 1
                skipped += 1
                continue

            index = int(m_word.group(1))
            word = m_word.group(2).strip()

            # 下一行应为 "> 例句"
            i += 1
            if i >= len(raw_lines):
                logger.warning(f"[{index}] {word}: 缺少例句行，跳过")
                problems.append(ProblemLine(i, line, "缺少例句行"))
                skipped += 1
                break

            m_sent = LINE_B_SENTENCE.match(raw_lines[i].strip())
            if m_sent:
                sentence = m_sent.group(1).strip()
                # 清洗：移除例句内嵌的粗体标记（如 **27** → 27）
                sentence = re.sub(r'\*\*(.+?)\*\*', r'\1', sentence)
            else:
                # 无 ">" 前缀，尝试整行作为例句
                sentence = raw_lines[i].strip()
                logger.warning(
                    f"[{index}] {word}: 例句行无 '>' 前缀，取整行"
                )

            if word.lower() not in sentence.lower():
                msg = f"[{index}] 单词 '{word}' 似不在例句中: '{sentence[:50]}...'"
                warnings.append(msg)
                logger.warning(msg)

            entries.append(WordEntry(index=index, word=word, sentence=sentence))
            i += 1

        if log:
            logger.info(
                f"解析完成: {len(entries)} 个条目, 跳过 {skipped} 行, "
                f"{len(warnings)} 个警告"
            )
        return entries, skipped, problems, detected

    # ---- 格式 A / C: 单行解析 ----
    for lineno, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or line == "---":
            continue

        # 先尝试格式 C: "数字. **单词**: 例句"
        m_c = LINE_C.match(line)
        if m_c:
            index = int(m_c.group(1))
            word = m_c.group(2).strip()
            sentence = m_c.group(3).strip()
            if word.lower() not in sentence.lower():
                msg = f"[行 {lineno}] 单词 '{word}' 似不在例句中"
                warnings.append(msg)
                logger.warning(msg)
            entries.append(WordEntry(index=index, word=word, sentence=sentence))
            continue

        # 再尝试格式 A: "数字. 单词 例句"
        m_a = LINE_A.match(line)
        if not m_a:
            # 可能是格式 B 的残余行（只有词条无例句）
            if LINE_B_WORD.match(line):
                logger.warning(f"[行 {lineno}] 检测到孤立词条行，无对应例句，跳过")
                problems.append(ProblemLine(lineno, line, "孤立词条行，无对应例句"))
            else:
                logger.warning(f"[行 {lineno}] 无法解析，跳过: {line[:60]}")
                problems.append(ProblemLine(lineno, line, "无法解析（不是以数字编号开头的词条行）"))
            skipped += 1
            continue

        index = int(m_a.group(1))
        body = m_a.group(2)

        # 空格扫描智能拆分
        spaces = [i for i, ch in enumerate(body) if ch == ' ']
        word, sentence = None, None
        for sp in spaces:
            w_cand = body[:sp]
            s_cand = body[sp + 1:]
            if w_cand.lower() in s_cand.lower():
                word, sentence = w_cand, s_cand

        if word is None and spaces:
            first_sp = spaces[0]
            word = body[:first_sp]
            sentence = body[first_sp + 1:]
        elif word is None:
            logger.warning(f"[行 {lineno}] 无法拆分，跳过: {body[:60]}")
            problems.append(ProblemLine(lineno, line, "无法拆分单词与例句"))
            skipped += 1
            continue

        if word.lower() not in sentence.lower():
            msg = f"[行 {lineno}] 单词 '{word}' 似不在例句中: '{sentence[:50]}...'"
            warnings.append(msg)
            logger.warning(msg)

        entries.append(WordEntry(index=index, word=word, sentence=sentence))

    if log:
        logger.info(
            f"解析完成: {len(entries)} 个条目, 跳过 {skipped} 行, "
            f"{len(warnings)} 个警告"
        )
    return entries, skipped, problems, detected
