# MinerU 纯规则公式修复设计

## 目标

为 `formula_audit.json` 中已经明确识别出的高置信度 MinerU 公式错误增加一套
纯确定性规则修复流程。修复发生在 MinerU 解析之后，保留每条原始公式，使用现有
KaTeX 校验器重新校验修复候选，并确保只有校验通过的 `normalized_formula` 能进入
最终 Markdown。

本次改造不调用视觉模型或文本模型，不修改翻译提示词、翻译模型选择、公式保护机制
或表格翻译行为。

## 范围

第一版只修复以下白名单：

1. MinerU 将字母 C 识别为 `\complement`；
2. 带 `\neqq` 的成对烯烃碳数表达；
3. 固定过程控制字段；
4. 带编号的 FIC 和 HIC 仪表位号；
5. 立方米每小时工程单位。

明确不做通用数学公式规范化，不全局替换 `\complement`、`\neq` 或 `\neqq`，
不处理 `\bar{\Pi}`、异常 `\mathfrak`、复杂上下标重建等无法由确定性规则确认的
错误。

完整 PDF 工作流和 Web 断点续跑工作流属于本次范围。现有独立
`--translate-only` 入口保持不变，因为该入口无法保证存在对应的 MinerU 原始文件
和公式审计报告。

## 总体架构

采用“审计阶段作出修复决定，渲染阶段消费决定”的架构：

```text
MinerU ZIP 解压
  -> 保持不变的原始 *_content_list.json
  -> 扫描并校验 raw_formula
  -> 对候选公式连续应用确定性白名单规则
  -> 使用现有 KaTeX 校验器复检修复候选
  -> 写出 formula_audit.json schema v2
  -> 现有清洗、跨页规范化和翻译流程
  -> Markdown 渲染器应用 accepted 修复
  -> rendered.md
```

MinerU 原始文件和现有中间 content list 文件继续保存原公式。审计报告是修复决定的
唯一权威来源，渲染器只消费 `accepted` 记录，因此被拒绝的候选不会改变最终输出。

该架构同时兼容已有 Web 断点。旧任务可以从原始 MinerU content list 重新生成审计
报告，并在渲染时应用修复，不需要覆盖旧的 normalized 或 translated 断点。

### 未采用的方案

方案二是在清洗前生成修复后的 content list。它对新任务比较直接，但会导致旧任务的
normalized、translated 断点与输入身份不一致。为了继续执行，需要修改或丢弃旧翻译
断点，可能重复调用翻译模型，超出本次范围。

方案三是直接覆盖 MinerU 原文件或 `normalized_content_list.json`。该方案会破坏
原公式留存，也不利于审计比较和安全回退，因此不采用。

## 规则引擎

规则引擎每次接收一条已经移除外层分隔符的 KaTeX 公式，返回：

- 修复后的 KaTeX 公式；
- 按应用顺序排列的规则标识元组；
- 是否至少有一条规则实际改变了公式。

规则按固定顺序执行，后一条规则接收前一条规则的输出。同一公式允许连续命中多条
规则。同一个规则标识只记录一次，顺序以首次实际应用为准。对已经规范化的公式再次
运行规则引擎必须保持不变。

“兼容空格”只允许 MinerU 已知会插入空格的位置出现 `\s*` 或 `\s+`，例如 LaTeX
命令、花括号、下标符号和被拆开的字母之间。不能因此放宽命令边界，也不能允许任意
内容插入匹配结构。

### `mineru_complement_subscript_c`

只有当 `\complement` 后面紧跟一个带花括号的十进制数字下标时才匹配，中间可以有
空格：

```latex
\complement _ { 4 }
```

规范化为：

```latex
\mathrm{C}_{4}
```

公式其余部分保持原样。因此：

```latex
${ \complement _ { 4 } } ^ { = }$
```

会变为分隔符和外围结构等价的：

```latex
${ \mathrm{C}_{4} } ^ { = }$
```

同一公式可以多次命中，例如：

```latex
\complement _ { 2 } . \complement _ { 7 }
```

没有数字下标限定的普通数学 `\complement` 不修改。

### `mineru_degree_complement_c`

只有当 `\complement` 前面紧邻内容恰好为 `\circ` 的花括号角度上标时才匹配，
中间可以有空格：

```latex
205 ^ { \circ } \complement
```

只规范 `\complement` 原子：

```latex
205 ^ { \circ } \mathrm{C}
```

### `alkene_carbon_count_equality_pair`

该规则匹配完整的左右成对表达式，不单独匹配其中任意一个比较命令。左右两侧都必须是
带十进制数字下标的 C 原子，中间必须由 `/` 分隔，左侧必须以 `\neqq` 结束。

实际 MinerU 原始结构为：

```latex
\mathsf { C } _ { 7 } \neqq / \mathsf { C } _ { 8 } \neq
```

右侧比较命令允许是 `\neq` 或 `\neqq`，但在完整成对结构以外，两种命令均不修改。
完整结构规范化为：

```latex
\mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}
```

字面量 `*{7}` 不属于该规则。原需求中的星号只是 Markdown 显示造成的结果，实际
LaTeX 以 `formula_audit.json` 中的单个下划线下标为准。

