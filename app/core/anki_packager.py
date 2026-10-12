"""Anki 打包 — genanki 模型、正面填空构建、背面 HTML 构建、.apkg 生成（原文搬运）。

模板与 CSS 在 Task 2 外置到 templates/；此文件先内置原文。
"""

import logging
import random
import re
import sys
from pathlib import Path
from typing import Optional

from app.core import cache, config, templates
from app.core.models import WordEntry

logger = logging.getLogger(__name__)

try:
    import genanki
except ImportError:
    print("[ERROR] genanki 未安装。fix: pip install genanki")
    sys.exit(1)


# ===========================================================================
# 5. 正面填空题构建
# ===========================================================================
def build_cloze_front(word: str, sentence: str, replace_all: bool = False) -> str:
    """
    构建 Anki 填空正面。

    在例句中查找单词/词组（忽略大小写），替换为 {{c1::单词}}。
    replace_all=False: 仅替换首次出现
    replace_all=True: 替换所有出现

    关键假设：
      - 首次替换使用不区分大小写的正则
      - Anki 填空标记 {{c1::...}} 中的内容保留原始大小写
      - 若单词未在例句中找到，在句首添加提示
    """
    # 尝试找到单词在原句中的位置和形式
    pattern = re.compile(re.escape(word), re.IGNORECASE)

    if not pattern.search(sentence):
        logger.warning(f"单词 '{word}' 未在例句中找到，添加前缀提示")
        return f"[缺词: {word}] {sentence}"

    if replace_all:
        result = pattern.sub(lambda m: "{{c1::" + m.group(0) + "}}", sentence)
    else:
        result = pattern.sub(lambda m: "{{c1::" + m.group(0) + "}}", sentence, count=1)

    return result


