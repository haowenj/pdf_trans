# Web 翻译统计与任务摘要设计

## 范围

本次改造补全翻译统计、阶段日志和 Web 任务最终摘要。正文和 HTML 表格分别统计；CLI 输出不在本次范围内。表格翻译、重试、单元格回退及任务成功判定逻辑保持不变。

## 统计模型

`TranslationStats` 保留现有正文统计字段及顺序，并在末尾追加默认值字段，避免已有 positional 构造失效：

- `table_count`：表格总数；
- `table_success_count`：`translation_status=success` 的表格数，包括部分成功表格；
- `table_failed_count`：`translation_status=failed` 的表格数；
- `table_pending_count`：`translation_status=pending` 的表格数；
- `table_partial_success_count`：成功且 `table_translation_partial=true` 的表格数；
- `table_translation_success_cell_count`：成功翻译单元格总数；
- `table_translation_fallback_cell_count`：回退 MinerU 原文单元格总数；
- `skipped_table_success_count`：断点续跑跳过的已成功表格数；
- `text_model_call_count` 和 `table_model_call_count`：正文、表格模型调用数。

`model_call_count` 是正文和表格调用数之和。最终统计从翻译结果对象汇总，已成功断点对象也纳入对应成功数和单元格数；跳过数单独记录。

## 数据流与断点

翻译准备阶段继续复用现有正文/表格恢复逻辑，仅将 `_prepare_resumed_items` 的返回值扩展为正文和表格两个跳过数。表格成功状态无论是否部分成功都计入表格成功，部分标记单独计数；现有表格元数据作为单元格汇总来源。

翻译结束时分别检查正文和表格 pending，保留当前异常行为。部分成功表格不触发异常。

## Web 日志和摘要

由统一格式化函数生成分组摘要，供以下位置复用：

- `process_translation_file` 的翻译阶段结果；
- 完整 PDF 工作流的翻译阶段结果；
- Web `TaskRunner` 的断点续传和完整工作流最终摘要。

摘要至少展示正文总数、跳过成功、成功、失败、pending；表格总数、跳过成功、成功、部分成功、失败、pending、成功单元格、回退单元格；以及正文/表格/总模型调用数。不会更改 Web 任务的 succeeded/failed 判定。

## 测试策略

在翻译统计测试中覆盖全成功、整表失败、部分成功带回退、多表单元格累加、断点跳过正文和表格、pending 表格报错及正文统计回归。工作流和 Web TaskRunner 测试验证阶段日志与最终摘要包含正文/表格分组和模型调用口径。
