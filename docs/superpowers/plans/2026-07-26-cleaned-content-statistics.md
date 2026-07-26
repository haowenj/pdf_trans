# 清洗结果统计实施计划（Implementation Plan）

> **供智能体执行者使用：** 必须使用 `superpowers:subagent-driven-development`
>（推荐）或 `superpowers:executing-plans`，按任务逐项执行本计划。步骤使用
> checkbox（`- [ ]`）跟踪。

**Goal（目标）：** 在现有清洗完成后统计清洗结果中的 `type`、`text_level` 和
`page_idx`，将统计输出到终端，同时保证 `cleaned_content_list.json` 的内容和结构
不发生变化。

**Architecture（架构）：** 在 `cleaner` 中增加独立纯函数
`summarize_items()`，清洗函数将统计结果封装进 `CleaningStats`。`workflow`
只透传统计对象，CLI 只负责按固定顺序展示，不重新读取或修改 JSON 文件。

**Tech Stack（技术栈）：** Python 3.11+、标准库 `collections.Counter`、
`dataclasses`、`json`，测试使用 `pytest`。

**设计依据：**
[清洗结果统计设计](../specs/2026-07-26-cleaned-content-statistics-design.md)

## Global Constraints（全局约束）

- 统计输入必须是清洗后的内存列表。
- `cleaned_content_list.json` 的数组顺序、对象字段、字段值和顶层结构不得改变。
- 不创建额外的统计 JSON 或文本文件。
- 仅字符串 `type` 计入类型统计，所有未知字符串类型都必须保留并计数。
- 仅 `type == "text"` 且 `type(text_level) is int` 的元素计入标题统计。
- 仅 `type(page_idx) is int` 的元素计入页码统计。
- 布尔值不视为有效的 `text_level` 或 `page_idx`。
- `type` 按字符串升序输出；`text_level` 和 `page_idx` 按整数升序输出。
- 空分组在终端输出 `  （无）`。
- 现有 PDF 解析、清洗规则、`svr_url` 参数和输出路径不得改变。
- 所有行为修改都必须先看到对应测试失败，再编写最小实现。

## 文件结构

- 修改 `src/mineru_cleaner/cleaner.py`：定义 `ContentStats`，实现纯统计函数，并将
  统计结果加入 `CleaningStats`。
- 修改 `src/mineru_cleaner/workflow.py`：在 `WorkflowResult` 中透传
  `ContentStats`。
- 修改 `src/mineru_cleaner/__main__.py`：按固定顺序输出三组统计。
- 修改 `tests/test_cleaner.py`：测试统计规则、排序、非变异和 JSON 保真。
- 修改 `tests/test_workflow.py`：测试工作流透传统计结果。
- 修改 `tests/test_cli.py`：测试统计输出格式、排序和空分组。
- 修改 `README.md`：补充统计输出说明。
- `.gitignore` 已在编写本计划时加入 `.DS_Store`，执行代码计划时不需再次修改。

---

### 任务 1：清洗层统计模型与纯统计函数

**文件：**

- 修改：`tests/test_cleaner.py`
- 修改：`src/mineru_cleaner/cleaner.py`

**接口：**

- 消费：清洗后的 `list[Any]`。
- 产出：`ContentStats`、`summarize_items(items) -> ContentStats`。
- 修改：`CleaningStats` 新增 `content_stats: ContentStats`。
- 后续依赖：工作流读取 `CleaningStats.content_stats`。

- [ ] **步骤 1：先修改清洗测试，声明统计行为**

对 `tests/test_cleaner.py` 应用以下修改：

