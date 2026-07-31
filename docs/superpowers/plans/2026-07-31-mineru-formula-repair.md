# MinerU 纯规则公式修复实施计划

> **供执行代理使用：** 必须使用 `superpowers:subagent-driven-development`
>（推荐）或 `superpowers:executing-plans`，按任务逐项执行。所有步骤使用
> checkbox（`- [ ]`）跟踪。

**目标：** 在不调用任何模型、不覆盖 MinerU 原公式的前提下，对审计命中的确定性
公式错误进行连续规则修复、KaTeX 复检，并只把 accepted 结果写入最终 Markdown。

**架构：** `formula_rules.py` 只负责 delimiter-free KaTeX 的确定性规范化；
`formula_scanner.py` 负责保留分隔符、重建公式和按公式跨度安全替换；
`formula_audit.py` 负责 schema v2、两阶段校验和修复决定；`renderer.py` 只消费
accepted 决定。完整工作流和 Web 断点均向渲染器传入 v2 报告，v1 仅在严格验证后
从 MinerU 原文件重建。

**技术栈：** Python 3.11、pytest、正则表达式、`html.parser.HTMLParser`、现有
Node/KaTeX 0.18.1 批量校验器。

## 全局约束

- 只实现设计规格中的五类白名单规则。
- 禁止全局替换 `\complement`、`\neq` 或 `\neqq`。
- 禁止调用视觉模型、文本模型或新增模型依赖。
- 不修改翻译提示词、翻译客户端、公式保护或表格翻译逻辑。
- `raw_formula` 和 MinerU 原始 content list 永不覆盖。
- 同一公式必须按固定顺序连续应用全部适用规则。
- 每条命中公式的最终候选必须用现有 `KaTeXFormulaValidator` 复检。
- 单公式复检失败只能 rejected 并回退 raw，不能阻断段落、表格或文档。
- 只有 KaTeX/Node 基础设施故障继续阻断审计。
- 所有字符串替换必须发生在已扫描出的完整公式跨度内。

---

## 文件结构

- 修改 `src/pdf_trans/formula_rules.py`：纯规则规范化器、规则结果和值边界。
- 修改 `src/pdf_trans/formula_scanner.py`：公式内容偏移、分隔符重建、正文和 HTML
  表格可见文本节点的精确公式替换。
- 修改 `src/pdf_trans/formula_audit.py`：两阶段校验、schema v2、严格读取、v1
  迁移识别、统计和明细日志。
- 修改 `src/pdf_trans/renderer.py`：选择最终字段后应用 accepted 修复。
- 修改 `src/pdf_trans/workflow.py`：完整流程向渲染器传入审计报告。
- 修改 `src/pdf_trans/web/task_runner.py`：Web 续跑复用 v2 或重建有效 v1，并向
  渲染器传入报告。
- 修改 `README.md`：说明确定性修复、raw 保留、accepted/rejected 和迁移行为。
- 修改对应测试文件，不创建模型相关模块。

---

### 任务 1：实现确定性白名单规则引擎

**文件：**

- 修改：`src/pdf_trans/formula_rules.py:12-146`
- 修改：`tests/test_formula_rules.py:1-104`

**接口：**

- 输入：`DeterministicFormulaNormalizer.normalize(formula: str)`，其中 `formula`
  是不含外层公式分隔符的 KaTeX。
- 输出：

```python
@dataclass(frozen=True)
class NormalizationResult:
    normalized_formula: str
    normalization_rules: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.normalization_rules)
```

- 后续任务依赖：`FormulaNormalizer` 协议、
  `DeterministicFormulaNormalizer`、稳定的规则标识和幂等输出。

- [ ] **步骤 1：先写白名单规则的失败测试**

将现有 no-op normalization 测试替换为以下行为测试，并保留 suspicion 测试：

```python
from pdf_trans.formula_rules import DeterministicFormulaNormalizer


@pytest.mark.parametrize(
    ("raw", "expected", "rules"),
    [
        (
            r"{ \complement _ { 4 } } ^ { = }",
            r"{ \mathrm{C}_{4} } ^ { = }",
            ("mineru_complement_subscript_c",),
        ),
        (
            r"20 5 ^ { \circ } \complement",
            r"20 5 ^ { \circ } \mathrm{C}",
            ("mineru_degree_complement_c",),
        ),
        (
            r"\complement _ { 2 } . \complement _ { 7 }",
            r"\mathrm{C}_{2} . \mathrm{C}_{7}",
            ("mineru_complement_subscript_c",),
        ),
        (
            r"\scriptstyle \mathsf { C } _ { 7 } \neqq / "
            r"\mathsf { C } _ { 8 } \neq",
            r"\scriptstyle \mathrm{C}_{7}^{=} / "
            r"\mathrm{C}_{8}^{=}",
            ("alkene_carbon_count_equality_pair",),
        ),
        (
            r"\text {M e t e r M a \max}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"\text {M e t e r M a ^ {\max}}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"\text {M e t e r M a ^ {m a x}}",
            r"\mathrm{MeterMax}",
            ("fixed_process_control_field",),
        ),
        (
            r"F I C 0 0 1 2 + H I C 0 0 2 1",
            r"\mathrm{FIC0012} + \mathrm{HIC0021}",
            ("numbered_instrument_tag",),
        ),
        (
            r"\frac {m 3}{h}",
            r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
            ("cubic_metre_per_hour",),
        ),
    ],
)
def test_deterministic_normalizer_applies_whitelist(
    raw, expected, rules
):
    result = DeterministicFormulaNormalizer().normalize(raw)
    assert result.normalized_formula == expected
    assert result.normalization_rules == rules
    assert result.changed is True


def test_normalizer_applies_multiple_rules_in_order():
    raw = (
        r"F I C 0 0 1 2 _ {\text {S e t P o i n t}} "
        r"\frac {m 3}{h} + "
        r"H I C 0 0 2 1 _ {\text {O u t p u t}} + "
        r"\text {E q u a t i o n 1} + "
        r"\text {M e t e r M a ^ {\max}}"
    )
    result = DeterministicFormulaNormalizer().normalize(raw)
    assert result.normalized_formula == (
        r"\mathrm{FIC0012} _ {\mathrm{SetPoint}} "
        r"\frac{\mathrm{m}^{3}}{\mathrm{h}} + "
        r"\mathrm{HIC0021} _ {\mathrm{Output}} + "
        r"\mathrm{Equation\,1} + \mathrm{MeterMax}"
    )
    assert result.normalization_rules == (
        "fixed_process_control_field",
        "numbered_instrument_tag",
        "cubic_metre_per_hour",
    )


@pytest.mark.parametrize(
    "formula",
    [
        r"A \complement B",
        r"x \neq y",
        r"C_7 \neqq C_8",
        r"F I C",
        r"Range of F I C",
        r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
        r"\bar{\Pi} + \mathfrak{A}",
    ],
)
def test_normalizer_does_not_change_outside_whitelist(formula):
    result = DeterministicFormulaNormalizer().normalize(formula)
    assert result.normalized_formula == formula
    assert result.normalization_rules == ()
    assert result.changed is False


def test_normalizer_is_idempotent():
    normalizer = DeterministicFormulaNormalizer()
    first = normalizer.normalize(
        r"F I C 0 0 1 2 + \frac {m 3}{h}"
    )
    second = normalizer.normalize(first.normalized_formula)
    assert second.normalized_formula == first.normalized_formula
    assert second.normalization_rules == ()
```

