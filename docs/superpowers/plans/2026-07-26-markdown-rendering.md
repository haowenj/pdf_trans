# Markdown 渲染功能实施计划（Implementation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将清洗后的 `cleaned_content_list.json` 按原数组顺序渲染为同目录的
`rendered.md`，支持正文、两级标题、参考文献、图片、图表、HTML 表格和公式，并由
现有命令行输出 Markdown 文件路径。

**Architecture:** 新增独立的 `mineru_cleaner.renderer` 模块，其中纯函数
`render_items()` 负责内容到 Markdown 的转换，文件函数
`render_content_list_file()` 负责 JSON 读取、校验和 Markdown 写入。现有
`process_pdf()` 在清洗完成后调用渲染器，并通过 `WorkflowResult` 将路径传给 CLI。

**Tech Stack:** Python 3.11+、标准库 `json` / `pathlib`、pytest 8。

## Global Constraints

- 输入必须是 `cleaned_content_list.json` 的 JSON 顶层数组。
- 输出文件固定为清洗文件同目录下的 `rendered.md`。
- 所有支持类型必须按原数组顺序渲染。
- 正文、参考文献、图注、脚注、公式和 `table_body` 不得修改。
- `table_body` 中的 HTML 必须原样输出，不转换为 Markdown 表格。
- `equation.text` 必须原样输出，不重复添加公式定界符。
- 图注和脚注数组按原顺序逐项输出为独立段落。
- 各个非空输出片段之间保留一个空行；非空文件末尾保留一个换行。
- 未支持类型、非字典元素、缺失字段和无效字段值直接跳过。
- 不修改 `cleaned_content_list.json` 或其内存对象。
- 现有清洗、统计、MinerU 地址参数和数据目录规则不得改变。
- 不使用子代理；在当前会话逐项执行本计划。

设计依据：
[Markdown 渲染功能设计](../specs/2026-07-26-markdown-rendering-design.md)

## 文件结构

- 新建 `src/mineru_cleaner/renderer.py`：纯 Markdown 渲染和 JSON/Markdown 文件
  读写。
- 新建 `tests/test_renderer.py`：渲染规则、保真、容错和文件行为测试。
- 修改 `src/mineru_cleaner/workflow.py`：清洗完成后生成 `rendered.md`，返回其路径。
- 修改 `src/mineru_cleaner/__main__.py`：输出 `rendered.md` 的路径。
- 修改 `tests/test_workflow.py`：验证工作流生成 Markdown。
- 修改 `tests/test_cli.py`：验证终端输出 Markdown 路径。
- 修改 `README.md`：说明渲染内容和输出位置。

---

### Task 1：实现独立 Markdown 渲染器

**Files:**

- Create: `src/mineru_cleaner/renderer.py`
- Create: `tests/test_renderer.py`

**Interfaces:**

- Consumes: `cleaned_content_list.json` 对应的 `list[Any]` 或文件路径。
- Produces: `render_items(items: list[Any]) -> str`。
- Produces: `render_content_list_file(source: Path, output: Path) -> None`。
- Raises: 读取、解析、顶层校验和写入失败时抛出 `ContentListError`。

- [ ] **Step 1：先编写所有支持类型的失败测试**

创建 `tests/test_renderer.py`：

