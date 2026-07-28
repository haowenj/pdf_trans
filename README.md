# PDF Trans

上传一个 PDF 到 MinerU 3.4.4 异步接口，下载解析结果，清洗并逐对象翻译 content
list。项目同时提供命令行模式和单进程 Web 面板模式。

## 环境

- Python 3.11+
- MinerU 3.4.4 服务；Web 默认地址为 `http://127.0.0.1:7100`
- OpenAI 兼容翻译接口

## 安装

命令行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Web 面板：

```bash
python3 -m pip install -e '.[web]'
```

如果需要 MySQL：

```bash
python3 -m pip install pymysql
export PDF_TRANS_DATABASE_URL='mysql+pymysql://user:password@127.0.0.1/pdf_trans?charset=utf8mb4'
```

MySQL 数据库需要预先创建好；Web 服务启动时会自动运行 Alembic 迁移。

## 命令行使用

```bash
python3 -m pdf_trans /path/to/document.pdf
# 任务 UUID：12345678-1234-4abc-8def-1234567890ab
```

连接其他地址的 MinerU：

```bash
python3 -m pdf_trans /path/to/document.pdf \
  --svr-url http://mineru.example:7100
```

如果 MinerU 流程已经完成，也可以只从规范化文件继续翻译：

```bash
python3 -m pdf_trans --translate-only \
  /path/to/normalized_content_list.json
```

每次 CLI 执行（包括 `--translate-only`）都会输出一个完整任务 UUID。使用 CLI 或 Web
面板生成的完整任务 UUID，都可以通过下列命令查看对应任务的诊断产物；加上 `--zip`
可在当前目录打包：

```bash
python3 -m pdf_trans cat_task \
  12345678-1234-4abc-8def-1234567890ab
python3 -m pdf_trans cat_task \
  12345678-1234-4abc-8def-1234567890ab --zip
```

ZIP 名称为 `pdf-trans-<uuid>.zip`；如果当前目录已有同名文件，命令会拒绝覆盖。

## Web 面板使用

```bash
python3 -m pdf_trans.web
```

默认监听 `127.0.0.1:8000`。Web 面板是单进程、单任务队列：

- 同一时刻只处理 1 个 PDF 任务；
- 任务状态固定为 `queued`、`running`、`succeeded`、`failed`、`interrupted`；
- 上传后立即入队，后台守护线程异步执行；
- 任务运行期间，脚本日志会持久化并通过页面右侧 Console 实时展示；
- 任务列表显示缩略 UUID，可一键复制完整 UUID；
- 如果服务重启时存在 `running` 任务，启动后会自动标记为 `interrupted`；
- 对 `failed` 或 `interrupted` 任务，页面会提供“继续”入口；
- “继续”优先复用已有 `normalized_content_list.json`，直接从翻译阶段断点续跑；
- 完成后可在新标签页预览 `rendered.md`，下载 Markdown，或使用浏览器“打印 / 导出
  PDF”。

Web 预览使用服务端 Markdown 渲染，保留 MinerU 生成的原生 HTML 表格；输出会经过
HTML 安全清洗，只保留允许的标签和属性。公式使用本地 KaTeX 0.18.1 资源渲染，不依赖
外网。图片等资源通过同源安全路由从任务目录读取。

如果要指定端口，例如改为 `8088`：

```bash
export PDF_TRANS_WEB_PORT="8088"
python3 -m pdf_trans.web
```

如果要同时指定监听地址和端口，例如开放到局域网：

```bash
export PDF_TRANS_WEB_HOST="0.0.0.0"
export PDF_TRANS_WEB_PORT="8088"
python3 -m pdf_trans.web
```

### Docker 单机部署

项目提供单容器 Web 部署方式。先在项目根目录创建未提交的 `.env`，填入翻译接口配置：

```dotenv
TRANSLATION_BASE_URL=https://api.example.com/v1
TRANSLATION_API_KEY=replace-with-api-key
TRANSLATION_MODEL=paper-translation-model
```

然后构建并启动：