- [ ] **步骤 2：运行规则测试并确认因新接口缺失而失败**

运行：

```bash
python -m pytest tests/test_formula_rules.py -q
```

预期：收集测试时因 `DeterministicFormulaNormalizer` 不存在而失败，或新断言针对
旧 no-op 返回值失败；不得是测试语法错误。

- [ ] **步骤 3：实现最小确定性规则引擎**

在 `formula_rules.py` 中保留现有 suspicion 检测，替换 normalization 结果和 no-op
实现。使用完整命令边界和成对结构：

```python
@dataclass(frozen=True)
class NormalizationResult:
    normalized_formula: str
    normalization_rules: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.normalization_rules)


class FormulaNormalizer(Protocol):
    def normalize(self, formula: str) -> NormalizationResult:
        ...


_COMPLEMENT_SUBSCRIPT_RE = re.compile(
    r"\\complement(?![A-Za-z])\s*_\s*"
    r"\{\s*(?P<number>\d+)\s*\}"
)
_DEGREE_COMPLEMENT_RE = re.compile(
    r"(?P<degree>\^\s*\{\s*\\circ(?![A-Za-z])\s*\})"
    r"\s*\\complement(?![A-Za-z])"
)
def _carbon(name: str) -> str:
    return (
        rf"(?:\\mathsf\s*\{{\s*C\s*\}}|C)\s*_\s*"
        rf"\{{\s*(?P<{name}>\d+)\s*\}}"
    )


_ALKENE_PAIR_RE = re.compile(
    _carbon("left")
    + r"\s*\\neqq(?![A-Za-z])\s*/\s*"
    + _carbon("right")
    + r"\s*\\neq(?:q)?(?![A-Za-z])"
)


def _split_letters(value: str) -> str:
    return r"\s+".join(re.escape(character) for character in value)


def _field_pattern(body: str) -> re.Pattern[str]:
    return re.compile(
        r"(?<![A-Za-z])(?:"
        r"\\text\s*\{\s*(?:" + body + r")\s*\}|"
        r"(?:" + body + r"))(?![A-Za-z])"
    )


_METER_MAX_BODY = (
    _split_letters("MeterMa")
    + r"\s+(?:x|\\max(?![A-Za-z])|"
    r"\^\s*\{\s*(?:\\max(?![A-Za-z])|"
    + _split_letters("max")
    + r")\s*\})"
)
_EQUATION_LABEL_RE = re.compile(
    r"(?<![A-Za-z])\\text\s*\{\s*"
    + _split_letters("Equation")
    + r"\s+(?P<number>\d+)\s*\}(?![A-Za-z])"
)
_FIELD_PATTERNS = (
    (_field_pattern(_METER_MAX_BODY), r"\mathrm{MeterMax}"),
    (
        _field_pattern(_split_letters("SetPoint")),
        r"\mathrm{SetPoint}",
    ),
    (
        _field_pattern(_split_letters("Output")),
        r"\mathrm{Output}",
    ),
    (
        _field_pattern(_split_letters("Equation")),
        r"\mathrm{Equation}",
    ),
)
_INSTRUMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<tag>[FH])\s+I\s+C\s+"
    r"(?P<digits>\d(?:[ \t\r\n]*\d)*)(?![A-Za-z0-9])"
)
_UNIT_RE = re.compile(
    r"\\frac(?![A-Za-z])\s*\{\s*m\s+3\s*\}"
    r"\s*\{\s*h\s*\}"
)


class DeterministicFormulaNormalizer:
    def normalize(self, formula: str) -> NormalizationResult:
        normalized = formula
        rules: list[str] = []

        normalized, count = _COMPLEMENT_SUBSCRIPT_RE.subn(
            lambda match: rf"\mathrm{{C}}_{{{match['number']}}}",
            normalized,
        )
        if count:
            rules.append("mineru_complement_subscript_c")

        normalized, count = _DEGREE_COMPLEMENT_RE.subn(
            lambda match: match["degree"] + r" \mathrm{C}",
            normalized,
        )
        if count:
            rules.append("mineru_degree_complement_c")

        normalized, count = _ALKENE_PAIR_RE.subn(
            lambda match: (
                rf"\mathrm{{C}}_{{{match['left']}}}^{{=}} / "
                rf"\mathrm{{C}}_{{{match['right']}}}^{{=}}"
            ),
            normalized,
        )
        if count:
            rules.append("alkene_carbon_count_equality_pair")

        normalized, field_count = _EQUATION_LABEL_RE.subn(
            lambda match: (
                r"\mathrm{Equation\,"
                + match["number"]
                + "}"
            ),
            normalized,
        )
        for pattern, replacement in _FIELD_PATTERNS:
            normalized, count = pattern.subn(
                lambda _match, value=replacement: value,
                normalized,
            )
            field_count += count
        if field_count:
            rules.append("fixed_process_control_field")

        normalized, count = _INSTRUMENT_RE.subn(
            lambda match: (
                r"\mathrm{"
                + match["tag"]
                + "IC"
                + re.sub(r"\s+", "", match["digits"])
                + "}"
            ),
            normalized,
        )
        if count:
            rules.append("numbered_instrument_tag")

        normalized, count = _UNIT_RE.subn(
            lambda _match: r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
            normalized,
        )
        if count:
            rules.append("cubic_metre_per_hour")

        return NormalizationResult(normalized, tuple(rules))
```