### `fixed_process_control_field`

只识别 `MeterMax`、`SetPoint`、`Output`、`Equation` 四个完整固定标识符。它们
可以是已知的拆字母形式，也可以位于一个花括号平衡的 `\text { ... }` 组内。

`MeterMax` 支持以下来源形式：

```latex
\text {M e t e r M a \max}
\text {M e t e r M a ^ {\max}}
\text {M e t e r M a ^ {m a x}}
M e t e r M a x
```

另外三个字段只识别各自精确的拆字母序列：

```latex
S e t P o i n t
O u t p u t
E q u a t i o n
```

每个完整字段连同存在的 `\text` 包装统一规范为：

```latex
\mathrm{MeterMax}
\mathrm{SetPoint}
\mathrm{Output}
\mathrm{Equation}
```

匹配 `\text` 时必须确认花括号平衡。该规则不通用替换 `\max`，也不规范其他带空格
的普通文字。

### `numbered_instrument_tag`

只匹配后面明确带有非空十进制数字序列的 `F I C` 或 `H I C`。字母之间和数字之间
可以有空格。匹配必须具有字母和数字边界，不能截取更长字母数字标识符的一部分。

示例：

```latex
F I C 0 0 1 2 -> \mathrm{FIC0012}
H I C 0 0 2 1 -> \mathrm{HIC0021}
```

以下无编号内容保持不变：

```latex
F I C
Range of F I C
\mathrm { F I C }
```

### `cubic_metre_per_hour`

只匹配以下 `\frac` 结构：分子花括号中的有效内容恰好为 `m`、空格和 `3`，分母
花括号中的有效内容恰好为 `h`。允许 MinerU 在命令和花括号周围插入空格：

```latex
\frac { m 3 } { h }
```

规范化为：

```latex
\frac{\mathrm{m}^{3}}{\mathrm{h}}
```

已经规范化的单位和其他分数不修改。

## 分隔符与结构化替换

扫描器继续保存包含外层分隔符的精确 `raw_formula`，同时向规则和校验流程提供已移除
分隔符的 `katex_formula`。重建 `normalized_formula` 时保留原分隔符风格：

- MinerU 独立公式保留原有 `$$...$$` 或 `\[...\]` 以及外围空格；
- 行内公式保留 `$...$` 或 `\(...\)`；
- 原本没有分隔符的 equation 对象继续不带分隔符。

accepted 修复不能通过全局字符串替换写入结果。

渲染时，渲染器先按照现有规则选择原本应输出的字段：

- text 翻译成功时使用 `translated_text`，否则使用 `text`；
- table 翻译成功时使用 `translated_table_body`，否则使用 `table_body`；
- equation 使用 `text`。

随后只在选定字段中按现有公式分隔符和 HTML 可见文本节点规则重新扫描公式，并通过
精确的 `(raw_formula, is_block)` 身份查找 accepted 修复。表格替换只允许发生在
可见 `td`、`th` 文本节点中的完整公式跨度内；HTML 属性、隐藏元素和非公式文字均
保持不变。

审计报告读取器拒绝同一个精确身份出现冲突的 accepted 修复。翻译阶段的公式保护机制
应当恢复完全一致的原公式；如果选中的翻译字段中不存在被审计的 raw 公式，渲染器
保持该内容不变，不做模糊匹配。

## KaTeX 复检

原公式继续使用现有第一次 KaTeX 批量校验。

每条公式完成全部适用规则后，只要至少发生一次变化，就把最终候选交给现有
`KaTeXFormulaValidator` 进行第二次批量校验。校验继续使用当前随项目提供的
KaTeX 0.18.1、现有配置、对应的 display mode 和会抛出语法错误的严格校验过程。

每条命中公式的结果为：

- 修复候选有效：`normalization_status = "accepted"`；
- 修复候选无效：`normalization_status = "rejected"`，KaTeX 错误写入
  `validation_error`。

单条 rejected 公式继续参与文档工作流，最终使用 raw 公式渲染。它不能导致整段、
整表、整份文档或整个校验批次失败。

Node 不可用、KaTeX 子进程失败、批量响应结构错误等校验基础设施故障继续沿用现有的
工作流失败行为，因为这种情况下无法产生可信审计结果。

## formula_audit.json schema v2

`formula_audit.json` 升级为 `schema_version: 2`。

每条公式记录保留现有身份、位置、原始语法和可疑规则字段，并使用以下字段替代第一版
预留的 normalization 字段：

```json
{
  "raw_formula": "$...$",
  "syntax_status": "valid",
  "raw_validation_error": null,
  "normalized_formula": "$...$",
  "normalization_rules": [
    "numbered_instrument_tag",
    "fixed_process_control_field",
    "cubic_metre_per_hour"
  ],
  "normalization_status": "accepted",
  "validation_error": null
}
```

字段语义如下：

