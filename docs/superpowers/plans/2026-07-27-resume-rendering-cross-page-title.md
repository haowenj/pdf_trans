# Resume Rendering and Cross-Page Title Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make command-line translation resume regenerate `rendered.md`, and prevent title text from being merged as a cross-page paragraph.

**Architecture:** Keep `process_translation_file` limited to validation and translation. The CLI will render the returned `translated_content_list.json` into sibling `rendered.md`, as the Web task runner already does. The detector rejects an adjacent text pair when either item declares `text_level`.

**Tech Stack:** Python 3.11+, pytest, `pdf_trans.renderer.render_content_list_file`.

## Global Constraints

- `--translate-only` overwrites sibling `rendered.md` only after translation returns successfully.
- Render from `TranslationFileResult.translated_path`, never from normalized input.
- A `text_level` key on either item excludes that pair regardless of its value or type.
- Do not change Web rendering, `process_translation_file`, sentence-ending rules, or candidate-report schema.

---

### Task 1: Render command-line translation-resume output

**Files:**

- Modify: `src/pdf_trans/__main__.py:8-12, 66-69`
- Modify: `tests/test_cli.py:157-190`

**Interfaces:**

- Consumes: `process_translation_file(normalized_path: Path) -> TranslationFileResult`; `translated_path: Path` targets `translated_content_list.json`.
- Consumes: `render_content_list_file(source: Path, output: Path) -> None`.
- Produces: CLI output containing `Markdown 文件：<absolute rendered.md path>` and a rendered sibling file.

- [ ] **Step 1: Write the failing CLI test**

Replace `test_main_translate_only_prints_translation_summary` with:

```python
def test_main_translate_only_renders_markdown(tmp_path, monkeypatch, capsys):
    normalized = tmp_path / "normalized_content_list.json"
    translated = tmp_path / "translated_content_list.json"
    rendered = tmp_path / "rendered.md"
    calls = []

    def fake_process(path):
        calls.append(("translate", path))
        return type("Result", (), {
            "translated_path": translated,
            "stats": TranslationStats(2, 3, 1, 2, 0, 0),
        })()

    def fake_render(source, output):
        calls.append(("render", source, output))

    monkeypatch.setattr(cli, "process_translation_file", fake_process)
    monkeypatch.setattr(cli, "render_content_list_file", fake_render)

    assert cli.main(["--translate-only", str(normalized)]) == 0
    assert calls == [("translate", normalized), ("render", translated, rendered)]
    assert f"Markdown 文件：{rendered.resolve()}\n" in capsys.readouterr().out
```

- [ ] **Step 2: Run the test and verify it fails**

Run `python3 -m pytest tests/test_cli.py::test_main_translate_only_renders_markdown -v`.

Expected: FAIL because `pdf_trans.__main__` neither imports nor calls `render_content_list_file`.

- [ ] **Step 3: Add the minimal CLI rendering call**

Import the existing renderer and amend the `--translate-only` branch:

```python
from pdf_trans.renderer import render_content_list_file

result = process_translation_file(args.translate_only)
markdown_path = result.translated_path.with_name("rendered.md")
render_content_list_file(result.translated_path, markdown_path)
_print_translation_summary(result.stats, result.translated_path)
print(f"Markdown 文件：{markdown_path.resolve()}")
return 0
```

Keep `except (PDFTransError, OSError)` unchanged so translation and rendering errors return `1`.

- [ ] **Step 4: Verify the CLI tests pass**

Run `python3 -m pytest tests/test_cli.py -v`.

Expected: PASS after updating existing stdout assertions for the extra Markdown line.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/pdf_trans/__main__.py tests/test_cli.py
git commit -m "feat: render markdown after translation resume"
```

### Task 2: Exclude headings from cross-page candidates

**Files:**

- Modify: `src/pdf_trans/cross_page.py:30-33`
- Modify: `tests/test_cross_page.py:84-101`

**Interfaces:**

- Consumes: `detect_cross_page_candidates(items: list[Any]) -> list[dict[str, Any]]`.
- Produces: no candidate when either element of an otherwise-valid adjacent pair contains `text_level`.

- [ ] **Step 1: Write failing title-exclusion tests**

Replace the title-detection test with:

```python
@pytest.mark.parametrize("title_position", [0, 1])
def test_detector_excludes_pairs_containing_text_level(title_position):
    items = [
        {"type": "text", "text": "上一页正文未结束", "page_idx": 1},
        {"type": "text", "text": "下一页文本", "page_idx": 2},
    ]
    items[title_position]["text_level"] = None

    assert detect_cross_page_candidates(items) == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run `python3 -m pytest tests/test_cross_page.py::test_detector_excludes_pairs_containing_text_level -v`.

Expected: FAIL because the detector currently checks only type, page, non-empty text, and sentence ending.

- [ ] **Step 3: Add the minimal title guard**

Insert after the text-type check in `src/pdf_trans/cross_page.py`:

```python
if "text_level" in previous or "text_level" in next_item:
    continue
```

Do not inspect the field value.

- [ ] **Step 4: Verify the detector tests pass**

Run `python3 -m pytest tests/test_cross_page.py -v`.

Expected: PASS; ordinary body-only candidate and multi-page-chain tests remain green.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/pdf_trans/cross_page.py tests/test_cross_page.py
git commit -m "fix: exclude headings from cross-page candidates"
```

### Task 3: Full regression verification

**Files:**

- Verify: `src/pdf_trans/__main__.py`, `src/pdf_trans/cross_page.py`, `tests/test_cli.py`, `tests/test_cross_page.py`

**Interfaces:**

- Consumes: the completed CLI and detector behavior from Tasks 1 and 2.
- Produces: evidence that the full suite has no regression.

- [ ] **Step 1: Run the complete suite**

Run `python3 -m pytest -v`.

Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the final diff**

Run:

```bash
git diff main...HEAD --check
git status --short
```

Expected: no whitespace errors and no generated runtime artifacts.

- [ ] **Step 3: Commit a correction only if verification required one**

If verification changed no files, make no commit. Otherwise, stage only the correction and commit it with a message describing the correction.