- [ ] **步骤 4：运行规则测试并修正边界**

运行：

```bash
python -m pytest tests/test_formula_rules.py -q
```

预期：全部通过，尤其是独立 `\neq`、无编号 `F I C`、普通 `\complement` 和幂等
测试。

- [ ] **步骤 5：提交规则引擎**

```bash
git add src/pdf_trans/formula_rules.py tests/test_formula_rules.py
git commit -m "feat: add deterministic MinerU formula rules"
```

---

### 任务 2：支持公式重建和结构化跨度替换

**文件：**

- 修改：`src/pdf_trans/formula_scanner.py:24-410`
- 修改：`tests/test_formula_scanner.py:1-190`

**接口：**

- 输入：扫描出的 `FormulaCandidate` 和 delimiter-free normalized KaTeX。
- 输出：

```python
def rebuild_raw_formula(
    candidate: FormulaCandidate,
    normalized_katex: str,
) -> str: ...

FormulaReplacement = Mapping[tuple[str, bool], str]

def replace_formula_spans(
    source: str,
    replacements: FormulaReplacement,
) -> str: ...

def replace_equation_formula(
    source: str,
    replacements: FormulaReplacement,
) -> str: ...

def replace_table_formula_spans(
    source: str,
    replacements: FormulaReplacement,
    *,
    field_path: str = "/render/table_body",
) -> str: ...
```

- 后续任务依赖：审计用 `rebuild_raw_formula` 写完整 `normalized_formula`；渲染器用
  三个 replace 函数，只替换完整公式跨度。

- [ ] **步骤 1：写公式重建和安全替换的失败测试**

在 `tests/test_formula_scanner.py` 追加：

```python
from pdf_trans.formula_scanner import (
    rebuild_raw_formula,
    replace_equation_formula,
    replace_formula_spans,
    replace_table_formula_spans,
)


def test_rebuilds_formula_without_changing_delimiters_or_outer_space():
    candidates = scan_content_list(
        [
            {
                "type": "equation",
                "text": "  $$\n\\complement _ {4}\n$$  ",
            },
            {
                "type": "text",
                "text": r"before \(\frac {m 3}{h}\) after",
            },
        ]
    )
    assert rebuild_raw_formula(
        candidates[0], "\n\\mathrm{C}_{4}\n"
    ) == "  $$\n\\mathrm{C}_{4}\n$$  "
    assert rebuild_raw_formula(
        candidates[1], r"\frac{\mathrm{m}^{3}}{\mathrm{h}}"
    ) == r"\(\frac{\mathrm{m}^{3}}{\mathrm{h}}\)"


def test_replaces_only_complete_delimited_formula_spans():
    replacements = {
        (r"$x \neq y$", False): r"$x = y$",
    }
    source = r"literal x \neq y; formula $x \neq y$; attr='$x \neq y$'"
    assert replace_formula_spans(source, replacements) == (
        r"literal x \neq y; formula $x = y$; attr='$x = y$'"
    )


def test_table_replacement_ignores_attributes_and_hidden_nodes():
    raw = r"$\frac {m 3}{h}$"
    normalized = r"$\frac{\mathrm{m}^{3}}{\mathrm{h}}$"
    html = (
        f'<table data-formula="{raw}"><tr><td>{raw}</td>'
        f"<td><script>{raw}</script></td></tr></table>"
    )
    assert replace_table_formula_spans(
        html, {(raw, False): normalized}
    ) == (
        f'<table data-formula="{raw}"><tr><td>{normalized}</td>'
        f"<td><script>{raw}</script></td></tr></table>"
    )


def test_equation_replacement_requires_exact_raw_and_display_mode():
    source = "$$x$$"
    assert replace_equation_formula(
        source, {(source, False): "$$y$$"}
    ) == source
    assert replace_equation_formula(
        source, {(source, True): "$$y$$"}
    ) == "$$y$$"
```

第一条普通正文测试允许 `$...$` 出现在普通字符串任意位置，因为该函数负责正文公式
分隔符，不负责 HTML；HTML 安全边界由表格函数单独验证。

- [ ] **步骤 2：运行 scanner 测试并确认新接口缺失**

```bash
python -m pytest tests/test_formula_scanner.py -q
```

预期：因四个新函数不存在而导入失败。

- [ ] **步骤 3：给公式跨度增加内容偏移并实现重建**

把 `_FormulaSpan` 扩展为：

```python
@dataclass(frozen=True)
class _FormulaSpan:
    raw_formula: str
    katex_formula: str
    is_block: bool
    start: int
    end: int
    katex_start: int
    katex_end: int
```

`_formula_spans(source)` 中的值使用相对于 `source` 的下标：

```python
_FormulaSpan(
    raw_formula=source[position:raw_end],
    katex_formula=source[position + len(opening):end],
    is_block=is_block,
    start=position,
    end=raw_end,
    katex_start=position + len(opening),
    katex_end=end,
)
```

`_equation_formula(source)` 计算 `stripped` 在原字符串中的起点，带分隔符时把
`katex_start` 和 `katex_end` 指向分隔符内部；无分隔符时使用 `0` 和
`len(source)`。

