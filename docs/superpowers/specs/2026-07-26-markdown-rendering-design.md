# Markdown 渲染功能设计

## 目标

在现有 MinerU PDF 解析与内容清洗流程中增加 Markdown 渲染步骤。渲染器读取
`cleaned_content_list.json`，严格按顶层数组原有顺序处理支持的元素，并在同目录
生成 `rendered.md`。

现有命令行仍以 PDF 路径作为入口。清洗完成后自动执行渲染，并额外输出
`rendered.md` 的绝对路径。

## 范围

本次支持以下内容类型：

- `text`
- `ref_text`
- `image`
- `chart`
- `table`
- `equation`

未列出的类型直接跳过，不写入 Markdown，也不影响其他元素的相对顺序。本次不增加
Markdown 转义、跨页处理、表格转换、代码块、列表或未知类型的通用渲染。

## 模块边界

新增 `mineru_cleaner.renderer` 模块，避免把渲染职责放入现有清洗模块。

该模块提供两个接口：

```python
def render_items(items: list[Any]) -> str:
    ...


def render_content_list_file(source: Path, output: Path) -> None:
    ...
```

`render_items` 是纯函数，只读取输入列表并返回完整 Markdown 字符串，不修改列表或
其中的字典。`render_content_list_file` 负责 UTF-8 JSON 读取、顶层数组校验和
UTF-8 Markdown 写入。

文件读取失败、JSON 无效、顶层不是数组或结果写入失败时，沿用
`ContentListError`。现有命令行已经捕获该异常的基类，无需增加新的退出处理。

## 渲染规则

### 文本

`type == "text"` 时读取字符串字段 `text`：

- `text_level == 1`：输出 `# {text}`；
- `text_level == 2`：输出 `## {text}`；
- 其他值或缺少 `text_level`：原样输出 `text`，作为普通段落。

空字符串或仅包含空白的 `text` 不生成 Markdown 内容。渲染器不修改正文中的字符、
换行、公式或其他 Markdown 标记。

### 参考文献

`type == "ref_text"` 时，将字符串字段 `text` 原样输出为普通段落。不增加列表符号，
不修改参考文献文本。空文本不生成内容。

### 图片

`type == "image"` 时按以下顺序生成同一个元素块：

1. 非空字符串 `img_path` 输出为 `![]({img_path})`；
2. `image_caption` 中的非空字符串按数组顺序逐项输出；
3. `image_footnote` 中的非空字符串按数组顺序逐项输出。

图注和脚注不用于图片替代文本，也不进行拼接或修改。

### 图表

`type == "chart"` 与图片采用相同结构：

1. 非空字符串 `img_path` 输出为 `![]({img_path})`；
2. `chart_caption` 中的非空字符串按数组顺序逐项输出；
3. `chart_footnote` 中的非空字符串按数组顺序逐项输出。

### 表格

`type == "table"` 时按以下顺序生成同一个元素块：

1. `table_caption` 中的非空字符串按数组顺序逐项输出；
2. 字符串字段 `table_body` 原样输出；
3. `table_footnote` 中的非空字符串按数组顺序逐项输出。

不把 `table_body` 中的 HTML 转换为 Markdown 表格，也不规范化或修改 HTML。

### 公式

MinerU 3.4.4 的 `equation` 元素使用 `text` 保存包含定界符的 LaTeX 内容，例如：

```json
{
  "type": "equation",
  "text": "$$\nQ = f(P)\n$$",
  "text_format": "latex"
}
```

渲染器将非空字符串 `text` 原样输出，不额外添加 `$$`，也不修改公式内容。

参考：
[MinerU 输出文件格式](https://opendatalab.github.io/MinerU/zh/reference/output_files/)

## 空行与文件格式

每个支持类型的元素先生成一个或多个非空片段。同一元素内的图片、图注、脚注，或
表格图注、HTML、脚注之间使用一个空行分隔；不同元素之间同样使用一个空行分隔。

整体实现等价于把所有非空片段以 `"\n\n"` 连接：

- 有输出内容时，文件末尾增加一个换行；
- 没有可渲染内容时，生成空文件；
- 被跳过或内容为空的元素不会产生额外空行。

## 工作流集成

现有 `process_pdf` 数据流调整为：

```text
PDF
  -> MinerU ZIP
  -> 解压并定位 content_list.json
  -> 生成 cleaned_content_list.json
  -> 生成 rendered.md
  -> 返回 WorkflowResult
```

`rendered.md` 位于 `cleaned_content_list.json` 同目录，因此
`images/...` 形式的相对图片路径仍能正确指向解压后的图片。

`WorkflowResult` 新增：

```python
markdown_path: Path
```

原有 `output_path` 继续表示 `cleaned_content_list.json`，避免改变现有字段语义。
命令行在原有数量、清洗路径和统计信息之外输出：

```text
Markdown 文件：/absolute/path/to/rendered.md
```

## 保真与容错

- 渲染器不修改 `cleaned_content_list.json`。
- 所有支持类型都保持原数组顺序。
- 正文、参考文献、公式、图注、脚注和表格 HTML 均按字段值原样使用。
- 非字典元素、未知类型、缺失字段和字段类型不符的内容直接跳过。
- 对数组型图注和脚注，只输出其中的非空字符串；不把其他值转换为字符串。
- 图片路径缺失时不输出空图片语法，但仍可输出有效图注和脚注。

## 测试

新增 `tests/test_renderer.py`，覆盖：

- 一级标题、二级标题、普通正文和参考文献；
- 图片、图表的路径、多个图注和多个脚注顺序；
- 表格图注、HTML 正文和脚注的原样输出；
- 公式 `text` 的原样输出，避免重复添加定界符；
- 不支持类型、非字典元素和无效字段的跳过行为；
- 元素间空行和文件末尾换行；
- 输入 JSON 顶层类型校验；
- 写出的 `rendered.md` 与预期完全一致；
- 渲染过程不修改输入对象。

扩展工作流和命令行测试，验证：

- `rendered.md` 在清洗文件同目录生成；
- `WorkflowResult.markdown_path` 正确；
- 命令行输出 Markdown 文件路径；
- 原有清洗结果和统计输出保持不变。