```bash
docker compose up --build
```

Dockerfile 默认使用清华 PyPI 镜像安装依赖；如需切换镜像，可在构建时覆盖：

```bash
docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
```

浏览器访问 `http://127.0.0.1:8000`。Compose 默认把宿主的 `8000` 映射到容器的
`8000`；如需换端口，可在启动前设置：

```bash
PDF_TRANS_WEB_PORT=8088 docker compose up --build
```

任务数据库、上传 PDF、解析结果、日志和 Markdown 会持久化在宿主机的 `data/web`，
执行 `docker compose down` 后仍会保留。默认情况下，容器通过
`host.docker.internal:7100` 访问宿主机上的 MinerU；如果 MinerU 在其他地址，设置
`PDF_TRANS_MINERU_URL`。其他 `PDF_TRANS_*` 和 `TRANSLATION_*` 变量也可以写入 `.env`，
Compose 会传入容器。

停止服务：

```bash
docker compose down
```

## 配置

翻译接口变量：

```bash
export TRANSLATION_BASE_URL="https://api.example.com/v1"
export TRANSLATION_API_KEY="replace-with-api-key"
export TRANSLATION_MODEL="paper-translation-model"
export TRANSLATION_TIMEOUT_SECONDS="120"
export TRANSLATION_MAX_RETRIES="1"
export TRANSLATION_CONCURRENCY="5"
```

Web 变量：

```bash
export PDF_TRANS_WEB_DATA_DIR="/absolute/path/to/data/web"
export PDF_TRANS_DATABASE_URL="sqlite:////absolute/path/to/data/web/pdf_trans.db"
export PDF_TRANS_MAX_UPLOAD_MIB="200"
export PDF_TRANS_MINERU_URL="http://127.0.0.1:7100"
export PDF_TRANS_WEB_HOST="127.0.0.1"
export PDF_TRANS_WEB_PORT="8000"
```

说明：

- `TRANSLATION_BASE_URL`、`TRANSLATION_API_KEY`、`TRANSLATION_MODEL` 为翻译必需项；
- `TRANSLATION_TIMEOUT_SECONDS` 默认 120，必须为正整数；
- `TRANSLATION_MAX_RETRIES` 默认 1，表示失败后的额外重试次数；
- `TRANSLATION_CONCURRENCY` 默认 5，控制单个翻译任务内部的并发请求数；
- `PDF_TRANS_WEB_DATA_DIR` 默认项目根目录下的 `data/web`；
- `PDF_TRANS_DATABASE_URL` 默认指向 `data/web/pdf_trans.db`；
- `PDF_TRANS_MAX_UPLOAD_MIB` 默认 `200`；
- `PDF_TRANS_MINERU_URL` 默认 `http://127.0.0.1:7100`；
- `PDF_TRANS_WEB_HOST` 默认 `127.0.0.1`；
- `PDF_TRANS_WEB_PORT` 默认 `8000`。

## 输出文件

MinerU 结果解压到数据目录，并按以下顺序生成处理结果：

```text
content_list.json
  -> cleaned_content_list.json
  -> cross_page_candidates.json（诊断报告）
  -> normalized_content_list.json
  -> translated_content_list.json
  -> rendered.md
```

CLI 每次完整解析都会生成独立 UUID 运行目录，避免同名 PDF 覆盖彼此的解析产物：

```text
data/runs/cli-<uuid>/
  task.json
  task.log
  mineru_result.zip
  <mineru-archive-layout>/...
```

`task.json` 记录任务状态和产物引用，`task.log` 保存无终端颜色的完整流程日志，
`mineru_result.zip` 是 MinerU 返回、尚未解压的原始结果包。

`--translate-only` 同样会生成 `data/runs/cli-<uuid>/` 下的 `task.json` 和
`task.log`，但翻译文件和 Markdown 仍写在指定的
`normalized_content_list.json` 旁；清单只引用这次明确使用或生成的三个文件。

Web 面板继续按任务 UUID 和执行次数隔离，例如：