给 `FormulaCandidate` 增加相对于 `raw_formula` 的：

```python
katex_start: int
katex_end: int
```

在 `_candidate` 中赋值：

```python
katex_start=span.katex_start - span.start,
katex_end=span.katex_end - span.start,
```

并实现：

```python
def rebuild_raw_formula(
    candidate: FormulaCandidate,
    normalized_katex: str,
) -> str:
    return (
        candidate.raw_formula[:candidate.katex_start]
        + normalized_katex
        + candidate.raw_formula[candidate.katex_end:]
    )
```

- [ ] **步骤 4：实现正文、equation 和表格可见节点替换**

在 `_TableFormulaParser` 构造函数接收原始 `source`，预先计算每行起点：

```python
self._line_starts = [0]
for match in re.finditer(r"\n", source):
    self._line_starts.append(match.end())
```

`handle_data` 使用 `self.getpos()` 得到当前 data 的行列，把 `_formula_spans(data)`
中的偏移转换为相对于整个 `table_body` 的偏移。用 `dataclasses.replace` 生成新的
span：

```python
line, column = self.getpos()
data_start = self._line_starts[line - 1] + column
absolute = dataclasses.replace(
    span,
    start=data_start + span.start,
    end=data_start + span.end,
    katex_start=data_start + span.katex_start,
    katex_end=data_start + span.katex_end,
)
```

加入统一的倒序替换函数和三个公共入口：

```python
FormulaReplacement = Mapping[tuple[str, bool], str]


def _replace_spans(
    source: str,
    spans: tuple[_FormulaSpan, ...],
    replacements: FormulaReplacement,
) -> str:
    result = source
    for span in reversed(spans):
        replacement = replacements.get(
            (span.raw_formula, span.is_block)
        )
        if replacement is not None:
            result = (
                result[:span.start]
                + replacement
                + result[span.end:]
            )
    return result


def replace_formula_spans(source, replacements):
    return _replace_spans(source, _formula_spans(source), replacements)


def replace_equation_formula(source, replacements):
    span = _equation_formula(source)
    replacement = replacements.get((span.raw_formula, True))
    return source if replacement is None else replacement


def replace_table_formula_spans(
    source,
    replacements,
    *,
    field_path="/render/table_body",
):
    formulas = _table_formulas(source, field_path)
    spans = tuple(value.span for value in formulas)
    return _replace_spans(source, spans, replacements)
```

同时更新所有现有 `_FormulaSpan(...)` 和 `_TableFormulaParser(...)` 调用，使旧扫描
测试继续通过。

- [ ] **步骤 5：运行 scanner 与 audit 旧测试**

```bash
python -m pytest \
  tests/test_formula_scanner.py \
  tests/test_formula_audit.py -q
```

预期：scanner 全部通过；audit 可能只因任务 1 已移除 no-op 字段而失败。不得出现
公式扫描顺序、formula_id 或表格行列回归。

- [ ] **步骤 6：提交结构化公式替换**

```bash
git add src/pdf_trans/formula_scanner.py tests/test_formula_scanner.py
git commit -m "feat: rebuild and replace audited formula spans"
```

---

### 任务 3：升级公式审计为 schema v2 并执行 KaTeX 复检

**文件：**

- 修改：`src/pdf_trans/formula_audit.py:1-554`
- 修改：`tests/test_formula_audit.py:1-361`
- 修改：`tests/test_formula_validation.py:1-261`（只增加真实 KaTeX 修复候选用例）

**接口：**

- 输入：原始 MinerU content list、`FormulaValidator`、可注入的
  `FormulaNormalizer`。
- 输出：schema v2 `FormulaAuditReport`。
- 新异常：

```python
class LegacyFormulaAuditError(FormulaAuditError):
    """报告是结构有效、但需要重建的 schema v1。"""
```

- `FormulaAuditReport.accepted_replacements` 返回只读语义的
  `dict[tuple[str, bool], str]`。

- [ ] **步骤 1：先把审计测试改为 schema v2 预期**

使用可区分 raw 和 normalized 批次的 fake validator：

```python
class FakeValidator:
    def __init__(self, *, reject_normalized_ids=()):
        self.calls = []
        self.reject_normalized_ids = set(reject_normalized_ids)

    def validate_batch(self, formulas):
        self.calls.append(formulas)
        normalized_call = len(self.calls) == 2
        return tuple(
            ValidationResult(
                formula_id=value.formula_id,
                syntax_status=(
                    "invalid_syntax"
                    if normalized_call
                    and value.formula_id in self.reject_normalized_ids
                    else "valid"
                ),
                validation_error=(
                    "normalized parse error"
                    if normalized_call
                    and value.formula_id in self.reject_normalized_ids
                    else None
                ),
            )
            for value in formulas
        )
```

新增或改写核心断言：

