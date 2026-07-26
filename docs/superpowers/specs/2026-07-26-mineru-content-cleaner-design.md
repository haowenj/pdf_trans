# MinerU 内容清洗器设计

## 目标

构建一个最小化的 Python 命令行项目：接收一个 PDF 文件路径，将 PDF
提交给指定地址的 MinerU 3.4.4 异步任务接口（默认使用本地服务），下载并解压返回的 ZIP
压缩包到项目本地的 `data/` 目录，清洗生成的内容列表 JSON，并输出条目数量统计。

本项目不生成 Markdown，不处理跨页段落或表格合并，不创建 Document IR，
不使用 Agent 或数据库，也不提供 Web 接口。

## 命令行接口

命令接受一个必填位置参数（PDF 路径）和一个可选的 MinerU 服务地址参数：

```bash
python -m mineru_cleaner /absolute/or/relative/path/document.pdf \
  --svr-url http://127.0.0.1:7100
```

命令会检查路径存在、目标是普通文件，并且扩展名不区分大小写地为 `.pdf`。
`--svr-url` 不传时默认为 `http://127.0.0.1:7100`。该值作为 MinerU API
基础地址逐层传给工作流和 HTTP 客户端，不在请求逻辑中写死。这里的 `svr_url`
是本程序连接 MinerU API 的地址，不是 MinerU 表单中供 HTTP 推理后端使用的
`server_url` 字段。

处理成功后输出：

- 处理前的条目数量；
- 过滤掉的条目数量；
- 处理后的条目数量；
- `cleaned_content_list.json` 的路径。

错误以简洁信息输出，并使用非零退出码。

## MinerU 请求

客户端向 `svr_url` 对应服务的 `POST /tasks` 上传 PDF，使用以下表单参数：

| 字段 | 值 |
| --- | --- |
| `backend` | `hybrid-engine` |
| `parse_method` | `auto` |
| `effort` | `medium` |
| `formula_enable` | `true` |
| `table_enable` | `true` |
| `return_md` | `false` |
| `return_middle_json` | `false` |
| `return_model_output` | `false` |
| `return_content_list` | `true` |
| `return_images` | `true` |
| `response_format_zip` | `true` |

服务端响应必须包含字符串类型的 `task_id`、`status_url` 和 `result_url`。
客户端每两秒轮询一次 `status_url`：

- `pending` 和 `processing`：继续轮询；
- `completed`：从 `result_url` 下载 ZIP；
- `failed`：停止并报告服务端错误；
- 未知状态或响应格式错误：停止并报告错误。

轮询总时限为 30 分钟。每次 HTTP 请求也设置有限超时，避免服务不可用时命令无限等待。

## 输出与 ZIP 处理

下载的压缩包只作为临时文件，不提交到 Git。压缩包内容直接解压到项目本地的
`data/` 目录下，并保留压缩包内的目录结构，路径形态如下：

```text
data/<pdf-stem>/<backend>_<parse-method>/...
```

如果 ZIP 条目包含绝对路径、`..` 路径段，或解析后的目标路径位于 `data/`
目录之外，则拒绝解压。对同一个 PDF 重复处理时，会用最新 MinerU 结果替换相同的
压缩包相对路径；`data/` 中无关的其他文件不受影响。

解压完成后，工作流只在本次 ZIP 实际解压出的文件集合中查找名称以
`_content_list.json` 结尾的文件，不扫描 `data/` 中以前任务留下的文件。
以 `_content_list_v2.json` 结尾的文件不算匹配。找不到文件或找到多个文件都视为错误。
清洗结果写在匹配到的原始文件旁边：

```text
cleaned_content_list.json
```

整个 `data/` 目录都加入 Git 忽略规则。

## 清洗规则

源 JSON 顶层必须是数组。程序只遍历一次，并保持数组原有顺序。仅当满足以下条件之一时删除条目：

1. `type` 为 `header`；
2. `type` 为 `footer`；
3. `type` 为 `page_number`；
4. `type` 为 `text`，且 `text` 是字符串并且 `text.strip()` 为空。

其他所有条目都保留，明确包括：

- 普通 `text`；
- 带 `text_level` 的标题 `text`；
- `image`；
- `table`；
- `chart`；
- `ref_text`；
- MinerU 未来新增的未知类型。

保留的 JSON 对象不修改、不添加、不删除任何字段或字段值。因此 `page_idx`、`bbox`、
`img_path`、caption、footnote、`content`、`table_body` 等字段都会完整保留。
JSON 序列化时可以改变无意义的空白格式，但不能改变 JSON 数据本身。

输出的数量满足：

```text
before_count = filtered_count + after_count
```

## 代码结构

程序包按职责拆分为以下模块：

- `mineru_cleaner.cleaner`：纯内容列表过滤和 JSON 文件写入；
- `mineru_cleaner.client`：MinerU 任务提交、状态轮询和 ZIP 下载；
- `mineru_cleaner.archive`：安全解压和内容列表文件定位；
- `mineru_cleaner.workflow`：从 PDF 到清洗结果的流程编排；
- `mineru_cleaner.__main__`：参数解析、统计输出和退出状态处理。

运行时唯一依赖为 `httpx`，测试使用 `pytest`。

## 失败处理

遇到以下情况时，工作流失败并且不会输出误导性的成功报告：

- 输入路径不是可读的 PDF 文件；
- MinerU 不可用，或返回非成功 HTTP 响应；
- 任务失败、超时，或返回无效的状态响应；
- 结果响应不是 ZIP；
- 压缩包包含不安全路径；
- 找不到内容列表文件，或匹配到多个文件；
- JSON 无法解析，或顶层不是数组；
- 输出文件无法写入。

## 测试策略

先编写测试，再编写生产代码。测试覆盖：

- 四条删除规则；
- 顺序、完整对象、标题、图片、表格、图表、参考文献和未知类型的保留；
- 处理前、过滤和处理后三个数量的准确性；
- MinerU 任务提交字段和 PDF multipart 上传；
- `pending`/`processing`/`completed` 轮询以及失败任务处理；
- ZIP 响应下载和安全解压；
- 路径穿越条目的拒绝；
- 内容列表文件的精确定位，确保不误选 `_content_list_v2.json`；
- 使用合成 MinerU ZIP 的端到端工作流；
- 命令行成功输出和失败时的非零退出行为。

测试套件不要求运行中的 MinerU 服务。单元测试通过后，还会验证本地 MinerU
健康检查接口；条件允许时，再使用用户提供的样例 PDF 进行实际验证。
