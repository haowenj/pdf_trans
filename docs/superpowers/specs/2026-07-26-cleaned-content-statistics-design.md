# 清洗结果统计设计

## 目标

在现有 PDF 解析和 content list 清洗流程完成后，基于内存中的清洗结果统计并在
命令行输出以下信息：

- 清洗后每种 `type` 的数量；
- 带整数 `text_level` 的 `text` 总数及其分组数量；
- 每个整数 `page_idx` 的元素数量。

统计功能不得改变 `cleaned_content_list.json` 的内容、数组顺序、对象字段或 JSON
结构，也不创建额外的统计文件。

## 方案

在 `mineru_cleaner.cleaner` 中新增纯统计函数
`summarize_items(items) -> ContentStats`。清洗函数先完成原有过滤并得到
`cleaned` 列表，再调用统计函数。统计函数只读取列表，不修改列表或其中的对象。

该方案不重新读取 `cleaned_content_list.json`，并保持过滤、统计、工作流和终端展示
四个职责彼此独立。

## 数据结构

新增不可变数据类 `ContentStats`：

```python
@dataclass(frozen=True)
class ContentStats:
    type_counts: dict[str, int]
    text_level_count: int
    text_level_counts: dict[int, int]
    page_idx_counts: dict[int, int]
```

现有 `CleaningStats` 增加字段：

```python
content_stats: ContentStats
```

现有 `WorkflowResult` 同样增加字段：

```python
content_stats: ContentStats
```

工作流将清洗层返回的 `ContentStats` 原样传给命令行层，不重新计算统计。

## 统计规则

统计输入只使用清洗后的列表。

### type 统计

- 仅统计字典元素中字符串类型的 `type`。
- 所有字符串 `type` 都统计，包括 `text`、`image`、`table`、`chart`、
  `ref_text` 和未来新增的未知类型。
- 缺少 `type` 或 `type` 不是字符串的元素不计入 `type_counts`，但仍保留在
  `cleaned_content_list.json` 中。

### text_level 统计

- 元素必须是字典；
- `type` 必须等于 `text`；
- `text_level` 必须是整数，且布尔值不视为整数级别；
- 满足条件的元素计入 `text_level_count`；
- 同时按 `text_level` 值计入 `text_level_counts`。

### page_idx 统计

- 元素必须是字典；
- `page_idx` 必须是整数，且布尔值不视为整数页码；
- 满足条件的每个元素按 `page_idx` 计入 `page_idx_counts`；
- 缺少或包含无效 `page_idx` 的元素不计入页码统计，但仍保留在输出 JSON 中。

## 命令行输出

原有输出保持不变，并在“输出文件”之后追加：

```text
清洗后 type 统计：
  chart: 3
  image: 2
  text: 66
带 text_level 的 text 数量：5
按 text_level 分组：
  1: 2
  2: 3
每个 page_idx 的元素数量：
  0: 12
  1: 9
```

输出顺序固定：

- `type` 按字符串升序；
- `text_level` 按整数升序；
- `page_idx` 按整数升序。

某个分组为空时，在对应标题下输出两个空格加 `（无）`，避免只出现无内容的标题。
`text_level_count` 始终输出，包括值为 0 的情况。

## JSON 保真

`cleaned_content_list.json` 的生成仍只使用原有的 `cleaned` 列表。统计数据仅存在于
Python 返回值和终端输出中：

- 不向任何清洗后元素添加统计字段；
- 不改变元素顺序；
- 不改变字段或字段值；
- 不把 `ContentStats` 序列化到清洗结果；
- 不创建额外的 JSON 或文本统计文件。

## 测试

单元测试先于实现编写，并覆盖：

- 混合 `type` 的准确计数和未知类型统计；
- `text_level` 总数及按级别分组；
- 仅对 `type == "text"` 的整数 `text_level` 计数；
- 每个整数 `page_idx` 的元素计数；
- 无效或缺失统计字段不会导致元素丢失；
- 三类分组的固定排序输出；
- 空分组输出 `（无）`；
- 工作流完整传递 `ContentStats`；
- `cleaned_content_list.json` 与未加入统计功能时的清洗结果完全相同。

完成后运行全部测试，并使用已有样例结果验证统计输出。