```python
import copy
import json

import pytest

from mineru_cleaner.errors import ContentListError
from mineru_cleaner.renderer import render_content_list_file, render_items


def test_render_items_renders_supported_types_in_order_without_mutation():
    items = [
        {"type": "text", "text": "一级标题", "text_level": 1},
        {"type": "text", "text": "二级标题", "text_level": 2},
        {"type": "text", "text": "正文 **保持原样**"},
        {"type": "ref_text", "text": "1. Reference $x$"},
        {
            "type": "image",
            "img_path": "images/a.png",
            "image_caption": ["图注一", "图注二"],
            "image_footnote": ["图片脚注"],
        },
        {
            "type": "chart",
            "img_path": "images/chart.png",
            "chart_caption": ["图表说明"],
            "chart_footnote": ["图表脚注一", "图表脚注二"],
        },
        {
            "type": "table",
            "table_caption": ["表格说明"],
            "table_body": "<table><tr><td>A</td></tr></table>",
            "table_footnote": ["表格脚注"],
        },
        {
            "type": "equation",
            "text": "$$\nE = mc^2\n$$",
            "text_format": "latex",
        },
    ]
    original = copy.deepcopy(items)

    rendered = render_items(items)

    assert rendered == (
        "# 一级标题\n\n"
        "## 二级标题\n\n"
        "正文 **保持原样**\n\n"
        "1. Reference $x$\n\n"
        "![](images/a.png)\n\n"
        "图注一\n\n"
        "图注二\n\n"
        "图片脚注\n\n"
        "![](images/chart.png)\n\n"
        "图表说明\n\n"
        "图表脚注一\n\n"
        "图表脚注二\n\n"
        "表格说明\n\n"
        "<table><tr><td>A</td></tr></table>\n\n"
        "表格脚注\n\n"
        "$$\nE = mc^2\n$$\n"
    )
    assert items == original
```

- [ ] **Step 2：编写空值、未知类型和层级回退的失败测试**

继续向 `tests/test_renderer.py` 添加：

```python
def test_render_items_skips_invalid_values_and_uses_plain_text_for_other_levels():
    items = [
        {"type": "text", "text": "三级按正文", "text_level": 3},
        {"type": "text", "text": "布尔层级按正文", "text_level": True},
        {"type": "text", "text": "   "},
        {
            "type": "image",
            "img_path": "",
            "image_caption": ["有效图注", " ", 9],
            "image_footnote": None,
        },
        {"type": "chart", "chart_caption": "不是数组"},
        {"type": "unknown", "text": "不应输出"},
        "非字典元素",
    ]

    assert render_items(items) == (
        "三级按正文\n\n"
        "布尔层级按正文\n\n"
        "有效图注\n"
    )


def test_render_items_returns_empty_string_when_nothing_is_renderable():
    assert render_items(
        [
            {"type": "unknown", "text": "内容"},
            {"type": "text", "text": ""},
            None,
        ]
    ) == ""
```

- [ ] **Step 3：编写文件读写和顶层校验的失败测试**

继续向 `tests/test_renderer.py` 添加：

```python
def test_render_content_list_file_writes_utf8_markdown(tmp_path):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "rendered.md"
    items = [
        {"type": "text", "text": "中文正文"},
        {"type": "equation", "text": "$$\nx + y\n$$"},
    ]
    source.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    render_content_list_file(source, output)

    assert output.read_text(encoding="utf-8") == (
        "中文正文\n\n"
        "$$\nx + y\n$$\n"
    )
    assert json.loads(source.read_text(encoding="utf-8")) == items


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"type": "text"}', "顶层必须是数组"),
        ("not json", "无法读取 content list"),
    ],
)
def test_render_content_list_file_rejects_invalid_input(
    tmp_path, content, message
):
    source = tmp_path / "cleaned_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ContentListError, match=message):
        render_content_list_file(source, tmp_path / "rendered.md")
```

- [ ] **Step 4：运行渲染测试并确认红灯**

Run:

```bash
.venv/bin/pytest tests/test_renderer.py -v
```

Expected: 测试收集或执行失败，原因是
`mineru_cleaner.renderer` / `render_items` / `render_content_list_file`
尚不存在，而不是测试语法错误。

- [ ] **Step 5：实现最小渲染器**

创建 `src/mineru_cleaner/renderer.py`：

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import ContentListError


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_non_blank_string(item)]


