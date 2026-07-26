# 跨页段落合并设计

## 目标

在现有 MinerU 内容处理工作流中，根据本次运行检测出的跨页段落候选，将清洗数组中
属于同一连续候选链的 `text` 对象合并，并生成：

```text
normalized_content_list.json
```

Markdown 渲染改为读取 `normalized_content_list.json`，重新生成 `rendered.md`。

本功能不修改：

- `cleaned_content_list.json`
- `cross_page_candidates.json`
- MinerU 原始文件和解压出的原始结果

## 输入语义

完整工作流在同一次执行中依次生成并使用以下数据：

1. 清洗阶段得到内存中的 `cleaned_items`，并写出
   `cleaned_content_list.json`；
2. 候选检测函数直接读取 `cleaned_items`，返回内存中的候选列表；
3. 候选列表写出为 `cross_page_candidates.json`，用于诊断和人工检查；
4. 合并函数直接消费同一份 `cleaned_items` 和同一份内存候选列表；
5. 合并结果写出为 `normalized_content_list.json`；
6. Markdown 渲染器读取 normalized 文件并写出 `rendered.md`。

`cross_page_candidates.json` 不是合并步骤的独立输入来源。工作流不会为了合并而
重新读取该报告，也不处理旧报告、文件哈希或版本号。

为保留已有工具接口，可以继续提供“从 cleaned 文件检测并写报告”的便利函数，但
完整工作流不使用该便利函数进行合并。

## 模块边界

### 清洗层

现有 `clean_content_list_file(source, output) -> CleaningStats` 保持兼容。

清洗模块增加一个供工作流使用的接口，同时返回本次写出的清洗列表和统计：

```python
def clean_content_list_file_with_items(
    source: Path,
    output: Path,
) -> tuple[list[Any], CleaningStats]:
    ...
```

原有 `clean_content_list_file` 调用该新接口并只返回 `CleaningStats`。这样既避免
工作流重新读取清洗文件，又不破坏现有调用方。

### 候选检测层

已有纯函数继续作为内存检测入口：

```python
def detect_cross_page_candidates(
    items: list[Any],
) -> list[dict[str, Any]]:
    ...
```

增加只负责写报告的接口：

```python
def write_cross_page_candidates_file(
    candidates: list[dict[str, Any]],
    output: Path,
) -> None:
    ...
```

已有 `detect_cross_page_candidates_file(source, output) -> int` 可以保留，并在内部
复用检测函数和报告写入函数。完整工作流改为直接调用纯检测函数和报告写入函数。

### 规范化层

新增 `mineru_cleaner.normalizer` 模块：

```python
def normalize_cross_page_items(
    items: list[Any],
    candidates: list[dict[str, Any]],
) -> list[Any]:
    ...


def write_normalized_content_list_file(
    items: list[Any],
    output: Path,
) -> None:
    ...
```

`normalize_cross_page_items` 是纯函数。它不修改输入列表、输入对象或候选字典，成功
时返回新的深拷贝列表，失败时抛出 `NormalizationError`。

`write_normalized_content_list_file` 只负责将已经验证和合并完成的内存列表写为
UTF-8 JSON，不读取候选报告。

### 错误类型

在 `mineru_cleaner.errors` 中新增：

```python
class NormalizationError(MinerUCleanerError):
    """Raised when cross-page normalization validation fails."""
```

现有 CLI 捕获 `MinerUCleanerError`，因此校验失败会正常输出错误并以非零状态退出。

## 候选图和链

每条候选由有向边表示：

```text
previous_index -> next_index
```

在创建 normalized 列表之前，必须对所有候选完成验证并构建全部链。

### 允许

- 单条链：`0 -> 1`
- 连续重叠链：`0 -> 1`、`1 -> 2`、`2 -> 3`
- 多条互不相关的链：`0 -> 1`、`8 -> 9`

连续重叠链最终表示一个合并组。例如：

```text
0 -> 1 -> 2 -> 3
```

合并组索引为：

```python
[0, 1, 2, 3]
```

### 禁止

以下情况抛出 `NormalizationError`：

- 候选不是字典；
- `previous_index` 或 `next_index` 不是严格整数；
- 索引小于 0 或超出 cleaned 数组范围；
- `next_index != previous_index + 1`；
- 同一候选对重复出现；
- 一个索引指向多个后继；
- 一个索引来自多个前驱；
- 候选形成环；
- 对应 cleaned 对象不是字典或 `type != "text"`；
- 对应对象的 `page_idx` 不是严格整数；
- 后一个页码不等于前一个页码加 1；
- 对应对象的 `text` 不是非空字符串。

虽然 `next_index == previous_index + 1` 已能排除当前零基数组中的反向环，仍保留显式
环检测，使链构建的约束和错误语义完整。

所有候选验证通过后才进行深拷贝和合并。任何一条候选失败时，纯函数不返回部分结果，
工作流也不会创建或覆盖本次的 `normalized_content_list.json`。

## 合并规则

对每条链按原始索引升序处理。

### 文本

文本按照链中原始对象顺序连接。每个边界保证恰好一个空格：

```python
merged_text = first_text
for next_text in remaining_texts:
    merged_text = merged_text.rstrip() + " " + next_text.lstrip()
```

