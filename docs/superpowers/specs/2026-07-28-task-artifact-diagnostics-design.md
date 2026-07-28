# 任务产物诊断与打包设计

## 背景

PDF 处理流程已经为解析、清洗、跨页合并、翻译和 Markdown 渲染保留阶段性文件，
但这些文件缺少统一的任务查询入口：

- CLI 完整解析虽然使用 UUID 目录隔离，但没有明确输出任务 UUID，也没有任务元数据和
  文件日志；
- CLI `--translate-only` 没有任务 UUID，其输入和输出位于外部断点目录；
- Web 已使用任务 UUID 和数据库日志，但页面不直接展示 UUID，日志也不在任务目录；
- MinerU 返回的原始 ZIP 在解压后不再保留，无法核对服务原始响应；
- 排查问题时需要手工查找多个目录和数据库记录。

本功能增加统一的 `cat_task` 命令，通过纯 UUID 定位 CLI 或 Web 任务，列出诊断产物，
并可把当前快照打成 ZIP。

## 目标

- CLI 完整解析和 `--translate-only` 每次执行都获得标准 UUID。
- CLI 在终端明确输出完整 UUID，并同时把日志写入任务目录。
- Web 任务列表显示可复制的 UUID，现有数据库日志同时镜像到任务日志文件。
- 保留每次 MinerU 请求返回的原始 ZIP。
- 通过同一个命令定位 CLI 或 Web 任务并列出相关文件。
- 使用 `--zip` 在当前目录创建可移交的诊断包。
- 为以后按目录清理 CLI 任务提供明确的 `cli-` 来源前缀。

## 非目标

- 不实现定时清理任务；本次只提供可识别的 CLI 目录命名。
- 不把 CLI 任务写入 Web 数据库。
- 不把原始 PDF 放入诊断列表或诊断 ZIP。
- 不改变现有解析、清洗、翻译和 Markdown 内容规则。
- 不为 Web 页面增加诊断包下载接口；打包入口仅为命令行。

## 方案选择

采用“任务清单 + 文件系统日志”的方案。每个 CLI 任务保存 `task.json` 和
`task.log`，新 Web 任务在保留数据库日志的同时写 `task.log`。`cat_task` 主要依赖
文件系统，因此可以在服务不运行时排查；对缺少日志文件的旧 Web 任务，再尝试从配置的
数据库读取日志。

没有采用完全动态的目录扫描和数据库查询方案，因为它不能可靠描述
`--translate-only` 的外部文件，并且会让新任务的诊断依赖数据库可用性。也没有把 CLI
任务统一写入 Web 数据库，因为这会使基础 CLI 依赖 SQLAlchemy、Alembic 和数据库
驱动。

## 目录布局

### CLI 完整解析

```text
data/runs/
  cli-<uuid>/
    task.json
    task.log
    mineru_result.zip
    <mineru-archive-layout>/
      <MinerU 解压文件>
      cleaned_content_list.json
      cross_page_candidates.json
      normalized_content_list.json
      translated_content_list.json
      rendered.md
```

CLI 对外展示和 `cat_task` 参数始终使用纯 UUID。`cli-` 只存在于磁盘目录名中，供未来
清理逻辑识别 CLI 数据来源。

### CLI `--translate-only`

```text
data/runs/
  cli-<uuid>/
    task.json
    task.log
```

翻译结果继续按现有行为写在输入 `normalized_content_list.json` 旁。
`task.json` 只记录本次明确使用或产生的规范化文件、翻译文件和 Markdown 路径；
`cat_task` 不递归打包该外部父目录。

功能上线前由现有 CLI 创建的 `data/runs/<uuid>/` 作为旧格式只读兼容。
`cat_task` 可以定位并列出其中已有文件，但会提示该任务没有清单、文件日志和保留的
MinerU 原始 ZIP。所有新 CLI 任务只写入带 `cli-` 前缀的目录。

### Web

```text
data/web/
  tasks/
    <uuid>/
      task.log
      upload/
        source.pdf
      attempts/
        1/
          mineru_result.zip
          <mineru-archive-layout>/
        2/
          mineru_result.zip
          <mineru-archive-layout>/
```

Web 诊断包含全部 `attempts/`。`upload/source.pdf` 继续保留供任务运行，但不进入
`cat_task` 的文件列表或 ZIP。

## 命令接口

```bash
python -m pdf_trans cat_task <task-uuid>
python -m pdf_trans cat_task <task-uuid> --zip
```

现有命令保持兼容：

```bash
python -m pdf_trans paper.pdf
python -m pdf_trans --translate-only normalized_content_list.json
```

不带 `--zip` 时输出：

- 任务来源：`cli` 或 `web`；
- 完整任务 UUID；
- 定位到的任务根目录；
- 按相对路径排序的诊断文件和字节数；
- 缺失的外部引用及其他警告。

带 `--zip` 时先输出同一份列表，再在当前工作目录创建：

```text
pdf-trans-<uuid>.zip
```

目标文件已存在时拒绝覆盖。

## 任务清单

CLI 启动后立即创建初始 `task.json`，至少包含：

- schema 版本；
- 纯 UUID；
- 任务类型：`full` 或 `translate-only`；
- 状态：`running`、`succeeded` 或 `failed`；
- 创建和结束时间；
- 原始输入路径，仅作为元数据，不进入诊断 ZIP；
- 任务根目录；
- 明确的外部产物引用，仅用于 `translate-only`；
- 失败时的安全错误摘要。

清单使用 UTF-8 JSON 和原子替换写入。任务失败也必须尽力更新状态，但清单更新失败不能
掩盖原始工作流异常。

## UUID 暴露

CLI 在开始执行完整解析或 `--translate-only` 后立即输出：

```text
任务 UUID：<uuid>
```

