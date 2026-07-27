# PDF 异步解析任务与 Markdown 阅读页设计

## 背景

当前项目通过命令行同步执行完整工作流：上传 PDF 到 MinerU、下载解析结果、清洗、
合并跨页段落、并发翻译，并生成 `rendered.md`。现有工作流已经提供阶段日志，以及基于
`translated_content_list.json` 的翻译断点续传能力。

本功能在不改变现有 CLI 行为的前提下，增加一个单体 Web 应用。用户可以通过页面上传
PDF，在后台串行执行完整工作流，查看任务状态和实时日志，并在翻译完成后阅读、下载或
打印 Markdown 结果。

## 目标

- 提供服务端渲染的上传页面，不引入前后端分离工程或 Node.js 构建链。
- 上传后异步执行任务，Web 请求不等待 PDF 工作流完成。
- 在首页展示任务历史、当前状态和执行次数。
- 通过右侧 Console 抽屉展示持久化历史日志和实时新增日志。
- 服务重启后保留任务、日志和输出，并允许继续中断的任务。
- 在新标签页安全渲染 `rendered.md`，支持原生 HTML 表格和 KaTeX 公式。
- 支持下载原始 Markdown，并通过浏览器打印对话框导出 PDF。
- 默认使用 SQLite，同时保持 ORM、迁移和查询方式与 MySQL 兼容。

## 非目标

- 首版不提供多 Web 进程、多主机或多个 PDF 任务并行执行。
- 首版不引入 Redis、Celery、RQ 或其他外部任务队列。
- 首版不提供账号、登录、权限系统、任务取消或任务删除。
- 首版不由后端生成 PDF 文件；PDF 导出使用浏览器打印能力。
- 首版不对已生成的 Markdown 提供在线编辑。

## 技术方案

### 应用形态

使用 FastAPI、Jinja2 和少量原生 JavaScript 构建单体应用。HTML 模板、CSS、JavaScript
和 KaTeX 静态资源均由同一个 Python 包提供，运行时不从 CDN 加载资源。

Web 相关 Python 依赖作为 `web` 可选依赖安装，避免仅使用 CLI 的用户被迫安装 Web
组件：

```bash
python -m pip install -e '.[web]'
python -m pdf_trans.web
```

Web 服务只允许单进程运行。首版启动文档明确禁止配置多个 Uvicorn worker，因为任务
执行器是进程内单工作线程。

### 组件边界

- `web.app`：创建 FastAPI 应用、挂载路由和静态资源、管理 lifespan。
- `web.config`：读取数据库 URL、数据目录、MinerU 地址、上传限制和监听地址。
- `web.db`：创建 SQLAlchemy engine 和 session factory。
- `web.models`：定义任务和任务日志 ORM 模型。
- `web.repositories`：封装短事务中的任务状态与日志读写。
- `web.worker`：单后台线程，按照创建时间取得排队任务并调用现有工作流。
- `web.task_runner`：决定执行完整工作流还是从规范化文件继续翻译。
- `web.task_logging`：在单个任务执行期间捕获非 Web 的 `pdf_trans` 日志并持久化。
- `web.markdown`：将 Markdown 转为 HTML，并执行允许列表清洗。
- `web.routes`：页面、上传、继续、SSE、下载和任务资源接口。
- `web/templates` 与 `web/static`：任务首页、阅读页及本地静态资源。

现有 `process_pdf()`、`process_translation_file()` 和
`render_content_list_file()` 继续作为解析、续传和 Markdown 生成的唯一实现，Web 层
不复制业务逻辑。

## 任务生命周期

任务状态使用普通字符串列，允许值如下：

- `queued`：等待单工作线程执行。
- `running`：当前正在执行。
- `succeeded`：`rendered.md` 已生成，可以查看。
- `failed`：本次执行因业务或环境错误结束。
- `interrupted`：服务启动时发现上次仍为 `running`，说明进程在任务完成前退出。

正常状态转换如下：

```text
上传 → queued → running → succeeded
                    └──→ failed

服务重启：running → interrupted
继续执行：interrupted/failed → queued → running
```

数据库中的 `queued` 任务在服务重启后仍会自动按顺序执行。遗留的 `running` 任务不会
自动执行，以免用户未确认时重复调用外部服务；它们转为 `interrupted` 并显示“继续”
按钮。

