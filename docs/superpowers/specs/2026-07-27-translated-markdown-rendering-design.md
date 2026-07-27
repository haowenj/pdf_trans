# 译文 Markdown 渲染修复设计

## 背景

完整 PDF 工作流会把翻译结果写入 `translated_content_list.json`，但当前
Markdown 阶段仍读取 `normalized_content_list.json`。渲染器也只使用 text
对象的原始 `text` 字段，因此最终的 `rendered.md` 始终是英文原文。

## 目标

`rendered.md` 应按翻译结果数组的原顺序生成：

- 翻译成功的 text 对象输出非空 `translated_text`；
- 翻译失败的 text 对象回退输出原始 `text`，避免内容缺失；
- `ref_text`、图片、图表、表格和公式继续沿用现有渲染规则；
- 标题级别继续由 text 对象原有的 `text_level` 决定；
- 不修改 `normalized_content_list.json` 或
  `translated_content_list.json`。

## 数据流

完整工作流保持现有的解析、清洗、跨页合并和翻译顺序，只调整最后一个
阶段的输入：

```text
normalized_content_list.json
  -> 翻译
  -> translated_content_list.json
  -> Markdown 渲染
  -> rendered.md
```

工作流把 `translation_result.translated_path` 传给 Markdown 文件渲染器，
不再传入 `normalized_path`。

## 渲染规则

渲染器处理 `type == "text"` 的对象时：

1. 当 `translation_status == "success"` 且 `translated_text` 是非空字符串，
   使用 `translated_text`；
2. 其他情况使用原始 `text`；
3. 若最终选中的字符串无效或为空，则跳过该对象；
4. 使用选中的字符串套用现有标题或普通段落格式。

失败回退包括显式 `failed` 状态，也兼容没有翻译状态的旧输入，因此现有
直接渲染规范化内容的调用方式不会失效。

## 错误处理

文件读取、JSON 顶层类型校验和 Markdown 写入错误继续使用现有
`ContentListError` 行为。本次修复不改变翻译失败的判定，也不新增工作流
中断条件。

## 测试

采用测试驱动方式增加回归覆盖：

- 渲染器对成功 text 使用 `translated_text`，并保留标题格式；
- 渲染器对 failed text 使用原始 `text`；
- 没有翻译字段的 text 继续使用原始 `text`；
- 完整工作流生成的 `rendered.md` 包含 fake translator 返回的译文，证明
  工作流渲染的是翻译结果文件；
- 运行完整测试套件，确认其他内容类型、断点翻译和 CLI 输出没有回归。

## 非目标

- 不翻译 `ref_text`、图片或图表说明、表格、公式；
- 不覆盖或删除原始 `text`；
- 不改变 `--translate-only` 的行为；该模式仍只生成
  `translated_content_list.json`；
- 不修改翻译接口、并发策略或断点文件格式。
