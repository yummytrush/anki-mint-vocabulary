"""提示词加载器 — prompts/ 目录外置 + 内置默认回退（Task 2）。

机制：
  - DEFAULT_PROMPTS 内置全部提示词的默认副本（现有 BATCH_PROMPT / 两个质检 /
    修复提示词逐字录入，新提示词为本任务新增），作为回退与"恢复默认"来源。
  - load_prompt(name) 读 prompts/<name>.md；缺失/为空回退默认并写 warning 日志。
  - 模块导入时全量加载一次；reload_prompt(name) 供热加载（提示词页保存后调用）。
  - 占位符沿用 str.replace()（不用 .format()——提示词 JSON 示例含花括号）。
    占位符清单见 prompts/README.md。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "prompts"


# ===========================================================================
# 内置默认提示词（回退来源；与历史行为逐字一致）
# ===========================================================================
DEFAULT_ENRICH_PROMPT = """你是一名英语词汇学专家。请为以下每个单词/词组评估考试重要性，并生成结构化词汇知识。

{word_list}

## 重要：词形还原
部分单词在原句中可能是时态/复数/变形形式（如 initiated, enquiries, pressurising）。
请识别并还原为**原形/词条形**（initiate, enquiry, pressurise），将还原后的词作为 `word` 字段的值。
原句中出现的变形形式记录在 `inflected_form` 字段中。

## 要求
对每个单词还原后评估重要性（极高/高/中/低），并生成以下字段:
- input_index(输入序号), input_word(输入原词形，用于核对归属)
- word(使用还原后的原形), inflected_form(原句中的变形形式，若无变形则与word相同)
- context_meaning(该词在原句上下文中的中文意思，贴合语境，非词典直译)
- phonetic(词条原形/词条形的英式 IPA 音标，不是变形形式的发音；不确定则返回空字符串，不要编造), importance, reason(评估理由≤15字)
- core_meanings(极高=4-5项,高=3-4项,中=2-3项,低=1-2项)
  每项: usage(中文释义), logic(引申逻辑), definition_en, grammar_note, examples(2-3个)
- etymology(极高/高=词源≤50字, 其他=null)
- derivatives(极高/高/中=2-4个{word,pos,meaning,example}, 低=[])
- confusable_words(极高/高=0-3组{words,difference}, 其他=[])
- grammar_tips(极高/高=1-3条, 其他=[])
- memory_tips(仅极高=记忆法, 其他=null)
- sentence_translation(原句的中文翻译，所有单词都需要)

## 质量要求
- core_meanings[0] 必须是该词在原句中的语境义；其它释义按 IELTS/学术阅读常见度排序。
- 禁止重复或近似重复释义，例如 ["重要", "重要的", "很重要"] 是不合格的。
- 若 lemma/词条形不确定，word 保留输入原词；不要为了“还原”而编造不存在的词。
- 中文翻译应自然、准确，保留原句逻辑关系，不要逐词硬译。
- 不要输出 “暂无详细释义 / 意思 / 含义 / 待补充” 等占位内容。
- 合格释义示例: ["关键的；至关重要的", "必不可少的"]。
- 不合格释义示例: ["重要", "重要的", "很重要"]。

## 词性格式要求
所有词性标注必须使用英文缩写+中文格式，例如:
  n. 名词 | v. 动词 | vt. 及物动词 | vi. 不及物动词 | adj. 形容词 | adv. 副词
  prep. 介词 | conj. 连词 | pron. 代词 | art. 冠词 | num. 数词 | int. 感叹词
  aux. 助动词 | modal. 情态动词 | det. 限定词
core_meanings 中的 grammar_note 和 derivatives 中的 pos 均须遵守此格式。

## 输出格式
返回 JSON 对象，使用**输入中的原词形（小写）**作为键，不要使用还原后的词形:
{{"原词1":{{"word":"还原后词形","inflected_form":"原词形",...}},
 "原词2":{{"word":"还原后词形","inflected_form":"原词形",...}}}}

## 拼写检查
请检查每个原词在例句上下文中是否可能是拼写错误（例如 scrapping 在"抓取数据"语境中应为 scraping；
writing 误拼为 writting；accommodation 误拼为 acomodation 等）。
若发现疑似拼写问题，在对应词条中增加 `_spelling_note` 字段（≤50字），说明可能正确的拼写及判断依据。
若无问题，则不输出该字段。