```python
def test_writes_schema_v2_with_accepted_normalizations(tmp_path):
    source = _write_source(
        tmp_path,
        [
            {
                "type": "text",
                "page_idx": 7,
                "text": r"value $\complement _ {4}$",
            },
            {
                "type": "text",
                "page_idx": 8,
                "text": r"real $x \neq y$",
            },
        ],
    )
    validator = FakeValidator()
    report = audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=validator,
    )
    assert len(validator.calls) == 2
    assert len(validator.calls[0]) == 2
    assert len(validator.calls[1]) == 1
    repaired = report.payload["formulas"][0]
    assert repaired["raw_formula"] == r"$\complement _ {4}$"
    assert repaired["normalized_formula"] == r"$\mathrm{C}_{4}$"
    assert repaired["normalization_rules"] == [
        "mineru_complement_subscript_c"
    ]
    assert repaired["normalization_status"] == "accepted"
    assert repaired["raw_validation_error"] is None
    assert repaired["validation_error"] is None
    untouched = report.payload["formulas"][1]
    assert untouched["normalization_status"] == "not_applicable"
    assert untouched["normalized_formula"] is None
    assert untouched["normalization_rules"] == []


def test_rejected_candidate_retains_normalized_detail_but_no_replacement(
    tmp_path,
):
    source = _write_source(
        tmp_path,
        [{"type": "text", "page_idx": 3, "text": r"$\frac{m 3}{h}$"}],
    )
    formula_id = scan_content_list(json.loads(source.read_text()))[0].formula_id
    report = audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=FakeValidator(reject_normalized_ids=(formula_id,)),
    )
    record = report.payload["formulas"][0]
    assert record["normalization_status"] == "rejected"
    assert record["validation_error"] == "normalized parse error"
    assert report.accepted_replacements == {}


def test_schema_v2_summary_and_issue_pages_include_all_hits(tmp_path):
    # 一条 syntactically valid 但命中确定性规则的公式也必须进入 issues。
    source = _write_source(
        tmp_path,
        [{"type": "text", "page_idx": 11, "text": r"$F I C 0 0 1 2$"}],
    )
    report = audit_content_list_file(
        source,
        tmp_path / "formula_audit.json",
        validator=FakeValidator(),
    )
    assert report.payload["summary"] == {
        "total_formulas": 1,
        "scanned_formula_count": 1,
        "valid_count": 1,
        "invalid_syntax_count": 0,
        "suspicious_count": 1,
        "issue_formula_count": 1,
        "matched_formula_count": 1,
        "normalization_accepted_count": 1,
        "normalization_rejected_count": 0,
    }
    assert report.problem_page_indices == (11,)
    assert len(report.payload["issues"]) == 1
```

把旧的“normalizer 尝试修改必须报错”测试删除，因为 v2 的目标就是规范化。空内容
测试仍断言 validator 完全不被调用。

- [ ] **步骤 2：运行审计测试并确认 schema/接口失败**

```bash
python -m pytest tests/test_formula_audit.py -q
```

预期：因旧 schema、旧 `NormalizationResult` 字段和禁止修改逻辑失败。

- [ ] **步骤 3：实现两阶段校验和 v2 记录**

把校验结果检查提取为接收显式输入的函数：

```python
def _checked_validation_results(
    inputs: tuple[ValidationInput, ...],
    validator: FormulaValidator,
) -> tuple[ValidationResult, ...]:
    if not inputs:
        return ()
    results = validator.validate_batch(inputs)
    if len(results) != len(inputs):
        raise FormulaAuditError("公式校验结果数量不一致")
    for expected, result in zip(inputs, results):
        if (
            not isinstance(result, ValidationResult)
            or result.formula_id != expected.formula_id
        ):
            raise FormulaAuditError("公式校验结果顺序不一致")
        if result.syntax_status == "valid":
            if result.validation_error is not None:
                raise FormulaAuditError("有效公式包含校验错误")
        elif result.syntax_status == "invalid_syntax":
            if (
                not isinstance(result.validation_error, str)
                or not result.validation_error
            ):
                raise FormulaAuditError("无效公式缺少校验错误")
        else:
            raise FormulaAuditError("公式校验状态无效")
    return tuple(results)
```

增加内部决定结构：

```python
@dataclass(frozen=True)
class _NormalizationDecision:
    normalized_formula: str | None
    normalization_rules: tuple[str, ...]
    normalization_status: Literal[
        "not_applicable", "accepted", "rejected"
    ]
    validation_error: str | None
```

在 `_build_report` 中：

1. 为全部 candidate 构造 raw `ValidationInput` 并校验；
2. 使用 `normalizer.normalize(candidate.katex_formula)`；
3. 只为 `changed` 候选构造第二批 `ValidationInput`；
4. 第二批 formula 使用 delimiter-free normalized 值；
5. 使用 `rebuild_raw_formula` 写完整 `normalized_formula`；
6. 按第二批结果生成 accepted/rejected 决定。

默认 normalizer 改为：

```python
normalizer or DeterministicFormulaNormalizer()
```

record 字段使用：

```python
"raw_validation_error": raw_validation.validation_error,
"normalized_formula": decision.normalized_formula,
"normalization_rules": list(decision.normalization_rules),
"normalization_status": decision.normalization_status,
"validation_error": decision.validation_error,
```

删除 `normalization_rule`、`confidence` 和“第一版禁止自动规范化”检查。

- [ ] **步骤 4：扩展统计、issues、页面集合与 accepted 映射**

`FormulaAuditStats` 增加：

```python
scanned_formula_count: int
matched_formula_count: int
normalization_accepted_count: int
normalization_rejected_count: int
```

命中谓词为：

```python
record["normalization_status"] in {"accepted", "rejected"}
```

issues 谓词为：

```python
(
    record["syntax_status"] == "invalid_syntax"
    or record["suspicious"]
    or record["normalization_status"] in {"accepted", "rejected"}
)
```

`problem_page_indices` 与 `issue_formula_count` 使用相同集合；
`invalid_syntax_page_indices` 和 `suspicious_page_indices` 保持原定义。

在 `FormulaAuditReport` 增加：

```python
@property
def accepted_replacements(self) -> dict[tuple[str, bool], str]:
    replacements = {}
    decisions = {}
    for record in self.payload["formulas"]:
        status = record["normalization_status"]
        if status == "not_applicable":
            continue
        key = (record["raw_formula"], record["is_block"])
        normalized = record["normalized_formula"]
        decision = (normalized, status)
        existing = decisions.get(key)
        if existing is not None and existing != decision:
            raise FormulaAuditError("同身份公式修复决定存在冲突")
        decisions[key] = decision
        if status == "accepted":
            replacements[key] = normalized
    return replacements
```

`_build_report` 返回前访问一次 `report.accepted_replacements`，使新生成报告中的同身份
决定冲突在写文件之前失败；`_report_from_v2_payload` 读取已有报告时执行相同检查。

- [ ] **步骤 5：实现 v2 严格读取和有效 v1 识别**

把当前 schema v1 的 `_RECORD_KEYS`、`_SUMMARY_KEYS`、`_validate_record` 和
`_report_from_payload` 逻辑分别重命名为 `_V1_*`、
`_validate_v1_record`、`_validate_v1_payload`，保持当前 v1 结构检查不变。
`_validate_v1_payload` 验证通过后不返回当前 v2 report。

