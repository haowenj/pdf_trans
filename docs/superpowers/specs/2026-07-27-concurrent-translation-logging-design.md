# 并发翻译与彩色工作流日志设计

## 目标

在现有全量逐对象翻译和断点续跑能力上增加有限多线程并发，并提高完整 PDF
工作流与仅翻译工作流的可观测性。

本次调整包括：

- 使用 Python 标准库 `concurrent.futures.ThreadPoolExecutor` 并发调用翻译模型；
- 新增环境变量 `TRANSLATION_CONCURRENCY`，默认值为 `5`；
- 将 `TRANSLATION_TIMEOUT_SECONDS` 默认值从 60 秒改为 120 秒；
- 保持每个 `type=text` 对象一次独立请求，不进行批量请求；
- 保持断点校验、逐对象原子写入、失败重试和原始数组顺序；
- 为工作流阶段、跨页合并和单段翻译增加带耗时的彩色日志；
- 明确翻译请求不启用深度思考参数。

不增加协程、进程池、数据库、Agent、长文本拆分、批量翻译、表格翻译、图注
翻译或 Web 接口。

## 配置

翻译配置继续从环境变量读取：

```text
TRANSLATION_BASE_URL
TRANSLATION_API_KEY
TRANSLATION_MODEL
TRANSLATION_TIMEOUT_SECONDS
TRANSLATION_MAX_RETRIES
TRANSLATION_CONCURRENCY
```

规则如下：

- `TRANSLATION_TIMEOUT_SECONDS` 默认 `120`，必须是大于 0 的有限数字；
- `TRANSLATION_MAX_RETRIES` 默认 `1`，表示首次调用失败后最多再调用 1 次；
- `TRANSLATION_CONCURRENCY` 默认 `5`，必须是大于 0 的整数；
- 配置非法时在发出模型请求之前终止，并输出红色错误日志。

`OpenAICompatibleTranslator` 暴露 `timeout_seconds`、`max_retries` 和
`concurrency` 三个只读配置属性。工作流未注入测试 translator 时使用环境变量中的
并发数；注入 translator 时允许测试通过函数参数指定并发数，未指定时同样使用默认值
5。

## 深度思考请求约束

当前 OpenAI 兼容请求体只包含：

```json
{
  "model": "...",
  "messages": []
}
```

当前实现没有主动启用深度思考。正式调整后仍不发送以下字段：

- `reasoning`
- `reasoning_effort`
- `thinking`

不主动发送 `reasoning_effort: "none"`，因为该字段及其取值不是所有 OpenAI
兼容服务都支持，增加它可能使原本可用的兼容接口报参数错误。单元测试明确断言请求体
不存在上述字段。

## 多线程翻译架构

### 调度

翻译引擎完成输入和断点校验后，按原数组顺序收集所有需要处理的 text 对象：

- `success` 跳过；
- `pending` 提交线程池；
- `failed` 提交线程池；
- 非 text 对象不提交。

使用 `ThreadPoolExecutor(max_workers=concurrency)`，默认最多同时存在 5 个翻译
任务。所有任务仍调用同一个线程安全的同步 HTTP 客户端，以复用连接池。

每个任务携带：

- 原数组索引；
- 从 1 开始的 text 段序号；
- 原始 `text`；
- 最大重试次数。

### 结果隔离与写入

工作线程不修改共享对象数组，也不写
`translated_content_list.json`。工作线程只返回一个不可变结果，包含：

- 原数组索引；
- text 段序号；
- `success` 或 `failed`；
- 译文或最终异常信息；
- 本段实际模型调用次数；
- 本段总耗时。

主线程通过 `as_completed` 接收结果。每收到一个结果：

1. 按原数组索引更新对应对象；
2. 成功时写入 `translated_text` 和 `success`，删除旧
   `translation_error`；
3. 失败时写入 `translated_text: null`、`failed` 和
   `translation_error`；
4. 累加实际模型调用次数；
5. 原子写入一次完整的 `translated_content_list.json`。

并发任务完成顺序可以不同，但对象始终写回原数组索引，所以输出对象数量和顺序不会
改变。只有主线程执行检查点写入，因此不会发生多个线程同时替换输出文件。

### 中断

初始待处理状态在提交任务前原子写入。主线程收到 `KeyboardInterrupt`、
`SystemExit` 或其他不应转换为单段失败的 `BaseException` 时：

- 取消尚未开始的任务；
- 不把尚未由主线程接收的结果写入文件；
- 保留已完成并已写入的检查点；
- 继续向上抛出中断。

下一次执行时，已写入的 `success` 会跳过，其余对象重新提交。

