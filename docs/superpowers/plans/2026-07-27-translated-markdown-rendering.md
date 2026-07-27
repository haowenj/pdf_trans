# Translated Markdown Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the full PDF workflow render successful Chinese translations into `rendered.md` while falling back to source text for failed translations.

**Architecture:** Keep translation output immutable and teach the Markdown renderer to select `translated_text` only for successful text objects. Then connect the workflow's render stage to `translated_content_list.json`; all non-text rendering and the standalone translate-only workflow remain unchanged.

**Tech Stack:** Python 3.11+, standard library JSON/path handling, pytest 8

## Global Constraints

- Preserve every original `text` value and do not mutate either JSON input file.
- Use non-blank `translated_text` only when `translation_status == "success"`.
- Fall back to original `text` for failed, incomplete, or legacy text objects.
- Preserve current rendering for `ref_text`, image, chart, table, and equation objects.
- Do not change `--translate-only`, translation API calls, concurrency, retries, or checkpoint format.
- Add no dependencies.

---

### Task 1: Select translated text in the Markdown renderer

**Files:**
- Modify: `tests/test_renderer.py:65-96`
- Modify: `src/pdf_trans/renderer.py:20-32`

**Interfaces:**
- Consumes: text objects containing `text: str`, optional `text_level: int`, optional `translation_status: str`, and optional `translated_text: str | None`.
- Produces: unchanged public function `render_items(items: list[Any]) -> str`; successful text objects render their translation, while all other text objects render their source text.

- [ ] **Step 1: Write the failing successful-translation test**

Insert this test after
`test_render_items_renders_supported_types_in_order_without_mutation`:

```python
def test_render_items_uses_successful_translation_with_original_heading_level():
    items = [
        {
            "type": "text",
            "text": "English title",
            "text_level": 1,
            "translated_text": "中文标题",
            "translation_status": "success",
        },
        {
            "type": "text",
            "text": "English body",
            "translated_text": "中文正文",
            "translation_status": "success",
        },
    ]
    original = copy.deepcopy(items)

    assert render_items(items) == "# 中文标题\n\n中文正文\n"
    assert items == original
```

- [ ] **Step 2: Run the successful-translation test to verify it fails**

Run:

```bash
.venv/bin/pytest \
  tests/test_renderer.py::test_render_items_uses_successful_translation_with_original_heading_level \
  -v
```

Expected: FAIL because the actual Markdown contains `English title` and
`English body`.

- [ ] **Step 3: Add regression coverage for source-text fallback**

Insert this test after the successful-translation test:

```python
@pytest.mark.parametrize(
    "item",
    [
        {
            "type": "text",
            "text": "Failed source",
            "translated_text": None,
            "translation_status": "failed",
            "translation_error": "timeout",
        },
        {
            "type": "text",
            "text": "Blank translation source",
            "translated_text": "   ",
            "translation_status": "success",
        },
        {
            "type": "text",
            "text": "Legacy source",
        },
    ],
)
def test_render_items_falls_back_to_source_text(item):
    assert render_items([item]) == f"{item['text']}\n"
```

- [ ] **Step 4: Implement the minimal text selection**

Replace the text branch in `_render_item` with:

```python
    if item_type == "text":
        text = item.get("text")
        translated_text = item.get("translated_text")
        if (
            item.get("translation_status") == "success"
            and _is_non_blank_string(translated_text)
        ):
            text = translated_text
        if not _is_non_blank_string(text):
            return []
        text_level = item.get("text_level")
        if type(text_level) is int and text_level in {1, 2}:
            return [f"{'#' * text_level} {text}"]
        return [text]
```

