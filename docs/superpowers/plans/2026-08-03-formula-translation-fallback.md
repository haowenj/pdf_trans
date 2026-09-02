# Formula Translation Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the enhanced formula-protection prompt the normal translation prompt and make formula-validation failures fall back to grouped complete-sentence translation without changing the normal one-request paragraph workflow.

**Architecture:** Keep `OpenAICompatibleTranslator._translate()` as the single place that builds the system and user messages. Replace only the shared base system prompt, and keep the retry instruction as an optional system-prompt suffix. Preserve the existing full-paragraph attempt and retry; after a formula validation failure, make the existing sentence packer force at least two grouped chunks when two or more complete sentences are available, then call each chunk with the strengthened formula instruction.

**Tech Stack:** Python 3.11+, pytest, existing `pdf_trans.translation` formula protection and translation outcome APIs, existing OpenAI-compatible HTTP client.

## Global Constraints

- Normal paragraph translation remains one full-paragraph model call and must not default to sentence-by-sentence translation.
- Only a paragraph that fails formula placeholder validation after the existing full retry may enter sentence splitting.
- Splitting must use complete English sentence boundaries and must never cut a formula placeholder.
- `FORMULA_SPLIT_MAX_CHARS = 600` and `FORMULA_SPLIT_MAX_FORMULAS = 4` remain upper bounds for grouped chunks.
- If at least two complete sentences are available but the current bounds would create one chunk, force at least two grouped chunks; do not create one request per sentence for a short three-or-more-sentence paragraph.
- A single-sentence paragraph remains unsplittable when formula validation keeps failing.
- Placeholder validation remains ID-based: required IDs, multiplicity, unknown IDs, and token integrity are checked; placeholder order is not a failure condition.
- Successful chunks retain their Chinese translation; only failed chunks fall back to their original English text.
- Existing page/object logging, status fields, retry count semantics, table translation, auxiliary translation, and formula restoration behavior remain unchanged except for the requested prompt and split-call behavior.

---

### Task 1: Replace the shared base translation prompt

**Files:**
- Modify: `/Users/wenjuhao/code/python/pdf_trans/src/pdf_trans/translation_client.py:11-25`
- Test: `/Users/wenjuhao/code/python/pdf_trans/tests/test_translation_client.py:260-320`

**Interfaces:**
- Consumes: existing `TRANSLATION_SYSTEM_PROMPT` import and `OpenAICompatibleTranslator._translate()` message construction.
- Produces: the same exported `TRANSLATION_SYSTEM_PROMPT` constant with explicit translation boundaries and formula-token rules; no method signature changes.

- [ ] **Step 1: Write the failing prompt-contract test**

Extend the existing system-prompt assertions in `test_translate_sends_required_system_prompt` so they require the new boundary language in addition to the existing preservation terms:

```python
    for required in (
        "完整翻译所有英文说明性内容",
        "公式占位符是不可翻译的原子字符串",
        "每个公式占位符只能出现一次",
        "占位符可以根据中文语序调整位置",
        "不得在占位符内部插入空格",
        "保留 HTML、LaTeX 和其他结构化标记的结构",
        "技术术语应结合化工论文语境翻译",
        "只输出译文",
    ):
        assert required in TRANSLATION_SYSTEM_PROMPT
```

Keep the existing `[38]`, `[39–41]`, `C-1`, `SS1`, and `$...$` assertions so the revised prompt cannot regress existing preservation requirements.

- [ ] **Step 2: Run the prompt test and verify the expected RED failure**

Run:

```bash
pytest tests/test_translation_client.py::test_translate_sends_required_system_prompt -q
```

Expected: FAIL because the current prompt does not contain the new explicit translation-boundary phrases, especially `完整翻译所有英文说明性内容` and `公式占位符是不可翻译的原子字符串`.

- [ ] **Step 3: Replace only the prompt constant**

Set `TRANSLATION_SYSTEM_PROMPT` to the following text and leave `_translate()`, `translate()`, and `translate_with_instruction()` unchanged:

