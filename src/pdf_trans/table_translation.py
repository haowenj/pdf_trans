from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from pdf_trans.table_cell_protection import (
    CellMarker,
    CellProtectionError,
    ProtectedCellSegment,
    estimate_model_tokens,
    protect_cell_text,
    restore_cell_segment,
    split_protected_segment,
)


MAX_BATCH_ITEMS = 12
MAX_BATCH_TOKENS = 2_000
MAX_BATCH_FORMULAS = 24
REQUEST_TOKEN_OVERHEAD = 80
ITEM_TOKEN_OVERHEAD = 12
CELL_SEGMENT_TOKEN_BUDGET = (
    MAX_BATCH_TOKENS
    - REQUEST_TOKEN_OVERHEAD
    - ITEM_TOKEN_OVERHEAD
    - 16
)

_CELL_TAGS = frozenset({"td", "th", "caption"})
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
_ENGLISH_RE = re.compile(r"[A-Za-z]")
_PURE_STANDARD_CODE_RE = re.compile(
    r"^(?:(?:ASTM\s+)?D\d+[A-Z]?|IP\s*\d+|UOP\s*\d+|"
    r"[A-Z]{1,4}-?\d+[A-Z0-9-]*)(?:\s*[/,;]\s*"
    r"(?:(?:ASTM\s+)?D\d+[A-Z]?|IP\s*\d+|UOP\s*\d+|"
    r"[A-Z]{1,4}-?\d+[A-Z0-9-]*))*$",
    re.IGNORECASE,
)


class TableTranslationError(ValueError):
    """One table cannot be parsed or safely reconstructed."""


@dataclass(frozen=True)
class CellWorkItem:
    work_id: str
    cell_id: str
    segment_index: int
    model_text: str
    estimated_tokens: int
    formula_count: int


@dataclass(frozen=True)
class CellFallback:
    cell_id: str
    error: str


@dataclass(frozen=True)
class PreparedCell:
    cell_id: str
    tag: str
    content_start: int
    content_end: int
    original_inner_html: str
    segments: tuple[ProtectedCellSegment, ...]
    work_ids: tuple[str, ...]
    skipped: bool


@dataclass(frozen=True)
class TableRebuildResult:
    translated_html: str
    success_cell_count: int
    fallback_cell_count: int
    fallbacks: tuple[CellFallback, ...]


@dataclass(frozen=True)
class PreparedTableTranslation:
    original_html: str
    cells: tuple[PreparedCell, ...]
    structure: tuple[tuple[object, ...], ...]

    @property
    def work_items(self) -> tuple[CellWorkItem, ...]:
        items: list[CellWorkItem] = []
        for cell in self.cells:
            for index, (work_id, segment) in enumerate(
                zip(cell.work_ids, cell.segments)
            ):
                items.append(
                    CellWorkItem(
                        work_id=work_id,
                        cell_id=cell.cell_id,
                        segment_index=index,
                        model_text=segment.model_text,
                        estimated_tokens=(
                            ITEM_TOKEN_OVERHEAD
                            + estimate_model_tokens(work_id)
                            + estimate_model_tokens(segment.model_text)
                        ),
                        formula_count=len(segment.formula_markers),
                    )
                )
        return tuple(items)

    def segment_for(self, work_id: str) -> ProtectedCellSegment:
        for cell in self.cells:
            for current_id, segment in zip(cell.work_ids, cell.segments):
                if current_id == work_id:
                    return segment
        raise KeyError(work_id)

    def rebuild(
        self,
        translations: dict[str, str],
        errors: dict[str, str],
    ) -> TableRebuildResult:
        replacements: list[tuple[int, int, str]] = []
        success_count = 0
        fallbacks: list[CellFallback] = []
        for cell in self.cells:
            if cell.skipped:
                continue
            failed_ids = [
                work_id
                for work_id in cell.work_ids
                if work_id not in translations or work_id in errors
            ]
            if failed_ids:
                reason = next(
                    (
                        errors[work_id]
                        for work_id in failed_ids
                        if work_id in errors
                    ),
                    "模型结果缺少单元格译文",
                )
                fallbacks.append(CellFallback(cell.cell_id, reason))
                continue
            try:
                restored_inner = "".join(
                    restore_cell_segment(translations[work_id], segment)
                    for work_id, segment in zip(
                        cell.work_ids,
                        cell.segments,
                    )
                )
            except CellProtectionError as exc:
                fallbacks.append(CellFallback(cell.cell_id, str(exc)))
                continue
            replacements.append(
                (cell.content_start, cell.content_end, restored_inner)
            )
            success_count += 1

        translated = self.original_html
        for start, end, replacement in reversed(replacements):
            translated = translated[:start] + replacement + translated[end:]
        translated_structure = _parse_html(translated).structure
        if translated_structure != self.structure:
            raise TableTranslationError("翻译后的 HTML 结构校验失败")
        return TableRebuildResult(
            translated_html=translated,
            success_cell_count=success_count,
            fallback_cell_count=len(fallbacks),
            fallbacks=tuple(fallbacks),
        )


@dataclass(frozen=True)
class _RawCell:
    cell_id: str
    tag: str
    content_start: int
    content_end: int
    original_inner_html: str
    model_source: str
    html_markers: tuple[CellMarker, ...]


@dataclass
class _OpenCell:
    cell_id: str
    tag: str
    content_start: int
    pieces: list[str]
    html_markers: list[CellMarker]


@dataclass(frozen=True)
class _ParsedHTML:
    cells: tuple[_RawCell, ...]
    structure: tuple[tuple[object, ...], ...]


