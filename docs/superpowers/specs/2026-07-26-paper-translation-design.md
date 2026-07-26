# 正式论文翻译与断点续跑设计

## 目标

将现有 sample 翻译升级为正式全量翻译。输入仍为
`normalized_content_list.json`，输出仍为同目录下的
`translated_content_list.json`。

所有 `type` 恰好为 `"text"` 的对象都以单个对象为单位依次调用翻译模型。其他类型
暂不翻译，并按当前规范化文件原样保留。正常运行结束后，每个 text 对象都必须是
`success` 或 `failed`，不得保留 `pending`。

正式模式增加可靠的文件级断点续跑、逐对象原子 checkpoint、请求超时配置、失败重试
和完整统计。当前不增加并发、批量请求、Agent、长文本拆分、表格翻译或图注翻译。

## 命令入口

原有 PDF 完整流程保持兼容：

```bash
python -m pdf_trans /path/to/document.pdf
```

该命令继续执行 MinerU 解析、清洗、跨页规范化、翻译和 Markdown 渲染。

新增翻译专用入口：

```bash
python -m pdf_trans --translate-only \
  /path/to/normalized_content_list.json
```

`--translate-only` 只运行翻译阶段，不调用 MinerU，不重新清洗、规范化或渲染。它是中途
退出后恢复同一翻译任务的推荐入口。

CLI 必须要求 PDF 路径与 `--translate-only` 二选一。两者同时提供或两者都未提供时，
参数解析失败。`--translate-only` 的输入必须存在、是普通文件，且文件名为
`normalized_content_list.json`。

## 组件职责

`translation.py` 负责：

- 读取和校验规范化内容；
- 初始化新的翻译结果；
- 校验并恢复已有翻译结果；
- 逐个处理全部 text 对象；
- 每个对象完成后的原子 checkpoint；
- 重试控制和运行统计。

`translation_client.py` 负责：

- 从环境变量读取接口、模型、超时和最大重试次数；
- 校验配置值；
- 单个 text 的 OpenAI 兼容 HTTP 请求；
- 解析并校验模型响应。

`workflow.py` 负责：

- 在完整 PDF 流程中调用同一个翻译文件处理函数；
- 提供翻译专用工作流入口；
- 将翻译统计传给 CLI。

两个入口共享完全相同的翻译、恢复和原子写入逻辑。

## 翻译提示词

每次请求只发送一个 text 对象的原始 `text` 值，不允许将整篇论文或多个 text 合并为
一次请求。

系统提示词继续要求模型：

- 将英文化工学术论文准确翻译为简体中文；
- 不总结、不改写、不补充；
- 保留引用编号，例如 `[38]`、`[39–41]`；
- 保留 LaTeX 公式及 `$...$` 内容；
- 保留数值、单位和百分数；
- 保留设备编号，例如 `C-1`、`E-1`、`D-1` 和 `SS1`；
- 术语翻译符合化工论文表达；
- 只返回译文，不返回解释或前缀。

响应中的 `choices[0].message.content` 必须是非空字符串，才算一次请求成功。

## 首次运行

当 `translated_content_list.json` 不存在时：

1. 读取并校验 `normalized_content_list.json` 的顶层是对象数组；
2. 深拷贝全部对象；
3. 对每个 text 对象设置 `translation_status: "pending"`，并移除可能存在的
   `translated_text` 和 `translation_error`；
4. 非 text 对象保持规范化输入中的原始字段和值；
5. 在发起第一个模型请求前，原子写出初始化结果；
6. 按数组顺序逐个处理全部 text 对象。

初始化 checkpoint 保证进程即使很早退出，下一次仍能识别未处理对象。

## 已有结果校验与恢复

当同目录下的 `translated_content_list.json` 已存在时，先完整读取并校验，不得直接
信任或覆盖旧结果。

校验规则：

- 顶层必须是对象数组；
- 对象数量必须与 `normalized_content_list.json` 相同；
- 按数组索引逐项比较，确保顺序一致；
- 每个对象的 `type` 字段是否存在及其值必须一致；
- 每个对象的 `text` 字段是否存在及其值必须一致；
- 每个 text 对象的 `translation_status` 必须是
  `success`、`failed` 或 `pending`；
- `success` 必须包含非空字符串 `translated_text`；
- `failed` 必须包含 `translated_text: null` 和非空
  `translation_error`。

任一校验失败时抛出内容错误，不调用模型，也不修改旧输出文件。

校验通过后，从当前 `normalized_content_list.json` 重新深拷贝工作数组，只从旧结果
继承 text 对象的 `translation_status`、`translated_text` 和
`translation_error`。这样可保证非 text 对象以及其他源字段始终来自当前规范化输入。

恢复规则：

- `success`：移除旧的 `translation_error`，保留有效译文并跳过模型调用；
- `pending`：移除旧的 `translated_text` 和 `translation_error`，进入翻译；
- `failed`：保留旧错误用于 checkpoint，然后重新进入翻译。

