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
python -m mineru_cleaner /path/to/document.pdf
```

连接其他地址的 MinerU：

```bash
python -m mineru_cleaner /path/to/document.pdf \
  --svr-url http://mineru.example:7100
```

MinerU 结果解压到项目的 `data/` 目录。清洗结果保存在原始 content list
同目录的 `cleaned_content_list.json` 中，并自动在同目录生成 `rendered.md`。
命令输出处理前数量、过滤数量、处理后数量、清洗结果路径、内容统计和 Markdown
文件路径。

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

`rendered.md` 按清洗后数组的原顺序输出以下内容：

- `text`：一级标题、二级标题或普通段落；
- `ref_text`：原始参考文献段落；
- `image` 和 `chart`：相对图片路径、图注和脚注；
- `table`：图注、原始 HTML 表格和脚注；
- `equation`：MinerU 提供的原始 LaTeX 文本。

各内容片段之间保留空行。渲染过程不会修改
`cleaned_content_list.json`，也不会把 HTML 表格转换为 Markdown 表格。

## 测试

```bash
python -m pip install -e '.[test]'
pytest -v
```