新增：

```python
class LegacyFormulaAuditError(FormulaAuditError):
    pass


def _report_from_payload(payload: object) -> FormulaAuditReport:
    if isinstance(payload, dict) and payload.get("schema_version") == 1:
        _validate_v1_payload(payload)
        raise LegacyFormulaAuditError("公式审计报告 schema v1 需要重建")
    return _report_from_v2_payload(payload)
```

`_report_from_v2_payload` 必须逐项校验：

- 顶层字段与 schema version；
- summary 字段和重新计算值；
- record 精确字段；
- `not_applicable` 必须是 normalized null、rules 空、validation error null；
- `accepted` 必须 normalized 非空、rules 非空、validation error null；
- `rejected` 必须 normalized 非空、rules 非空、validation error 非空；
- raw syntax status 与 `raw_validation_error` 组合；
- issues 顺序和内容；
- 三类页面集合；
- accepted replacement 不冲突。
- 同一 `(raw_formula, is_block)` 的 normalized 内容和 accepted/rejected 状态不
  冲突。

新增测试：

```python
def _valid_empty_v1_payload():
    return {
        "schema_version": 1,
        "validator": {
            "name": "katex",
            "version": "0.18.1",
            "config": {
                "throwOnError": False,
                "trust": False,
                "maxSize": 20,
                "maxExpand": 500,
            },
        },
        "summary": {
            "total_formulas": 0,
            "valid_count": 0,
            "invalid_syntax_count": 0,
            "suspicious_count": 0,
            "issue_formula_count": 0,
        },
        "problem_page_indices": [],
        "invalid_syntax_page_indices": [],
        "suspicious_page_indices": [],
        "formulas": [],
        "issues": [],
    }


def test_valid_v1_report_raises_legacy_migration_signal(tmp_path):
    path = tmp_path / "formula_audit.json"
    path.write_text(
        json.dumps(_valid_empty_v1_payload()),
        encoding="utf-8",
    )
    with pytest.raises(LegacyFormulaAuditError):
        read_formula_audit_file(path)


def test_malformed_v1_report_is_not_treated_as_migratable(tmp_path):
    payload = _valid_empty_v1_payload()
    payload["summary"]["total_formulas"] += 1
    path = tmp_path / "formula_audit.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FormulaAuditError) as error:
        read_formula_audit_file(path)
    assert not isinstance(error.value, LegacyFormulaAuditError)
```

- [ ] **步骤 6：更新修复统计和明细日志测试与实现**

日志实现：

```python
logger.info(
    "公式修复统计：扫描 %d，命中 %d，accepted %d，rejected %d",
    stats.scanned_formula_count,
    stats.matched_formula_count,
    stats.normalization_accepted_count,
    stats.normalization_rejected_count,
)
for record in report.payload["formulas"]:
    if record["normalization_status"] == "not_applicable":
        continue
    logger.info(
        "公式修复明细：formula_id=%s page_idx=%s status=%s "
        "rules=%s raw=%s normalized=%s validation_error=%s",
        record["formula_id"],
        record["page_idx"],
        record["normalization_status"],
        record["normalization_rules"],
        record["raw_formula"],
        record["normalized_formula"],
        record["validation_error"],
    )
```

测试断言汇总字段、formula_id、page_idx、before/after 和规则均出现；不得再保留旧测试
中“日志不能出现公式内容”的相反断言。

- [ ] **步骤 7：用生产 KaTeX 验证典型修复候选**

在 `tests/test_formula_validation.py` 增加：

```python
def test_production_katex_accepts_normalized_whitelist_examples():
    validator = KaTeXFormulaValidator()
    formulas = (
        ValidationInput("complement", r"\mathrm{C}_{4}^{=}", False),
        ValidationInput(
            "alkene",
            r"\mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}",
            False,
        ),
        ValidationInput(
            "control",
            r"\mathrm{FIC0012}_{\mathrm{SetPoint}}"
            r"\frac{\mathrm{m}^{3}}{\mathrm{h}}",
            True,
        ),
    )
    assert all(
        result.syntax_status == "valid"
        for result in validator.validate_batch(formulas)
    )
```

- [ ] **步骤 8：运行 schema v2 和生产 KaTeX 测试**

```bash
python -m pytest \
  tests/test_formula_rules.py \
  tests/test_formula_scanner.py \
  tests/test_formula_validation.py \
  tests/test_formula_audit.py -q
```

预期：全部通过，测试输出无未捕获 warning。

- [ ] **步骤 9：提交 schema v2**

```bash
git add \
  src/pdf_trans/formula_audit.py \
  tests/test_formula_audit.py \
  tests/test_formula_validation.py
git commit -m "feat: validate and record formula repairs"
```

---

### 任务 4：让 Markdown 渲染只消费 accepted 修复

**文件：**

- 修改：`src/pdf_trans/renderer.py:1-100`
- 修改：`tests/test_renderer.py:1-220`

**接口：**

```python
def render_items(
    items: list[Any],
    *,
    formula_audit: FormulaAuditReport | None = None,
) -> str: ...

def render_content_list_file(
    source: Path,
    output: Path,
    *,
    formula_audit: FormulaAuditReport | None = None,
) -> None: ...
```

- 无报告时行为与当前完全一致。
- 有报告时只使用 `formula_audit.accepted_replacements`。

- [ ] **步骤 1：先写渲染 accepted/rejected 的失败测试**

使用只暴露 `accepted_replacements` 的 `SimpleNamespace` 覆盖正文、equation 和
table；rejected 记录不会出现在该映射中：