仅输出 JSON，不要 markdown 包裹。"""

# --- 质检 checker 提示词 ---
# 原实现以 f-string 在代码内拼接 role/focus；外置后拆为两个文件，
# 动态部分收敛为占位符 {items_keys} / {payload}，渲染结果与原版逐字一致。
_ACCURACY_ROLE = "语义准确性审校员"
_ACCURACY_FOCUS = (
    "检查 lemma/inflected_form 是否对应原词，context_meaning 与 core_meanings[0] 是否贴合原句，"
    "sentence_translation 是否忠实翻译原句，英文定义/例句是否与该词真实用法一致。"
)
_COMPLETENESS_ROLE = "内容完整性审校员"
_COMPLETENESS_FOCUS = (
    "检查必填字段是否完整：context_meaning、sentence_translation、phonetic、core_meanings 子字段、"
    "derivatives/confusable_words/grammar_tips/memory_tips 是否按重要性足够充实。"
    "对词形变化的发音不确定时，不要因为过去式/复数音标与 lemma 音标略有差异而阻断；"
    "只在音标明显属于另一个词、为空且非短语、或会严重误导时阻断。"
    "单个单词通常应有英式 IPA；多词短语可不强制 IPA，但不能误填其他单词音标。"
)


def _render_quality_check_template(role: str, focus: str) -> str:
    """按原 build_quality_check_prompt 的 f-string 结构渲染默认模板。"""
    keys_placeholder = "{items_keys}"
    payload_placeholder = "{payload}"
    return f"""
你是{role}。请只输出 JSON，不要输出解释文本。
任务：{focus}

对每个 items 条目返回：
{{
  "items": {{
    "word_lower": {{
      "passed": true,
      "severity": "pass|warning|blocking",
      "issues": [{{"field": "字段名", "problem": "问题", "expected": "应如何修正"}}],
      "fix_instructions": ["给修复代理的具体指令"]
    }}
  }}
}}

判定规则：
- 会误导学习者或导致卡片缺失关键信息的问题，severity 必须是 blocking。
- 轻微风格问题不要阻断。
- 如果词条是短语，phonetic 可以为空；除非 phonetic 明显是错误单词的音标。
- derivatives/confusable_words/memory_tips 只在明显错误或空洞占位时阻断；没有合适易混词时允许为空。
- checker 返回的 items 键必须使用原始词条小写：{keys_placeholder}。
- 若不确定，请标记 blocking 并说明需要修正什么。

待检查数据：
{payload_placeholder}
""".strip()


def _render_fix_template() -> str:
    """按原 build_quality_fix_prompt 的 f-string 结构渲染默认模板。"""
    return f"""
你是 IELTS 词汇卡修复代理。请只输出修复后的单个词条 JSON 对象，不要 Markdown，不要解释。

原词条：{{word}}
原句：{{sentence}}
质量问题：{{issues_json}}
修复指令：{{fix_instructions_json}}

要求：
- 保留原词作为 inflected_form；word 字段应为 lemma/base form。
- context_meaning 必须贴合原句。
- sentence_translation 必须完整翻译原句。
- core_meanings[0] 必须是最贴合原句的义项，且包含 usage/logic/definition_en/grammar_note/examples。
- 单词补充英式 IPA；多词短语可 phonetic 为空，但不要填错误音标。
- 不得使用“待补充/暂无/意思/含义”等占位内容。

当前 JSON：
{{data_json}}
""".strip()


# --- 新增提示词（Task 2 新写；Task 4 / GUI 接入） ---
DEFAULT_SENTENCE_GEN_PROMPT = """你是一名雅思备考教练。请为以下每个单词/词组创作一个雅思语境例句。

{word_list}

## 要求
- 每个词一个例句，使用输入的原词形，不要改变词形。
- 语境优先：教育、科技、环境、社会、职场、学术讨论等雅思高频话题。
- 句长适中（12-25 词），难度贴近雅思阅读/写作，不得滥用超纲词。
- 例句必须自然包含该词（不区分大小写），且词义清晰可辨。
- 同时给出例句的中文翻译，翻译应自然、忠实，保留原句逻辑关系。