```diff
@@
+from copy import deepcopy
 import json
@@
 from mineru_cleaner.cleaner import (
     CleaningStats,
+    ContentStats,
     clean_content_list_file,
     clean_items,
+    summarize_items,
 )
@@
-    assert stats == CleaningStats(before_count=11, filtered_count=4, after_count=7)
+    assert stats == CleaningStats(
+        before_count=11,
+        filtered_count=4,
+        after_count=7,
+        content_stats=ContentStats(
+            type_counts={
+                "chart": 1,
+                "future_type": 1,
+                "image": 1,
+                "ref_text": 1,
+                "table": 1,
+                "text": 2,
+            },
+            text_level_count=1,
+            text_level_counts={2: 1},
+            page_idx_counts={1: 2, 2: 1, 3: 1},
+        ),
+    )
@@
-    assert stats == CleaningStats(before_count=3, filtered_count=0, after_count=3)
+    assert stats == CleaningStats(
+        before_count=3,
+        filtered_count=0,
+        after_count=3,
+        content_stats=ContentStats(
+            type_counts={"text": 3},
+            text_level_count=0,
+            text_level_counts={},
+            page_idx_counts={},
+        ),
+    )
+
+
+def test_summarize_items_counts_valid_fields_in_sorted_groups_without_mutation():
+    items = [
+        {"type": "text", "text": "标题二", "text_level": 2, "page_idx": 0},
+        {"type": "image", "text_level": 1, "page_idx": 1},
+        {"type": "text", "text": "标题一", "text_level": 1, "page_idx": 0},
+        {"type": "text", "text": "另一个标题", "text_level": 2, "page_idx": 1},
+        {"type": "future", "page_idx": 2},
+        {"type": 9, "page_idx": True},
+        {"text": "缺少类型", "text_level": False, "page_idx": "3"},
+        "原始非字典元素",
+    ]
+    original = deepcopy(items)
+
+    stats = summarize_items(items)
+
+    assert stats == ContentStats(
+        type_counts={"future": 1, "image": 1, "text": 3},
+        text_level_count=3,
+        text_level_counts={1: 1, 2: 2},
+        page_idx_counts={0: 2, 1: 2, 2: 1},
+    )
+    assert list(stats.type_counts) == ["future", "image", "text"]
+    assert list(stats.text_level_counts) == [1, 2]
+    assert list(stats.page_idx_counts) == [0, 1, 2]
+    assert items == original
@@
-    assert stats == CleaningStats(before_count=2, filtered_count=1, after_count=1)
+    assert stats == CleaningStats(
+        before_count=2,
+        filtered_count=1,
+        after_count=1,
+        content_stats=ContentStats(
+            type_counts={"text": 1},
+            text_level_count=0,
+            text_level_counts={},
+            page_idx_counts={0: 1},
+        ),
+    )
@@
     assert "中文正文" in output.read_text(encoding="utf-8")
+
+
+def test_clean_content_list_file_does_not_serialize_statistics(tmp_path):
+    source = tmp_path / "paper_content_list.json"
+    output = tmp_path / "cleaned_content_list.json"
+    heading = {
+        "type": "text",
+        "text": "标题",
+        "text_level": 1,
+        "page_idx": 0,
+        "bbox": [1, 2, 3, 4],
+    }
+    source.write_text(
+        json.dumps(
+            [{"type": "header", "text": "页眉"}, heading],
+            ensure_ascii=False,
+        ),
+        encoding="utf-8",
+    )
+
+    stats = clean_content_list_file(source, output)
+
+    assert json.loads(output.read_text(encoding="utf-8")) == [heading]
+    assert stats.content_stats == ContentStats(
+        type_counts={"text": 1},
+        text_level_count=1,
+        text_level_counts={1: 1},
+        page_idx_counts={0: 1},
+    )
```

- [ ] **步骤 2：运行清洗测试并确认红灯**

运行：

```bash
.venv/bin/pytest tests/test_cleaner.py -v
```

预期：测试收集失败，包含
`ImportError: cannot import name 'ContentStats'` 或
`ImportError: cannot import name 'summarize_items'`。

- [ ] **步骤 3：实现 ContentStats 和 summarize_items**

对 `src/mineru_cleaner/cleaner.py` 应用以下修改：

```diff
@@
 import json
+from collections import Counter
 from dataclasses import dataclass
@@
 from mineru_cleaner.errors import ContentListError


+@dataclass(frozen=True)
+class ContentStats:
+    type_counts: dict[str, int]
+    text_level_count: int
+    text_level_counts: dict[int, int]
+    page_idx_counts: dict[int, int]
+
+
 @dataclass(frozen=True)
 class CleaningStats:
     before_count: int
     filtered_count: int
     after_count: int
+    content_stats: ContentStats
@@
+def summarize_items(items: list[Any]) -> ContentStats:
+    type_counts: Counter[str] = Counter()
+    text_level_counts: Counter[int] = Counter()
+    page_idx_counts: Counter[int] = Counter()
+
+    for item in items:
+        if not isinstance(item, dict):
+            continue
+
+        item_type = item.get("type")
+        if isinstance(item_type, str):
+            type_counts[item_type] += 1
+
+        text_level = item.get("text_level")
+        if item_type == "text" and type(text_level) is int:
+            text_level_counts[text_level] += 1
+
+        page_idx = item.get("page_idx")
+        if type(page_idx) is int:
+            page_idx_counts[page_idx] += 1
+
+    sorted_text_level_counts = dict(sorted(text_level_counts.items()))
+    return ContentStats(
+        type_counts=dict(sorted(type_counts.items())),
+        text_level_count=sum(sorted_text_level_counts.values()),
+        text_level_counts=sorted_text_level_counts,
+        page_idx_counts=dict(sorted(page_idx_counts.items())),
+    )
+
+
 def clean_items(items: list[Any]) -> tuple[list[Any], CleaningStats]:
     cleaned = [item for item in items if not _should_filter(item)]
     before_count = len(items)
     after_count = len(cleaned)
     return cleaned, CleaningStats(
         before_count=before_count,
         filtered_count=before_count - after_count,
         after_count=after_count,
+        content_stats=summarize_items(cleaned),
     )
```

