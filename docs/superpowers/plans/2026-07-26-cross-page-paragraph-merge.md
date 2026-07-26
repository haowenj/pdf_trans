# 跨页段落合并 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一次 PDF 处理工作流中，使用同一份内存中的清洗结果和候选列表完成跨页段落链合并，写出 `normalized_content_list.json`，并让 Markdown 改为从该文件渲染。

**Architecture:** 保留现有文件级兼容接口，同时为清洗结果和候选报告增加内存接口；新增纯函数规范化模块，先完整校验候选图并构建全部连续链，再深拷贝生成规范化数组；工作流按“清洗 → 检测并写诊断报告 → 内存合并并写规范化文件 → 渲染 Markdown”的顺序执行。

**Tech Stack:** Python 3.11、标准库 `copy` / `json` / `pathlib`、pytest 8、setuptools。

## Global Constraints

- 用户明确要求在当前会话直接实现，不启用子代理。
- 严格使用测试驱动开发：每项功能先写失败测试，确认失败原因正确，再写最小实现。
- `cross_page_candidates.json` 只作诊断报告；规范化步骤不得读取该文件。
- 合并必须使用本次运行中候选检测函数返回的对象，以及同一份内存中的 `cleaned_items`。
- 在修改或写出规范化结果前完成全部候选校验和链构建。
- 校验失败时终止工作流，不创建或覆盖本次的 `normalized_content_list.json`。
- 不修改 `cleaned_content_list.json`、`cross_page_candidates.json` 或 MinerU 原始文件。
- 不把 `normalized_content_list.json` 加入 Git；真实运行结果继续位于已忽略的 `data/`。
- 不改变候选检测规则，不增加哈希、版本号或旧报告兼容机制。
- 不顺手实现跨页表格、Document IR、Agent、数据库或 Web 接口。

---

## Task 1：暴露清洗结果和候选报告的内存接口

**Files:**

- Modify: `src/mineru_cleaner/cleaner.py`
- Modify: `src/mineru_cleaner/cross_page.py`
- Test: `tests/test_cleaner.py`
- Test: `tests/test_cross_page.py`

### 1.1 先为清洗内存接口写失败测试

- [ ] 在 `tests/test_cleaner.py` 的导入中加入：

```python
from mineru_cleaner.cleaner import (
    clean_content_list_file,
    clean_content_list_file_with_items,
    clean_items,
    summarize_items,
)
```

- [ ] 新增测试，证明新接口返回的列表与写出的 JSON 完全一致，同时保持原统计语义：

```python
def test_clean_content_list_file_with_items_returns_written_items(tmp_path):
    source = tmp_path / "content_list.json"
    output = tmp_path / "cleaned_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "header", "text": "页眉"},
                {
                    "type": "text",
                    "text": "正文",
                    "page_idx": 3,
                    "bbox": [1, 2, 3, 4],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cleaned, stats = clean_content_list_file_with_items(source, output)

    assert cleaned == [
        {
            "type": "text",
            "text": "正文",
            "page_idx": 3,
            "bbox": [1, 2, 3, 4],
        }
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == cleaned
    assert stats.before_count == 2
    assert stats.filtered_count == 1
    assert stats.after_count == 1
```

### 1.2 先为独立候选报告写入函数写失败测试

- [ ] 在 `tests/test_cross_page.py` 的导入中加入
  `write_cross_page_candidates_file`。

- [ ] 新增测试，确认写入内容、UTF-8 和末尾换行，且不修改候选输入：

```python
def test_write_cross_page_candidates_file_writes_existing_candidates(
    tmp_path,
):
    output = tmp_path / "cross_page_candidates.json"
    candidates = [
        {
            "previous_index": 0,
            "next_index": 1,
            "previous_page_idx": 5,
            "next_page_idx": 6,
            "previous_text": "上半段",
            "next_text": "下半段",
            "reason": "诊断原因",
        }
    ]
    original = copy.deepcopy(candidates)

    write_cross_page_candidates_file(candidates, output)

    assert json.loads(output.read_text(encoding="utf-8")) == candidates
    assert output.read_text(encoding="utf-8").endswith("\n")
    assert candidates == original
```