def _render_item(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    if item_type == "text":
        text = item.get("text")
        if not _is_non_blank_string(text):
            return []
        text_level = item.get("text_level")
        if type(text_level) is int and text_level in {1, 2}:
            return [f"{'#' * text_level} {text}"]
        return [text]

    if item_type == "ref_text":
        text = item.get("text")
        return [text] if _is_non_blank_string(text) else []

    if item_type in {"image", "chart"}:
        prefix = item_type
        parts: list[str] = []
        img_path = item.get("img_path")
        if _is_non_blank_string(img_path):
            parts.append(f"![]({img_path})")
        parts.extend(_string_list(item.get(f"{prefix}_caption")))
        parts.extend(_string_list(item.get(f"{prefix}_footnote")))
        return parts

    if item_type == "table":
        parts = _string_list(item.get("table_caption"))
        table_body = item.get("table_body")
        if _is_non_blank_string(table_body):
            parts.append(table_body)
        parts.extend(_string_list(item.get("table_footnote")))
        return parts

    if item_type == "equation":
        text = item.get("text")
        return [text] if _is_non_blank_string(text) else []

    return []


def render_items(items: list[Any]) -> str:
    parts: list[str] = []
    for item in items:
        parts.extend(_render_item(item))
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def render_content_list_file(source: Path, output: Path) -> None:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    rendered = render_items(items)
    try:
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise ContentListError(f"无法写入 Markdown：{exc}") from exc
```

- [ ] **Step 6：运行渲染测试并确认绿灯**

Run:

```bash
.venv/bin/pytest tests/test_renderer.py -v
```

Expected: 6 个渲染测试用例全部通过（其中输入校验测试包含 2 组参数）。

- [ ] **Step 7：运行现有测试，确认没有清洗回归**

Run:

```bash
.venv/bin/pytest -q
```

Expected: 现有 25 个测试加新增 6 个测试用例，共 31 个测试通过。

- [ ] **Step 8：提交渲染器**

```bash
git add src/mineru_cleaner/renderer.py tests/test_renderer.py
git commit -m "feat: render cleaned content as markdown"
```

---

### Task 2：接入 PDF 工作流并输出 Markdown 路径

**Files:**

- Modify: `src/mineru_cleaner/workflow.py`
- Modify: `src/mineru_cleaner/__main__.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

- Consumes: Task 1 的
  `render_content_list_file(source: Path, output: Path) -> None`。
- Produces: `WorkflowResult.markdown_path: Path`。
- Produces: CLI 输出行 `Markdown 文件：{result.markdown_path}`。

- [ ] **Step 1：先扩展工作流测试**

在 `tests/test_workflow.py` 的
`test_process_pdf_runs_complete_workflow` 中增加：

```python
    assert result.markdown_path == (
        tmp_path / "data/paper/hybrid_auto/rendered.md"
    ).resolve()
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "正文\n\n"
        "![](images/a.jpg)\n"
    )
```

该测试使用的清洗后元素是一个正文和一个带 `img_path` 的 `chart`，因此同时验证
数组顺序和相对图片路径。

- [ ] **Step 2：先扩展 CLI 测试**

在 `tests/test_cli.py` 的
`test_main_prints_counts_and_output_path` 中定义：

```python
    markdown = tmp_path / "data/paper/hybrid_auto/rendered.md"
```

并在伪造的 `WorkflowResult` 中加入：

```python
            markdown_path=markdown,
```

在精确输出断言末尾加入：

```python
        f"Markdown 文件：{markdown}\n"
```

在 `test_main_passes_custom_svr_url` 的伪造结果中加入：

```python
            markdown_path=Path("rendered.md"),
```

原有错误路径测试不返回 `WorkflowResult`，无需修改。

- [ ] **Step 3：运行工作流和 CLI 测试并确认红灯**

Run:

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: 因 `WorkflowResult` 尚无 `markdown_path` 且工作流尚未生成
`rendered.md` 而失败。

- [ ] **Step 4：在工作流中生成 Markdown**

修改 `src/mineru_cleaner/workflow.py` 的导入：

```python
from mineru_cleaner.renderer import render_content_list_file
```

向 `WorkflowResult` 增加：

```python
    markdown_path: Path
```

在 `process_pdf()` 清洗完成后加入：

```python
    markdown_path = output_path.parent / "rendered.md"
    render_content_list_file(output_path, markdown_path)
```

返回结果时加入：

```python
        markdown_path=markdown_path.resolve(),
```

- [ ] **Step 5：在 CLI 最后输出 Markdown 路径**

修改 `src/mineru_cleaner/__main__.py`，在三个统计分组全部输出后加入：

```python
    print(f"Markdown 文件：{result.markdown_path}")
```

- [ ] **Step 6：运行工作流和 CLI 测试并确认绿灯**

Run:

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: 7 个测试全部通过。

- [ ] **Step 7：运行全量测试**

Run:

```bash
.venv/bin/pytest -q
```

Expected: 31 个测试全部通过。

- [ ] **Step 8：提交工作流集成**

```bash
git add src/mineru_cleaner/workflow.py src/mineru_cleaner/__main__.py \
  tests/test_workflow.py tests/test_cli.py
git commit -m "feat: generate markdown after cleaning"
```

---

### Task 3：更新使用文档并验证真实样例

**Files:**

- Modify: `README.md`

**Interfaces:**

- Documents: 现有 PDF 命令会自动生成 `rendered.md`。
- Verifies: 真实清洗样例可以生成包含图片和原始 HTML 表格的 Markdown。

- [ ] **Step 1：更新 README 的输出说明**

在 `README.md` 的使用说明中，把：

```markdown
MinerU 结果解压到项目的 `data/` 目录。清洗结果保存在原始 content list
同目录的 `cleaned_content_list.json` 中。命令输出处理前数量、过滤数量、
处理后数量和结果文件路径。
```

替换为：

```markdown
MinerU 结果解压到项目的 `data/` 目录。清洗结果保存在原始 content list
同目录的 `cleaned_content_list.json` 中，并自动在同目录生成 `rendered.md`。
命令输出处理前数量、过滤数量、处理后数量、清洗结果路径、内容统计和 Markdown
文件路径。
```

在统计说明之后增加：

```markdown
## Markdown 渲染

`rendered.md` 按清洗后数组的原顺序输出以下内容：

- `text`：一级标题、二级标题或普通段落；
- `ref_text`：原始参考文献段落；
- `image` 和 `chart`：相对图片路径、图注和脚注；
- `table`：图注、原始 HTML 表格和脚注；
- `equation`：MinerU 提供的原始 LaTeX 文本。

各内容片段之间保留空行。渲染过程不会修改
`cleaned_content_list.json`，也不会把 HTML 表格转换为 Markdown 表格。
```

- [ ] **Step 2：执行格式与全量测试验证**

Run:

```bash
git diff --check
.venv/bin/pytest -q
```

Expected:

- `git diff --check` 无输出且退出码为 0；
- 31 个测试全部通过。

- [ ] **Step 3：使用现有样例生成 Markdown**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; from mineru_cleaner.renderer import render_content_list_file; source=Path("data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json"); output=source.parent/"rendered.md"; render_content_list_file(source, output); print(output.resolve())'
```

Expected:

```text
/Users/wenjuhao/code/python/pdf_trans/data/processes-12-02888-v2/hybrid_auto/rendered.md
```

- [ ] **Step 4：验证样例包含图片和原始 HTML 表格**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; p=Path("data/processes-12-02888-v2/hybrid_auto/rendered.md"); text=p.read_text(encoding="utf-8"); assert "![](images/" in text; assert "<table>" in text and "</table>" in text; assert text.endswith("\n"); print(f"{p}: {len(text)} characters")'
```

Expected: 命令成功，打印 Markdown 文件路径和非零字符数。

- [ ] **Step 5：确认数据文件和 macOS 元数据仍被 Git 忽略**

Run:

```bash
git check-ignore -v \
  data/processes-12-02888-v2/hybrid_auto/rendered.md \
  .DS_Store
```

Expected: `rendered.md` 命中 `data/` 规则，`.DS_Store` 命中对应忽略规则。

- [ ] **Step 6：提交文档**

```bash
git add README.md
git commit -m "docs: describe markdown rendering"
```

- [ ] **Step 7：完成前进行新鲜验证**

Run:

```bash
.venv/bin/pytest -q
git diff --check
git status --short --branch
```

Expected:

- 31 个测试全部通过；
- `git diff --check` 无错误；
- 工作区无未提交修改；
- 当前分支仍为 `main`。
