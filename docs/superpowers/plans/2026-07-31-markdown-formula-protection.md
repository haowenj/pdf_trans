# Markdown Formula Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止 CommonMark 把数学公式中的 `_`、`*` 等符号解析成 Markdown 标记，并重新生成、验证任务 `b948a6d2-de9b-4f55-9e07-787635a2eee5` 的 `rendered.md`。

**Architecture:** 在 `render_safe_markdown` 内先扫描四类现有数学定界符，将完整公式替换为随机、纯字母数字占位符；Markdown-It 生成 HTML 后，再以经过 HTML 转义的原公式恢复占位符，最后继续使用 nh3 清洗和浏览器端 KaTeX auto-render。公式修复、翻译和 Markdown 文件格式保持不变。

**Tech Stack:** Python 3.11、markdown-it-py 4、nh3、KaTeX 0.18.1、pytest 8

## Global Constraints

- 支持并保留 `$...$`、`$$...$$`、`\(...\)`、`\[...\]`。
- 优先识别 `$$...$$`，不能把块公式拆成两个行内公式。
- 跳过已转义或未闭合的定界符。
- 恢复公式前必须执行 HTML 转义，nh3 继续作为最终 HTML 安全边界。
- 不修改翻译模型逻辑、公式修复白名单、KaTeX 校验器或 `rendered.md` 文件格式。
- 当前会话、当前分支执行，不创建工作区或新分支。

---

### Task 1: 用公式占位符隔离 CommonMark 解析

**Files:**
- Modify: `tests/web/test_markdown.py`
- Modify: `src/pdf_trans/web/markdown.py`

**Interfaces:**
- Consumes: `render_safe_markdown(source: str, *, asset_base_url: str) -> str`
- Produces: `_protect_math(source: str) -> tuple[str, tuple[_ProtectedMath, ...]]`
- Produces: `_restore_math(rendered: str, records: tuple[_ProtectedMath, ...]) -> str`
- Preserves: `render_safe_markdown` 的公开签名与 nh3 清洗行为

- [ ] **Step 1: 写入能够复现跨公式斜体的失败测试**

在 `tests/web/test_markdown.py` 追加：

```python
def test_markdown_does_not_parse_emphasis_across_inline_formulas() -> None:
    source = (
        r"The catalyst converts ${ \mathrm{C}_{4} } ^ { = }$ to "
        r"$\scriptstyle \mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}$ "
        "olefins."
    )

    rendered = render_safe_markdown(
        source,
        asset_base_url="/tasks/a/assets",
    )

    assert "<em>" not in rendered
    assert (
        r"${ \mathrm{C}_{4} } ^ { = }$ to "
        r"$\scriptstyle \mathrm{C}_{7}^{=} / \mathrm{C}_{8}^{=}$"
        in rendered
    )
```

- [ ] **Step 2: 运行测试并确认它因跨公式 `<em>` 而失败**

Run:

```bash
.venv/bin/python -m pytest \
  tests/web/test_markdown.py::test_markdown_does_not_parse_emphasis_across_inline_formulas \
  -v
```

Expected: FAIL；输出中的实际 HTML 包含 `<em>{4} ... \mathrm{C}</em>`。

- [ ] **Step 3: 写入其他边界和安全测试**

在 `tests/web/test_markdown.py` 追加：

```python
def test_markdown_preserves_all_supported_math_delimiters() -> None:
    source = (
        r"$a_b$ and $$c_d$$ and \(e_f\) and \[g_h\]"
    )

    rendered = render_safe_markdown(
        source,
        asset_base_url="/tasks/a/assets",
    )

    assert source in rendered
    assert "<em>" not in rendered


def test_markdown_escapes_html_inside_protected_formula() -> None:
    rendered = render_safe_markdown(
        r"$x <img src=x onerror=alert(1)> y$",
        asset_base_url="/tasks/a/assets",
    )

    assert "<img" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in rendered


def test_markdown_keeps_regular_emphasis_outside_formulas() -> None:
    rendered = render_safe_markdown(
        "before _important_ after",
        asset_base_url="/tasks/a/assets",
    )

    assert "<em>important</em>" in rendered


def test_markdown_leaves_escaped_and_unclosed_dollars_as_text() -> None:
    rendered = render_safe_markdown(
        r"cost \$5 and unfinished $x_y",
        asset_base_url="/tasks/a/assets",
    )

    assert "cost $5 and unfinished $x_y" in rendered
```