### 1.3 运行测试，确认因接口尚不存在而失败

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_cleaner.py tests/test_cross_page.py
```

预期：收集测试时因两个新函数尚未定义而失败；不得出现与本任务无关的失败。

### 1.4 实现清洗接口并保持旧接口兼容

- [ ] 在 `src/mineru_cleaner/cleaner.py` 中，把现有文件读取、校验、清洗和写入逻辑移动到：

```python
def clean_content_list_file_with_items(
    source: Path,
    output: Path,
) -> tuple[list[Any], CleaningStats]:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    cleaned, stats = clean_items(items)
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入清洗结果：{exc}") from exc
    return cleaned, stats
```

- [ ] 将旧接口改为薄包装，返回值和异常行为保持不变：

```python
def clean_content_list_file(source: Path, output: Path) -> CleaningStats:
    _, stats = clean_content_list_file_with_items(source, output)
    return stats
```

### 1.5 拆出候选报告写入接口

- [ ] 在 `src/mineru_cleaner/cross_page.py` 增加：

```python
def write_cross_page_candidates_file(
    candidates: list[dict[str, Any]],
    output: Path,
) -> None:
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(candidates, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入跨页候选报告：{exc}") from exc
```

- [ ] 将现有便利函数的写入部分替换为复用新函数：

```python
candidates = detect_cross_page_candidates(items)
write_cross_page_candidates_file(candidates, output)
return len(candidates)
```

### 1.6 验证 Task 1 并提交

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_cleaner.py tests/test_cross_page.py
```

预期：原有测试和新增测试全部通过。

- [ ] 运行完整回归：

```bash
.venv/bin/pytest -q
```

预期：49 个测试通过。

- [ ] 提交：

```bash
git add src/mineru_cleaner/cleaner.py \
  src/mineru_cleaner/cross_page.py \
  tests/test_cleaner.py \
  tests/test_cross_page.py
git commit -m "refactor: expose in-memory content results"
```

---

## Task 2：实现候选链校验和规范化纯函数

**Files:**

- Modify: `src/mineru_cleaner/errors.py`
- Create: `src/mineru_cleaner/normalizer.py`
- Create: `tests/test_normalizer.py`

### 2.1 为基础合并行为写失败测试

- [ ] 创建 `tests/test_normalizer.py`，先加入公共导入：

```python
import copy
import json

import pytest

from mineru_cleaner.errors import NormalizationError
from mineru_cleaner.normalizer import (
    normalize_cross_page_items,
    write_normalized_content_list_file,
)
```

- [ ] 新增两对象合并测试，覆盖字段保留、边界空格、顺序和输入不变：

```python
def test_normalize_cross_page_items_merges_pair_without_mutating_inputs():
    items = [
        {"type": "image", "img_path": "images/before.jpg"},
        {
            "type": "text",
            "text": "  上一页末尾   ",
            "page_idx": 5,
            "bbox": [1, 2, 3, 4],
            "custom": {"keep": True},
        },
        {
            "type": "text",
            "text": "   下一页开头  ",
            "page_idx": 6,
            "bbox": [5, 6, 7, 8],
        },
        {"type": "table", "table_body": "<table></table>"},
    ]
    candidates = [{"previous_index": 1, "next_index": 2}]
    original_items = copy.deepcopy(items)
    original_candidates = copy.deepcopy(candidates)

    normalized = normalize_cross_page_items(items, candidates)

    assert len(normalized) == 3
    assert normalized[0] == items[0]
    assert normalized[2] == items[3]
    assert normalized[1] == {
        "type": "text",
        "text": "  上一页末尾 下一页开头  ",
        "page_idx": 5,
        "bbox": [1, 2, 3, 4],
        "custom": {"keep": True},
        "source_page_indices": [5, 6],
        "source_bboxes": [[1, 2, 3, 4], [5, 6, 7, 8]],
        "merged_cross_page": True,
    }
    assert items == original_items
    assert candidates == original_candidates
    assert normalized[0] is not items[0]
    assert normalized[1]["custom"] is not items[1]["custom"]
```

### 2.2 为重叠链和互不相关的链写失败测试

- [ ] 新增测试：

```python
def test_normalize_cross_page_items_merges_overlapping_and_independent_chains():
    items = [
        {"type": "text", "text": "A", "page_idx": 0, "bbox": [0]},
        {"type": "text", "text": "B", "page_idx": 1, "bbox": [1]},
        {"type": "text", "text": "C", "page_idx": 2, "bbox": [2]},
        {"type": "image", "img_path": "separator.jpg"},
        {"type": "text", "text": "D", "page_idx": 8, "bbox": [8]},
        {"type": "text", "text": "E", "page_idx": 9, "bbox": [9]},
        {"type": "table", "table_body": "<table></table>"},
    ]
    candidates = [
        {"previous_index": 1, "next_index": 2},
        {"previous_index": 4, "next_index": 5},
        {"previous_index": 0, "next_index": 1},
    ]

    normalized = normalize_cross_page_items(items, candidates)

    assert [item["type"] for item in normalized] == [
        "text",
        "image",
        "text",
        "table",
    ]
    assert normalized[0]["text"] == "A B C"
    assert normalized[0]["source_page_indices"] == [0, 1, 2]
    assert normalized[0]["source_bboxes"] == [[0], [1], [2]]
    assert normalized[2]["text"] == "D E"
    assert normalized[2]["source_page_indices"] == [8, 9]
```

这同时证明候选输入顺序不决定链中文字顺序，最终始终按原数组索引连接。

### 2.3 为深拷贝、缺失 bbox 和空候选写失败测试

- [ ] 增加缺失 `bbox` 测试：

```python
def test_normalize_cross_page_items_records_missing_bbox_as_none():
    items = [
        {"type": "text", "text": "前", "page_idx": 2},
        {"type": "text", "text": "后", "page_idx": 3, "bbox": [3]},
    ]

    normalized = normalize_cross_page_items(
        items,
        [{"previous_index": 0, "next_index": 1}],
    )

    assert normalized[0]["source_bboxes"] == [None, [3]]
```

- [ ] 增加空候选测试：

```python
def test_normalize_cross_page_items_deep_copies_when_no_candidates():
    items = [{"type": "image", "metadata": {"value": 1}}]

    normalized = normalize_cross_page_items(items, [])

    assert normalized == items
    assert normalized is not items
    assert normalized[0] is not items[0]
    assert normalized[0]["metadata"] is not items[0]["metadata"]
```

### 2.4 为全部非法候选写参数化失败测试

- [ ] 在测试文件加入严格整数和候选图校验用例。每个 case 均使用合法基础数组，只替换触发错误所需的最小字段：

```python
@pytest.mark.parametrize(
    ("items", "candidates", "message"),
    [
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            ["not-a-dict"],
            "候选必须是对象",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": True, "next_index": 1}],
            "索引必须是整数",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": -1, "next_index": 0}],
            "索引越界",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 1, "next_index": 2}],
            "索引越界",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [{"previous_index": 0, "next_index": 2}],
            "索引必须相邻",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 0, "next_index": 1},
            ],
            "候选对重复",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 0, "next_index": 2},
            ],
            "多个后继",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ],
            [
                {"previous_index": 0, "next_index": 2},
                {"previous_index": 1, "next_index": 2},
            ],
            "多个前驱",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [
                {"previous_index": 0, "next_index": 1},
                {"previous_index": 1, "next_index": 0},
            ],
            "形成环",
        ),
        (
            [
                {"type": "image", "img_path": "a.jpg", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "必须都是 text",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": True},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "page_idx 必须是整数",
        ),
        (
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 2},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "页面必须连续",
        ),
        (
            [
                {"type": "text", "text": "   ", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
            ],
            [{"previous_index": 0, "next_index": 1}],
            "text 必须是非空字符串",
        ),
    ],
)
def test_normalize_cross_page_items_rejects_invalid_candidates(
    items,
    candidates,
    message,
):
    with pytest.raises(NormalizationError, match=message):
        normalize_cross_page_items(items, candidates)
```

实现时校验顺序必须使“重复、分叉、汇聚、环”得到各自明确错误，不被更一般的
“索引不相邻”提前掩盖。

### 2.5 为文件输出和 139→138 验收写失败测试

- [ ] 增加规范化文件格式测试：

```python
def test_write_normalized_content_list_file_writes_utf8_json(tmp_path):
    output = tmp_path / "normalized_content_list.json"
    items = [{"type": "text", "text": "中文"}]

    write_normalized_content_list_file(items, output)

    assert json.loads(output.read_text(encoding="utf-8")) == items
    assert "中文" in output.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8").endswith("\n")
```

- [ ] 增加自包含的数量与原索引 37 删除测试：

```python
def test_normalize_cross_page_items_reduces_139_items_to_138():
    items = [
        {"type": "image", "img_path": f"images/{index}.jpg"}
        for index in range(139)
    ]
    items[36] = {
        "type": "text",
        "text": "前半段唯一标记",
        "page_idx": 5,
        "bbox": [36],
    }
    items[37] = {
        "type": "text",
        "text": "后半段唯一标记",
        "page_idx": 6,
        "bbox": [37],
    }

    normalized = normalize_cross_page_items(
        items,
        [{"previous_index": 36, "next_index": 37}],
    )

    assert len(items) == 139
    assert len(normalized) == 138
    assert "前半段唯一标记" in normalized[36]["text"]
    assert "后半段唯一标记" in normalized[36]["text"]
    assert normalized[36]["source_page_indices"] == [5, 6]
    assert normalized[36]["source_bboxes"] == [[36], [37]]
    assert not any(
        item == items[37]
        for item in normalized
    )
```

### 2.6 运行测试，确认因模块尚不存在而失败

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_normalizer.py
```

预期：因 `NormalizationError` 或 `mineru_cleaner.normalizer` 尚不存在而失败。

### 2.7 定义规范化异常

- [ ] 在 `src/mineru_cleaner/errors.py` 增加：

```python
class NormalizationError(MinerUCleanerError):
    """Raised when cross-page normalization validation fails."""
```

该异常继承现有 CLI 已捕获的 `MinerUCleanerError`。

### 2.8 实现候选图校验和链构建

- [ ] 创建 `src/mineru_cleaner/normalizer.py`，导入：

```python
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import NormalizationError
```

- [ ] 实现私有函数 `_build_candidate_chains`，遵守以下顺序：

1. 逐条确认候选为字典；
2. 使用 `type(value) is int` 确认两个索引为严格整数，排除 `bool`；
3. 检查索引非负且小于 `len(items)`；
4. 先记录并拒绝重复边、多个后继和多个前驱；
5. 根据 `successors` / `predecessors` 寻找链首并遍历所有链；
6. 如果遍历边数少于候选边数，说明存在环；
7. 图结构验证完成后，再逐边验证 `next == previous + 1`；
8. 验证两个原对象均为字典且 `type == "text"`；
9. 验证页码为严格整数且后一页等于前一页加一；
10. 验证两个 `text` 都是 `str` 且 `strip()` 后非空。

核心结构如下：

```python
def _build_candidate_chains(
    items: list[Any],
    candidates: list[dict[str, Any]],
) -> list[list[int]]:
    successors: dict[int, int] = {}
    predecessors: dict[int, int] = {}
    pairs: set[tuple[int, int]] = set()

    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise NormalizationError(
                f"第 {position} 条候选必须是对象"
            )

        previous_index = candidate.get("previous_index")
        next_index = candidate.get("next_index")
        if type(previous_index) is not int or type(next_index) is not int:
            raise NormalizationError(
                f"第 {position} 条候选索引必须是整数"
            )
        if not (
            0 <= previous_index < len(items)
            and 0 <= next_index < len(items)
        ):
            raise NormalizationError(
                f"第 {position} 条候选索引越界"
            )

        pair = (previous_index, next_index)
        if pair in pairs:
            raise NormalizationError(f"候选对重复：{pair}")
        if previous_index in successors:
            raise NormalizationError(
                f"索引 {previous_index} 存在多个后继"
            )
        if next_index in predecessors:
            raise NormalizationError(
                f"索引 {next_index} 存在多个前驱"
            )

        pairs.add(pair)
        successors[previous_index] = next_index
        predecessors[next_index] = previous_index

    chains: list[list[int]] = []
    visited_pairs: set[tuple[int, int]] = set()
    starts = sorted(
        index for index in successors if index not in predecessors
    )
    for start in starts:
        chain = [start]
        current = start
        while current in successors:
            next_index = successors[current]
            pair = (current, next_index)
            if pair in visited_pairs:
                raise NormalizationError("候选形成环")
            visited_pairs.add(pair)
            chain.append(next_index)
            current = next_index
        chains.append(chain)

    if len(visited_pairs) != len(pairs):
        raise NormalizationError("候选形成环")

    for chain in chains:
        for previous_index, next_index in zip(chain, chain[1:]):
            _validate_candidate_edge(
                items,
                previous_index,
                next_index,
            )

    return chains
```

- [ ] 将逐边对象校验放进 `_validate_candidate_edge`，错误消息需包含对应索引，便于
  CLI 定位：

```python
def _validate_candidate_edge(
    items: list[Any],
    previous_index: int,
    next_index: int,
) -> None:
    if next_index != previous_index + 1:
        raise NormalizationError(
            f"候选索引必须相邻：{previous_index} -> {next_index}"
        )

    previous = items[previous_index]
    next_item = items[next_index]
    if (
        not isinstance(previous, dict)
        or not isinstance(next_item, dict)
        or previous.get("type") != "text"
        or next_item.get("type") != "text"
    ):
        raise NormalizationError(
            f"候选对象必须都是 text：{previous_index} -> {next_index}"
        )

    previous_page_idx = previous.get("page_idx")
    next_page_idx = next_item.get("page_idx")
    if (
        type(previous_page_idx) is not int
        or type(next_page_idx) is not int
    ):
        raise NormalizationError(
            f"候选对象的 page_idx 必须是整数："
            f"{previous_index} -> {next_index}"
        )
    if next_page_idx != previous_page_idx + 1:
        raise NormalizationError(
            f"候选对象页面必须连续：{previous_index} -> {next_index}"
        )

    previous_text = previous.get("text")
    next_text = next_item.get("text")
    if (
        not isinstance(previous_text, str)
        or not previous_text.strip()
        or not isinstance(next_text, str)
        or not next_text.strip()
    ):
        raise NormalizationError(
            f"候选对象的 text 必须是非空字符串："
            f"{previous_index} -> {next_index}"
        )
```

### 2.9 实现链合并纯函数

- [ ] 实现 `normalize_cross_page_items`：

```python
def normalize_cross_page_items(
    items: list[Any],
    candidates: list[dict[str, Any]],
) -> list[Any]:
    chains = _build_candidate_chains(items, candidates)
    chains_by_start = {chain[0]: chain for chain in chains}
    removed_indices = {
        index
        for chain in chains
        for index in chain[1:]
    }
    normalized: list[Any] = []

    for index, item in enumerate(items):
        chain = chains_by_start.get(index)
        if chain is not None:
            merged = copy.deepcopy(items[chain[0]])
            merged_text = items[chain[0]]["text"]
            for source_index in chain[1:]:
                merged_text = (
                    merged_text.rstrip()
                    + " "
                    + items[source_index]["text"].lstrip()
                )
            merged["text"] = merged_text
            merged["source_page_indices"] = [
                items[source_index]["page_idx"]
                for source_index in chain
            ]
            merged["source_bboxes"] = [
                copy.deepcopy(items[source_index].get("bbox"))
                for source_index in chain
            ]
            merged["merged_cross_page"] = True
            normalized.append(merged)
        elif index not in removed_indices:
            normalized.append(copy.deepcopy(item))

    return normalized
```

注意：

- `chains` 必须在任何深拷贝或数组输出前全部构建成功；
- `page_idx` 不重新赋值，因此自然保留链首对象原值；
- 若链首已有 `source_page_indices`、`source_bboxes` 或
  `merged_cross_page`，必须用本次计算结果覆盖；
- 未参与对象也要深拷贝，确保纯函数不共享嵌套可变结构。

### 2.10 实现规范化文件写入函数

- [ ] 在同一模块实现：

```python
def write_normalized_content_list_file(
    items: list[Any],
    output: Path,
) -> None:
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise NormalizationError(
            f"无法写入规范化结果：{exc}"
        ) from exc
```

### 2.11 验证 Task 2 并提交

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_normalizer.py
```

预期：19 个测试通过，其中参数化非法输入覆盖 13 个 case。

- [ ] 运行完整回归：

```bash
.venv/bin/pytest -q
```

预期：68 个测试通过。

- [ ] 提交：

```bash
git add src/mineru_cleaner/errors.py \
  src/mineru_cleaner/normalizer.py \
  tests/test_normalizer.py
git commit -m "feat: normalize cross-page paragraph chains"
```

---

## Task 3：把内存规范化接入工作流、Markdown 和 CLI

**Files:**

- Modify: `src/mineru_cleaner/workflow.py`
- Modify: `src/mineru_cleaner/__main__.py`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_cli.py`

### 3.1 先扩展工作流输出测试

- [ ] 修改 `test_process_pdf_runs_complete_workflow`，在无候选场景增加：

```python
assert result.normalized_count == 2
assert result.normalized_path == (
    tmp_path / "data/paper/hybrid_auto/normalized_content_list.json"
).resolve()
assert json.loads(
    result.normalized_path.read_text(encoding="utf-8")
) == [
    body,
    {"type": "chart", "img_path": "images/a.jpg"},
]
```

- [ ] 将
  `test_process_pdf_writes_cross_page_report_without_changing_other_outputs`
  扩展为：

```python
assert result.after_count == 2
assert result.normalized_count == 1
assert result.normalized_path == (
    tmp_path / "data/paper/hybrid_auto/normalized_content_list.json"
).resolve()
assert json.loads(
    result.normalized_path.read_text(encoding="utf-8")
) == [
    {
        "type": "text",
        "text": "上一页未结束 下一页继续。",
        "page_idx": 0,
        "source_page_indices": [0, 1],
        "source_bboxes": [None, None],
        "merged_cross_page": True,
    }
]
assert result.markdown_path.read_text(encoding="utf-8") == (
    "上一页未结束 下一页继续。\n"
)
```

原有 assertions 必须继续确认：

- `cleaned_content_list.json` 仍包含两个独立对象；
- `cross_page_candidates.json` 仍包含完整诊断信息；
- `candidate_count == 1`。

### 3.2 用对象身份测试证明工作流不回读候选报告

- [ ] 新增测试，通过包装真实纯函数记录对象身份：

```python
def test_process_pdf_passes_same_in_memory_items_and_candidates_to_normalizer(
    tmp_path,
    monkeypatch,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "text", "text": "前", "page_idx": 0},
                {"type": "text", "text": "后", "page_idx": 1},
            ]
        )
    )
    seen = {}

    from mineru_cleaner import workflow

    real_detect = workflow.detect_cross_page_candidates
    real_normalize = workflow.normalize_cross_page_items

    def tracking_detect(items):
        candidates = real_detect(items)
        seen["detected_items"] = items
        seen["detected_candidates"] = candidates
        return candidates

    def tracking_normalize(items, candidates):
        seen["normalized_items"] = items
        seen["normalized_candidates"] = candidates
        return real_normalize(items, candidates)

    monkeypatch.setattr(
        workflow,
        "detect_cross_page_candidates",
        tracking_detect,
    )
    monkeypatch.setattr(
        workflow,
        "normalize_cross_page_items",
        tracking_normalize,
    )

    process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert seen["normalized_items"] is seen["detected_items"]
    assert (
        seen["normalized_candidates"]
        is seen["detected_candidates"]
    )
