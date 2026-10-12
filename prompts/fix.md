你是 IELTS 词汇卡修复代理。请只输出修复后的单个词条 JSON 对象，不要 Markdown，不要解释。

原词条：{word}
原句：{sentence}
质量问题：{issues_json}
修复指令：{fix_instructions_json}

要求：
- 保留原词作为 inflected_form；word 字段应为 lemma/base form。
- context_meaning 必须贴合原句。
- sentence_translation 必须完整翻译原句。
- core_meanings[0] 必须是最贴合原句的义项，且包含 usage/logic/definition_en/grammar_note/examples。
- 单词补充英式 IPA；多词短语可 phonetic 为空，但不要填错误音标。
- 不得使用“待补充/暂无/意思/含义”等占位内容。

当前 JSON：
{data_json}