继续执行不创建新任务。它保留原任务 ID、上传文件、历史日志和输出目录，并把任务重新
放入队列。工作线程实际领取任务时才将 `attempt_count` 加一，并追加明确的“开始第 N
次执行”日志。对已经是 `queued`、`running` 或 `succeeded` 的任务调用继续接口返回
HTTP 409。

### 断点恢复

继续任务时按以下顺序选择入口：

1. 如果任务目录中存在唯一的 `normalized_content_list.json`，调用现有
   `process_translation_file()`。该函数校验已有翻译文件，跳过 `success`，重新处理
   `pending` 和 `failed`。翻译完成后调用 `render_content_list_file()` 重新生成
   `rendered.md`。
2. 如果尚未生成规范化文件，但原始上传 PDF 仍存在，则忽略本次未形成有效断点的解析
   结果，在新的执行次数目录中调用 `process_pdf()` 重新执行完整工作流，包括重新提交
   MinerU。旧的部分输出保留用于诊断，不参与新执行。
3. 如果规范化文件不唯一、原始上传文件缺失或断点校验失败，将任务标记为 `failed`，
   在错误摘要和 Console 中说明原因。

每个任务固定串行执行；单个任务内部现有的翻译并发保持不变。

## 文件布局

默认 Web 数据根目录为项目的 `data/web/`：

```text
data/web/
  pdf_trans.db
  tasks/
    <task-uuid>/
      upload/
        source.pdf
      attempts/
        1/
          <mineru-archive-layout>/
            normalized_content_list.json
            translated_content_list.json
            rendered.md
            images/
        2/
          <mineru-archive-layout>/
```

原始文件名只保存在数据库中用于显示，不参与磁盘路径拼接。任务 UUID 使用标准字符串
形式。每次必须重新运行完整 MinerU 流程时使用新的 `attempts/<attempt_count>/` 目录；
从规范化文件继续翻译时仍在该断点所在目录写入结果。数据库存储任务根目录下的相对
路径，不存储依赖某台机器的绝对路径。

## 数据模型

### `tasks`

- `id`：36 字符 UUID 字符串主键。
- `original_filename`：用户上传时的文件名，`String(255)`。
- `status`：任务状态，`String(16)`，并建立索引。
- `attempt_count`：已开始的执行次数，默认 0。
- `source_pdf_path`：相对 Web 数据根目录的上传文件路径，`String(1024)`。
- `normalized_path`：可为空的规范化文件相对路径，`String(1024)`。
- `markdown_path`：可为空的 Markdown 文件相对路径，`String(1024)`。
- `error_message`：可为空的最后错误摘要，`String(2000)`。
- `created_at`、`updated_at`、`started_at`、`finished_at`：UTC 时间。

### `task_logs`

- `id`：自增整数主键，同时作为日志顺序和 SSE 事件 ID。
- `task_id`：外键指向 `tasks.id`，并建立联合查询所需索引。
- `level`：日志级别名称，`String(16)`。
- `message`：`Text`，保存已格式化消息正文，不包含 ANSI 颜色码。
- `created_at`：UTC 时间。

不使用 SQLite JSON、原生 Enum、部分索引或其他方言专属字段。数据库 URL 从
`PDF_TRANS_DATABASE_URL` 读取，默认指向 `data/web/pdf_trans.db`。SQLAlchemy engine、
session、事务和 Alembic 迁移使用标准 2.x API；未来切换 MySQL 时只需要安装相应 DBAPI
驱动、创建数据库并修改 URL。

## 后台执行与日志

FastAPI lifespan 在启动时执行数据库迁移、把遗留 `running` 标记为 `interrupted`，
然后启动一个 daemon 工作线程。工作线程从数据库领取最早的 `queued` 任务；完成一项
后再领取下一项。新上传或继续任务时使用线程条件变量唤醒工作线程，但数据库状态仍是
唯一事实来源。

任务领取和状态变更使用短事务和带状态条件的原子更新，避免同一任务重复领取。执行
PDF 工作流期间不持有数据库事务或 session。

任务开始时，工作线程为进程级日志捕获器设置唯一的活动任务 ID，结束时清除。附加到
`pdf_trans` logger 的数据库日志 Handler 使用内部锁串行写入当前活动任务的记录，并
排除 `pdf_trans.web` 命名空间，因此既能捕获翻译线程池中的日志，也不会把访问日志或
其他 Web 请求日志混入 Console。该方案依赖首版“同一时间只有一个 PDF 任务”的约束；
未来实现多任务并行时必须把任务 ID 传播到每个翻译线程后再替换捕获器。每条日志使用
独立短事务；Handler 自身写库失败时写入服务 stderr，不递归记录到 `pdf_trans`。