# ===========================================================================
# 6. 背面 HTML 构建
# ===========================================================================
def build_back_html(data: dict, sentence: str = "", sentence_translation: str = "") -> str:
    """
    将富化 JSON 转换为美观的 HTML 背面内容。

    分层结构:
      - 原句 + 中文翻译 (顶部)
      - 词头 (word + phonetic + importance badge)
      - 音频播放器
      - 词源与词根
      - 核心释义 (表格)
      - 衍生词 (列表)
      - 易混淆词 (列表)
      - 语法要点 (列表)
      - 终极记忆技巧 (高亮框)

    关键假设：
      - Anki WebView 支持 CSS3 基础特性（flex, border-radius, gradients）
      - 音频通过 Anki 内置 [sound:...] 语法播放
      - 所有用户内容经过 HTML 转义防止破坏
    """

    def esc(text: Optional[str]) -> str:
        """HTML 转义"""
        if text is None:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("\n", "<br>")
        )

    def safe(text: Optional[str]) -> str:
        """返回安全字符串，None → 空"""
        return str(text) if text is not None else ""

    word = esc(data.get("word", ""))
    phonetic = esc(data.get("phonetic", ""))
    importance = safe(data.get("importance", ""))
    audio_filename = safe(data.get("audio_filename", ""))

    # 重要性徽章颜色
    badge_colors = {
        "极高": "#c0392b",
        "高": "#e67e22",
        "中": "#2980b9",
        "低": "#95a5a6",
    }
    badge_color = badge_colors.get(importance, "#95a5a6")

    parts = []

    # --- 原句 + 中文翻译（顶部） ---
    if sentence:
        sent_trans = esc(sentence_translation) if sentence_translation else ""
        parts.append(f"""<div class="sentence-section">
  <div class="sentence-original">{esc(sentence)}</div>
  {f'<div class="sentence-translation">📝 {sent_trans}</div>' if sent_trans else ""}
</div>""")

    # --- 词头 ---
    parts.append(f"""<div class="word-header">
  <span class="word-title">{word}</span>
  <span class="phonetic">{phonetic}</span>
  <span class="importance-badge" style="background:{badge_color}">{importance}</span>
</div>""")

    # --- 音频 ---
    if audio_filename and audio_filename.startswith("[sound:"):
        parts.append(f'<div class="audio-section">{audio_filename}</div>')

    # --- 词源 ---
    etymology = data.get("etymology")
    if etymology:
        parts.append(f"""<div class="section etymology-section">
  <h2>📖 词源与词根</h2>
  <p>{esc(etymology)}</p>
</div>""")

    # --- 核心释义 ---
    core_meanings = data.get("core_meanings", [])
    if core_meanings:
        rows = []
        for i, cm in enumerate(core_meanings, 1):
            usage = esc(cm.get("usage", ""))
            logic = esc(cm.get("logic", ""))
            def_en = esc(cm.get("definition_en", ""))
            grammar = esc(cm.get("grammar_note", ""))
            examples_html = "<br>".join(
                f'<span class="example">{esc(ex)}</span>'
                for ex in cm.get("examples", [])
            )
            rows.append(f"""    <tr>
      <td class="cm-index">{i}</td>
      <td class="cm-content">
        <div class="cm-usage">{usage}</div>
        <div class="cm-en">{def_en}</div>
        <div class="cm-logic">🧠 {logic}</div>
        <div class="cm-grammar">📝 {grammar}</div>
        <div class="cm-examples">{examples_html}</div>
      </td>
    </tr>""")

        parts.append(f"""<div class="section core-meanings-section">
  <h2>📚 核心释义</h2>
  <table class="cm-table">
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
</div>""")

    # --- 衍生词 ---
    derivatives = data.get("derivatives", [])
    if derivatives:
        items = []
        for d in derivatives:
            dw = esc(d.get("word", ""))
            dpos = esc(d.get("pos", ""))
            dmean = esc(d.get("meaning", ""))
            dex = esc(d.get("example", ""))
            items.append(
                f'<li><strong>{dw}</strong> <span class="pos">{dpos}</span> — {dmean} <span class="example">{dex}</span></li>'
            )
        parts.append(f"""<div class="section derivatives-section">
  <h2>🔗 衍生词</h2>
  <ul class="derivatives-list">
    {chr(10).join(items)}
  </ul>
</div>""")

    # --- 易混淆词 ---
    confusable = data.get("confusable_words", [])
    if confusable:
        items = []
        for cw in confusable:
            cwords = esc(cw.get("words", ""))
            cdiff = esc(cw.get("difference", ""))
            items.append(
                f'<li><strong>{cwords}</strong><br><span class="diff">{cdiff}</span></li>'
            )
        parts.append(f"""<div class="section confusable-section">
  <h2>⚠️ 易混淆词</h2>
  <ul class="confusable-list">
    {chr(10).join(items)}
  </ul>
</div>""")

    # --- 语法要点 ---
    grammar_tips = data.get("grammar_tips", [])
    if grammar_tips:
        items = [f"<li>{esc(tip)}</li>" for tip in grammar_tips]
        parts.append(f"""<div class="section grammar-section">
  <h2>📝 语法 / 易错点</h2>
  <ul class="grammar-list">
    {chr(10).join(items)}
  </ul>
</div>""")

    # --- 终极记忆技巧 ---
    memory_tips = data.get("memory_tips")
    if memory_tips:
        parts.append(f"""<div class="memory-tip">
  <h2>💡 终极记忆技巧</h2>
  <p>{esc(memory_tips)}</p>
</div>""")

    return "\n".join(parts)


# ===========================================================================
# 8. Anki 卡组生成
# ===========================================================================
# 卡组默认名（GUI/CLI 可用 --deck-name / 卡组名称输入框自定义，Task 6）
DEFAULT_DECK_NAME = "AI 词汇强化训练"