```

此测试是“候选报告仅用于诊断”的关键回归保护。

### 3.3 先为校验失败不写 normalized 增加工作流测试

- [ ] 在 `tests/test_workflow.py` 导入 `NormalizationError`，并新增测试：

```python
def test_process_pdf_does_not_write_normalized_when_validation_fails(
    tmp_path,
    monkeypatch,
):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {"type": "text", "text": "A", "page_idx": 0},
                {"type": "text", "text": "B", "page_idx": 1},
                {"type": "text", "text": "C", "page_idx": 2},
            ]
        )
    )
    invalid_candidates = [
        {"previous_index": 0, "next_index": 2}
    ]
    monkeypatch.setattr(
        "mineru_cleaner.workflow.detect_cross_page_candidates",
        lambda items: invalid_candidates,
    )
    output_dir = tmp_path / "data/paper/hybrid_auto"

    with pytest.raises(
        NormalizationError,
        match="索引必须相邻",
    ):
        process_pdf(pdf, data_dir=tmp_path / "data", client=client)

    assert not (
        output_dir / "normalized_content_list.json"
    ).exists()
    assert not (output_dir / "rendered.md").exists()
    assert json.loads(
        (output_dir / "cross_page_candidates.json").read_text(
            encoding="utf-8"
        )
    ) == invalid_candidates