Web 上传接口和任务事件继续返回完整 `id`。任务列表额外显示缩略 UUID，并提供复制
按钮；按钮复制完整 UUID。服务端首次渲染和 SSE 动态刷新必须保持一致。

## 日志

CLI 日志继续以现有彩色格式写入终端，同时以无 ANSI 颜色的文本格式追加到
`data/runs/cli-<uuid>/task.log`。文件日志包含时间、级别和消息，并覆盖成功与失败路径。

Web 继续把任务日志写入数据库 Console，同时把同一条无 ANSI 日志镜像追加到
`data/web/tasks/<uuid>/task.log`。日志镜像沿用当前单活动任务约束和 Handler 锁，不把
`pdf_trans.web` 请求日志混入任务日志。

对功能上线前创建、没有 `task.log` 的 Web 任务，`cat_task` 使用当前
`PDF_TRANS_DATABASE_URL` 读取该 UUID 的数据库日志，在列表中显示虚拟
`task.log`，并在打包时直接写入 ZIP。数据库不可访问时警告并继续处理其他文件。

日志继续遵守现有安全规则，不写 API Key、完整提示词或额外的完整原文和译文。

## MinerU 原始 ZIP

完整工作流收到 MinerU ZIP 后，先在本次输出根目录写入 `mineru_result.zip`，再进行安全
解压。即使解压、清洗、规范化、翻译或渲染失败，已经收到的原始 ZIP 仍保留。

CLI 完整任务保存在 `cli-<uuid>/mineru_result.zip`；Web 每次完整重试保存在对应的
`attempts/<attempt-count>/mineru_result.zip`。`--translate-only` 不调用 MinerU，
因此没有该文件。

## 任务定位与文件收集

`cat_task` 先严格校验标准 UUID，再按以下顺序构造候选路径：

1. `DEFAULT_DATA_DIR / "runs" / f"cli-{uuid}"`；
2. 只读兼容路径 `DEFAULT_DATA_DIR / "runs" / uuid`；
3. `WebSettings` 所配置数据目录下的 `"tasks" / uuid`。

只找到一个候选时确定任务来源；多个候选同时存在时报定位冲突；都不存在时显示检查过
的路径并返回非零状态。

CLI 完整任务递归收集任务根目录内的普通文件。CLI `translate-only` 额外读取
`task.json` 中列出的精确文件引用，在 ZIP 中放入 `referenced/`。Web 递归收集
`attempts/` 和 `task.log`，并明确排除整个 `upload/`。

目录项按相对展示路径排序，并显示文件大小。缺失的 `translate-only` 引用在列表中标为
缺失；打包时跳过并警告。符号链接一律不跟随、不列出、不打包。

## ZIP 结构与安全

CLI 诊断 ZIP 使用受控相对条目名：

```text
pdf-trans-<uuid>/
  task.json
  task.log
  mineru_result.zip
  <任务目录内的其他产物>
  referenced/
    normalized_content_list.json
    translated_content_list.json
    rendered.md
```

Web ZIP 不包含 `task.json`，保留 `attempts/<n>/...`；新 Web 任务包含顶层
`task.log`，旧 Web 的虚拟日志也直接写为顶层 `task.log`。旧格式 CLI ZIP 只包含实际
存在的目录文件。

ZIP 创建使用独占方式，目标存在时失败。条目名不接受绝对路径或 `..` 路径段。
`translate-only` 的外部路径仅以清单中声明的精确文件加入，不递归其父目录。正在运行的
任务允许列出和打包当前快照，但输出警告，说明文件可能继续变化。

## 错误处理

- 非标准 UUID：报参数错误，不扫描文件系统。
- 任务不存在：显示 CLI 和 Web 候选路径并返回非零状态。
- CLI/Web 同 UUID 冲突：报错，不自动选择。
- 清单损坏或类型不匹配：报错，不猜测外部引用。
- 外部引用缺失：警告并继续列出或打包其他文件。
- 旧 Web 数据库日志读取失败：警告并继续，不阻止产物打包。
- 目标 ZIP 已存在：报错，不覆盖。
- 原始 ZIP 落盘失败：完整工作流失败并保留明确日志。
- 最终清单状态更新失败：记录次级错误，但不替换原始工作流异常。

## 测试

### CLI 和清单

- 保持现有 PDF 路径、`--translate-only` 和 `--svr-url` 语法兼容；
- `cat_task` 子命令、UUID 校验和 `--zip` 参数；
- 完整解析与 `--translate-only` 使用 `cli-<uuid>` 目录；
- 两种 CLI 模式都打印纯 UUID 并写初始、成功和失败清单；
- `--translate-only` 仍把翻译和 Markdown 写在输入旁。

### 日志和 MinerU ZIP

- CLI 终端保持彩色输出，文件日志无 ANSI 且包含同一消息；
- Web 数据库和文件日志收到同一任务消息；
- Web 请求日志和非活动任务日志不进入 `task.log`；
- 原始 MinerU ZIP 在解压前写入，后续阶段失败时仍存在。

### 任务定位和打包

- 新旧 CLI 完整任务、CLI `translate-only`、新 Web 任务和旧 Web 任务定位；
- Web 全部 attempts 被收集，`upload/source.pdf` 被排除；
- 路径排序、字节数、缺失引用和运行中警告；
- 符号链接和越界 ZIP 条目被排除；
- ZIP 目录结构、虚拟 Web 日志、当前目录输出和禁止覆盖；
- 数据库不可用时仍能打包其余文件。

### Web UUID

- 服务端页面显示缩略 UUID 和复制入口；
- SSE 动态任务行生成相同元素；
- 复制行为使用完整 UUID。

### 回归验证

- 完整 pytest 测试套件；
- sdist 和 wheel 构建；
- `git diff --check`。