```python
from types import SimpleNamespace


def test_render_items_applies_only_accepted_formula_replacements():
    report = SimpleNamespace(
        accepted_replacements={
            (
                r"$\complement _ {4}$",
                False,
            ): r"$\mathrm{C}_{4}$",
            (
                "$$F I C 0 0 1 2$$",
                True,
            ): r"$$\mathrm{FIC0012}$$",
        }
    )
    items = [
        {
            "type": "text",
            "text": (
                r"raw $\complement _ {4}$ "
                r"and $\frac {m 3}{h}$"
            ),
        },
        {"type": "equation", "text": "$$F I C 0 0 1 2$$"},
    ]
    assert render_items(items, formula_audit=report) == (
        r"raw $\mathrm{C}_{4}$ and $\frac {m 3}{h}$"
        "\n\n"
        r"$$\mathrm{FIC0012}$$"
        "\n"
    )


def test_render_uses_selected_translated_fields_and_safe_table_spans():
    raw = r"$\frac {m 3}{h}$"
    normalized = r"$\frac{\mathrm{m}^{3}}{\mathrm{h}}$"
    report = SimpleNamespace(
        accepted_replacements={(raw, False): normalized}
    )
    items = [
        {
            "type": "text",
            "text": f"原文 {raw}",
            "translated_text": f"译文 {raw}",
            "translation_status": "success",
        },
        {
            "type": "table",
            "table_body": f"<table><tr><td>{raw}</td></tr></table>",
            "translated_table_body": (
                f'<table data-x="{raw}"><tr><td>{raw}</td></tr></table>'
            ),
            "translation_status": "success",
        },
    ]
    rendered = render_items(items, formula_audit=report)
    assert f"译文 {normalized}" in rendered
    assert f'data-x="{raw}"' in rendered
    assert f"<td>{normalized}</td>" in rendered
```

- [ ] **步骤 2：运行 renderer 测试并确认签名失败**

```bash
python -m pytest tests/test_renderer.py -q
```

预期：`render_items()` 不接受 `formula_audit`，测试失败。

- [ ] **步骤 3：给渲染器接入结构化替换**

把 `_render_item` 签名改为接收 replacements：

```python
def _render_item(
    item: Any,
    replacements: FormulaReplacement,
) -> list[str]:
```

在现有字段选择完成后分别调用：

```python
text = replace_formula_spans(text, replacements)
table_body = replace_table_formula_spans(
    table_body, replacements
)
text = replace_equation_formula(text, replacements)
```

只在值通过 `_is_non_blank_string` 后调用对应函数。`ref_text`、图片、图表、caption、
footnote 不新增替换。

`render_items` 构造映射：

```python
replacements = (
    {}
    if formula_audit is None
    else formula_audit.accepted_replacements
)
```

`render_content_list_file` 把可选 report 传给 `render_items`。

- [ ] **步骤 4：运行 renderer 和 scanner 测试**

```bash
python -m pytest \
  tests/test_formula_scanner.py \
  tests/test_renderer.py -q
```

预期：全部通过；无审计参数的旧 renderer 测试结果字节不变。

- [ ] **步骤 5：提交 Markdown 消费逻辑**

```bash
git add src/pdf_trans/renderer.py tests/test_renderer.py
git commit -m "feat: render accepted formula repairs"
```

---

### 任务 5：接入完整工作流和 Web v1 迁移

**文件：**

- 修改：`src/pdf_trans/workflow.py:278-369`
- 修改：`src/pdf_trans/web/task_runner.py:1-147`
- 修改：`tests/test_workflow.py:200-430`
- 修改：`tests/web/test_task_runner.py:1-260`

**接口：**

- 完整流程调用：

```python
render_content_list_file(
    translation_result.translated_path,
    markdown_path,
    formula_audit=audit_report,
)
```

- Web `_ensure_formula_audit(normalized: Path) -> FormulaAuditReport`。
- Web resume 的 renderer 同样接收 `formula_audit=report`。

- [ ] **步骤 1：先写完整工作流最终 Markdown 的失败测试**

在 `tests/test_workflow.py` 使用真实规则、可接受全部输入的 fake validator 和不改变
公式的 fake translator：

```python
from pdf_trans.formula_audit import audit_content_list_file
from pdf_trans.formula_validation import ValidationResult


class AcceptingFormulaValidator:
    def validate_batch(self, formulas):
        return tuple(
            ValidationResult(value.formula_id, "valid", None)
            for value in formulas
        )


def test_process_pdf_renders_accepted_normalized_formula(
    tmp_path, monkeypatch
):
    from pdf_trans import workflow

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    client = FakeMinerUClient(
        make_result_zip(
            [
                {
                    "type": "equation",
                    "page_idx": 7,
                    "text": r"$$\complement _ {4}^{=}$$",
                }
            ]
        )
    )

    def audit_with_accepting_validator(source, output):
        return audit_content_list_file(
            source,
            output,
            validator=AcceptingFormulaValidator(),
        )

    monkeypatch.setattr(
        workflow,
        "audit_content_list_file",
        audit_with_accepting_validator,
    )
    result = process_pdf(
        pdf,
        data_dir=tmp_path / "data",
        client=client,
        translator=TrackingTranslator(),
    )
    assert result.markdown_path.read_text(encoding="utf-8") == (
        r"$$\mathrm{C}_{4}^{=}$$" + "\n"
    )
    source = json.loads(result.source_path.read_text(encoding="utf-8"))
    assert source[0]["text"] == r"$$\complement _ {4}^{=}$$"
```

- [ ] **步骤 2：写 Web v2 复用和有效 v1 重建测试**

修改 `WorkflowServices` 测试 fake renderer 接收 keyword：

```python
def render(source, output, *, formula_audit=None):
    calls.append(("render", source, output, formula_audit))
    output.write_text("# resumed", encoding="utf-8")
```

v2 复用断言：

```python
assert calls[:3] == [
    ("read_audit", audit),
    ("translate", normalized),
    ("render", translated, rendered, v2_report),
]
```

v1 重建测试让 reader 抛出精确迁移信号：

```python
def read_report(path):
    calls.append(("read_v1", path))
    raise LegacyFormulaAuditError("schema v1")


def create_audit(source_path, output_path):
    calls.append(("rebuild_v2", source_path, output_path))
    return v2_report
```