该规则只删除段落边界处的空白：

- 保留首段开头空白；
- 保留末段末尾空白；
- 保留各段内部字符和空白；
- 不修改公式、标点或其他正文内容。

### 字段

合并对象从链首对象深拷贝而来，因此保留链首对象的原有字段。然后覆盖或增加：

```json
{
  "text": "合并后的正文",
  "source_page_indices": [5, 6],
  "source_bboxes": [
    [270, 516, 944, 921],
    [270, 102, 942, 298]
  ],
  "merged_cross_page": true
}
```

规则：

- `page_idx` 不改变，继续使用链首对象的 `page_idx`；
- `source_page_indices` 按链顺序收集每个对象的 `page_idx`；
- `source_bboxes` 按链顺序收集每个对象的 `bbox`；
- 对象缺少 `bbox` 时对应位置写入 JSON `null`，不因此校验失败；
- 如果链首对象原来已有同名规范化字段，本次结果覆盖为当前链计算值。

### 数组

生成 normalized 数组时按 cleaned 原索引从前向后遍历：

- 遇到链首索引时写入一个合并对象；
- 遇到链中其他索引时跳过；
- 未参与合并的对象深拷贝后原样写入。

因此：

- 未参与对象的相对顺序不变；
- 每条长度为 `N` 的链使数组长度减少 `N - 1`；
- 原 cleaned 数组及其对象不被修改。

## 文件格式

`normalized_content_list.json`：

- 顶层是 JSON 数组；
- 使用 UTF-8；
- `ensure_ascii=False`；
- 两个空格缩进；
- 文件末尾包含一个换行。

写入函数只在纯合并函数成功返回后调用。校验失败时不调用写入函数。

## 工作流集成

数据流调整为：

```text
PDF
  -> MinerU ZIP
  -> content_list.json
  -> clean_content_list_file_with_items()
       ├── cleaned_items（内存）
       └── cleaned_content_list.json
  -> detect_cross_page_candidates(cleaned_items)
       ├── candidates（内存）
       └── cross_page_candidates.json（诊断报告）
  -> normalize_cross_page_items(cleaned_items, candidates)
       └── normalized_content_list.json
  -> render_content_list_file(normalized_content_list.json, rendered.md)
  -> WorkflowResult
```

这会替换当前“先渲染，再检测候选”的顺序。Markdown 只读取 normalized 文件。

`WorkflowResult` 新增：

```python
normalized_path: Path
normalized_count: int
```

已有字段保持语义不变：

- `output_path` 仍指向 `cleaned_content_list.json`；
- `markdown_path` 仍指向 `rendered.md`；
- `candidates_path` 仍指向诊断报告；
- `candidate_count` 仍表示候选边数量；
- `after_count` 仍表示清洗后、合并前的对象数量。

CLI 在候选报告信息之后增加：

```text
规范化后数量：138
规范化文件：/absolute/path/to/normalized_content_list.json
```

## 样例结果

现有样例：

```text
cleaned_content_list.json 对象数：139
候选：36 -> 37
```

规范化后预期：

```text
normalized_content_list.json 对象数：138
```

原索引 36 的对象成为合并对象：

- `page_idx == 5`
- `source_page_indices == [5, 6]`
- `source_bboxes` 包含原索引 36 和 37 的两个 bbox
- `merged_cross_page is true`
- `text` 同时包含两段原始正文

原索引 37 的对象不再作为独立数组元素存在。

`cleaned_content_list.json` 和 `cross_page_candidates.json` 内容保持不变。
`rendered.md` 会重新生成，其原来的两个相邻段落变为一个合并段落。

## 测试

新增 `tests/test_normalizer.py`，覆盖：

- 两对象合并、文本边界空格和新增字段；
- 输入 items 和 candidates 不变；
- 未参与对象字段及顺序不变；
- 缺失 bbox 写入 `None`；
- 连续三页或四页链合并为一个对象；
- 多条独立链分别合并；
- 重复候选；
- 分叉；
- 汇聚；
- 环；
- 非相邻索引；
- 索引越界和负索引；
- 非整数索引，包括布尔值；
- 对应对象不是 `text`；
- 页码类型无效或页面不连续；
- 文本类型无效或空白；
- 写出的 normalized JSON 格式；
- 校验失败时不创建 normalized 文件；
- 139 个输入对象变为 138 个；
- 两部分文字都存在于合并文本；
- 原索引 37 的对象不再独立存在。

扩展清洗测试，验证新接口返回的内存列表与写出的 cleaned JSON 一致，且旧接口仍返回
原有 `CleaningStats`。

扩展工作流和 CLI 测试，验证：

- 工作流使用内存候选进行合并；
- 报告仍在合并前写出；
- normalized 文件路径和数量正确；
- cleaned 文件保持合并前内容；
- candidate 报告保持诊断内容；
- Markdown 从 normalized 文件渲染，包含合并后的单段文本；
- CLI 输出规范化数量和路径。

最终使用现有样例验证：

- cleaned 数量为 139；
- normalized 数量为 138；
- 合并文本同时包含原索引 36 和 37 的正文；
- normalized 中不存在与原索引 37 完全相同的独立对象；
- cleaned 和候选报告在规范化前后摘要不变。