数据库日志不保存 API Key、完整原文或完整译文；继续遵循现有日志约束。
任务完成时，`task_runner` 还会把原 CLI 展示的清洗数量、候选数量、翻译统计和结果
路径转换为 INFO 日志，确保 Console 可以看到完整执行摘要。

## 页面与接口

### 任务首页

首页采用已经确认的纵向任务中心布局：

- 顶部为拖放/选择 PDF 上传区。
- 下方按创建时间倒序展示任务。
- 每行展示文件名、创建时间、状态、执行次数和可用操作。
- `running` 显示 Console；`queued` 显示排队状态。
- `interrupted` 和 `failed` 显示 Console 与“继续”。
- `succeeded` 显示 Console 与“查看”。
- “查看”使用新标签页打开阅读页。

Console 从页面右侧抽屉打开。打开时先加载全部历史日志，再通过 SSE 追加新日志。默认
自动滚动到底部；用户主动向上滚动后暂停自动滚动，并可手动恢复。Console 支持复制全部
日志，并显示 SSE 连接或重连状态。

### HTTP 接口

- `GET /`：服务端渲染任务首页。
- `POST /tasks`：流式接收 PDF，校验成功后创建任务。
- `POST /tasks/{task_id}/resume`：继续 `interrupted` 或 `failed` 任务。
- `GET /tasks/events`：任务状态 SSE；连接时先发送当前任务快照，之后发送变化后的快照。
- `GET /tasks/{task_id}/logs`：按日志 ID 分页读取历史日志。
- `GET /tasks/{task_id}/logs/events`：按 `Last-Event-ID` 续接新增日志 SSE。
- `GET /tasks/{task_id}/view`：服务端渲染安全的 Markdown 阅读页。
- `GET /tasks/{task_id}/markdown`：下载原始 `rendered.md`。
- `GET /tasks/{task_id}/assets/{asset_path:path}`：读取任务内图片等资源。

SSE 每 15 秒发送 keepalive。任务状态流重连后直接发送数据库当前快照，不依赖历史
事件；日志流重连时携带最后日志 ID，服务端从数据库补发遗漏记录。生成器在客户端断开
时结束，并且每次查询都使用独立短 session。

不存在的任务返回 404；状态不允许继续或结果尚未生成返回 409；上传校验失败返回 400
或 413。页面使用同源请求，不启用宽泛 CORS。

## 上传与文件安全

- 默认最大上传大小为 200 MiB，可通过 `PDF_TRANS_MAX_UPLOAD_MIB` 修改。
- 接收时分块写入临时文件并累计大小，超过限制立即停止并删除临时文件。
- 同时校验 `.pdf` 扩展名、声明的 PDF Content-Type 和文件头 `%PDF-`。
- 只有全部校验通过后才原子移动为 `upload/source.pdf` 并创建任务记录。
- 任务文件路由将目标路径解析为真实路径，并确认仍位于该任务允许的输出目录内。
- 拒绝绝对路径、`..` 越界、符号链接越界和不存在的资源。
- 下载响应使用安全的 `Content-Disposition`，显示名经过框架提供的响应头编码处理。

## Markdown 阅读与打印

服务端使用 `markdown-it-py` 将 `rendered.md` 转换为 HTML，并开启原生 HTML，以保留
MinerU 产生的表格。转换结果必须经过 `nh3` 清洗后才能传给模板。

允许列表包括文章常用标签、`table`、`thead`、`tbody`、`tfoot`、`tr`、`th`、`td`、
`img`、`a`、`pre`、`code` 和 KaTeX 执行前需要保留的文本结构。只允许必要属性：

- 表格单元格的 `rowspan`、`colspan`；
- 图片的 `src`、`alt`、`title`；
- 链接的 `href`、`title`；
- 代码块的受控 class。

不允许原始 `style`、事件属性、`script`、`iframe`、表单或任意 class。相对图片 URL
在清洗阶段改写为当前任务的 `/assets/` 接口；URL scheme 只允许 `http`、`https` 和
任务内相对资源。外部链接统一增加 `rel="noopener noreferrer"`。

阅读页加载随包分发的 KaTeX 0.18.1 CSS、字体、脚本和 auto-render 扩展，不访问 CDN。
仓库同时保留 KaTeX 的许可证文件和版本来源记录。
KaTeX 在安全 HTML 插入后遍历正文及 HTML 表格单元格，按顺序识别 `$$...$$`、
`$...$`、`\(...\)` 和 `\[...\]`。配置包括：