def create_anki_package(
    entries: list[WordEntry],
    enrichment_data: dict[str, dict],
    audio_map: dict[str, str],
    force_model_id: Optional[int] = None,
    media_dir: Path = config.MEDIA_DIR,
    deck_name: Optional[str] = None,
) -> genanki.Package:
    """
    生成 Anki .apkg 卡组包。

    参数:
      entries: 单词条目列表
      enrichment_data: {word_lower: enrichment_json}
      audio_map: {word_lower: audio_filename}
      force_model_id: 可选固定 Model ID（用于增量重用同一模型）

    返回: genanki.Package 实例

    关键假设：
      - genanki 会自动处理媒体文件路径映射
      - 模型 ID 使用随机生成（范围 1<<30 ~ 1<<31 避免冲突）
      - 每个笔记的 guid 使用序号 + 单词的 hash 确保唯一性
    """
    if force_model_id is None:
        force_model_id = random.randrange(1 << 30, 1 << 31)

    # --- 加载卡片模板（templates/ 外置；缺失回退内置默认） ---
    tpl = templates.load_card_templates()

    # --- 创建模型 ---
    model = genanki.Model(
        model_id=force_model_id,
        name="AI 词汇强化填空",
        fields=[
            {"name": "序号"},
            {"name": "正面"},
            {"name": "背面"},
            {"name": "提示"},
            {"name": "原句翻译"},
        ],
        templates=[
            {
                "name": "填空卡",
                "qfmt": tpl.qfmt,
                "afmt": tpl.afmt,
            },
        ],
        css=tpl.css,
    )

    # --- 创建卡组 ---
    deck = genanki.Deck(
        deck_id=random.randrange(1 << 30, 1 << 31),
        name=(deck_name or "").strip() or DEFAULT_DECK_NAME,
        description="由 AnkiMint 自动生成 — AI 富化雅思词汇填空卡组",
    )

    # --- 媒体文件列表：只打包当前卡片实际引用且非空的音频 ---
    media_files: list[str] = []
    referenced_media: set[str] = set()

    # --- 添加笔记 ---
    added_count = 0
    for entry in entries:
        word_key = entry.word.lower()
        rich = enrichment_data.get(word_key)
        if rich is None:
            logger.warning(f"单词 '{entry.word}' 没有富化数据，跳过卡片生成")
            continue

        # 正面填空
        front = build_cloze_front(entry.word, entry.sentence, config.REPLACE_ALL)

        # 中文提示（使用 LLM 给出的上下文相关释义）
        hint = rich.get("context_meaning", "")
        if not hint:
            # 回退：从 core_meanings 提取第一项的 usage
            core_meanings = rich.get("core_meanings", [])
            if core_meanings:
                hint = core_meanings[0].get("usage", "")

        # 原句中文翻译
        sent_trans = rich.get("sentence_translation", "")

        # 背面 HTML（带上原句和翻译）
        audio_ref = rich.get("audio_filename", "")
        audio_file = cache.extract_sound_filename(audio_ref)
        if audio_file and cache.audio_file_is_valid(audio_file, media_dir):
            referenced_media.add(audio_file)
        elif audio_file:
            logger.warning(f"单词 '{entry.word}' 的音频文件缺失或为空，已从卡片中移除引用: {audio_file}")
            rich = {**rich, "audio_filename": ""}
        back = build_back_html(rich, entry.sentence, sent_trans)

        # GUID: 序号 + 单词 hash 确保唯一
        guid = genanki.guid_for(entry.index, entry.word)

        note = genanki.Note(
            model=model,
            fields=[str(entry.index), front, back, hint, sent_trans],
            tags=["AI-enriched"],
            guid=guid,
        )
        deck.add_note(note)
        added_count += 1

    logger.info(f"已添加 {added_count} 张卡片到卡组")

    for fname in sorted(referenced_media):
        path = cache.resolve_audio_file(fname, media_dir)
        if path and path.suffix.lower() in (".mp3", ".wav", ".ogg"):
            media_files.append(str(path))

    # --- 创建包 ---
    package = genanki.Package(deck)
    package.media_files = media_files

    return package
