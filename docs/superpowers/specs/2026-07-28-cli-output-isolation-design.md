# CLI 解析结果目录隔离设计

## 背景

`process_pdf()` 会把 MinerU 返回的 ZIP 直接解压到传入的 `data_dir`。MinerU
归档通常以 PDF 文件名为顶层目录，因此 CLI 使用默认全局 `data/` 目录时，两次处理
同名、不同内容的 PDF 会写入同一路径，后一次执行可能覆盖前一次解析结果。

Web 入口已经使用 `tasks/<task-uuid>/attempts/<attempt-count>/` 作为每次完整工作流
的数据目录，不存在该问题。本次修改只补齐 CLI 入口的运行级隔离，不改变 Web 目录
布局。

## 目标

- 每次 CLI 完整 PDF 工作流都使用独立且不可碰撞的输出目录。
- 同名文件的串行或并发 CLI 执行不能覆盖彼此的解析产物。
- 保持 `process_pdf(data_dir=...)` 的现有公开行为，避免影响 Web 和测试调用方。
- `--translate-only` 继续在指定规范化文件所在目录中写入翻译和 Markdown，不生成新的
  运行目录。

## 方案

CLI 在调用 `process_pdf()` 前生成标准字符串形式的 UUID，并将
`DEFAULT_DATA_DIR / "runs" / <run-uuid>` 作为显式 `data_dir` 传入。MinerU 自己的
归档布局保留在该运行目录下：

```text
data/
  runs/
    <run-uuid>/
      <mineru-archive-layout>/
        normalized_content_list.json
        translated_content_list.json
        rendered.md
```

UUID 只用于磁盘命名，不写入 PDF 内容或 MinerU 请求。CLI 仍通过现有结果对象打印实际
产物路径，因此用户可以直接定位本次运行目录。

没有采用内容哈希目录，因为相同内容的并发执行仍会共享写入位置；没有采用时间戳目录，
因为时间精度和碰撞处理会引入不必要的额外规则。

## 组件与数据流

`pdf_trans.__main__.main()` 负责创建运行 ID 和运行目录路径，并通过现有
`process_pdf(..., data_dir=...)` 参数传递隔离边界。`workflow.process_pdf()` 和
`archive.extract_zip()` 不新增随机命名职责，仍把调用方提供的目录视为完整输出根目录。

数据流为：

1. CLI 校验参数并开始完整 PDF 工作流。
2. CLI 生成 UUID，构造 `data/runs/<uuid>` 路径。
3. `process_pdf()` 将 MinerU ZIP 解压到该路径。
4. 后续清洗、规范化、翻译和 Markdown 产物继续写在归档内容目录旁。
5. CLI 打印结果对象中的最终绝对路径。

Web `TaskRunner` 继续传入任务和执行次数组成的目录，不经过 CLI 的 UUID 生成逻辑。

## 错误处理

UUID 和路径构造不执行磁盘写入，目录仍由解压阶段按现有逻辑创建。目录创建、解压或
后续文件写入失败时，继续使用现有异常处理和 CLI 非零退出状态，不新增自动删除，以免
误删可供诊断或续查的部分产物。

## 测试

- CLI 回归测试固定 UUID，并断言完整工作流收到
  `DEFAULT_DATA_DIR / "runs" / <uuid>`。
- 用两个不同 UUID 模拟两次同名 PDF 的 CLI 执行，断言传入的 `data_dir` 不同。
- 保持现有 Web `TaskRunner` 测试，确认其仍使用
  `tasks/<task-id>/attempts/<attempt-count>`。
- 运行完整测试套件，确认 CLI 输出、翻译续传和 Web 路径行为没有回归。
