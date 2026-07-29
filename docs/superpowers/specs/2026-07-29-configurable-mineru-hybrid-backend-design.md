# 可配置 MinerU Hybrid 后端设计

## 目标

让 `pdf_trans` 在不改变现有 PDF 清洗、翻译和渲染流程的前提下，通过全局配置支持
两种 MinerU 解析方式：

- `hybrid-engine`：保持现有行为，由 MinerU 服务所在机器运行本地 Hybrid 推理；
- `hybrid-http-client`：MinerU 服务保留本地 Pipeline 处理，并把 Hybrid 流程中的
  VLM 推理请求发送到 GPUStack 提供的 OpenAI 兼容 MinerU 模型服务。

默认配置必须继续使用 `hybrid-engine`，保证现有部署升级后行为不变。两种模式都固定
使用 `effort=medium` 并显式设置 `image_analysis=false`，继续跳过图片和图表的 VLM
语义分析。

## 服务边界

项目涉及两个不同的服务地址：

- `PDF_TRANS_MINERU_URL` 是 `pdf_trans` 调用的完整 MinerU API 地址，提供
  `POST /tasks`、任务状态查询和 ZIP 结果下载接口；
- `PDF_TRANS_MINERU_SERVER_URL` 是 MinerU 在 `hybrid-http-client` 模式下调用的
  OpenAI 兼容模型推理地址，例如 GPUStack。`pdf_trans` 只把它作为 `/tasks`
  multipart 表单的 `server_url` 字段传给 MinerU，不直接调用该地址。

GPUStack API Key 不进入 `pdf_trans` 配置或请求日志。真正调用 GPUStack 的 MinerU
服务通过自身环境变量配置：

```dotenv
MINERU_VL_API_KEY=<GPUStack Key>
```

使用本地 Pipeline 模型的 MinerU 服务可以继续配置：

```dotenv
MINERU_MODEL_SOURCE=local
```

该变量只控制 MinerU 从哪里加载本地 Pipeline 模型。`hybrid-http-client` 的 VLM
推理由 GPUStack 承担，不在 MinerU 服务机器上加载本地 MinerU VLM。

## 全局配置

新增两个环境变量：

```dotenv
PDF_TRANS_MINERU_BACKEND=hybrid-engine
PDF_TRANS_MINERU_SERVER_URL=
```

配置规则如下：

1. `PDF_TRANS_MINERU_BACKEND` 默认值为 `hybrid-engine`；
2. backend 只允许 `hybrid-engine` 和 `hybrid-http-client`；
3. `hybrid-http-client` 必须配置非空的
   `PDF_TRANS_MINERU_SERVER_URL`；
4. `hybrid-engine` 即使存在 `PDF_TRANS_MINERU_SERVER_URL` 也忽略该值，并且不在
   MinerU 请求中发送 `server_url`；
5. 非法 backend 或远端模式缺少 URL 时立即给出明确的配置错误；
6. URL 在进入设置对象时去除结尾的 `/`，但不改写其余内容。

Web 模式在启动时通过 `WebSettings.from_env()` 读取并验证配置。配置是整个 Web
进程的全局设置，上传页面和任务数据库不增加 backend 选择字段。

Docker Compose 向 Web 容器转发这两个变量，默认值保持本地模式：

```yaml
PDF_TRANS_MINERU_BACKEND: ${PDF_TRANS_MINERU_BACKEND:-hybrid-engine}
PDF_TRANS_MINERU_SERVER_URL: ${PDF_TRANS_MINERU_SERVER_URL:-}
```

## 命令行接口

CLI 新增两个参数：

```text
--mineru-backend
--mineru-server-url
```

参数默认读取 `PDF_TRANS_MINERU_BACKEND` 和
`PDF_TRANS_MINERU_SERVER_URL`，同时允许单次 CLI 调用覆盖环境配置。
`--svr-url` 保持现有语义和兼容性，仍然用于指定完整 MinerU API 地址。

示例：

```bash
python3 -m pdf_trans paper.pdf \
  --svr-url http://mineru:8000 \
  --mineru-backend hybrid-http-client \
  --mineru-server-url http://gpustack:8000
```

CLI 在任务开始前验证 backend 和 server URL 组合。配置错误通过现有命令行错误路径
输出，并返回非零退出码。

