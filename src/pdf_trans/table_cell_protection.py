from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape

from pdf_trans.formula_scanner import scan_formula_spans


_MARKER_RE = re.compile(r"⟦([MH])(\d+)⟧")
_ATOMIC_MARKER_RE = re.compile(r"⟦[MH]\d+⟧")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？;；])\s+")
_WHITESPACE_RE = re.compile(r"\s+")
_HARD_HTML_BOUNDARY_RE = re.compile(
    r"^<\s*(?:br\b|/?li\b|/?p\b)",
    re.IGNORECASE,
)


class CellProtectionError(ValueError):
    """A translated cell cannot be restored without protected-data changes."""


@dataclass(frozen=True)
class CellMarker:
    marker_id: str
    placeholder: str
    original: str
    sha256: str


@dataclass(frozen=True)
class ProtectedCellSegment:
    model_text: str
    formula_markers: tuple[CellMarker, ...]
    html_markers: tuple[CellMarker, ...] = ()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_formulas(text: str) -> tuple[str, ...]:
    return tuple(span.raw_formula for span in scan_formula_spans(text))


def _family_name(family: str) -> str:
    return "公式" if family == "M" else "结构"


def _validate_marker_definitions(
    markers: tuple[CellMarker, ...],
    family: str,
) -> None:
    for number, value in enumerate(markers):
        expected_id = f"{family}{number}"
        if (
            value.marker_id != expected_id
            or value.placeholder != f"⟦{expected_id}⟧"
        ):
            raise CellProtectionError(
                f"{_family_name(family)}占位符本地定义无效"
            )
        if "⟦M" in value.original or "⟦H" in value.original:
            raise CellProtectionError("原文包含保留的占位符")


def _validate_marker_family(
    text: str,
    family: str,
    expected: tuple[CellMarker, ...],
) -> None:
    name = _family_name(family)
    matches = [
        match
        for match in _MARKER_RE.finditer(text)
        if match.group(1) == family
    ]
    actual_ids = [f"{family}{match.group(2)}" for match in matches]
    expected_ids = [value.marker_id for value in expected]

    duplicates = sorted(
        value for value in set(actual_ids) if actual_ids.count(value) > 1
    )
    if duplicates:
        raise CellProtectionError(f"{name}占位符存在重复 ID")
    additions = sorted(set(actual_ids) - set(expected_ids))
    if additions:
        raise CellProtectionError(f"{name}占位符存在新增 ID")
    missing = sorted(set(expected_ids) - set(actual_ids))
    if missing:
        raise CellProtectionError(f"{name}占位符存在缺失 ID")
    if len(actual_ids) != len(expected_ids):
        raise CellProtectionError(f"{name}占位符数量不一致")
    if actual_ids != expected_ids:
        raise CellProtectionError(f"{name}占位符顺序变化")


def protect_cell_text(
    text: str,
    html_markers: tuple[CellMarker, ...] = (),
) -> ProtectedCellSegment:
    if not isinstance(text, str):
        raise CellProtectionError("单元格文本必须是字符串")
    if "⟦M" in text:
        raise CellProtectionError("原文包含保留的公式占位符")
    _validate_marker_definitions(html_markers, "H")
    _validate_marker_family(text, "H", html_markers)

    pieces: list[str] = []
    markers: list[CellMarker] = []
    cursor = 0
    for number, span in enumerate(scan_formula_spans(text)):
        original = span.raw_formula
        marker_id = f"M{number}"
        placeholder = f"⟦{marker_id}⟧"
        pieces.extend((text[cursor : span.start], placeholder))
        markers.append(
            CellMarker(
                marker_id=marker_id,
                placeholder=placeholder,
                original=original,
                sha256=_digest(original),
            )
        )
        cursor = span.end
    pieces.append(text[cursor:])
    return ProtectedCellSegment(
        model_text="".join(pieces),
        formula_markers=tuple(markers),
        html_markers=html_markers,
    )


def _reject_malformed_markers(text: str) -> None:
    without_valid_markers = _MARKER_RE.sub("", text)
    if (
        "⟦" in without_valid_markers
        or "⟧" in without_valid_markers
        or "⟦M" in without_valid_markers
        or "⟦H" in without_valid_markers
    ):
        raise CellProtectionError("占位符被篡改")