```

这证明诊断报告先写出，但规范化校验失败后不会调用 normalized 写入或 Markdown 渲染。

### 3.4 先扩展 CLI 测试

- [ ] 在两个构造 `WorkflowResult` 的测试中加入：

```python
normalized_path=Path("normalized_content_list.json"),
normalized_count=6,
```

首个测试使用实际临时路径变量 `normalized`，并在精确输出末尾追加：

```python
"规范化后数量：6\n"
f"规范化文件：{normalized}\n"
```

第二个测试继续用相对路径，并断言：

```python
assert "规范化后数量：1" in captured.out
assert "规范化文件：normalized_content_list.json" in captured.out
```

第二个 fake result 的 `normalized_count` 设置为 `1`。

### 3.5 运行测试，确认缺少新结果字段和工作流步骤

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_workflow.py tests/test_cli.py
```

预期：因 `WorkflowResult` 没有规范化字段、规范化文件未生成、Markdown 仍从 cleaned
渲染而失败。

### 3.6 调整 WorkflowResult 和处理顺序

- [ ] 修改 `src/mineru_cleaner/workflow.py` 导入：

```python
from mineru_cleaner.cleaner import (
    ContentStats,
    clean_content_list_file_with_items,
)
from mineru_cleaner.cross_page import (
    detect_cross_page_candidates,
    write_cross_page_candidates_file,
)
from mineru_cleaner.normalizer import (
    normalize_cross_page_items,
    write_normalized_content_list_file,
)
```