```text
data/web/tasks/<task-id>/
  task.log
  upload/source.pdf
  attempts/1/
    mineru_result.zip
    ...
  attempts/2/
    mineru_result.zip
    ...
```

命令行最终统计会输出处理前数量、过滤数量、处理后数量、清洗统计、Markdown 路径、
跨页候选数量、规范化文件路径，以及翻译统计和翻译文件路径。Web 面板会把这些流程
日志写入 Console 和任务目录，并把成功产物关联到对应任务。

### 任务诊断

`cat_task` 可按同一个纯 UUID 定位新旧 CLI 任务或 Web 任务，列出相对路径和文件大小。
Web 任务会包含全部 `attempts/`；旧 Web 任务如果还没有 `task.log`，命令会尝试从
数据库导出日志。正在执行的任务也可以查看和打包，但结果只是当时的文件快照。

诊断列表和 ZIP 不包含上传的原始 PDF，也不会跟随符号链接。CLI
`--translate-only` 的外部产物只按 `task.json` 中记录的精确文件加入，不会递归打包
它们所在的整个目录。

检查 Web 任务时，运行命令的环境必须使用与 Web 部署相同的
`PDF_TRANS_WEB_DATA_DIR` 和 `PDF_TRANS_DATABASE_URL`。数据库暂时不可用时，命令仍会
收集文件系统中的产物并给出警告。

## 翻译行为

翻译阶段只处理 `type` 为 `text` 的对象，每个对象单独调用一次 OpenAI 兼容接口：

- 成功：保留原 `text`，增加 `translated_text` 和
  `translation_status: "success"`；
- 失败：保留原对象，设置 `translated_text: null`、
  `translation_status: "failed"`，并增加 `translation_error`；
- 其他类型：完全原样输出。

数组顺序、对象数量和原始 `text` 字段不会改变。正常结束时所有 text 对象都是
`success` 或 `failed`，不会保留 `pending`。每完成一个对象，结果都会原子写入
`translated_content_list.json`。再次运行时，程序会先校验已有输出与
`normalized_content_list.json` 的对象数量、顺序、`type` 和 `text`；校验通过后跳过
已有 `success`，并重新处理 `pending` 和 `failed`。

运行日志写入 stderr，最终统计写入 stdout。日志不会输出 API Key、完整原文或完整译文。

## Markdown 渲染

`rendered.md` 按 `translated_content_list.json` 数组的原顺序输出以下内容：

- `text`：成功翻译时输出 `translated_text`，并保留原有一级/二级标题层级；翻译失败、
  译文为空或旧格式对象时回退到原始 `text`；
- `ref_text`：原始参考文献段落；
- `image` 和 `chart`：相对图片路径、图注和脚注；
- `table`：图注、原始 HTML 表格和脚注；
- `equation`：MinerU 提供的原始 LaTeX 文本。

Web reader 会把 `rendered.md` 渲染为安全 HTML，保留 `<table>`、`rowspan`、
`colspan` 等结构，补充表格样式、图片自适应和打印样式，并用本地 KaTeX 渲染正文及
表格中的公式。

## 跨页段落候选

`cross_page_candidates.json` 只检查清洗数组中立即相邻的两个 `text` 对象。当后一个
`page_idx` 等于前一个加 1，且前一个文本忽略尾部空白后不以 `. ! ? : ;` 结尾时，
记录为疑似跨页段落。

首尾相接的候选会构成一条链，按原始索引顺序合并为一个对象，只保留链首对象。合并对象
保留链首的原有字段和 `page_idx`，并增加 `source_page_indices`、`source_bboxes`
与 `merged_cross_page: true`。

## 当前限制

- Web 服务目前只支持单进程、单任务串行执行；
- 不提供任务取消、删除和鉴权；
- 页面预览依赖浏览器自身的打印能力导出 PDF，不在服务端生成第二份 PDF；
- Web 队列层暂未扩展为多任务并行方案。

## 测试

```bash
python3 -m pip install -e '.[test,web]'
python3 -m pytest -v
```
