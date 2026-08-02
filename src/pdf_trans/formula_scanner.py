from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Literal, Mapping

from pdf_trans.errors import FormulaAuditError


DELIMITERS = (
    ("$$", "$$", True),
    ("\\[", "\\]", True),
    ("\\(", "\\)", False),
    ("$", "$", False),
)
_CELL_TAGS = frozenset({"td", "th"})
_HIDDEN_TAGS = frozenset({"script", "style", "template"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_HTML_BOUNDARY_TAG_RE = re.compile(
    r"</?([A-Za-z][A-Za-z0-9:-]*)\b[^>]*>",
    re.DOTALL,
)
_NUMERIC_DOLLAR_PREFIX_RE = re.compile(
    r"\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?=[^A-Za-z0-9_]|$)"
)
_CLEAR_NUMERIC_MATH_RE = re.compile(
    r"(?:\\|[{}_^=+*/()<>]|\d\s*[-−]\s*\d)"
)


@dataclass(frozen=True)
class FormulaCandidate:
    formula_id: str
    page_idx: Any
    bbox: Any
    source_type: Literal["equation", "text", "table_cell"]
    field_path: str
    table_row_idx: int | None
    table_col_idx: int | None
    cell_tag: str | None
    formula_index: int
    raw_formula: str
    katex_formula: str
    katex_start: int
    katex_end: int
    is_block: bool
    content_hash: str


@dataclass(frozen=True)
class FormulaSpan:
    raw_formula: str
    katex_formula: str
    is_block: bool
    start: int
    end: int
    katex_start: int
    katex_end: int


@dataclass(frozen=True)
class _TableFormula:
    row_idx: int
    col_idx: int
    cell_tag: str
    formula_index: int
    span: FormulaSpan


@dataclass
class _OpenCell:
    tag: str
    row_idx: int
    col_idx: int
    formula_count: int = 0


def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and source[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _find_closing(
    source: str,
    start: int,
    closing: str,
) -> int | None:
    position = start
    while position < len(source):
        if (
            source.startswith(closing, position)
            and not _is_escaped(source, position)
            and (
                closing != "$"
                or not source.startswith("$$", position)
            )
        ):
            return position
        position += 1
    return None


def _contains_html_boundary(
    source: str,
    start: int,
    end: int,
) -> bool:
    """Detect structural tags, while preserving void-tag formula text."""
    return any(
        match.group(1).lower() not in _VOID_TAGS
        for match in _HTML_BOUNDARY_TAG_RE.finditer(
            source,
            start,
            end,
        )
    )


def _looks_like_ambiguous_numeric_dollar(
    content: str,
) -> bool:
    """Reject numeric-leading dollar text unless it has clear TeX syntax."""
    if _NUMERIC_DOLLAR_PREFIX_RE.match(content) is None:
        return False
    return _CLEAR_NUMERIC_MATH_RE.search(content) is None


def scan_formula_spans(source: str) -> tuple[FormulaSpan, ...]:
    spans: list[FormulaSpan] = []
    position = 0
    while position < len(source):
        matched = False
        for opening, closing, is_block in DELIMITERS:
            if not source.startswith(opening, position):
                continue
            if _is_escaped(source, position):
                continue
            if opening == "$" and source.startswith("$$", position):
                continue
            end = _find_closing(
                source,
                position + len(opening),
                closing,
            )
            if end is None:
                continue
            raw_end = end + len(closing)
            if opening == "$":
                content = source[position + 1 : end]
                if _contains_html_boundary(
                    source,
                    position + 1,
                    end,
                ) or _looks_like_ambiguous_numeric_dollar(content):
                    continue
            spans.append(
                FormulaSpan(
                    raw_formula=source[position:raw_end],
                    katex_formula=source[
                        position + len(opening) : end
                    ],
                    is_block=is_block,
                    start=position,
                    end=raw_end,
                    katex_start=position + len(opening),
                    katex_end=end,
                )
            )
            position = raw_end
            matched = True
            break
        if not matched:
            position += 1
    return tuple(spans)


def _equation_formula(source: str) -> FormulaSpan:
    stripped = source.strip()
    stripped_start = len(source) - len(source.lstrip())
    stripped_end = stripped_start + len(stripped)
    for opening, closing in (("$$", "$$"), ("\\[", "\\]")):
        if (
            stripped.startswith(opening)
            and stripped.endswith(closing)
            and len(stripped) >= len(opening) + len(closing)
        ):
            return FormulaSpan(
                raw_formula=source,
                katex_formula=stripped[
                    len(opening) : len(stripped) - len(closing)
                ],
                is_block=True,
                start=0,
                end=len(source),
                katex_start=stripped_start + len(opening),
                katex_end=stripped_end - len(closing),
            )
    return FormulaSpan(
        raw_formula=source,
        katex_formula=source,
        is_block=True,
        start=0,
        end=len(source),
        katex_start=0,
        katex_end=len(source),
    )


def _positive_span(
    attrs: list[tuple[str, str | None]],
    name: str,
    field_path: str,
) -> int:
    values = [value for key, value in attrs if key.lower() == name]
    if not values:
        return 1
    if len(values) != 1 or values[0] is None:
        raise FormulaAuditError(
            f"{field_path} 的 {name} 必须是正整数"
        )
    try:
        parsed = int(values[0])
    except ValueError as exc:
        raise FormulaAuditError(
            f"{field_path} 的 {name} 必须是正整数"
        ) from exc
    if parsed <= 0:
        raise FormulaAuditError(
            f"{field_path} 的 {name} 必须是正整数"
        )
    return parsed


class _TableFormulaParser(HTMLParser):
    def __init__(self, source: str, field_path: str) -> None:
        super().__init__(convert_charrefs=False)
        self.field_path = field_path
        self.formulas: list[_TableFormula] = []
        self._line_starts = [0]
        self._line_starts.extend(
            match.end() for match in re.finditer(r"\n", source)
        )
        self._stack: list[str] = []
        self._table_depth = 0
        self._saw_table = False
        self._row_idx = -1
        self._column = 0
        self._occupied_until: dict[int, int] = {}
        self._current_cell: _OpenCell | None = None

    def _error(self, detail: str) -> FormulaAuditError:
        return FormulaAuditError(f"{self.field_path} 的表格 HTML {detail}")

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if tag == "table":
            if self._table_depth:
                raise self._error("不支持嵌套 table")
            self._table_depth = 1
            self._saw_table = True
        elif tag == "tr":
            if self._table_depth != 1 or self._current_cell is not None:
                raise self._error("tr 位置无效")
            self._row_idx += 1
            self._column = 0
        elif tag in _CELL_TAGS:
            if (
                self._table_depth != 1
                or "tr" not in self._stack
                or self._current_cell is not None
            ):
                raise self._error(f"{tag} 位置无效")
            while (
                self._occupied_until.get(self._column, 0)
                > self._row_idx
            ):
                self._column += 1
            cell_column = self._column
            rowspan = _positive_span(
                attrs,
                "rowspan",
                self.field_path,
            )
            colspan = _positive_span(
                attrs,
                "colspan",
                self.field_path,
            )
            for occupied_column in range(
                cell_column,
                cell_column + colspan,
            ):
                self._occupied_until[occupied_column] = max(
                    self._occupied_until.get(occupied_column, 0),
                    self._row_idx + rowspan,
                )
            self._column = cell_column + colspan
            self._current_cell = _OpenCell(
                tag=tag,
                row_idx=self._row_idx,
                col_idx=cell_column,
            )
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if tag in _CELL_TAGS or tag in {"table", "tr"}:
            raise self._error(f"{tag} 不能自闭合")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1] != tag:
            raise self._error(f"结束标签 {tag} 不匹配")
        if tag in _CELL_TAGS:
            if self._current_cell is None or self._current_cell.tag != tag:
                raise self._error(f"结束标签 {tag} 不匹配")
            self._current_cell = None
        elif tag == "tr" and self._current_cell is not None:
            raise self._error("tr 结束前单元格未闭合")
        elif tag == "table":
            if self._current_cell is not None:
                raise self._error("table 结束前单元格未闭合")
            self._table_depth = 0
        self._stack.pop()

    def handle_data(self, data: str) -> None:
        cell = self._current_cell
        if cell is None or any(
            tag in _HIDDEN_TAGS for tag in self._stack
        ):
            return
        line, column = self.getpos()
        data_start = self._line_starts[line - 1] + column
        for span in scan_formula_spans(data):
            absolute_span = replace(
                span,
                start=data_start + span.start,
                end=data_start + span.end,
                katex_start=data_start + span.katex_start,
                katex_end=data_start + span.katex_end,
            )
            self.formulas.append(
                _TableFormula(
                    row_idx=cell.row_idx,
                    col_idx=cell.col_idx,
                    cell_tag=cell.tag,
                    formula_index=cell.formula_count,
                    span=absolute_span,
                )
            )
            cell.formula_count += 1

    @property
    def result(self) -> tuple[_TableFormula, ...]:
        if (
            self._stack
            or self._table_depth
            or self._current_cell is not None
        ):
            raise self._error("标签未闭合")
        if not self._saw_table:
            raise self._error("缺少 table 标签")
        return tuple(self.formulas)


def _table_formulas(
    source: str,
    field_path: str,
) -> tuple[_TableFormula, ...]:
    parser = _TableFormulaParser(source, field_path)
    try:
        parser.feed(source)
        parser.close()
        return parser.result
    except FormulaAuditError:
        raise
    except Exception as exc:
        raise FormulaAuditError(
            f"{field_path} 的表格 HTML 解析失败：{exc}"
        ) from exc


def _candidate(
    *,
    page_idx: Any,
    bbox: Any,
    source_type: Literal["equation", "text", "table_cell"],
    field_path: str,
    table_row_idx: int | None,
    table_col_idx: int | None,
    cell_tag: str | None,
    formula_index: int,
    span: FormulaSpan,
) -> FormulaCandidate:
    content_hash = hashlib.sha256(
        span.raw_formula.encode("utf-8")
    ).hexdigest()
    identity = {
        "page_idx": page_idx,
        "bbox": bbox,
        "source_type": source_type,
        "field_path": field_path,
        "table_row_idx": table_row_idx,
        "table_col_idx": table_col_idx,
        "formula_index": formula_index,
        "content_hash": content_hash,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    formula_id = (
        "formula_" + hashlib.sha256(encoded).hexdigest()[:24]
    )
    return FormulaCandidate(
        formula_id=formula_id,
        page_idx=page_idx,
        bbox=bbox,
        source_type=source_type,
        field_path=field_path,
        table_row_idx=table_row_idx,
        table_col_idx=table_col_idx,
        cell_tag=cell_tag,
        formula_index=formula_index,
        raw_formula=span.raw_formula,
        katex_formula=span.katex_formula,
        katex_start=span.katex_start - span.start,
        katex_end=span.katex_end - span.start,
        is_block=span.is_block,
        content_hash=content_hash,
    )


def rebuild_raw_formula(
    candidate: FormulaCandidate,
    normalized_katex: str,
) -> str:
    return (
        candidate.raw_formula[: candidate.katex_start]
        + normalized_katex
        + candidate.raw_formula[candidate.katex_end :]
    )


FormulaReplacement = Mapping[tuple[str, bool], str]


def _replace_spans(
    source: str,
    spans: tuple[FormulaSpan, ...],
    replacements: FormulaReplacement,
) -> str:
    result = source
    for span in reversed(spans):
        replacement = replacements.get(
            (span.raw_formula, span.is_block)
        )
        if replacement is not None:
            result = (
                result[: span.start]
                + replacement
                + result[span.end :]
            )
    return result


def replace_formula_spans(
    source: str,
    replacements: FormulaReplacement,
) -> str:
    return _replace_spans(
        source,
        scan_formula_spans(source),
        replacements,
    )


def replace_equation_formula(
    source: str,
    replacements: FormulaReplacement,
) -> str:
    span = _equation_formula(source)
    return replacements.get((span.raw_formula, True), source)


def replace_table_formula_spans(
    source: str,
    replacements: FormulaReplacement,
    *,
    field_path: str = "/render/table_body",
) -> str:
    formulas = _table_formulas(source, field_path)
    return _replace_spans(
        source,
        tuple(value.span for value in formulas),
        replacements,
    )


def scan_content_list(
    items: list[object],
) -> tuple[FormulaCandidate, ...]:
    candidates: list[FormulaCandidate] = []
    for item_index, value in enumerate(items):
        if not isinstance(value, dict):
            continue
        page_idx = value.get("page_idx")
        bbox = value.get("bbox")
        text = value.get("text")
        text_path = f"/{item_index}/text"
        if value.get("type") == "equation":
            if isinstance(text, str):
                candidates.append(
                    _candidate(
                        page_idx=page_idx,
                        bbox=bbox,
                        source_type="equation",
                        field_path=text_path,
                        table_row_idx=None,
                        table_col_idx=None,
                        cell_tag=None,
                        formula_index=0,
                        span=_equation_formula(text),
                    )
                )
        elif isinstance(text, str):
            for formula_index, span in enumerate(
                scan_formula_spans(text)
            ):
                candidates.append(
                    _candidate(
                        page_idx=page_idx,
                        bbox=bbox,
                        source_type="text",
                        field_path=text_path,
                        table_row_idx=None,
                        table_col_idx=None,
                        cell_tag=None,
                        formula_index=formula_index,
                        span=span,
                    )
                )

        table_body = value.get("table_body")
        if isinstance(table_body, str):
            field_path = f"/{item_index}/table_body"
            for table_formula in _table_formulas(
                table_body,
                field_path,
            ):
                candidates.append(
                    _candidate(
                        page_idx=page_idx,
                        bbox=bbox,
                        source_type="table_cell",
                        field_path=field_path,
                        table_row_idx=table_formula.row_idx,
                        table_col_idx=table_formula.col_idx,
                        cell_tag=table_formula.cell_tag,
                        formula_index=table_formula.formula_index,
                        span=table_formula.span,
                    )
                )
    return tuple(candidates)