- `trust: false`
- `throwOnError: false`
- 有限的 `maxSize` 和 `maxExpand`

阅读样式为居中的论文正文，表格带边框和单元格间距；网页窄屏下表格容器可横向滚动。
图片限制为正文宽度。

顶部工具栏提供“下载 Markdown”和“打印 / 导出 PDF”。打印按钮在 DOM 与 KaTeX 渲染
完成后启用，并调用 `window.print()`。打印样式：

- 隐藏工具栏和其他交互控件；
- 设置 A4 页边距与打印字号；
- 保留表格边框、图片和公式；
- 尽量避免标题、图片、公式和表格在内部断页；
- 取消横向滚动容器，允许宽表格缩放或换行；
- 保留用户在浏览器打印对话框中选择横向布局和“另存为 PDF”的能力。

## 错误处理

- 上传阶段失败时不创建任务，并删除临时文件。
- 工作流抛出的 `PDFTransError`、`OSError` 或未预期异常都会记录 ERROR 日志，将任务标记
  为 `failed`，并保存适合列表展示的错误摘要。
- 错误摘要不包含堆栈、密钥、完整原文或完整译文；完整服务端堆栈只写 stderr。
- SSE 暂时断开不影响任务；任务状态流重连后获取最新快照，日志流从最后日志 ID 补发。
- Markdown 文件或任务资源缺失时返回明确的 404，不暴露真实文件系统路径。
- 数据库不可用时拒绝启动；不在无法持久化状态时继续执行任务。

## 配置

- `PDF_TRANS_DATABASE_URL`：SQLAlchemy 数据库 URL。
- `PDF_TRANS_WEB_DATA_DIR`：Web 数据根目录，默认 `data/web/`。
- `PDF_TRANS_MAX_UPLOAD_MIB`：上传上限，默认 `200`。
- `PDF_TRANS_MINERU_URL`：MinerU 服务地址，默认 `http://127.0.0.1:7100`。
- `PDF_TRANS_WEB_HOST`：监听地址，默认 `127.0.0.1`。
- `PDF_TRANS_WEB_PORT`：监听端口，默认 `8000`。
- 现有 `TRANSLATION_*` 环境变量继续配置翻译客户端。
- 首版 Web 页面不允许逐任务覆盖 MinerU 服务地址。

可信本机或内网是首版部署前提。监听非回环地址时，部署者负责在反向代理或网络边界上
限制访问。

## 测试策略

所有生产代码遵循测试先行。测试使用临时 SQLite 数据库和临时任务目录，不调用真实
MinerU 或翻译接口。

### 单元测试

- ORM 创建、查询、状态条件更新和 UTC 时间。
- 任务恢复入口选择：规范化断点、完整重跑、无效断点。
- `running → interrupted` 启动恢复。
- 日志 Handler 的任务隔离、顺序、级别和无 ANSI 输出。
- Markdown 普通内容、原生 HTML 表格、`rowspan`、`colspan`、危险标签、事件属性和危险
  URL 清洗。
- 任务资源路径解析和路径穿越拒绝。
- 上传文件头、扩展名、Content-Type 和大小限制。

### 集成测试

- 上传请求快速返回，后台线程串行执行两个任务。
- 任务成功、失败、中断、继续和续传的完整状态转换。
- 继续翻译跳过已有成功段落并最终生成 `rendered.md`。
- 历史日志接口和 SSE 使用最后事件 ID 补发。
- 完成任务可以打开阅读页并下载 Markdown；未完成任务返回 409。
- 表格 CSS 包含边框和窄屏横向滚动规则。
- 阅读页加载本地 KaTeX 资源，包含安全配置和四组公式分隔符。
- 打印按钮等待公式初始化，打印 CSS 隐藏工具栏并保留正文内容。
- 图片资源可从任务目录读取，越界资源无法读取。

现有 CLI 和工作流测试必须全部继续通过。

## README 更新

实现完成后 README 增加：

- Web 可选依赖安装和启动命令；
- 单进程、单任务串行约束；
- 数据库、数据目录和上传上限配置；
- 任务继续执行语义；
- Console、Markdown 阅读、下载和浏览器打印说明；
- SQLite 切换 MySQL 的数据库 URL 示例；
- 将已经完成的 Markdown 网页预览待办项移入功能说明。