断言重建发生在翻译和渲染之前，render 收到 `v2_report`。再增加一个损坏 v2 的
`FormulaAuditError` 测试，断言不会调用 `create_audit`、translate 或 render。

- [ ] **步骤 3：运行 workflow 测试并确认审计报告尚未传入 renderer**

```bash
python -m pytest \
  tests/test_workflow.py \
  tests/web/test_task_runner.py -q
```

预期：新断言因 renderer 未收到 report、`_ensure_formula_audit` 返回 None、v1 未捕获
而失败。

- [ ] **步骤 4：接入完整工作流**

在 `workflow.py` 的 Markdown 阶段使用已生成的 `audit_report`：

```python
render_content_list_file(
    translation_result.translated_path,
    markdown_path,
    formula_audit=audit_report,
)
```

不要改变清洗、跨页合并或 `_run_translation`。

- [ ] **步骤 5：实现 Web 续跑迁移和报告传递**

`WorkflowServices.render_content_list_file` 类型改为允许 keyword report：

```python
Callable[..., None]
```

`TaskRunner.run`：

```python
audit_report = self._ensure_formula_audit(normalized)
result = self.services.process_translation_file(normalized)
self.services.render_content_list_file(
    result.translated_path,
    markdown,
    formula_audit=audit_report,
)
```

`_ensure_formula_audit`：

```python
def _ensure_formula_audit(
    self, normalized: Path
) -> FormulaAuditReport:
    audit_path = normalized.with_name("formula_audit.json")
    if audit_path.is_file():
        try:
            report = self.services.read_formula_audit_file(audit_path)
        except LegacyFormulaAuditError:
            source = _original_content_list(normalized)
            return self.services.audit_content_list_file(
                source, audit_path
            )
        log_formula_audit_summary(report, LOGGER)
        return report
    source = _original_content_list(normalized)
    return self.services.audit_content_list_file(source, audit_path)
```

只能捕获 `LegacyFormulaAuditError`；普通 `FormulaAuditError` 必须继续向上抛。

- [ ] **步骤 6：运行 workflow、Web 和 CLI 回归测试**

```bash
python -m pytest \
  tests/test_workflow.py \
  tests/web/test_task_runner.py \
  tests/test_cli.py -q
```

预期：全部通过。`--translate-only` 行为和输出参数不改变。

- [ ] **步骤 7：提交工作流接入**

```bash
git add \
  src/pdf_trans/workflow.py \
  src/pdf_trans/web/task_runner.py \
  tests/test_workflow.py \
  tests/web/test_task_runner.py
git commit -m "feat: apply formula repairs in workflow rendering"
```

---

### 任务 6：更新中文文档并执行全量验证

**文件：**

- 修改：`README.md:250-267`
- 修改：`README.md:325-337`
- 修改：`docs/superpowers/specs/2026-07-31-mineru-formula-repair-design.md:292-323`

**接口：**

- 文档明确 schema v2、纯规则、raw 保留、accepted/rejected、最终渲染和 v1 重建。
- 不新增配置项、命令行选项或模型依赖。

- [ ] **步骤 1：更新 README**

把“审计只读且不修复”的旧描述替换为以下含义明确的中文内容：

```markdown
公式审计使用纯确定性白名单规则生成修复候选，不调用视觉模型或文本模型。
MinerU 的 `raw_formula` 和原始 content list 始终保留。每条候选会通过同版本
KaTeX 严格复检：accepted 候选在生成 `rendered.md` 时替换对应完整公式；
rejected 候选继续使用 raw 公式，不会阻断段落、表格或文档。

`formula_audit.json` schema v2 记录 `normalized_formula`、
`normalization_rules`、`normalization_status`、原公式校验错误和修复候选校验错误，
并输出扫描、命中、accepted、rejected 统计。Web 继续旧任务时会复用有效 v2；
有效 v1 会从原始 MinerU content list 原子重建为 v2。
```

Markdown 渲染章节把“equation 输出 MinerU 原始 LaTeX”改为“无 accepted 修复时输出
原始 LaTeX；有 accepted 修复时按精确公式跨度输出 normalized_formula”。

- [ ] **步骤 2：运行静态检查和相关测试**

```bash
git diff --check
python -m pytest \
  tests/test_formula_rules.py \
  tests/test_formula_scanner.py \
  tests/test_formula_validation.py \
  tests/test_formula_audit.py \
  tests/test_renderer.py \
  tests/test_workflow.py \
  tests/web/test_task_runner.py -q
```

预期：`git diff --check` 无输出；所有相关测试通过。

- [ ] **步骤 3：运行完整测试套件**

```bash
python -m pytest -q
```

预期：全部测试通过，无新增失败。

- [ ] **步骤 4：核对真实 formula_audit 样例但不覆盖它**

用临时目录读取工作区现有实际 raw 公式，调用规则引擎和生产 KaTeX 校验器。检查：

```text
page_idx 7: complement 下标 -> accepted
page_idx 20: 205 度 complement -> accepted
page_idx 36: 位号、MeterMax、SetPoint、Output、Equation、m3/h 连续命中
page_idx 43/44: 无编号 F I C 不被修改
```

该检查只读
`data/web/tasks/57e00874-4c4e-4dc0-97b8-34ba97654ed1/attempts/1/source/hybrid_auto/formula_audit.json`，
所有新输出写到 `mktemp -d` 返回的明确临时目录。不得覆盖样例报告或任务产物。

预期：所有白名单候选通过生产 KaTeX；无编号 FIC 记录的规则结果为空。

- [ ] **步骤 5：提交文档和最终验证调整**

```bash
git add \
  README.md \
  docs/superpowers/specs/2026-07-31-mineru-formula-repair-design.md
git commit -m "docs: document deterministic formula repair"
```

- [ ] **步骤 6：检查最终工作树和提交范围**

```bash
git status --short
git log -7 --oneline
```

预期：工作树干净；新增提交只包含规则、scanner、audit、renderer、workflow、Web
runner、对应测试和文档，没有翻译模型逻辑或真实任务产物。