校验和状态规范化全部完成后，先原子写入一次准备好的恢复结果，再开始新的模型调用。

## 单对象翻译与重试

每个需要翻译的 text 对象独立处理：

1. 使用原始 `text` 发起模型请求；
2. 成功时：
   - 保留原始 `text`；
   - 写入 `translated_text`；
   - 设置 `translation_status: "success"`；
   - 删除旧的 `translation_error`；
3. 请求抛出异常时，根据 `TRANSLATION_MAX_RETRIES` 立即重试；
4. 所有尝试仍失败时：
   - 设置 `translated_text: null`；
   - 设置 `translation_status: "failed"`；
   - 将最后一次异常字符串写入 `translation_error`；
5. 成功或最终失败后，立即原子 checkpoint；
6. 继续处理下一个 text，不因单对象失败终止全文。

`TRANSLATION_MAX_RETRIES=1` 表示首次请求失败后额外重试 1 次，因此一个对象最多产生
2 次模型调用。当前不增加退避等待。

仅捕获普通 `Exception` 作为对象级翻译失败。`KeyboardInterrupt`、`SystemExit`、
配置错误、恢复校验错误和文件写入错误仍可终止进程；下次通过翻译专用入口继续。

## 原子写入

所有 checkpoint 使用同一个原子写入函数：

1. 在目标文件同目录创建唯一临时文件；
2. 以 UTF-8 和缩进 JSON 写入完整数组；
3. flush 并对临时文件执行 `fsync`；
4. 使用 `os.replace` 原子替换 `translated_content_list.json`；
5. 写入失败时尽力清理临时文件，并抛出内容错误。

旧输出在新文件完整落盘前保持可用。进程中断最多只会丢失当前尚未完成并 checkpoint
的对象；此前已完成对象不会丢失。

## 环境配置

继续要求以下非空变量：

- `TRANSLATION_BASE_URL`
- `TRANSLATION_API_KEY`
- `TRANSLATION_MODEL`

新增：

- `TRANSLATION_TIMEOUT_SECONDS`
  - 未设置时默认 `60`；
  - 必须解析为有限的正数；
  - 用作 `httpx.Client` 的请求超时；
- `TRANSLATION_MAX_RETRIES`
  - 未设置时默认 `1`；
  - 必须解析为非负整数；
  - 表示首次失败后的额外重试次数。

所有环境配置在创建正式翻译客户端时读取。配置值无效属于致命配置错误，必须在发起
模型请求前终止。

测试中仍允许注入 fake translator，并显式传入重试次数，从而不依赖真实环境变量。

## 运行统计

翻译结果统计包含：

- `text_count`：输入中的 text 对象总数；
- `model_call_count`：本次真实调用 translator/模型的次数，包含重试；
- `skipped_success_count`：本次因已有有效 success 而跳过的对象数量；
- `success_count`：最终输出中的 success 数量；
- `failed_count`：最终输出中的 failed 数量；
- `pending_count`：最终输出中的 pending 数量；
- 输出文件绝对路径。

如果同一对象首次失败、第二次成功，则 `model_call_count` 增加 2，而
`success_count` 增加 1。

正常处理完全部对象后重新从工作数组汇总最终状态，并验证 `pending_count == 0`。模型
失败不会让命令返回工作流错误；它会正常输出 failed 数量，用户可再次运行
`--translate-only` 重试。

CLI 最终打印：

```text
text 对象总数：<text_count>
本次模型调用数量：<model_call_count>
跳过的已成功数量：<skipped_success_count>
翻译成功数量：<success_count>
翻译失败数量：<failed_count>
待翻译数量：<pending_count>
翻译文件：<absolute output path>
```

## 测试

测试必须先于实现编写，且不得访问真实模型接口。

覆盖范围：

- 首次运行翻译全部 text，非 text 原样保留；
- 每次只把一个 text 传给 translator；
- 不再存在 sample 数量限制；
- 正常结束后 pending 为 0；
- 单对象失败后继续处理后续对象；
- success 删除旧 `translation_error`；
- 默认重试 1 次、配置零重试和多次重试；
- 模型调用统计包含重试；
- 已有 success 跳过，failed 和 pending 重新处理；
- 旧结果数量、顺序、`type` 或 `text` 不一致时拒绝恢复且不修改旧文件；
- 无效状态和无效 success/failed 字段拒绝恢复；
- 每完成一个对象执行一次原子 checkpoint；
- 中途异常后再次运行时只处理未成功对象；
- 临时文件、flush、`fsync` 和 `os.replace` 的原子写入行为；
- 超时和重试环境变量的默认值、有效值与非法值；
- 完整 PDF 命令保持兼容；
- `--translate-only` 只执行翻译阶段；
- CLI 输出完整正式统计。

## 不在范围内

本次不增加：

- 并发；
- 批量模型请求；
- Agent；
- 长文本拆分；
- 表格翻译；
- 图注翻译；
- 数据库或额外 checkpoint 文件；
- 自动退避或任务队列。