- [ ] 给 `WorkflowResult` 增加：

```python
normalized_path: Path
normalized_count: int
```

保持 `after_count` 为清洗后、合并前数量。

- [ ] 将 `process_pdf` 中从清洗开始的顺序替换为：

```python
output_path = source_path.parent / "cleaned_content_list.json"
cleaned_items, stats = clean_content_list_file_with_items(
    source_path,
    output_path,
)

candidates = detect_cross_page_candidates(cleaned_items)
candidates_path = output_path.parent / "cross_page_candidates.json"
write_cross_page_candidates_file(candidates, candidates_path)

normalized_items = normalize_cross_page_items(
    cleaned_items,
    candidates,
)
normalized_path = output_path.parent / "normalized_content_list.json"
write_normalized_content_list_file(
    normalized_items,
    normalized_path,
)

markdown_path = output_path.parent / "rendered.md"
render_content_list_file(normalized_path, markdown_path)
```

- [ ] 返回结果时使用：

```python
normalized_path=normalized_path.resolve(),
normalized_count=len(normalized_items),
candidate_count=len(candidates),
```

校验失败发生在 `write_normalized_content_list_file` 之前，因此不会写出本次 normalized
文件；不要用 `try/finally` 写占位文件。

### 3.7 增加 CLI 输出

