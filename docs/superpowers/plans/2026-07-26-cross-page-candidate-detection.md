# 跨页段落候选检测实施计划（Implementation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 检测 `cleaned_content_list.json` 中立即相邻、位于连续页面且前文没有指定
完整句结束符的两个 `text` 对象，生成独立的 `cross_page_candidates.json` 报告，
不修改清洗结果或 Markdown。

**Architecture:** 新增独立的 `mineru_cleaner.cross_page` 模块，其中纯函数
`detect_cross_page_candidates()` 负责候选判定，文件函数
`detect_cross_page_candidates_file()` 负责读取清洗 JSON 和写出报告。现有
`process_pdf()` 在清洗与 Markdown 渲染完成后调用检测器，并通过 `WorkflowResult`
将候选数量和报告路径传给 CLI。

**Tech Stack:** Python 3.11+、标准库 `json` / `pathlib`、pytest 8。

## Global Constraints

- 输入必须是 `cleaned_content_list.json` 的 JSON 顶层数组。
- 输出文件固定为清洗文件同目录下的 `cross_page_candidates.json`。
- 只检查数组中位置立即相邻的两个对象。
- 两个对象必须都是字典且 `type == "text"`。
- 两个 `page_idx` 必须满足 `type(page_idx) is int`。
- 后一个 `page_idx` 必须等于前一个 `page_idx + 1`。
- 两个 `text` 必须是字符串且 `bool(text.strip())` 为真。
- 判断结尾时只对前一个文本使用 `rstrip()`，报告保留两个原始文本。
- 完整句结束符严格限定为 ASCII 字符 `. ! ? : ;`。
- `text_level` 不参与判定，标题和正文使用相同规则。
- 不跨过图片、表格、公式或其他对象寻找文本。
- 只检测并输出报告，不自动合并内容。
- 不修改 `cleaned_content_list.json`。
- 不读取、修改或重新生成 `rendered.md`。
- 报告索引使用原数组中的零基索引。
- JSON 使用 UTF-8、`ensure_ascii=False`、两个空格缩进，并以换行结尾。
- 不使用子代理；在当前会话逐项执行本计划。

设计依据：
[跨页段落候选检测设计](../specs/2026-07-26-cross-page-candidate-detection-design.md)

## 文件结构

- 新建 `src/mineru_cleaner/cross_page.py`：纯候选检测和报告 JSON 文件读写。
- 新建 `tests/test_cross_page.py`：判定规则、索引、保真、容错和文件行为测试。
- 修改 `src/mineru_cleaner/workflow.py`：生成报告并返回候选数量和路径。
- 修改 `src/mineru_cleaner/__main__.py`：输出候选数量和报告路径。
- 修改 `tests/test_workflow.py`：验证端到端报告及现有文件内容不变。
- 修改 `tests/test_cli.py`：验证终端输出。
- 修改 `README.md`：说明候选规则、报告结构和只检测不合并的边界。

---

### Task 1：实现独立跨页候选检测器

**Files:**

- Create: `src/mineru_cleaner/cross_page.py`
- Create: `tests/test_cross_page.py`

**Interfaces:**

- Consumes: `cleaned_content_list.json` 对应的 `list[Any]` 或文件路径。
- Produces:
  `detect_cross_page_candidates(items: list[Any]) -> list[dict[str, Any]]`。
- Produces:
  `detect_cross_page_candidates_file(source: Path, output: Path) -> int`。
- Produces: 固定原因常量 `CROSS_PAGE_REASON: str`。
- Raises: 读取、解析、顶层校验和写入失败时抛出 `ContentListError`。

- [ ] **Step 1：先编写命中条件、原始文本和非变异测试**

创建 `tests/test_cross_page.py`：