- [ ] **步骤 4：运行清洗测试并确认绿灯**

运行：

```bash
.venv/bin/pytest tests/test_cleaner.py -v
```

预期：6 个清洗测试全部通过。

- [ ] **步骤 5：运行当前完整测试套件**

运行：

```bash
.venv/bin/pytest -q
```

预期：25 个测试全部通过。

- [ ] **步骤 6：提交清洗层统计**

```bash
git add src/mineru_cleaner/cleaner.py tests/test_cleaner.py
git commit -m "feat: summarize cleaned content"
```

---

### 任务 2：工作流透传与 CLI 统计输出

**文件：**

- 修改：`tests/test_workflow.py`
- 修改：`tests/test_cli.py`
- 修改：`src/mineru_cleaner/workflow.py`
- 修改：`src/mineru_cleaner/__main__.py`

**接口：**

- 消费：`CleaningStats.content_stats`。
- 产出：`WorkflowResult.content_stats`。
- 终端输出：类型统计、标题总数及分组、页码分组。

- [ ] **步骤 1：先修改工作流和 CLI 测试**

对 `tests/test_workflow.py` 应用以下修改：

```diff
@@
 import pytest

+from mineru_cleaner.cleaner import ContentStats
 from mineru_cleaner.errors import WorkflowError
@@
     assert result.after_count == 2
+    assert result.content_stats == ContentStats(
+        type_counts={"chart": 1, "text": 1},
+        text_level_count=0,
+        text_level_counts={},
+        page_idx_counts={0: 1},
+    )
```

对 `tests/test_cli.py` 应用以下修改：

```diff
@@
 from mineru_cleaner import __main__ as cli
+from mineru_cleaner.cleaner import ContentStats
 from mineru_cleaner.client import DEFAULT_SVR_URL
@@
         return WorkflowResult(
             source_path=Path("paper_content_list.json"),
             output_path=output,
             before_count=10,
             filtered_count=3,
             after_count=7,
+            content_stats=ContentStats(
+                type_counts={"text": 5, "image": 2},
+                text_level_count=2,
+                text_level_counts={2: 1, 1: 1},
+                page_idx_counts={1: 3, 0: 4},
+            ),
         )
@@
-    assert "处理前数量：10" in captured.out
-    assert "过滤数量：3" in captured.out
-    assert "处理后数量：7" in captured.out
-    assert f"输出文件：{output}" in captured.out
+    assert captured.out == (
+        "处理前数量：10\n"
+        "过滤数量：3\n"
+        "处理后数量：7\n"
+        f"输出文件：{output}\n"
+        "清洗后 type 统计：\n"
+        "  image: 2\n"
+        "  text: 5\n"
+        "带 text_level 的 text 数量：2\n"
+        "按 text_level 分组：\n"
+        "  1: 1\n"
+        "  2: 1\n"
+        "每个 page_idx 的元素数量：\n"
+        "  0: 4\n"
+        "  1: 3\n"
+    )
     assert captured.err == ""
@@
-def test_main_passes_custom_svr_url(tmp_path, monkeypatch):
+def test_main_passes_custom_svr_url(tmp_path, monkeypatch, capsys):
@@
         return WorkflowResult(
             source_path=Path("paper_content_list.json"),
             output_path=Path("cleaned_content_list.json"),
             before_count=1,
             filtered_count=0,
             after_count=1,
+            content_stats=ContentStats(
+                type_counts={},
+                text_level_count=0,
+                text_level_counts={},
+                page_idx_counts={},
+            ),
         )
@@
     assert exit_code == 0
     assert received["svr_url"] == "http://mineru.example:7200"
+    captured = capsys.readouterr()
+    assert "带 text_level 的 text 数量：0" in captured.out
+    assert captured.out.count("  （无）") == 3
```

- [ ] **步骤 2：运行目标测试并确认红灯**

运行：

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

预期：至少一个测试失败，失败原因包含
`WorkflowResult.__init__() got an unexpected keyword argument 'content_stats'`
或 `WorkflowResult` 没有 `content_stats` 属性。

- [ ] **步骤 3：让工作流透传 ContentStats**

对 `src/mineru_cleaner/workflow.py` 应用以下修改：