## 输出格式
返回 JSON 对象，以输入单词（小写）为键：
{{"单词1":{{"sentence":"English sentence here.","translation":"对应中文翻译"}},
 "单词2":{{"sentence":"...","translation":"..."}}}}

仅输出 JSON，不要 markdown 包裹。"""

DEFAULT_FORMAT_ASSIST_PROMPT = """你是一名格式转换助手。请把用户提供的笔记文本转换为指定的标准格式。

目标格式：{target_format}

## 支持的标准格式
1. 单行式:      1. 单词 例句
2. 两行式:      1. **单词**
                   > 例句            （推荐；Obsidian 友好）
3. 冒号式:      1. **单词**: 例句
4. 纯单词列表:   每行一个单词（可选附带中文提示，如 initiate 发起）

## 要求
- 保留原文中的全部词条与例句信息，不要增删词条；无法判断的内容保留原样。
- 单词保持原样（不做词形还原）；例句保留原句标点与大小写。
- 保留原笔记编号；若无编号则从 1 开始重新编号。
- 只输出转换后的 Markdown 文本，不要解释、不要代码块包裹。

## 待转换文本
{text}"""

DEFAULT_ASSISTANT_PROMPT = """你是「AnkiMint」内置的 AI 助手。

## 身份限定
- 你只回答与本工具相关的问题：提示词（prompts/）的编写与优化、输入格式（四种标准格式）的转换建议、卡片模板（templates/）的样式调整，以及本工具的用法答疑。
- 你不能联网搜索，不能读取用户电脑上的其它文件，也看不到当前对话之外的内容。用户问及无关话题时，礼貌说明并引导回本工具。

## 输出规范
- 用中文回答，简洁、直接、可操作。
- 涉及提示词/模板修改时，给出**完整可整体替换的文件内容**，用 ``` 代码块包裹，并在代码块前注明目标文件名（如 prompts/enrich.md 或 templates/card.css）。
- 涉及占位符时说明其用法，占位符清单见 prompts/README.md。
- 不确定用户当前文件内容时，先请用户粘贴当前内容，再给修改建议。"""


DEFAULT_PROMPTS: dict[str, str] = {
    "enrich": DEFAULT_ENRICH_PROMPT,
    "check_accuracy": _render_quality_check_template(_ACCURACY_ROLE, _ACCURACY_FOCUS),
    "check_completeness": _render_quality_check_template(_COMPLETENESS_ROLE, _COMPLETENESS_FOCUS),
    "fix": _render_fix_template(),
    "sentence_gen": DEFAULT_SENTENCE_GEN_PROMPT,
    "format_assist": DEFAULT_FORMAT_ASSIST_PROMPT,
    "assistant": DEFAULT_ASSISTANT_PROMPT,
}


# ===========================================================================
# 加载与热加载
# ===========================================================================
_loaded: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """读取 prompts/<name>.md；缺失/读取失败/为空时回退内置默认并告警。"""
    default = DEFAULT_PROMPTS.get(name, "")
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        logger.warning(f"提示词文件缺失 {path}，回退内置默认: {name}")
        return default
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning(f"提示词文件读取失败 {path}: {e}，回退内置默认: {name}")
        return default
    if not text:
        logger.warning(f"提示词文件为空 {path}，回退内置默认: {name}")
        return default
    return text


def load_all_prompts() -> dict[str, str]:
    """全量加载（或重新加载）全部提示词到缓存。"""
    for name in DEFAULT_PROMPTS:
        _loaded[name] = load_prompt(name)
    return _loaded


def reload_prompt(name: str) -> str:
    """热加载单个提示词（提示词页保存后调用；下一次生成立即生效）。"""
    _loaded[name] = load_prompt(name)
    return _loaded[name]


def get_prompt(name: str) -> str:
    """取当前生效的提示词文本。"""
    if name not in _loaded:
        return reload_prompt(name)
    return _loaded[name]


def restore_default(name: str) -> str:
    """恢复默认：返回内置默认文本（调用方负责写回文件）。"""
    _loaded[name] = DEFAULT_PROMPTS.get(name, "")
    return _loaded[name]


# 模块导入时全量加载一次（启动加载）
load_all_prompts()