- `raw_formula`：MinerU 原始公式，永不覆盖；
- `raw_validation_error`：原公式的 KaTeX 错误，无错误时为 `null`；
- `normalized_formula`：包含原分隔符风格的完整修复候选；未命中规则时为 `null`；
- `normalization_rules`：按实际应用顺序排列的规则标识；未命中时为空数组；
- `normalization_status`：`not_applicable`、`accepted` 或 `rejected`；
- `validation_error`：rejected 候选的 KaTeX 错误，其他情况为 `null`。

v1 的单数 `normalization_rule` 和 `confidence` 字段删除。所有修复均来自确定性
白名单，不记录概率置信度。

汇总在保留现有语法和可疑数量的基础上增加：

```json
{
  "scanned_formula_count": 100,
  "matched_formula_count": 8,
  "normalization_accepted_count": 7,
  "normalization_rejected_count": 1
}
```

`scanned_formula_count` 与现有 `total_formulas` 相等。v2 同时保留两个名称，使现有
审计使用方可以继续读取 `total_formulas`，新修复日志则使用需求中指定的“扫描公式
数”表述。

`issues` 数组继续包含原公式语法无效或可疑的记录，并额外包含所有 accepted 和
rejected 规范化记录。这样，即使某条高置信度公式原本能通过 KaTeX 且旧可疑规则
没有覆盖，它的修复明细仍不会丢失。

## 统计与明细日志

每次公式修复审计完成后输出一条汇总日志，包含：

- 扫描公式数；
- 命中公式数；
- accepted 数；
- rejected 数。

随后为每条命中公式输出一条明细日志，包含：

- `formula_id`；
- `page_idx`；
- 修复前 raw 公式；
- 修复后的候选公式；
- 按顺序排列的命中规则；
- accepted 或 rejected 状态；
- rejected 时的校验错误。

审计报告同时作为机器可读的完整明细日志。因为需求明确要求输出修复前后公式，日志中
允许记录公式原文。

## schema 迁移与断点续跑

v2 读取器严格校验记录字段、汇总数量、状态组合、错误字段语义以及 accepted 替换的
一致性。

Web 断点续跑按以下方式处理：

1. 有效 v2 报告：直接复用；
2. 有效旧 v1 报告：定位唯一的 MinerU 原始 content list，并原子重建为 v2；
3. 缺少报告：按当前流程生成 v2；
4. 损坏的 v2 报告：任务失败，不静默覆盖可能已损坏的数据。

完整工作流和断点续跑工作流都会把 v2 报告交给渲染器。旧任务的 normalized 和
translated 断点不覆盖；accepted 修复只在重新生成 `rendered.md` 时应用。

## 错误隔离

规则引擎自身发生异常属于审计基础设施错误。此时审计失败，并且不能写出部分报告。

修复候选的 KaTeX 语法失败属于单公式 rejected，不阻断后续流程。

渲染器找不到 accepted 记录对应的精确公式跨度时安全跳过。禁止退化为子串、模糊或
全局替换。

审计报告继续原子写出。v1 重建 v2 时不能留下部分写入的 `formula_audit.json`。

## 测试设计

规则测试至少覆盖：

- `\complement_{4}^{=}`；
- `205^{\circ}\complement`；
- `\complement_{2} . \complement_{7}`；
- 实际 raw 烯烃结构
  `\mathsf { C } _ {7}\neqq / \mathsf { C } _ {8}\neq`；
- 三种错误 MeterMax 和拆字母 MeterMax；
- `SetPoint`、`Output`、`Equation`；
- 带编号的 `F I C` 和 `H I C`；
- 无编号 `F I C` 保持不变；
- `\frac{m 3}{h}` 及空格变体；
- 真正的数学 `\neq` 保持不变；
- 无上下文限定的数学 `\complement` 保持不变；
- 同一公式连续命中多条规则和同一规则多次命中；
- 幂等性。

审计测试覆盖：

- 一次 raw 批量校验和一次 normalized 候选批量校验；
- accepted 与 rejected 的字段语义；
- rejected 最终保留 raw；
- schema v2 结构校验和汇总一致性；
- 修复前后明细日志；
- v1 迁移识别；
- MinerU 原文件字节不变。

渲染器和工作流测试覆盖：

- accepted 的行内公式、独立公式和表格单元格公式进入 Markdown；
- rejected 公式继续渲染 raw；
- 翻译成功字段和失败回退字段；
- HTML 属性和非公式文本不被替换；
- 仿照第 36 页控制公式，在一条公式中同时修复仪表位号、全部固定字段和 m³/h；
- 完整 PDF 工作流把 v2 审计结果传递给 Markdown 渲染；
- Web 断点直接复用 v2；
- Web 断点把 v1 重建为 v2，并在不改变 normalized、translated 断点的情况下把
  accepted 公式写入 Markdown。

先运行公式修复相关测试，再运行完整 `pytest`。集成测试必须使用生产
KaTeX 校验器，不能只通过 fake validator 证明 accepted 行为。

## 文档更新

更新 README 的公式审计和 Markdown 渲染章节，说明：

- schema v2 会执行确定性公式修复；
- raw 公式始终保留；
- 只有 accepted 公式进入最终渲染；
- rejected 候选回退到 raw；
- 公式修复不使用任何模型；
- Web 断点续跑会重建旧 v1 审计报告。