- [ ] 在 `src/mineru_cleaner/__main__.py` 的候选报告输出之后追加：

```python
print(f"规范化后数量：{result.normalized_count}")
print(f"规范化文件：{result.normalized_path}")
```

### 3.8 验证 Task 3 并提交

- [ ] 运行：

```bash
.venv/bin/pytest -q tests/test_workflow.py tests/test_cli.py
```

预期：10 个测试通过。

- [ ] 运行完整回归：

```bash
.venv/bin/pytest -q
```

预期：70 个测试通过。

- [ ] 提交：

```bash
git add src/mineru_cleaner/workflow.py \
  src/mineru_cleaner/__main__.py \
  tests/test_workflow.py \
  tests/test_cli.py
git commit -m "feat: integrate normalized content workflow"
```

---

## Task 4：更新使用文档并用真实 MinerU 样例验收

**Files:**

- Modify: `README.md`
- Runtime-only, ignored: `data/processes-12-02888-v2/hybrid_auto/normalized_content_list.json`
- Runtime-only, ignored: `data/processes-12-02888-v2/hybrid_auto/rendered.md`

### 4.1 更新 README 的工作流和输出说明

- [ ] 在 `README.md` 中明确完整顺序：

```text
content_list.json
  -> cleaned_content_list.json
  -> cross_page_candidates.json（诊断报告）
  -> normalized_content_list.json
  -> rendered.md
```