### 重试与统计

每个工作线程独立执行该段的首次调用和额外重试。某段单次调用失败但仍有剩余重试时，
输出黄色警告；最终仍失败时返回 `failed`，不终止其他任务。

最终统计保持：

- text 对象总数；
- 本次实际模型调用数量，包含重试；
- 跳过的已有 success 数量；
- success 数量；
- failed 数量；
- pending 数量，正常结束必须为 0；
- 输出文件绝对路径。

## 日志设计

### 输出与颜色

新增独立的日志配置模块，使用 Python 标准库 `logging`。工作流日志写入标准错误流，
最终统计继续写入标准输出流，便于将 JSON 处理统计与运行日志分别重定向。

日志级别和前缀：

```text
\033[32m[INFO]\033[0m
\033[33m[WARN]\033[0m
\033[31m[ERROR]\033[0m
```

- 绿色 `[INFO]`：正常阶段、操作说明、完成结果和耗时；
- 黄色 `[WARN]`：跨页合并候选和准备重试的单次调用失败；
- 红色 `[ERROR]`：单段最终失败或工作流异常。

日志不打印 API Key、完整原文或完整译文。

### 阶段日志

完整 PDF 工作流增加总流程和以下阶段日志：

1. 校验 PDF；
2. 调用 MinerU 解析 PDF；
3. 解压结果并定位 content list；
4. 清洗数据；
5. 检测跨页段落；
6. 合并跨页段落并写出规范化文件；
7. 翻译 text 对象；
8. 渲染 Markdown。

仅翻译工作流增加：

1. 校验 `normalized_content_list.json`；
2. 校验或创建断点文件；
3. 并发翻译；
4. 汇总结果。

每个阶段至少打印：

```text
[INFO] 开始清洗数据：移除 header、footer、page_number 和空 text
[INFO] 清洗数据完成：输入 138 项，过滤 4 项，保留 134 项，耗时 0.01 秒
```

如果阶段抛出异常，打印阶段名称、错误和已用时间，然后由原有错误处理继续向上返回。

### 跨页日志

检测完成后，对每个候选输出黄色日志。日志同时包含从 1 开始的 text 段序号和数组
位置，避免非 text 对象导致“段号”含义不清：

```text
[WARN] 检测到第 18 段（数组第 23 项）和第 19 段（数组第 24 项）
       被分页分裂，将合并为一个段落
```

候选报告 JSON 的索引和字段保持不变。

### 翻译日志

每次真实模型调用都打印开始日志，因此重试也有独立调用记录：

```text
[INFO] 第 12 段开始翻译：第 1/2 次调用
```

成功时只打印状态、耗时和译文字符数：

```text
[INFO] 第 12 段翻译完成：success，耗时 3.42 秒，译文 286 字符
```

单次失败且准备重试：

```text
[WARN] 第 12 段第 1 次调用失败：请求超时，将重试
```

最终失败：

```text
[ERROR] 第 12 段翻译完成：failed，耗时 240.13 秒，错误：请求超时
```

并发执行时不同段的日志允许交错，段序号用于识别对应任务。

## 错误处理

- 非法 timeout、retry 或 concurrency 配置在创建线程池前报错；
- 单段普通异常在工作线程内按重试规则处理，最终转为 `failed`；
- 检查点校验不一致时不提交任何模型请求，也不覆盖旧输出；
- 原子写入失败时主线程停止接收并写入新结果，取消未开始任务，并报告红色错误；
- 一个单段最终失败不影响其他已提交段落；
- 正常结束后不允许存在 `pending`。

## 测试

所有模型调用继续使用 fake translator 或 `httpx.MockTransport`，不访问真实接口。

新增或调整测试覆盖：

- timeout 默认值为 120；
- concurrency 默认值为 5；
- concurrency 环境变量合法解析和非法值拒绝；
- 请求体不包含任何深度思考字段；
- 同时运行的翻译任务不超过配置值，并且确实能观察到多个任务同时在途；
- 并发完成顺序不同但输出数组顺序不变；
- 只有主线程执行检查点写入，每完成一段写一次；
- 并发下 success 跳过、pending 和 failed 继续处理；
- 每段重试与模型调用统计正确；
- 单段最终失败不取消其他段；
- 中断后已有检查点可以继续；
- 彩色 INFO、WARN、ERROR 前缀；
- 阶段开始、结束、耗时、清洗统计、跨页候选和翻译结果日志；
- 日志不包含完整原文、完整译文或 API Key；
- PDF 完整流程和 `--translate-only` 流程均保持可用。