```diff
@@
-from mineru_cleaner.cleaner import clean_content_list_file
+from mineru_cleaner.cleaner import ContentStats, clean_content_list_file
@@
 class WorkflowResult:
     source_path: Path
     output_path: Path
     before_count: int
     filtered_count: int
     after_count: int
+    content_stats: ContentStats
@@
         before_count=stats.before_count,
         filtered_count=stats.filtered_count,
         after_count=stats.after_count,
+        content_stats=stats.content_stats,
     )
```

- [ ] **步骤 4：实现固定顺序的 CLI 展示**

对 `src/mineru_cleaner/__main__.py` 应用以下修改：

```diff
@@
 from mineru_cleaner.workflow import process_pdf


+def _print_group(
+    title: str,
+    counts: dict[str, int] | dict[int, int],
+) -> None:
+    print(title)
+    if not counts:
+        print("  （无）")
+        return
+    for key in sorted(counts):
+        print(f"  {key}: {counts[key]}")
+
+
 def build_parser() -> argparse.ArgumentParser:
@@
     print(f"处理后数量：{result.after_count}")
     print(f"输出文件：{result.output_path}")
+    _print_group("清洗后 type 统计：", result.content_stats.type_counts)
+    print(
+        "带 text_level 的 text 数量："
+        f"{result.content_stats.text_level_count}"
+    )
+    _print_group(
+        "按 text_level 分组：",
+        result.content_stats.text_level_counts,
+    )
+    _print_group(
+        "每个 page_idx 的元素数量：",
+        result.content_stats.page_idx_counts,
+    )
     return 0
```

- [ ] **步骤 5：运行工作流和 CLI 测试并确认绿灯**

运行：

```bash
.venv/bin/pytest tests/test_workflow.py tests/test_cli.py -v
```

预期：7 个测试全部通过。

- [ ] **步骤 6：运行当前完整测试套件**

运行：

```bash
.venv/bin/pytest -q
```

预期：25 个测试全部通过。

- [ ] **步骤 7：提交工作流和 CLI 统计输出**

```bash
git add src/mineru_cleaner/workflow.py src/mineru_cleaner/__main__.py \
  tests/test_workflow.py tests/test_cli.py
git commit -m "feat: print cleaned content statistics"
```

---

### 任务 3：使用文档与完整验证

**文件：**

- 修改：`README.md`
- 不修改：`cleaned_content_list.json`

**接口：**

- 文档说明终端统计格式。
- 验证现有样例的统计结果和 Git 忽略规则。

- [ ] **步骤 1：在 README 中说明统计输出**

在 `README.md` 的“其他条目按原顺序、原字段保留。”之后加入：

````markdown

命令还会在终端输出：

- 清洗后每种 `type` 的数量；
- 带 `text_level` 的 `text` 总数以及按级别分组的数量；
- 每个 `page_idx` 的元素数量。

统计结果只输出到终端，不会写入 `cleaned_content_list.json`，也不会创建额外
统计文件。
````

- [ ] **步骤 2：运行完整测试和格式检查**

运行：

```bash
git diff --check
.venv/bin/pytest -q
```

预期：`git diff --check` 无输出；25 个测试全部通过。

- [ ] **步骤 3：使用现有样例清洗结果验证统计值**

运行：

```bash
.venv/bin/python -c 'import json; from pathlib import Path; from mineru_cleaner.cleaner import ContentStats,summarize_items; p=Path("data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json"); items=json.loads(p.read_text(encoding="utf-8")); actual=summarize_items(items); expected=ContentStats(type_counts={"chart":3,"image":2,"ref_text":49,"table":19,"text":66},text_level_count=6,text_level_counts={1:1,2:5},page_idx_counts={0:14,1:4,2:6,3:6,4:4,5:3,6:4,7:5,8:6,9:5,10:6,11:4,12:11,13:6,14:27,15:28}); assert actual==expected; print(actual)'
```

预期：命令退出码为 0，并输出与 `expected` 相同的 `ContentStats`。

- [ ] **步骤 4：验证清洗文件不包含统计结构**

运行：

```bash
jq 'type == "array" and all(.[]; (has("type_counts") or has("text_level_counts") or has("page_idx_counts")) | not)' data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json
```

预期：输出 `true`。

- [ ] **步骤 5：验证 `.DS_Store` 和数据目录均被 Git 忽略**

运行：

```bash
git check-ignore -v .DS_Store
git check-ignore -v data/processes-12-02888-v2/hybrid_auto/cleaned_content_list.json
git status --short --branch
```

预期：

- 第一条显示 `.gitignore` 中的 `.DS_Store` 规则；
- 第二条显示 `.gitignore` 中的 `data/` 规则；
- Git 状态不显示 `.DS_Store` 或 `data/` 中的文件。

- [ ] **步骤 6：提交 README**

```bash
git add README.md
git commit -m "docs: describe cleaned content statistics"
```
