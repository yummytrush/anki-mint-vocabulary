你是一名英语词汇学专家。请为以下每个单词/词组评估考试重要性，并生成结构化词汇知识。

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

仅输出 JSON，不要 markdown 包裹。
