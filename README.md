# PDF Trans

上传一个 PDF 到 MinerU 3.4.4 异步接口，下载解析结果，清洗并逐对象翻译 content
list。

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

配置 OpenAI 兼容翻译接口：

```bash
export TRANSLATION_BASE_URL="https://api.example.com/v1"
export TRANSLATION_API_KEY="replace-with-api-key"
export TRANSLATION_MODEL="paper-translation-model"
export TRANSLATION_TIMEOUT_SECONDS="120"
export TRANSLATION_MAX_RETRIES="1"
export TRANSLATION_CONCURRENCY="5"
```

程序会在运行时读取前三个必需变量；`TRANSLATION_TIMEOUT_SECONDS` 是单次请求超时秒数，
默认 120（2 分钟），必须为正数；`TRANSLATION_MAX_RETRIES` 是失败后的额外重试次数，
默认 1；`TRANSLATION_CONCURRENCY` 是同时进行的翻译请求数，默认 5，必须是正整数。
缺少必需变量或配置值非法时命令会报错并停止。

MinerU 结果解压到项目的 `data/` 目录，并按以下顺序生成处理结果：

```text
content_list.json
  -> cleaned_content_list.json
  -> cross_page_candidates.json（诊断报告）
  -> normalized_content_list.json
  -> translated_content_list.json
  -> rendered.md
```

命令输出处理前数量、过滤数量、处理后数量、清洗结果路径、内容统计、Markdown
文件路径、跨页候选数量、候选报告路径、规范化后数量、规范化文件路径，以及翻译统计
和翻译文件路径。

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

## 论文翻译

翻译阶段只处理 `type` 为 `text` 的对象，每个对象单独调用一次 OpenAI 兼容接口：

- 成功：保留原 `text`，增加 `translated_text` 和
  `translation_status: "success"`；
- 失败：保留原对象，设置 `translated_text: null`、
  `translation_status: "failed"`，并增加 `translation_error`；
- 其他类型：完全原样输出。

数组顺序、对象数量和原始 `text` 字段不会改变。正常结束时所有 text 对象都是
`success` 或 `failed`，不会保留 `pending`。输出文件为同一数据目录下的
`translated_content_list.json`。命令最后打印：

```text
text 对象总数：<数量>
本次模型调用数量：<数量>
跳过的已成功数量：<数量>
翻译成功数量：<数量>
翻译失败数量：<数量>
待翻译数量：<数量>
翻译文件：<路径>
```

每完成一个对象，结果都会原子写入输出文件。再次运行时，程序会先校验已有输出与
`normalized_content_list.json` 的对象数量、顺序、`type` 和 `text`；校验通过后跳过
已有 `success`，并重新处理 `pending` 和 `failed`。校验不通过会直接报错，不使用旧结果。

翻译使用 5 个线程并发执行（可通过 `TRANSLATION_CONCURRENCY` 调整）。线程完成顺序
不影响 JSON 中的原数组顺序；检查点由主线程串行原子写入。当前请求体只包含 `model`
和 `messages`，不会主动发送 `reasoning`、`reasoning_effort` 或 `thinking` 参数。

运行日志写入 stderr，最终统计写入 stdout。日志使用彩色级别前缀：INFO 为绿色、WARN
为黄色、ERROR 为红色，并记录每个流程的开始、结束和耗时。例如：

```text
[INFO] 开始翻译 text 对象：按配置的线程数逐段调用 OpenAI 兼容接口
[INFO] 第 12 段翻译完成：success，耗时 3.42 秒，译文 286 字符
[WARN] 检测到第 18 段和第 19 段被分页分裂，将合并为一个段落
[ERROR] 第 20 段翻译完成：failed，耗时 240.13 秒，错误：请求超时
```

日志不会输出 API Key、完整原文或完整译文。

如果 MinerU 流程已经完成，也可以只从规范化文件继续翻译：

```bash
python -m pdf_trans --translate-only \
  /path/to/normalized_content_list.json
```

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