- [ ] 说明：

- `cross_page_candidates.json` 不会被合并步骤重新读取；
- 重叠候选 `0→1、1→2` 会形成一条链并合并为索引 0 对象；
- `normalized_content_list.json` 保留链首字段并增加来源页码、bbox 和合并标记；
- Markdown 从 normalized 文件渲染；
- CLI 额外打印规范化后数量和文件路径。

### 4.2 完整测试和静态占位检查

- [ ] 运行：

```bash
.venv/bin/pytest -q
```

预期：70 个测试通过。

- [ ] 扫描调试占位：

```bash
rg -n "TODO|FIXME|pass$|NotImplemented" src tests README.md
```

预期：没有本功能遗留的占位实现；若命中已有合法内容，逐项人工确认。

### 4.3 记录真实样例的不可变文件摘要

- [ ] 确认样例路径存在：

```bash
test -f data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json
test -f data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json
```

- [ ] 在运行规范化前记录：

```bash
shasum -a 256 \
  data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json \
  data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json
```

保存命令输出，供 4.5 比较。

### 4.4 使用内存检测结果生成真实样例 normalized 和 Markdown

- [ ] 运行以下一次性验证命令。它读取 cleaned 作为本地验收输入，但候选仍由本次
  内存检测返回，绝不读取候选报告来合并：