```python
TRANSLATION_SYSTEM_PROMPT = """你是一名化工工程学术论文翻译助手。请将英文化工学术内容准确翻译为简体中文。

总体要求：
- 完整翻译所有英文说明性内容；
- 不总结、不删减、不改写、不补充；
- 保持原文的信息顺序、逻辑关系、数值和技术含义；
- 只输出译文；如果请求要求返回结构化 JSON，则只输出符合要求的 JSON，不输出解释。

不可变内容：
- 公式占位符是不可翻译的原子字符串，必须逐字保留；
- 每个公式占位符只能出现一次；
- 不得删除、复制、翻译、拆开、插入空格、插入文字或篡改公式占位符内部内容；
- 占位符可以根据中文语序调整位置，不要求保持原文中的先后位置；
- 保留引用编号，例如 [38]、[39–41]；
- 保留数字、单位、百分数、化学式、设备编号、控制回路编号、产品型号和必要的专有名词，例如 OG-200、JH-100、FIC0041、C-1；
- 保留 HTML、LaTeX 和其他结构化标记的结构；输入中可能包含 $...$ 形式的数学内容。

翻译边界：
- 除上述明确不可变内容外，输入中的英文自然语言内容都必须翻译成中文；
- 包含公式占位符的句子也必须翻译占位符周围的英文内容，占位符本身保持不变；
- 技术术语应结合化工论文语境翻译；不要因为术语邻近公式、设备编号或专有名词而保留整句英文；
- 只有明确属于设备编号、型号、控制回路编号、化学式、引用编号、单位或必要专有名词的内容才可以保留原文；
- 不要把普通技术术语默认当作不可翻译内容，也不要按脱离上下文的普通词义误译固定化工术语；
- 不要原样复制包含英文说明性内容的完整句子。
"""
```

The phrase “明确不可变内容” keeps model judgment limited to identifiers, established names, and domain terms rather than asking it to classify arbitrary “ordinary English sentences”.

- [ ] **Step 4: Run the client prompt and message-construction tests**

Run:

```bash
pytest tests/test_translation_client.py -q
```

Expected: PASS, including the existing assertion that a retry instruction is appended to the same base system prompt while the user message remains the translation content.

- [ ] **Step 5: Commit the prompt change**

```bash
git add src/pdf_trans/translation_client.py tests/test_translation_client.py
git commit -m "feat: strengthen translation system prompt"
```

### Task 2: Force grouped splitting for short multi-sentence failures

**Files:**
- Modify: `/Users/wenjuhao/code/python/pdf_trans/src/pdf_trans/translation.py:333-390`
- Test: `/Users/wenjuhao/code/python/pdf_trans/tests/test_translation.py:182-320`

**Interfaces:**
- Consumes: `_split_english_sentences()`, `_pack_formula_sentence_chunks()`, `FormulaProtectionContext`, and `ProtectedText`.
- Produces: `_build_formula_split_segments()` that returns at least two grouped segments for any protected text containing at least two complete sentences, even when the existing limits would otherwise produce one chunk; it returns no segments for one-sentence input.

- [ ] **Step 1: Write the failing short-paragraph regression test**

Add a test using three short sentences and three formulas. The initial full response removes one token, forcing the fallback; the current implementation returns no split segments because the paragraph is under both limits, so this test must fail before production code changes:

```python
def test_short_multi_sentence_formula_failure_uses_grouped_sentence_chunks(
    tmp_path,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = (
        "First sentence with $x$. Second sentence with $y$. "
        "Third sentence with $z$."
    )
    source.write_text(
        json.dumps([{"type": "text", "text": source_text, "page_idx": 12}]),
        encoding="utf-8",
    )
    translator = FormulaFailureThenSplitTranslator(
        lambda text: text.replace(FORMULA_TOKEN_RE.findall(text)[0], "", 1)
    )

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translation_status"] == "success"
    assert result["translated_text"] == (
        "第一句 with $x$. 第二句 with $y$. 第三句 with $z$."
    )
    assert len(translator.received) == 3
    assert len(translator.received[1:]) == 2
    assert all(
        len(FORMULA_TOKEN_RE.findall(chunk))
        == chunk.count("PDFTRANS_FORMULA")
        for chunk in translator.received[1:]
    )
    assert stats.model_call_count == 3
```

- [ ] **Step 2: Run the new test and verify the expected RED failure**

Run:

```bash
pytest tests/test_translation.py::test_short_multi_sentence_formula_failure_uses_grouped_sentence_chunks -q
```

Expected: FAIL with the existing final failure `公式占位符校验失败，无法按完整句子拆分`, and the translator receives only the full failed call.

- [ ] **Step 3: Add the minimum grouped fallback helper**

Add this helper immediately after `_pack_formula_sentence_chunks()`:

```python
def _force_grouped_sentence_chunks(sentences: list[str]) -> list[str]:
    if len(sentences) < 2:
        return []
    split_at = max(1, len(sentences) // 2)
    return [
        "".join(sentences[:split_at]),
        "".join(sentences[split_at:]),
    ]
```

