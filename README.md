# MinerU Content Cleaner

上传一个 PDF 到 MinerU 3.4.4 异步接口，下载解析结果，并清洗其中的
content list。

## 环境

- Python 3.11+
- MinerU 3.4.4 服务；默认地址为 `http://127.0.0.1:7100`

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## 使用

```bash
python -m pdf_trans /path/to/document.pdf
```

连接其他地址的 MinerU：

```bash
python -m pdf_trans /path/to/document.pdf \
  --svr-url http://mineru.example:7100
```

MinerU 结果解压到项目的 `data/` 目录，并按以下顺序生成处理结果：

```text
content_list.json
  -> cleaned_content_list.json
  -> cross_page_candidates.json（诊断报告）
  -> normalized_content_list.json
  -> rendered.md
```

命令输出处理前数量、过滤数量、处理后数量、清洗结果路径、内容统计、Markdown
文件路径、跨页候选数量、候选报告路径、规范化后数量和规范化文件路径。

程序只删除以下内容：

- `header`
- `footer`
- `page_number`
- `type` 为 `text` 且 `text.strip()` 为空的条目

其他条目按原顺序、原字段保留。

清洗完成后，命令行还会输出：

- 清洗后每种 `type` 的数量；
- 带 `text_level` 的 `text` 数量及按 `text_level` 的分组；
- 每个 `page_idx` 的元素数量。

统计结果只输出到终端，不会写入 `cleaned_content_list.json`，也不会创建额外的统计文件。

## Markdown 渲染

`rendered.md` 按 `normalized_content_list.json` 数组的原顺序输出以下内容：

- `text`：一级标题、二级标题或普通段落；
- `ref_text`：原始参考文献段落；
- `image` 和 `chart`：相对图片路径、图注和脚注；
- `table`：图注、原始 HTML 表格和脚注；
- `equation`：MinerU 提供的原始 LaTeX 文本。

各内容片段之间保留空行。渲染过程不会修改
`cleaned_content_list.json`、`cross_page_candidates.json`，也不会把 HTML 表格转换为
Markdown 表格。

## 跨页段落候选

`cross_page_candidates.json` 只检查清洗数组中立即相邻的两个 `text` 对象。
当后一个 `page_idx` 等于前一个加 1，且前一个文本忽略尾部空白后不以
`. ! ? : ;` 结尾时，记录为疑似跨页段落。

报告包含两个对象的零基数组索引、两个页码、原始文本和判断原因。候选检测和合并
在同一次工作流中完成：合并步骤直接使用内存中的清洗数组和候选对象，不会重新读取
`cross_page_candidates.json`。该 JSON 仍只作为诊断和人工检查报告。

首尾相接的候选（例如 `0→1`、`1→2`）会构成一条链，按原始索引顺序合并为一个
对象，只保留链首对象；多条互不相关的链分别处理。合并对象保留链首的原有字段和
`page_idx`，并增加 `source_page_indices`、`source_bboxes` 与
`merged_cross_page: true`。如果候选索引越界、不相邻，出现重复、分叉、汇聚或环，
或者对象类型、页码不符合要求，工作流会终止并且不会写出
`normalized_content_list.json`。

## 测试

```bash
python -m pip install -e '.[test]'
pytest -v
```
