# 最小化论文翻译设计

## 目标

在现有 PDF 处理流程中增加最小化的 sample 翻译阶段。该阶段读取生成的
`normalized_content_list.json`，最多翻译前 3 个符合条件的 text 对象，并在同一目录
写出 `translated_content_list.json`。

同时，将 Python 包和模块入口从 `mineru_cleaner` 完整重命名为 `pdf_trans`。支持的命令为：

```bash
python -m pdf_trans /path/to/document.pdf
```

不再保留旧的 `python -m mineru_cleaner` 入口。

## 架构

现有的 MinerU 解析、清洗、跨页规范化和 Markdown 渲染阶段保持不变。新增一个聚焦于
翻译的模块，放在 `src/pdf_trans/` 下，并由工作流在写出
`normalized_content_list.json` 后立即调用。

翻译模块分离以下职责：

- OpenAI 兼容接口的 HTTP 通信；
- content list 转换和 sample 选择；
- JSON 文件读写；
- 返回给工作流和 CLI 的结果统计。

继续使用现有的 `httpx` 依赖，直接调用 OpenAI 兼容接口的
`/chat/completions` 端点。不新增 OpenAI SDK 或其他运行时依赖。

## 配置

客户端从进程环境变量读取以下配置：

- `TRANSLATION_BASE_URL`
- `TRANSLATION_API_KEY`
- `TRANSLATION_MODEL`

三个变量都必须是非空值。缺少配置属于工作流级配置错误：命令报错退出，不将对象
宣称为翻译失败。环境变量在客户端创建时读取，而不是在模块导入时读取。

客户端会去掉 base URL 末尾的斜杠，然后追加 `/chat/completions`。因此可以支持如下常见配置：

`https://api.example.com/v1`

## 翻译提示词

每个符合条件对象的原始 `text` 值会不作修改地发送。系统提示词要求模型：

- 将英文化工学术论文准确翻译为简体中文；
- 不总结、不改写、不补充；
- 保留引用编号，例如 `[38]`、`[39–41]`；
- 保留 LaTeX 公式及 `$...$` 内容；
- 保留数值、单位和百分数；
- 保留设备编号，例如 `C-1`、`E-1`、`D-1` 和 `SS1`；
- 术语翻译符合化工论文表达；
- 只返回译文，不返回解释或前缀。

原文会作为单独的 user 消息发送。响应中的
`choices[0].message.content` 必须是非空字符串，才算翻译成功。

## Sample 选择和数据保留

输入必须是对象数组形式的 JSON。对象按原始顺序处理，并构建新的输出数组，因此不会
修改输入对象本身。

对于 `type` 恰好为 `"text"` 的对象：

1. 尝试翻译前 3 个这样的对象，不受前面调用成功或失败的影响；
2. 成功时保留所有原始字段，并增加：
   - `translated_text`：模型返回的译文；
   - `translation_status: "success"`；
3. 失败时保留所有原始字段，并增加：
   - `translated_text: null`；
   - `translation_status: "failed"`；
   - `translation_error`：简短错误信息；
4. 后续 text 对象不发送给模型。保留所有原始字段，并增加
   `translation_status: "pending"`。pending 对象不增加
   `translated_text` 或 `translation_error`。

`type` 不是恰好为 `"text"` 的对象直接复制，不增加任何翻译字段。

输出数组的顺序和对象数量始终与输入相同。如果输入对象已经包含翻译相关字段，阶段
只覆盖其最终状态所规定的字段，其他字段保持不变。

## 错误处理

配置错误和顶层 JSON 结构无效属于致命错误，并使用项目既有的应用错误层级。

翻译开始后，每个选中对象的模型调用彼此隔离。网络错误、非成功 HTTP 响应、响应体
格式错误和模型返回空内容只会让当前对象标记为失败；程序随后继续处理下一个选中的
text 对象，并仍然写出输出文件。

文件读写错误仍属于工作流级错误。

## 工作流结果和 CLI 输出

工作流结果新增：

- 翻译输出路径；
- 尝试翻译数量；
- 成功数量；
- 失败数量；
- pending 数量。

尝试翻译数量定义为成功数量加失败数量。该数量最多为 3；当规范化内容中的 text
对象少于 3 个时，也可能小于 3。

现有输出之后，CLI 打印：

```text
实际翻译对象数量：<attempted>
翻译成功数量：<success>
翻译失败数量：<failed>
待翻译数量：<pending>
翻译文件：<absolute path to translated_content_list.json>
```

## 测试

测试先于实现编写。模型通信始终使用 mock 或注入的 fake translator 替代；测试套件
绝不调用真实翻译服务。

测试覆盖以下内容：

- 翻译成功并保留所有源字段；
- 一个选中对象调用失败后，后续选中对象仍继续处理；
- 确实只尝试前 3 个 text 对象；
- 后续 text 对象标记为 pending；
- 非 text 对象经 JSON 解析后仍保持字段和值等价；
- 数组长度和顺序不变；
- 符合条件的 text 对象少于 3 个；
- 环境配置读取和缺少变量错误；
- 使用 `httpx.MockTransport` 验证请求 URL、请求头、模型、消息和兼容响应解析；
- 翻译位于生成规范化 JSON 之后；
- 翻译文件内容和结果统计；
- 重命名后的 `pdf_trans` CLI 及其统计输出。

## 不在范围内

本次改动不增加并发、数据库、Agent、重试、重试队列、Web 接口、批量控制或完整翻译
模式。