Update `_build_formula_split_segments()` so the existing limit-based packing remains first, but a single packed chunk is replaced by the two grouped chunks only when two or more complete sentences exist:

```python
    sentences = _split_english_sentences(protected.model_text)
    chunks = _pack_formula_sentence_chunks(sentences)
    if len(chunks) <= 1:
        chunks = _force_grouped_sentence_chunks(sentences)
    if not chunks:
        return []
```

Leave the segment construction, per-segment formula ID extraction, and `context.restore()` calls unchanged. This preserves complete sentence boundaries and protects each token as an indivisible string.

- [ ] **Step 4: Run the new and existing split tests**

Run:

```bash
pytest tests/test_translation.py::test_short_multi_sentence_formula_failure_uses_grouped_sentence_chunks tests/test_translation.py::test_formula_validation_failure_falls_back_to_sentence_chunks tests/test_translation.py::test_partial_sentence_chunk_failure_only_falls_back_that_chunk tests/test_translation.py::test_sentence_split_ignores_common_academic_abbreviations -q
```

Expected: PASS. The existing five-sentence case continues to use grouped chunks bounded by four formulas; the new short case uses two groups, not three one-sentence requests; abbreviation handling remains unchanged.

- [ ] **Step 5: Commit the sentence-splitting change**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "fix: split short formula-failed paragraphs"
```

### Task 3: Use the strengthened formula instruction for retry and split calls

**Files:**
- Modify: `/Users/wenjuhao/code/python/pdf_trans/src/pdf_trans/translation.py:394-418, 500-506`
- Test: `/Users/wenjuhao/code/python/pdf_trans/tests/test_translation.py:323-365`

**Interfaces:**
- Consumes: `_call_text_translator(translator, text, formula_retry=True)` and `TextTranslator.translate_with_instruction()` when available.
- Produces: one strengthened `_FORMULA_RETRY_INSTRUCTION` used by full formula retries and every split fragment; ordinary calls continue using `translator.translate(text)`.

- [ ] **Step 1: Write the failing split-instruction test**

Add a translator that corrupts the normal call and the first instruction call (the full retry), then succeeds only when the instruction call is used for split fragments:

```python
class SplitInstructionRecordingTranslator:
    def __init__(self):
        self.normal_calls = []
        self.instruction_calls = []

    def translate(self, text, *, response_format=None):
        assert response_format is None
        self.normal_calls.append(text)
        token = FORMULA_TOKEN_RE.findall(text)[0]
        return text.replace(token, "", 1)

    def translate_with_instruction(self, text, instruction):
        self.instruction_calls.append((text, instruction))
        if len(self.instruction_calls) == 1:
            token = FORMULA_TOKEN_RE.findall(text)[0]
            return text.replace(token, "", 1)
        return (
            text.replace("First sentence", "第一句")
            .replace("Second sentence", "第二句")
            .replace("Third sentence", "第三句")
        )


def test_formula_split_calls_use_strengthened_instruction(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = (
        "First sentence with $x$. Second sentence with $y$. "
        "Third sentence with $z$."
    )
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )
    translator = SplitInstructionRecordingTranslator()

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translation_status"] == "success"
    assert result["translated_text"] == (
        "第一句 with $x$. 第二句 with $y$. 第三句 with $z$."
    )
    assert len(translator.normal_calls) == 1
    assert len(translator.instruction_calls) == 3
    assert all("不得在占位符内部插入空格" in instruction for _, instruction in translator.instruction_calls)
    assert stats.model_call_count == 4
```

- [ ] **Step 2: Run the test and verify the expected RED failure**

Run:

```bash
pytest tests/test_translation.py::test_formula_split_calls_use_strengthened_instruction -q
```

Expected: FAIL because `_translate_formula_split()` currently calls `_call_text_translator()` without `formula_retry=True`, so split fragments use `translate()` and are corrupted or fall back.

- [ ] **Step 3: Replace the retry instruction and opt split calls into it**

Replace `_FORMULA_RETRY_INSTRUCTION` with:

```python
_FORMULA_RETRY_INSTRUCTION = """这是一次公式占位符校验失败后的重试，请严格执行以下规则：

1. 先完整处理输入中的所有英文内容，不得只翻译部分句子；
2. 包含公式占位符的句子也必须翻译占位符周围的说明性英文；
3. 每个原文公式占位符必须完整、逐字保留；
4. 每个占位符只能出现一次；
5. 不得删除、复制、翻译、拆开或篡改占位符；
6. 不得在占位符内部插入空格、换行、标点、中文或英文；
7. 占位符可以按照中文语序调整位置，但只能移动完整占位符；
8. 除公式占位符、编号、单位、型号和必要专有名词外，不得原样保留英文说明；
9. 只返回完整译文，不返回说明、分析、前缀或重试原因。"""
```

Change only the split call in `_translate_formula_split()`:

```python
            translated = _call_text_translator(
                translator,
                model_body,
                formula_retry=True,
            )