- [ ] **Step 4: 实现最小公式扫描、保护与安全恢复**

在 `src/pdf_trans/web/markdown.py` 中增加以下导入和私有实现：

```python
import html
import secrets
from dataclasses import dataclass


_MATH_DELIMITERS = (
    ("$$", "$$"),
    (r"\[", r"\]"),
    (r"\(", r"\)"),
    ("$", "$"),
)


@dataclass(frozen=True)
class _ProtectedMath:
    placeholder: str
    source: str


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _opening_at(source: str, position: int) -> tuple[str, str] | None:
    for opening, closing in _MATH_DELIMITERS:
        if not source.startswith(opening, position):
            continue
        if _is_escaped(source, position):
            continue
        if opening == "$" and source.startswith("$$", position):
            continue
        return opening, closing
    return None


def _find_closing(
    source: str,
    start: int,
    closing: str,
) -> int | None:
    position = start
    while True:
        position = source.find(closing, position)
        if position < 0:
            return None
        if _is_escaped(source, position):
            position += len(closing)
            continue
        if closing == "$" and source.startswith("$$", position):
            position += 2
            continue
        return position


def _protect_math(
    source: str,
) -> tuple[str, tuple[_ProtectedMath, ...]]:
    nonce = secrets.token_hex(16).upper()
    parts: list[str] = []
    records: list[_ProtectedMath] = []
    copied_until = 0
    position = 0

    while position < len(source):
        delimiters = _opening_at(source, position)
        if delimiters is None:
            position += 1
            continue
        opening, closing = delimiters
        closing_at = _find_closing(
            source,
            position + len(opening),
            closing,
        )
        if closing_at is None:
            position += len(opening)
            continue

        formula_end = closing_at + len(closing)
        placeholder = (
            f"PDFTRANSMATH{nonce}{len(records):08d}TOKEN"
        )
        parts.append(source[copied_until:position])
        parts.append(placeholder)
        records.append(
            _ProtectedMath(
                placeholder=placeholder,
                source=source[position:formula_end],
            )
        )
        copied_until = formula_end
        position = formula_end

    parts.append(source[copied_until:])
    return "".join(parts), tuple(records)


def _restore_math(
    rendered: str,
    records: tuple[_ProtectedMath, ...],
) -> str:
    positions: list[int] = []
    for record in records:
        if rendered.count(record.placeholder) != 1:
            raise ValueError("Markdown 公式占位符数量异常")
        positions.append(rendered.index(record.placeholder))
    if positions != sorted(positions):
        raise ValueError("Markdown 公式占位符顺序异常")

    restored = rendered
    for record in records:
        restored = restored.replace(
            record.placeholder,
            html.escape(record.source, quote=True),
            1,
        )
    return restored
```

把 `render_safe_markdown` 开头改为：

```python
def render_safe_markdown(source: str, *, asset_base_url: str) -> str:
    protected, math_records = _protect_math(source)
    rendered = PARSER.render(protected)
    rendered = _restore_math(rendered, math_records)
```

其余 URL 重写和 `nh3.clean` 代码保持不变。

- [ ] **Step 5: 运行 Web Markdown 测试并确认全部通过**

Run:

```bash
.venv/bin/python -m pytest tests/web/test_markdown.py -v
```

Expected: 8 passed。

- [ ] **Step 6: 检查格式与差异**

Run:

```bash
git diff --check
git diff -- src/pdf_trans/web/markdown.py tests/web/test_markdown.py
```