- [ ] **Step 5: Run renderer tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_renderer.py -v
```

Expected: all renderer tests PASS.

- [ ] **Step 6: Commit renderer behavior**

```bash
git add src/pdf_trans/renderer.py tests/test_renderer.py
git commit -m "fix: render successful text translations"
```

---

### Task 2: Render the translated workflow output

**Files:**
- Modify: `tests/test_workflow.py:44-123`
- Modify: `tests/test_workflow.py:125-182`
- Modify: `src/pdf_trans/workflow.py:309-316`
- Modify: `README.md:131-143`

**Interfaces:**
- Consumes: `TranslationFileResult.translated_path: Path` returned by `_run_translation`.
- Produces: `WorkflowResult.markdown_path: Path` whose file content is rendered from `translated_content_list.json`.

- [ ] **Step 1: Change workflow expectations to translated Markdown**

In `test_process_pdf_runs_complete_workflow`, replace the Markdown assertion
with:

```python
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "译文：正文\n\n"
        "![](images/a.jpg)\n"
    )
```

In
`test_process_pdf_writes_cross_page_report_without_changing_other_outputs`,
replace the Markdown assertion with:

```python
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "译文：上一页未结束 下一页继续。\n"
    )
```

- [ ] **Step 2: Run the workflow tests to verify they fail**

Run:

```bash
.venv/bin/pytest \
  tests/test_workflow.py::test_process_pdf_runs_complete_workflow \
  tests/test_workflow.py::test_process_pdf_writes_cross_page_report_without_changing_other_outputs \
  -v
```

Expected: both tests FAIL because `rendered.md` still contains the original
Chinese source strings without the `译文：` prefix.

- [ ] **Step 3: Connect Markdown rendering to the translation output**

Replace the Markdown stage in `_process_pdf_stages` with:

```python
    markdown_path = output_path.parent / "rendered.md"
    with logged_stage(
        LOGGER,
        "渲染 Markdown",
        "按翻译结果对象顺序生成 rendered.md",
    ) as stage:
        render_content_list_file(
            translation_result.translated_path,
            markdown_path,
        )
        stage.set_result(f"输出文件 {markdown_path.resolve()}")
```

- [ ] **Step 4: Run the targeted workflow tests to verify they pass**

Run:

```bash
.venv/bin/pytest \
  tests/test_workflow.py::test_process_pdf_runs_complete_workflow \
  tests/test_workflow.py::test_process_pdf_writes_cross_page_report_without_changing_other_outputs \
  -v
```

Expected: both tests PASS.

- [ ] **Step 5: Update the Markdown rendering documentation**

Replace the opening Markdown-rendering paragraph and text bullet in
`README.md` with:

```markdown
`rendered.md` 按 `translated_content_list.json` 数组的原顺序输出以下内容：

- `text`：翻译成功时输出 `translated_text`，失败或译文无效时回退原始
  `text`，并保留一级标题、二级标题或普通段落格式；
```

Replace the final non-mutation paragraph with:

```markdown
各内容片段之间保留空行。渲染过程不会修改
`cleaned_content_list.json`、`cross_page_candidates.json`、
`normalized_content_list.json` 或 `translated_content_list.json`，也不会把
HTML 表格转换为 Markdown 表格。
```

- [ ] **Step 6: Run workflow, CLI, and renderer regression tests**

Run:

```bash
.venv/bin/pytest \
  tests/test_workflow.py \
  tests/test_cli.py \
  tests/test_renderer.py \
  -v
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit workflow integration and documentation**

```bash
git add src/pdf_trans/workflow.py tests/test_workflow.py README.md
git commit -m "fix: render markdown from translated content"
```

---

### Task 3: Verify the complete repository

**Files:**
- Verify only: entire repository

**Interfaces:**
- Consumes: completed renderer selection and workflow integration from Tasks 1-2.
- Produces: evidence that the fix passes all automated checks and introduces no whitespace errors.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: all tests PASS with no errors or warnings.

- [ ] **Step 2: Check the final diff for whitespace errors**

Run:

```bash
git diff --check HEAD~2..HEAD
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Confirm the worktree contains no uncommitted implementation changes**

Run:

```bash
git status --short
```

Expected: no output.