```

The full retry already passes `formula_retry=isinstance(last_error, FormulaPlaceholderError)` and therefore automatically uses the same strengthened instruction after a formula failure. The normal first call still uses the enhanced base prompt through `translator.translate()`.

- [ ] **Step 4: Run retry and split tests**

Run:

```bash
pytest tests/test_translation.py::test_formula_full_retry_uses_explicit_instruction_before_splitting tests/test_translation.py::test_formula_split_calls_use_strengthened_instruction tests/test_translation.py::test_short_multi_sentence_formula_failure_uses_grouped_sentence_chunks -q
```

Expected: PASS. The full retry and every split fragment record the strengthened instruction; normal successful paragraphs do not use it.

- [ ] **Step 5: Commit the retry-instruction change**

```bash
git add src/pdf_trans/translation.py tests/test_translation.py
git commit -m "fix: reinforce formula retry instructions"
```

### Task 4: Verify complete behavior and regression coverage

**Files:**
- Modify: `/Users/wenjuhao/code/python/pdf_trans/tests/test_translation.py:106-320, 323-365`
- Modify: `/Users/wenjuhao/code/python/pdf_trans/tests/test_translation_client.py:260-320`
- Inspect: `/Users/wenjuhao/code/python/pdf_trans/src/pdf_trans/formula_protection.py:107-217`

**Interfaces:**
- Consumes: the implemented prompt, split grouping, and retry instruction behavior.
- Produces: regression evidence that formula IDs restore independently of order, abbreviation boundaries remain intact, partial chunk fallback is local, no-formula paragraphs keep the normal path, and logs retain page/object and validation details.

- [ ] **Step 1: Confirm order-independent formula restoration remains unchanged**

Run the existing protection regression test:

```bash
pytest tests/test_formula_protection.py::test_allows_placeholder_order_change_and_restores_by_id -q
```

Expected: PASS. Do not modify `FormulaProtectionContext.restore()` because its current behavior already validates the expected ID multiset and restores formulas by ID rather than response order.

- [ ] **Step 2: Strengthen the normal-path regression assertion**

Update `test_text_translation_hides_multiple_formulas_and_restores_exactly` to assert:

```python
    assert len(translator.received) == 1
```

This proves a normal paragraph with formulas still uses one full-paragraph request and does not enter split fallback. Keep the existing no-formula coverage in `test_translate_file_processes_every_text_object_and_preserves_non_text`, where each ordinary text object is translated once.

- [ ] **Step 3: Run the focused formula translation suite**

Run:

```bash
pytest tests/test_translation.py tests/test_formula_protection.py tests/test_translation_client.py -q
```

Expected: PASS, including:

- normal full translation success without splitting;
- missing, duplicate, tampered, and unknown token failures entering split fallback;
- abbreviation-safe sentence detection for `Fig.`, `Eq.`, `e.g.`, `i.e.`, `No.`, and `et al.`;
- no token cut across split chunks;
- local fallback for only failed chunks;
- page/object and expected-vs-actual validation details in logs;
- order-independent formula restoration;
- no-formula ordinary text behavior;
- existing table and auxiliary tests in `test_translation.py`.

- [ ] **Step 4: Run the complete repository test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass with no new warnings or errors. This is the required check that the shared base prompt does not break structured table or auxiliary translation callers.

- [ ] **Step 5: Review the final diff and working tree**

Run:

```bash
git diff HEAD~3..HEAD --stat
git diff HEAD~3..HEAD --check
git status --short --branch
```

Expected: only the translation client prompt, formula fallback logic, and their tests are changed by the implementation commits; no table workflow or unrelated refactor is present. The generated untracked `uv.lock` from the earlier read-only environment setup must not be staged.

- [ ] **Step 6: Commit any final test-only adjustment**

If Step 5 identifies only a needed assertion adjustment, stage only the relevant test file and commit it with:

```bash
git add tests/test_translation.py tests/test_translation_client.py
git commit -m "test: cover formula translation fallback"
```

If no adjustment is needed, do not create an empty commit.