```python
import copy
import json

import pytest

from mineru_cleaner.cross_page import (
    CROSS_PAGE_REASON,
    detect_cross_page_candidates,
    detect_cross_page_candidates_file,
)
from mineru_cleaner.errors import ContentListError


def test_detect_cross_page_candidates_reports_full_candidate_without_mutation():
    previous_text = "跨页前半段 \n\t"
    next_text = "跨页后半段。"
    items = [
        {
            "type": "text",
            "text": previous_text,
            "page_idx": 5,
            "bbox": [1, 2, 3, 4],
        },
        {
            "type": "text",
            "text": next_text,
            "page_idx": 6,
            "bbox": [5, 6, 7, 8],
        },
    ]
    original = copy.deepcopy(items)

    candidates = detect_cross_page_candidates(items)

    assert candidates == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 5,
            "next_page_idx": 6,
            "previous_text": previous_text,
            "next_text": next_text,
            "reason": CROSS_PAGE_REASON,
        }
    ]
    assert items == original
```

- [ ] **Step 2：编写五种结束符的失败测试**

继续向 `tests/test_cross_page.py` 添加：

```python
@pytest.mark.parametrize("ending", [".", "!", "?", ":", ";"])
def test_detect_cross_page_candidates_rejects_complete_sentence_endings(ending):
    items = [
        {"type": "text", "text": f"完整句{ending} \n", "page_idx": 2},
        {"type": "text", "text": "下一页", "page_idx": 3},
    ]

    assert detect_cross_page_candidates(items) == []


def test_detector_treats_non_configured_punctuation_as_candidate():
    items = [
        {"type": "text", "text": "中文句号。", "page_idx": 2},
        {"type": "text", "text": "下一页", "page_idx": 3},
    ]

    assert len(detect_cross_page_candidates(items)) == 1
```

- [ ] **Step 3：编写页码、立即相邻、标题和无效字段测试**

继续向 `tests/test_cross_page.py` 添加：

```python
def test_detect_cross_page_candidates_requires_consecutive_pages():
    invalid_page_pairs = [
        (2, 2),
        (2, 4),
        (2, 1),
    ]

    for previous_page, next_page in invalid_page_pairs:
        items = [
            {"type": "text", "text": "未结束", "page_idx": previous_page},
            {"type": "text", "text": "下一段", "page_idx": next_page},
        ]
        assert detect_cross_page_candidates(items) == []


def test_detector_does_not_skip_objects_but_still_detects_text_titles():
    items = [
        {"type": "text", "text": "不会跨过图片", "page_idx": 0},
        {"type": "image", "img_path": "images/a.jpg", "page_idx": 0},
        {"type": "text", "text": "上一页正文未结束", "page_idx": 1},
        {
            "type": "text",
            "text": "下一页标题",
            "text_level": 1,
            "page_idx": 2,
        },
    ]

    candidates = detect_cross_page_candidates(items)

    assert [(item["previous_index"], item["next_index"]) for item in candidates] == [
        (2, 3)
    ]


def test_detect_cross_page_candidates_skips_invalid_objects_and_fields():
    invalid_pairs = [
        [
            "非字典",
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "ref_text", "text": "未结束", "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "未结束", "page_idx": True},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": None, "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "   ", "page_idx": 0},
            {"type": "text", "text": "下一段", "page_idx": 1},
        ],
        [
            {"type": "text", "text": "未结束", "page_idx": 0},
            {"type": "text", "text": "", "page_idx": 1},
        ],
    ]

    for items in invalid_pairs:
        assert detect_cross_page_candidates(items) == []
```

- [ ] **Step 4：编写重叠候选测试**

继续向 `tests/test_cross_page.py` 添加：

```python
def test_detect_cross_page_candidates_allows_overlapping_adjacent_pairs():
    items = [
        {"type": "text", "text": "第一页未结束", "page_idx": 0},
        {"type": "text", "text": "第二页仍未结束", "page_idx": 1},
        {"type": "text", "text": "第三页结束。", "page_idx": 2},
    ]

    candidates = detect_cross_page_candidates(items)

    assert [
        (item["previous_index"], item["next_index"]) for item in candidates
    ] == [(0, 1), (1, 2)]
```

- [ ] **Step 5：编写报告文件、空报告和输入错误测试**

继续向 `tests/test_cross_page.py` 添加：

