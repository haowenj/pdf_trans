# 六类附属文本翻译设计

## 范围

本次只翻译以下六个外层数组字段：`table_caption`、`table_footnote`、`image_caption`、`image_footnote`、`chart_caption`、`chart_footnote`。不处理 `ref_text`、`type=equation`、图片内部文字或现有 `table_body` 翻译逻辑；不引入 VL/OCR 流程，不修改 CLI 输出。

## 数据结构

保留六个 MinerU 原始字段，并新增对应的等长译文数组。每个数组元素一个位置，不成功时译文位置为 `null`。新增统一状态字段 `auxiliary_translation`，按原字段名保存等长状态对象数组：

```json
{
  "table_caption": [
    "TABLE 1 Detailed requirements",
    "Values are reported at $25^\\circ C$"
  ],
  "translated_table_caption": ["表 1 详细要求", null],
  "auxiliary_translation": {
    "table_caption": [
      {"translation_status": "success", "translation_error": null},
      {"translation_status": "failed", "translation_error": "..."}
    ]
  }
}
```

支持的状态为 `pending`、`success`、`failed`。`success` 必须有非空字符串译文且错误为 `null`；`pending` 必须是 `null` 译文且错误为 `null`；`failed` 必须是 `null` 译文且有非空错误原因。原字段不是数组时不生成附属文本任务，保留现有渲染行为；数组中的无效元素单独标记为 failed，不影响其他元素。

## 翻译与断点

翻译准备阶段为六类字段的每个数组元素建立统一附属文本 work item，并复用正文的 `FormulaProtectionContext`、重试和并发模型调用。`$...$`、`$$...$$`、`\\(...\\)`、`\\[...\\]` 都由现有公式扫描和占位符校验处理；占位符新增、缺失、重复、顺序变化或公式变化只使该元素 failed，并回退该元素原文。

断点身份校验扩展到六个原字段的存在性和精确内容。恢复时严格校验原数组长度、译文数组长度和状态数组长度，以及每个位置的状态/译文/错误组合。已有 success 元素计入附属文本跳过数； failed 和 pending 元素重新进入翻译队列。没有附属文本状态的旧断点按全量 pending 初始化，但仍必须通过原字段身份校验。

附属文本状态不写入对象级 `translation_status`，因此单条失败不会改变表格、图片或图表对象状态，也不会使整个任务失败。翻译结束后只要不存在附属文本 pending 即可继续；附属文本 failed 数量进入统计和日志。

## 渲染

renderer 按原数组索引逐条选择：对应状态为 success 且译文非空时使用译文，否则使用该位置原文。输出顺序保持：表格为 caption → table body → footnote；图片/图表为图片 → caption → footnote。caption/footnote 的公式继续走现有 Markdown 公式替换机制，且不修改输入 JSON。

## 统计与日志

`TranslationStats` 追加以下字段并保留现有 positional 字段兼容：`auxiliary_count`、`skipped_auxiliary_success_count`、`auxiliary_success_count`、`auxiliary_failed_count`、`auxiliary_pending_count`、`auxiliary_model_call_count`。其中 success/failed/pending 按最终位置状态统计，跳过成功单独统计；`model_call_count` 增加附属文本调用数。

Web 阶段日志和任务摘要在正文、表格之后增加“附属文本翻译”分组，展示总数、跳过已有成功、成功、失败、pending 和模型调用数，并在总模型调用中明确正文/表格/附属文本三类。CLI 输出保持不变。

## 测试策略

测试覆盖六类字段翻译、数组中间元素失败与位置保持、四种公式边界和公式占位符篡改、图片/图表 caption 与 footnote、断点只跳过 success、原字段修改拒绝断点、逐条渲染回退、附属文本失败不使任务失败及统计日志。现有正文和表格单元格测试完整回归。