```bash
.venv/bin/python - <<'PY'
import json
from pathlib import Path

from mineru_cleaner.cross_page import detect_cross_page_candidates
from mineru_cleaner.normalizer import (
    normalize_cross_page_items,
    write_normalized_content_list_file,
)
from mineru_cleaner.renderer import render_content_list_file

base = Path(
    "data/processes-12-02888-v2/hybrid_auto"
)
cleaned_path = base / "cleaned_content_list.json"
normalized_path = base / "normalized_content_list.json"
markdown_path = base / "rendered.md"

cleaned = json.loads(cleaned_path.read_text(encoding="utf-8"))
candidates = detect_cross_page_candidates(cleaned)
normalized = normalize_cross_page_items(cleaned, candidates)
write_normalized_content_list_file(normalized, normalized_path)
render_content_list_file(normalized_path, markdown_path)

previous_text = cleaned[36]["text"]
next_text = cleaned[37]["text"]
merged = normalized[36]

assert len(cleaned) == 139
assert len(candidates) == 1
assert candidates[0]["previous_index"] == 36
assert candidates[0]["next_index"] == 37
assert len(normalized) == 138
assert previous_text.rstrip() in merged["text"]
assert next_text.lstrip() in merged["text"]
assert merged["page_idx"] == 5
assert merged["source_page_indices"] == [5, 6]
assert merged["source_bboxes"] == [
    cleaned[36].get("bbox"),
    cleaned[37].get("bbox"),
]
assert merged["merged_cross_page"] is True
assert not any(item == cleaned[37] for item in normalized)

print(
    f"cleaned={len(cleaned)}, "
    f"candidates={len(candidates)}, "
    f"normalized={len(normalized)}"
)
print(normalized_path.resolve())
print(markdown_path.resolve())
PY
```

预期输出包含：

```text
cleaned=139, candidates=1, normalized=138
```

### 4.5 确认 cleaned 和候选报告未变化

- [ ] 再次运行：

```bash
shasum -a 256 \
  data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json \
  data/processes-12-02888-v2/hybrid_auto/cross_page_candidates.json
```

预期：两行摘要与 4.3 完全一致。`rendered.md` 允许且应当变化，因为它现在基于
normalized 内容重新生成。

### 4.6 确认运行产物仍被 Git 忽略

- [ ] 运行：

```bash
git check-ignore -v \
  data/processes-12-02888-v2/hybrid_auto/normalized_content_list.json \
  data/processes-12-02888-v2/hybrid_auto/rendered.md
git status --short
```

预期：两个运行产物由 `data/` 规则忽略，状态中只出现计划内的 README 修改。

### 4.7 提交文档

- [ ] 提交：

```bash
git add README.md
git commit -m "docs: describe normalized content workflow"
```

---

## Final Verification

### 功能回归

- [ ] 运行完整测试：

```bash
.venv/bin/pytest -q
```

预期：70 个测试通过。

### 工作树检查

- [ ] 运行：

```bash
git status --short
git log -5 --oneline
```

预期：

- 工作树干净；
- 最新提交依次包含内存接口、规范化核心、工作流集成和 README；
- `data/` 下的样例产物不进入提交。

### 需求逐项核对

- [ ] 候选检测返回候选对象，报告仅作诊断。
- [ ] 合并使用同一次运行的 `cleaned_items` 和候选对象身份。
- [ ] 允许首尾相接的重叠链和多条独立链。
- [ ] 拒绝重复边、分叉、汇聚、环、非相邻索引、非 text、非连续页面和越界索引。
- [ ] 文本按原索引顺序连接，每个边界恰好一个空格。
- [ ] 来源页码和 bbox 按链顺序累积，缺失 bbox 写 `null`。
- [ ] 只保留链首对象，`page_idx` 保持链首页码。
- [ ] 未参与对象深拷贝原样保留，顺序不变。
- [ ] 校验失败不写 normalized 文件。
- [ ] Markdown 从 `normalized_content_list.json` 渲染。
- [ ] 真实样例由 139 个对象变为 138 个，原索引 37 不再独立存在。
- [ ] cleaned、候选报告和 MinerU 原始结果未被修改。