```python
def test_detect_cross_page_candidates_file_writes_report_without_changing_source(
    tmp_path,
):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "cross_page_candidates.json"
    items = [
        {"type": "text", "text": "上一页未结束", "page_idx": 7},
        {"type": "text", "text": "下一页正文。", "page_idx": 8},
    ]
    source.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    source_before = source.read_bytes()

    count = detect_cross_page_candidates_file(source, output)

    assert count == 1
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 7,
            "next_page_idx": 8,
            "previous_text": "上一页未结束",
            "next_text": "下一页正文。",
            "reason": CROSS_PAGE_REASON,
        }
    ]
    assert output.read_bytes().endswith(b"\n")
    assert source.read_bytes() == source_before


def test_detect_cross_page_candidates_file_writes_empty_array(tmp_path):
    source = tmp_path / "cleaned_content_list.json"
    output = tmp_path / "cross_page_candidates.json"
    source.write_text(
        '[{"type": "text", "text": "完整句。", "page_idx": 0}]\n',
        encoding="utf-8",
    )

    count = detect_cross_page_candidates_file(source, output)

    assert count == 0
    assert output.read_text(encoding="utf-8") == "[]\n"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ('{"type": "text"}', "顶层必须是数组"),
        ("not json", "无法读取 content list"),
    ],
)
def test_detect_cross_page_candidates_file_rejects_invalid_input(
    tmp_path, content, message
):
    source = tmp_path / "cleaned_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ContentListError, match=message):
        detect_cross_page_candidates_file(
            source,
            tmp_path / "cross_page_candidates.json",
        )
```

- [ ] **Step 6：运行检测器测试并确认红灯**

Run:

```bash
.venv/bin/pytest tests/test_cross_page.py -v
```

Expected: 测试收集或执行失败，原因是
`mineru_cleaner.cross_page` / `detect_cross_page_candidates` /
`detect_cross_page_candidates_file` 尚不存在，而不是测试语法错误。

- [ ] **Step 7：实现最小检测器**

创建 `src/mineru_cleaner/cross_page.py`：

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import ContentListError

