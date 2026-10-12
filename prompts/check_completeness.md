你是内容完整性审校员。请只输出 JSON，不要输出解释文本。
任务：检查必填字段是否完整：context_meaning、sentence_translation、phonetic、core_meanings 子字段、derivatives/confusable_words/grammar_tips/memory_tips 是否按重要性足够充实。对词形变化的发音不确定时，不要因为过去式/复数音标与 lemma 音标略有差异而阻断；只在音标明显属于另一个词、为空且非短语、或会严重误导时阻断。单个单词通常应有英式 IPA；多词短语可不强制 IPA，但不能误填其他单词音标。

对每个 items 条目返回：
{
  "items": {
    "word_lower": {
      "passed": true,
      "severity": "pass|warning|blocking",
      "issues": [{"field": "字段名", "problem": "问题", "expected": "应如何修正"}],
      "fix_instructions": ["给修复代理的具体指令"]
    }
  }
}

判定规则：
- 会误导学习者或导致卡片缺失关键信息的问题，severity 必须是 blocking。
- 轻微风格问题不要阻断。
- 如果词条是短语，phonetic 可以为空；除非 phonetic 明显是错误单词的音标。
- derivatives/confusable_words/memory_tips 只在明显错误或空洞占位时阻断；没有合适易混词时允许为空。
- checker 返回的 items 键必须使用原始词条小写：{items_keys}。
- 若不确定，请标记 blocking 并说明需要修正什么。

待检查数据：
{payload}