def _escape_unprotected_text(text: str) -> str:
    return "".join(
        part
        if _MARKER_RE.fullmatch(part)
        else escape(part, quote=False)
        for part in re.split(r"(⟦[MH]\d+⟧)", text)
    )


def restore_cell_segment(
    translated_text: str,
    protected: ProtectedCellSegment,
) -> str:
    if not isinstance(translated_text, str) or not translated_text.strip():
        raise CellProtectionError("单元格译文不能为空")
    _reject_malformed_markers(translated_text)
    if "$" in translated_text:
        raise CellProtectionError("模型译文新增了未保护公式符号")
    _validate_marker_family(
        translated_text,
        "M",
        protected.formula_markers,
    )
    _validate_marker_family(
        translated_text,
        "H",
        protected.html_markers,
    )

    restored = _escape_unprotected_text(translated_text)
    for value in (*protected.formula_markers, *protected.html_markers):
        if _digest(value.original) != value.sha256:
            raise CellProtectionError(f"{value.marker_id} 本地哈希校验失败")
        restored = restored.replace(value.placeholder, value.original, 1)

    if _MARKER_RE.search(restored):
        raise CellProtectionError("占位符被篡改或未完全恢复")
    restored_formulas = _extract_formulas(restored)
    originals = tuple(value.original for value in protected.formula_markers)
    if restored_formulas != originals:
        raise CellProtectionError("恢复后的公式内容不一致")
    return restored


def estimate_model_tokens(text: str) -> int:
    ascii_count = sum(ord(character) < 128 for character in text)
    non_ascii_count = len(text) - ascii_count
    return max(1, (ascii_count + 3) // 4 + non_ascii_count)


def _marker_subset(
    text: str,
    markers: tuple[CellMarker, ...],
) -> tuple[CellMarker, ...]:
    return tuple(value for value in markers if value.placeholder in text)


def _safe_chunks(text: str) -> list[str]:
    marker_spans = list(_ATOMIC_MARKER_RE.finditer(text))
    blocked = {
        offset
        for match in marker_spans
        for offset in range(match.start() + 1, match.end())
    }
    boundaries: set[int] = {
        match.end() for match in marker_spans
    }
    boundaries.update(
        match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(text)
    )
    boundaries.update(match.end() for match in _WHITESPACE_RE.finditer(text))
    safe_boundaries = sorted(
        boundary
        for boundary in boundaries
        if 0 < boundary < len(text) and boundary not in blocked
    )
    chunks: list[str] = []
    cursor = 0
    for boundary in safe_boundaries:
        if boundary > cursor:
            chunks.append(text[cursor:boundary])
            cursor = boundary
    if cursor < len(text):
        chunks.append(text[cursor:])
    return chunks or [text]


def _formula_count(text: str) -> int:
    return sum(
        match.group(1) == "M" for match in _MARKER_RE.finditer(text)
    )


def _ends_at_hard_html_boundary(
    text: str,
    html_markers: tuple[CellMarker, ...],
) -> bool:
    for value in html_markers:
        if (
            text.endswith(value.placeholder)
            and _HARD_HTML_BOUNDARY_RE.match(value.original)
        ):
            return True
    return False


def split_protected_segment(
    protected: ProtectedCellSegment,
    max_tokens: int,
    max_formulas: int = 24,
) -> tuple[ProtectedCellSegment, ...]:
    if max_tokens <= 0 or max_formulas <= 0:
        raise ValueError("segment 限制必须大于 0")
    if (
        estimate_model_tokens(protected.model_text) <= max_tokens
        and len(protected.formula_markers) <= max_formulas
    ):
        return (protected,)

    grouped: list[str] = []
    current = ""
    for chunk in _safe_chunks(protected.model_text):
        candidate = current + chunk
        if current and (
            estimate_model_tokens(candidate) > max_tokens
            or _formula_count(candidate) > max_formulas
        ):
            grouped.append(current)
            current = chunk
        else:
            current = candidate
        if _ends_at_hard_html_boundary(current, protected.html_markers):
            grouped.append(current)
            current = ""
    if current:
        grouped.append(current)

    return tuple(
        ProtectedCellSegment(
            model_text=value,
            formula_markers=_marker_subset(
                value,
                protected.formula_markers,
            ),
            html_markers=_marker_subset(value, protected.html_markers),
        )
        for value in grouped
        if value
    )