Expected: `git diff --check` 无输出，差异仅包含公式保护实现和回归测试。

- [ ] **Step 7: 提交公式保护修复**

```bash
git add src/pdf_trans/web/markdown.py tests/web/test_markdown.py
git commit -m "fix: protect formulas during markdown parsing"
```

---

### Task 2: 重新生成并验证指定任务产物

**Files:**
- Regenerate: `data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/rendered.md`
- Read: `data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/translated_content_list.json`
- Read: `data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/formula_audit.json`

**Interfaces:**
- Consumes: `read_formula_audit_file(path: Path) -> FormulaAuditReport`
- Consumes: `render_content_list_file(source: Path, output: Path, *, formula_audit: FormulaAuditReport | None = None) -> None`
- Consumes: `render_safe_markdown(source: str, *, asset_base_url: str) -> str`
- Produces: 重新生成的 `rendered.md` 和目标段落 HTML 验证结果

- [ ] **Step 1: 记录原产物哈希并重新调用现有渲染器**

Run:

```bash
shasum -a 256 \
  data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/rendered.md

PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path

from pdf_trans.formula_audit import read_formula_audit_file
from pdf_trans.renderer import render_content_list_file

root = Path(
    "data/web/tasks/"
    "b948a6d2-de9b-4f55-9e07-787635a2eee5/"
    "attempts/1/source/hybrid_auto"
)
audit = read_formula_audit_file(root / "formula_audit.json")
render_content_list_file(
    root / "translated_content_list.json",
    root / "rendered.md",
    formula_audit=audit,
)
PY

shasum -a 256 \
  data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/rendered.md
```

Expected: 命令成功；由于本次不改变 Markdown 文件格式，重建前后 SHA-256 应一致。

- [ ] **Step 2: 对目标段落执行服务端 HTML 回归验证**

Run:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path

from pdf_trans.web.markdown import render_safe_markdown

path = Path(
    "data/web/tasks/"
    "b948a6d2-de9b-4f55-9e07-787635a2eee5/"
    "attempts/1/source/hybrid_auto/rendered.md"
)
source = path.read_text(encoding="utf-8")
html = render_safe_markdown(
    source,
    asset_base_url="/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/assets",
)
start = html.index("<p>The First Stage OG-200")
end = html.index("</p>", start) + len("</p>")
paragraph = html[start:end]

assert "<em>" not in paragraph
assert paragraph.count("$") == 12
assert r"${ \mathrm{C}_{4} } ^ { = }$" in paragraph
assert (
    r"$\scriptstyle \mathrm{C}_{7}^{=} / "
    r"\mathrm{C}_{8}^{=}$"
    in paragraph
)
print("目标段落 HTML 验证通过")
PY
```

Expected: 输出 `目标段落 HTML 验证通过`。

- [ ] **Step 3: 确认任务产物未产生非预期版本库差异**

Run:

```bash
git status --short
git diff -- \
  data/web/tasks/b948a6d2-de9b-4f55-9e07-787635a2eee5/attempts/1/source/hybrid_auto/rendered.md
```

Expected: 数据目录没有需要提交的差异；代码提交后工作树为空。

---

### Task 3: 完整回归验证

**Files:**
- Verify: `src/pdf_trans/web/markdown.py`
- Verify: `tests/web/test_markdown.py`
- Verify: entire `tests/` suite

**Interfaces:**
- Consumes: Task 1 的公式保护实现
- Produces: 完整测试、源码编译和工作树状态证据

- [ ] **Step 1: 运行完整测试套件**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: 全部测试通过，0 failed。

- [ ] **Step 2: 编译 Python 源码**

Run:

```bash
.venv/bin/python -m compileall -q src tests
```

Expected: exit code 0，无语法错误。

- [ ] **Step 3: 检查最终提交与工作树**

Run:

```bash
git status --short --branch
git log -3 --oneline --decorate
```

Expected: 工作树干净；最近提交包含设计文档、实施计划和公式保护修复。