CROSS_PAGE_REASON = (
    "相邻 text 位于连续页面，且前一个 text "
    "未以完整句结束符 . ! ? : ; 结尾"
)
_SENTENCE_ENDINGS = (".", "!", "?", ":", ";")


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def detect_cross_page_candidates(
    items: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for previous_index in range(len(items) - 1):
        next_index = previous_index + 1
        previous = items[previous_index]
        next_item = items[next_index]

        if not isinstance(previous, dict) or not isinstance(next_item, dict):
            continue
        if previous.get("type") != "text" or next_item.get("type") != "text":
            continue

        previous_page_idx = previous.get("page_idx")
        next_page_idx = next_item.get("page_idx")
        if type(previous_page_idx) is not int or type(next_page_idx) is not int:
            continue
        if next_page_idx != previous_page_idx + 1:
            continue

        previous_text = previous.get("text")
        next_text = next_item.get("text")
        if not _is_non_blank_string(previous_text):
            continue
        if not _is_non_blank_string(next_text):
            continue
        if previous_text.rstrip().endswith(_SENTENCE_ENDINGS):
            continue

        candidates.append(
            {
                "previous_index": previous_index,
                "next_index": next_index,
                "previous_page_idx": previous_page_idx,
                "next_page_idx": next_page_idx,
                "previous_text": previous_text,
                "next_text": next_text,
                "reason": CROSS_PAGE_REASON,
            }
        )

    return candidates


def detect_cross_page_candidates_file(source: Path, output: Path) -> int:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    candidates = detect_cross_page_candidates(items)
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(candidates, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入跨页候选报告：{exc}") from exc
    return len(candidates)
```

- [ ] **Step 8：运行检测器测试并确认绿灯**

Run:

```bash
.venv/bin/pytest tests/test_cross_page.py -v
```

Expected: 15 个检测器测试用例全部通过。

- [ ] **Step 9：运行全量测试**

Run:

```bash
.venv/bin/pytest -q
```

Expected: 现有 31 个测试加新增 15 个测试用例，共 46 个测试通过。

- [ ] **Step 10：提交检测器**

```bash
git add src/mineru_cleaner/cross_page.py tests/test_cross_page.py
git commit -m "feat: detect cross-page paragraph candidates"
```

---

### Task 2：接入 PDF 工作流并输出报告信息

**Files:**

- Modify: `src/mineru_cleaner/workflow.py`
- Modify: `src/mineru_cleaner/__main__.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

- Consumes: Task 1 的
  `detect_cross_page_candidates_file(source: Path, output: Path) -> int`。
- Produces: `WorkflowResult.candidates_path: Path`。
- Produces: `WorkflowResult.candidate_count: int`。
- Produces: CLI 输出候选数量和报告路径。

- [ ] **Step 1：先增加端到端工作流失败测试**

在 `tests/test_workflow.py` 中增加：

```python
def test_process_pdf_writes_cross_page_report_without_changing_other_outputs(
    tmp_path,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    previous = {"type": "text", "text": "上一页未结束", "page_idx": 0}
    next_item = {"type": "text", "text": "下一页继续。", "page_idx": 1}
    client = FakeMinerUClient(make_result_zip([previous, next_item]))

    result = process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert result.candidate_count == 1
    assert result.candidates_path == (
        tmp_path / "data/paper/hybrid_auto/cross_page_candidates.json"
    ).resolve()
    assert json.loads(result.candidates_path.read_text(encoding="utf-8")) == [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 0,
            "next_page_idx": 1,
            "previous_text": "上一页未结束",
            "next_text": "下一页继续。",
            "reason": (
                "相邻 text 位于连续页面，且前一个 text "
                "未以完整句结束符 . ! ? : ; 结尾"
            ),
        }
    ]
    assert json.loads(result.output_path.read_text(encoding="utf-8")) == [
        previous,
        next_item,
    ]
    assert result.markdown_path.read_text(encoding="utf-8") == (
        "上一页未结束\n\n"
        "下一页继续。\n"
    )
```

- [ ] **Step 2：先扩展 CLI 失败测试**

在 `tests/test_cli.py` 的
`test_main_prints_counts_and_output_path` 中定义：

```python
    candidates = (
        tmp_path
        / "data/paper/hybrid_auto/cross_page_candidates.json"
    )
```

向伪造的 `WorkflowResult` 增加：

```python
            candidates_path=candidates,
            candidate_count=2,
```

在精确终端输出断言末尾加入：

```python
        "跨页段落候选数量：2\n"
        f"跨页候选报告：{candidates}\n"
```

在 `test_main_passes_custom_svr_url` 的伪造结果中增加：

```python
            candidates_path=Path("cross_page_candidates.json"),
            candidate_count=0,
```

并在该测试中保存一次捕获结果：

```python
    captured = capsys.readouterr()
```

把现有空分组断言改为：

```python
    assert captured.out.count("  （无）") == 3
    assert "跨页段落候选数量：0" in captured.out
    assert (
        "跨页候选报告：cross_page_candidates.json"
        in captured.out
    )
```

- [ ] **Step 3：运行工作流和 CLI 测试并确认红灯**

Run:

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: 因 `WorkflowResult` 尚无候选字段，且工作流尚未生成报告而失败。

- [ ] **Step 4：在工作流中生成候选报告**

修改 `src/mineru_cleaner/workflow.py`，增加导入：

```python
from mineru_cleaner.cross_page import detect_cross_page_candidates_file
```

向 `WorkflowResult` 增加：

```python
    candidates_path: Path
    candidate_count: int
```

在 `render_content_list_file(output_path, markdown_path)` 之后加入：

```python
    candidates_path = output_path.parent / "cross_page_candidates.json"
    candidate_count = detect_cross_page_candidates_file(
        output_path,
        candidates_path,
    )
```

返回 `WorkflowResult` 时加入：

```python
        candidates_path=candidates_path.resolve(),
        candidate_count=candidate_count,
```

- [ ] **Step 5：在 CLI 末尾输出候选数量和路径**

修改 `src/mineru_cleaner/__main__.py`，在 Markdown 路径输出后加入：

```python
    print(f"跨页段落候选数量：{result.candidate_count}")
    print(f"跨页候选报告：{result.candidates_path}")
```

- [ ] **Step 6：运行工作流和 CLI 测试并确认绿灯**

Run:

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

Expected: 新增工作流测试在内的 8 个测试用例全部通过。

- [ ] **Step 7：运行全量测试**

Run:

```bash
.venv/bin/pytest -q
```

Expected: 47 个测试全部通过。

- [ ] **Step 8：提交工作流集成**

```bash
git add src/mineru_cleaner/workflow.py src/mineru_cleaner/__main__.py \
  tests/test_workflow.py tests/test_cli.py
git commit -m "feat: report cross-page candidates in workflow"
```

---

### Task 3：更新文档并验证真实样例

**Files:**

- Modify: `README.md`

**Interfaces:**

- Documents: 自动生成 `cross_page_candidates.json`。
- Documents: 候选判定只用于报告，不执行正文合并。
- Verifies: 真实样例仅命中索引 `36 -> 37`。

- [ ] **Step 1：更新 README 的输出说明**

把 `README.md` 使用说明中的：

```markdown
同目录的 `cleaned_content_list.json` 中，并自动在同目录生成 `rendered.md`。
命令输出处理前数量、过滤数量、处理后数量、清洗结果路径、内容统计和 Markdown
文件路径。
```

替换为：

```markdown
同目录的 `cleaned_content_list.json` 中，并自动在同目录生成 `rendered.md` 和
`cross_page_candidates.json`。命令输出处理前数量、过滤数量、处理后数量、
清洗结果路径、内容统计、Markdown 文件路径、跨页候选数量和报告路径。
```

在 Markdown 渲染章节之后增加：

```markdown
## 跨页段落候选

`cross_page_candidates.json` 只检查清洗数组中立即相邻的两个 `text` 对象。
当后一个 `page_idx` 等于前一个加 1，且前一个文本忽略尾部空白后不以
`. ! ? : ;` 结尾时，记录为疑似跨页段落。

报告包含两个对象的零基数组索引、两个页码、原始文本和判断原因。检测过程不会
修改 `cleaned_content_list.json` 或 `rendered.md`，也不会自动合并正文。
```

- [ ] **Step 2：执行格式和全量测试验证**

Run:

```bash
git diff --check
.venv/bin/pytest -q
```

Expected:

- `git diff --check` 无输出且退出码为 0；
- 47 个测试全部通过。

- [ ] **Step 3：使用现有样例生成候选报告**

Run:

```bash
.venv/bin/python -c 'from pathlib import Path; from mineru_cleaner.cross_page import detect_cross_page_candidates_file; source=Path("data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json"); output=source.parent/"cross_page_candidates.json"; count=detect_cross_page_candidates_file(source, output); print(f"{output.resolve()} ({count})")'
```

Expected:

```text
/Users/wenjuhao/code/python/pdf_trans/data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json (1)
```

- [ ] **Step 4：验证样例候选和两个既有文件未改变**

先记录现有文件摘要：

```bash
shasum -a 256 \
  data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json \
  data/processes-12-02888-v2/hybrid_auto/rendered.md
```

再次运行 Step 3 的检测命令，然后再次运行相同的 `shasum` 命令。两次摘要必须完全
一致。

验证报告内容：

```bash
.venv/bin/python -c 'import json; from pathlib import Path; p=Path("data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json"); items=json.loads(p.read_text(encoding="utf-8")); assert len(items)==1; assert items[0]["previous_index"]==36; assert items[0]["next_index"]==37; assert items[0]["previous_page_idx"]==5; assert items[0]["next_page_idx"]==6; print(items[0]["reason"])'
```

Expected:

```text
相邻 text 位于连续页面，且前一个 text 未以完整句结束符 . ! ? : ; 结尾
```

- [ ] **Step 5：确认报告继续被 Git 忽略**

Run:

```bash
git check-ignore -v \
  data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json
```

Expected: 报告命中 `.gitignore` 的 `data/` 规则。

- [ ] **Step 6：提交文档**

```bash
git add README.md
git commit -m "docs: describe cross-page candidate report"
```

- [ ] **Step 7：完成前进行新鲜验证**

Run:

```bash
.venv/bin/pytest -q
git diff --check
git status --short --branch
```

Expected:

- 47 个测试全部通过；
- `git diff --check` 无错误；
- 工作区无未提交修改；
- 当前分支仍为 `main`。
