# 跨页段落候选检测设计

## 目标

在现有 MinerU PDF 解析、内容清洗和 Markdown 渲染流程中增加只读的跨页段落候选
检测。检测器读取 `cleaned_content_list.json`，检查数组中立即相邻的两个对象，并将
符合规则的候选写入同目录的 `cross_page_candidates.json`。

本功能只检测和报告，不自动合并正文，不修改 `cleaned_content_list.json`，也不修改
`rendered.md`。

## 范围

本次仅实现：

- 检查数组中位置立即相邻的对象；
- 根据 `type`、`page_idx` 和前一个 `text` 的结尾判断候选；
- 输出包含原始索引、页码、文本和判断原因的 JSON 报告；
- 自动接入现有 PDF 工作流；
- 在命令行输出候选数量和报告路径。

本次不实现：

- 自动合并跨页正文；
- 修改正文、标题或页码；
- 跳过中间的图片、表格、公式等对象寻找下一段文本；
- 使用版面坐标、字体、缩进、语言模型或其他启发式规则；
- 根据 `text_level` 排除标题；
- 修改 Markdown 渲染结果。

## 模块边界

新增 `mineru_cleaner.cross_page` 模块，避免把检测逻辑放入清洗器或 Markdown
渲染器。

模块提供：

```python
def detect_cross_page_candidates(
    items: list[Any],
) -> list[dict[str, Any]]:
    ...


def detect_cross_page_candidates_file(
    source: Path,
    output: Path,
) -> int:
    ...
```

`detect_cross_page_candidates` 是纯函数，只读取输入列表，不修改列表或其中的对象。
返回列表顺序与候选在原数组中出现的顺序一致。

`detect_cross_page_candidates_file` 负责：

1. 以 UTF-8 读取 `cleaned_content_list.json`；
2. 校验 JSON 顶层是数组；
3. 调用纯检测函数；
4. 以 UTF-8、`ensure_ascii=False`、两个空格缩进写出报告；
5. 在 JSON 末尾写入换行；
6. 返回候选数量。

读取失败、JSON 无效、顶层不是数组或报告写入失败时，沿用
`ContentListError`。现有 CLI 已捕获其基类，不需要新增退出逻辑。

## 候选判定

检测器按 `0` 到 `len(items) - 2` 遍历索引 `previous_index`，并将
`next_index = previous_index + 1`。只有以下条件全部满足时才生成候选：

1. 两个元素都是字典；
2. 两个元素的 `type` 都严格等于字符串 `"text"`；
3. 两个 `page_idx` 都满足 `type(page_idx) is int`；
4. `next_page_idx == previous_page_idx + 1`；
5. 两个 `text` 都是字符串，且 `bool(text.strip())` 为真；
6. `previous_text.rstrip()` 不以 `. ! ? : ;` 中的任意一个字符结尾。

`rstrip()` 只用于结尾判断，报告中的 `previous_text` 和 `next_text` 必须保持原始
字符串，包括其原有空格和换行。

结束符严格限定为以下 ASCII 字符：

```text
. ! ? : ;
```

不把中文句号、引号、括号或其他字符视为结束符。例如，前一个文本以 `。` 或 `."`
结尾时仍会成为候选，因为其最后一个字符不在指定集合中。

`text_level` 不参与判断。只要两个对象的 `type` 都是 `text`，标题和正文采用完全
相同的检测规则。

如果三个相邻对象分别位于三个连续页面，且前两个文本均未以结束符结尾，则可以生成
两条候选：第一条对应索引 `0 -> 1`，第二条对应索引 `1 -> 2`。

## 报告结构

报告顶层固定为 JSON 数组。每条候选使用以下字段：

```json
{
  "previous_index": 36,
  "next_index": 37,
  "previous_page_idx": 5,
  "next_page_idx": 6,
  "previous_text": "原始前文",
  "next_text": "原始后文",
  "reason": "相邻 text 位于连续页面，且前一个 text 未以完整句结束符 . ! ? : ; 结尾"
}
```

字段定义：

- `previous_index`：前一个对象在原数组中的零基索引；
- `next_index`：后一个对象在原数组中的零基索引；
- `previous_page_idx`：前一个对象的原始页码；
- `next_page_idx`：后一个对象的原始页码；
- `previous_text`：前一个对象的原始文本；
- `next_text`：后一个对象的原始文本；
- `reason`：固定判断原因。

固定原因文本定义为模块常量：

```python
CROSS_PAGE_REASON = (
    "相邻 text 位于连续页面，且前一个 text "
    "未以完整句结束符 . ! ? : ; 结尾"
)
```

没有候选时仍生成合法的空数组报告：

```json
[]
```

## 工作流集成

现有数据流调整为：

```text
PDF
  -> MinerU ZIP
  -> 解压并定位 content_list.json
  -> 生成 cleaned_content_list.json
  -> 生成 rendered.md
  -> 检测并生成 cross_page_candidates.json
  -> 返回 WorkflowResult
```

检测器在 Markdown 渲染完成后读取 `cleaned_content_list.json`。检测过程中不会打开
或写入 `rendered.md`。报告写在清洗文件同目录：

```text
<解析结果目录>/
├── cleaned_content_list.json
├── rendered.md
└── cross_page_candidates.json
```

`WorkflowResult` 新增：

```python
candidates_path: Path
candidate_count: int
```

现有字段的名称和语义保持不变。

CLI 在现有输出末尾增加：

```text
跨页段落候选数量：1
跨页候选报告：/absolute/path/to/cross_page_candidates.json
```

## 保真与副作用

- 检测函数不修改内存中的列表或字典；
- 文件函数只读取 `cleaned_content_list.json`；
- 文件函数只创建或覆盖明确指定的 `cross_page_candidates.json`；
- 不向清洗对象添加标记字段；
- 不在 `cleaned_content_list.json` 中写入候选信息；
- 不读取、重写或格式化 `rendered.md`；
- 报告中的文本字段直接使用原始字符串，不做 `strip()`、拼接或转义转换；
- 自动合并完全不在本次范围内。

## 测试

新增 `tests/test_cross_page.py`，覆盖：

- 满足全部条件时生成完整候选；
- 数组索引和页码字段正确；
- 前一个文本尾部空白只在判断时忽略，报告文本保持原样；
- `. ! ? : ;` 五种结束符分别阻止候选；
- 相同页、跨越多页和倒序页码不生成候选；
- 中间存在非文本对象时不跨对象匹配；
- 标题文本仍按普通 `text` 检测；
- 非字典元素、非整数页码、缺失或空文本被安全跳过；
- 三个连续页面可以生成两条候选；
- 输入列表和对象不被修改；
- 文件函数输出 UTF-8 JSON、空数组和末尾换行；
- 无效 JSON 和非数组顶层抛出 `ContentListError`；
- 源 `cleaned_content_list.json` 内容保持不变。

扩展工作流和 CLI 测试，验证：

- `cross_page_candidates.json` 在清洗文件同目录生成；
- 工作流返回正确的报告路径和候选数量；
- 现有 `cleaned_content_list.json` 内容保持不变；
- 现有 `rendered.md` 内容保持不变；
- CLI 输出候选数量和报告路径；
- 原有清洗、统计和 Markdown 路径输出保持不变。

使用现有样例进行最终验证时，预期索引 `36 -> 37` 是候选：页码从 `5` 到 `6`，
前一个文本以单词 `column` 结尾，没有指定的完整句结束符。