## MinerU 请求

`client.py` 将当前固定的 `PARSE_FORM` 拆分为不可变的公共字段和按客户端配置生成的
请求表单。公共字段保持：

| 字段 | 值 |
| --- | --- |
| `parse_method` | `auto` |
| `effort` | `medium` |
| `formula_enable` | `true` |
| `table_enable` | `true` |
| `image_analysis` | `false` |
| `return_md` | `false` |
| `return_middle_json` | `false` |
| `return_model_output` | `false` |
| `return_content_list` | `true` |
| `return_images` | `true` |
| `response_format_zip` | `true` |

每次提交根据配置补充：

- 本地模式：`backend=hybrid-engine`，不包含 `server_url`；
- 远端模式：`backend=hybrid-http-client`，包含经过验证的 `server_url`。

表单为每次请求独立创建，不修改模块级共享字典。现有异步任务提交、轮询、超时和 ZIP
下载行为不变。

## 代码传递链

配置沿现有调用链显式传递：

```text
CLI 参数 / WebSettings
  -> process_pdf(mineru_backend, mineru_server_url)
  -> MinerUClient(backend, server_url)
  -> POST /tasks multipart 表单
```

`process_pdf()` 和内部完整工作流增加 `mineru_backend`、
`mineru_server_url` 关键字参数。测试或调用方注入自定义 `client` 时仍使用该对象，
不创建新的 `MinerUClient`，从而保持现有依赖注入行为。

Web `TaskRunner` 从 `WebSettings` 读取两个值并传给 `process_pdf()`。断点续传只执行
翻译，不调用 MinerU，因此不使用这两个配置。

## 错误处理和安全

- 配置验证使用项目自身的明确错误消息，指出变量名、收到的 backend 和允许值；
- `hybrid-http-client` 缺少 server URL 时，不提交 MinerU 任务；
- MinerU 或 GPUStack 运行时错误继续由 MinerU 任务状态中的 `error` 返回，并通过现有
  `MinerUClientError` 路径记录；
- 日志可以记录 backend 和 server URL 以便诊断，但不读取、传递或记录
  `MINERU_VL_API_KEY`；
- 本地模式忽略多余的 server URL，避免无关环境残留阻止现有部署启动。

## 测试

新增或调整自动化测试，覆盖：

1. 默认 `MinerUClient` 请求继续发送 `backend=hybrid-engine`；
2. 两种模式都发送 `effort=medium` 和 `image_analysis=false`；
3. 本地模式不发送 `server_url`；
4. 远端模式发送 `backend=hybrid-http-client` 和配置的 `server_url`；
5. 非法 backend 被拒绝；
6. 远端模式缺少 server URL 被拒绝；
7. Web 默认设置保持本地模式；
8. Web 环境变量可以启用远端模式并规范化 URL；
9. `TaskRunner` 将 backend 和 server URL 传入完整工作流；
10. CLI 默认值、环境值和显式参数覆盖均能传到工作流；
11. 现有本地模式客户端、工作流、CLI 和 Web 测试继续通过。

测试使用 `httpx.MockTransport` 检查实际 multipart 请求体，不访问真实 MinerU 或
GPUStack 服务。

## 文档

README 增加以下内容：

- 两种 backend 的职责区别；
- 本地模式的默认配置；
- `hybrid-http-client` 的 `pdf_trans` 配置示例；
- MinerU 服务侧的 `MINERU_MODEL_SOURCE=local` 和
  `MINERU_VL_API_KEY` 配置说明；
- `PDF_TRANS_MINERU_URL` 与 `PDF_TRANS_MINERU_SERVER_URL` 的区别；
- Docker Compose 环境变量示例；
- 两种模式都使用 `medium` 且关闭图片和图表语义分析。

## 不在范围内

- 不支持 `vlm-engine`、`vlm-http-client`、`pipeline` 或其他 backend；
- 不在 Web 上传页面按任务选择 backend；
- 不把 backend 或 server URL 写入任务数据库；
- 不由 `pdf_trans` 保存或转发 GPUStack API Key；
- 不增加自动回退：远端模式失败时不会静默切换到本地模式；
- 不改变 MinerU 结果清洗、跨页合并、翻译或 Markdown 渲染行为。