class _TableHTMLParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(
            match.end() for match in re.finditer(r"\n", source)
        )
        self._stack: list[str] = []
        self._cells: list[_RawCell] = []
        self._structure: list[tuple[object, ...]] = []
        self._current: _OpenCell | None = None
        self._saw_table = False

    def _absolute_position(self) -> int:
        line, offset = self.getpos()
        return self._line_offsets[line - 1] + offset

    def _raw_until_gt(self) -> str:
        start = self._absolute_position()
        end = self._source.find(">", start)
        if end < 0:
            raise TableTranslationError("HTML 标签缺少结束符")
        return self._source[start : end + 1]

    def _protect_raw(self, raw: str) -> None:
        if self._current is None:
            return
        number = len(self._current.html_markers)
        marker_id = f"H{number}"
        placeholder = f"⟦{marker_id}⟧"
        self._current.pieces.append(placeholder)
        self._current.html_markers.append(
            CellMarker(
                marker_id=marker_id,
                placeholder=placeholder,
                original=raw,
                sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            )
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        raw = self.get_starttag_text()
        event = "void" if tag in _VOID_TAGS else "start"
        self._structure.append((event, tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True
        if tag in _CELL_TAGS and "table" in self._stack:
            if self._current is not None:
                raise TableTranslationError("HTML 中存在嵌套单元格")
            self._current = _OpenCell(
                cell_id=f"cell-{len(self._cells) + 1:04d}",
                tag=tag,
                content_start=self._absolute_position() + len(raw),
                pieces=[],
                html_markers=[],
            )
        elif self._current is not None:
            self._protect_raw(raw)
        if tag not in _VOID_TAGS:
            self._stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        raw = self.get_starttag_text()
        self._structure.append(("self", tag, tuple(attrs)))
        if tag == "table":
            self._saw_table = True
        if tag in _CELL_TAGS:
            raise TableTranslationError("单元格标签不能自闭合")
        self._protect_raw(raw)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1] != tag:
            expected = self._stack[-1] if self._stack else "无"
            raise TableTranslationError(
                f"HTML 结束标签不匹配：期望 {expected}，实际 {tag}"
            )
        raw = self._raw_until_gt()
        if self._current is not None and tag == self._current.tag:
            content_end = self._absolute_position()
            self._cells.append(
                _RawCell(
                    cell_id=self._current.cell_id,
                    tag=tag,
                    content_start=self._current.content_start,
                    content_end=content_end,
                    original_inner_html=self._source[
                        self._current.content_start : content_end
                    ],
                    model_source="".join(self._current.pieces),
                    html_markers=tuple(self._current.html_markers),
                )
            )
            self._current = None
        elif self._current is not None:
            self._protect_raw(raw)
        self._stack.pop()
        self._structure.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if any(tag in _HIDDEN_TAGS for tag in self._stack):
            self._protect_raw(data)
        else:
            self._current.pieces.append(data)

    def handle_entityref(self, name: str) -> None:
        self._protect_raw(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._protect_raw(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._protect_raw(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._protect_raw(self._raw_until_gt())

    def handle_pi(self, data: str) -> None:
        self._protect_raw(self._raw_until_gt())

    @property
    def result(self) -> _ParsedHTML:
        if self._current is not None or self._stack:
            raise TableTranslationError("HTML 标签未闭合")
        if not self._saw_table:
            raise TableTranslationError("HTML 中缺少 table 标签")
        return _ParsedHTML(tuple(self._cells), tuple(self._structure))


def _parse_html(source: str) -> _ParsedHTML:
    if not isinstance(source, str) or not source.strip():
        raise TableTranslationError("HTML 必须是非空字符串")
    parser = _TableHTMLParser(source)
    try:
        parser.feed(source)
        parser.close()
        return parser.result
    except TableTranslationError:
        raise
    except Exception as exc:
        raise TableTranslationError(f"HTML 解析失败：{exc}") from exc


def _needs_translation(model_text: str) -> bool:
    plain = re.sub(r"⟦[MH]\d+⟧", "", model_text).strip()
    if not plain or _ENGLISH_RE.search(plain) is None:
        return False
    if _PURE_STANDARD_CODE_RE.fullmatch(plain):
        return False
    return True


def _build_cell(raw: _RawCell) -> PreparedCell:
    try:
        protected = protect_cell_text(
            raw.model_source,
            html_markers=raw.html_markers,
        )
    except CellProtectionError as exc:
        raise TableTranslationError(
            f"单元格 {raw.cell_id} 保护失败：{exc}"
        ) from exc
    if not _needs_translation(protected.model_text):
        return PreparedCell(
            cell_id=raw.cell_id,
            tag=raw.tag,
            content_start=raw.content_start,
            content_end=raw.content_end,
            original_inner_html=raw.original_inner_html,
            segments=(),
            work_ids=(),
            skipped=True,
        )
    segments = split_protected_segment(
        protected,
        CELL_SEGMENT_TOKEN_BUDGET,
        max_formulas=MAX_BATCH_FORMULAS,
    )
    if len(segments) == 1:
        work_ids = (raw.cell_id,)
    else:
        work_ids = tuple(
            f"{raw.cell_id}:segment-{index + 1:04d}"
            for index in range(len(segments))
        )
    return PreparedCell(
        cell_id=raw.cell_id,
        tag=raw.tag,
        content_start=raw.content_start,
        content_end=raw.content_end,
        original_inner_html=raw.original_inner_html,
        segments=segments,
        work_ids=work_ids,
        skipped=False,
    )


def prepare_table_translation(
    table_html: str,
) -> PreparedTableTranslation:
    parsed = _parse_html(table_html)
    return PreparedTableTranslation(
        original_html=table_html,
        cells=tuple(_build_cell(raw) for raw in parsed.cells),
        structure=parsed.structure,
    )
