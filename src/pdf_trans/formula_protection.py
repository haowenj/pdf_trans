from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass


_SENTINEL_MARKER = "PDFTRANS_FORMULA"
_PLACEHOLDER_RE = re.compile(
    r"⟪PDFTRANS_FORMULA:([0-9a-f]{16}):(\d{4}):([0-9a-f]{64})⟫"
)


class FormulaProtectionError(ValueError):
    """A model response cannot be safely restored to source formulas."""


@dataclass(frozen=True)
class ProtectedText:
    model_text: str
    formula_ids: tuple[str, ...]


@dataclass(frozen=True)
class _FormulaRecord:
    formula_id: str
    original: str
    digest: str
    placeholder: str


def _is_escaped(text: str, position: int) -> bool:
    backslashes = 0
    position -= 1
    while position >= 0 and text[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _find_closing(text: str, start: int, delimiter: str) -> int | None:
    position = start
    while position < len(text):
        if (
            text.startswith(delimiter, position)
            and not _is_escaped(text, position)
            and (delimiter == "$$" or not text.startswith("$$", position))
        ):
            return position
        position += 1
    return None


def _formula_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    position = 0
    while position < len(text):
        if text.startswith("$$", position) and not _is_escaped(text, position):
            close = _find_closing(text, position + 2, "$$")
            if close is not None:
                spans.append((position, close + 2))
                position = close + 2
                continue
            position += 2
            continue
        if text[position] == "$" and not _is_escaped(text, position):
            close = _find_closing(text, position + 1, "$")
            if close is not None:
                spans.append((position, close + 1))
                position = close + 1
                continue
        position += 1
    return tuple(spans)


def _extract_formulas(text: str) -> tuple[str, ...]:
    return tuple(text[start:end] for start, end in _formula_spans(text))


class FormulaProtectionContext:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(8)
        if re.fullmatch(r"[0-9a-f]{16}", self._nonce) is None:
            raise ValueError(
                "formula protection nonce must be 16 lowercase hex digits"
            )
        self._records: dict[str, _FormulaRecord] = {}

    def protect(self, text: str) -> ProtectedText:
        if not isinstance(text, str):
            raise FormulaProtectionError("原文必须是字符串")
        if _SENTINEL_MARKER in text:
            raise FormulaProtectionError("原文包含公式保护保留哨兵")

        parts: list[str] = []
        formula_ids: list[str] = []
        cursor = 0
        for start, end in _formula_spans(text):
            parts.append(text[cursor:start])
            original = text[start:end]
            formula_id = f"{len(self._records) + 1:04d}"
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
            placeholder = (
                f"⟪{_SENTINEL_MARKER}:{self._nonce}:{formula_id}:{digest}⟫"
            )
            self._records[formula_id] = _FormulaRecord(
                formula_id=formula_id,
                original=original,
                digest=digest,
                placeholder=placeholder,
            )
            formula_ids.append(formula_id)
            parts.append(placeholder)
            cursor = end
        parts.append(text[cursor:])
        return ProtectedText("".join(parts), tuple(formula_ids))

    def restore(self, translated_text: str, protected: ProtectedText) -> str:
        if not isinstance(translated_text, str):
            raise FormulaProtectionError("模型译文必须是字符串")
        matches = list(_PLACEHOLDER_RE.finditer(translated_text))
        expected_ids = list(protected.formula_ids)
        actual_ids = [match.group(2) for match in matches]

        if translated_text.count(_SENTINEL_MARKER) != len(matches):
            raise FormulaProtectionError("公式占位符被篡改")
        duplicates = sorted(
            value for value in set(actual_ids) if actual_ids.count(value) > 1
        )
        if duplicates:
            raise FormulaProtectionError("公式占位符存在重复 ID")
        unknown = sorted(set(actual_ids) - set(expected_ids))
        if unknown:
            raise FormulaProtectionError("公式占位符存在新增 ID")
        missing = sorted(set(expected_ids) - set(actual_ids))
        if missing:
            raise FormulaProtectionError("公式占位符存在缺失 ID")
        if len(matches) != len(expected_ids):
            raise FormulaProtectionError("公式占位符数量不一致")

        for match in matches:
            record = self._records[match.group(2)]
            if (
                match.group(1) != self._nonce
                or match.group(3) != record.digest
                or match.group(0) != record.placeholder
            ):
                raise FormulaProtectionError("公式占位符被篡改")
        if actual_ids != expected_ids:
            raise FormulaProtectionError("公式占位符顺序变化")
        if _extract_formulas(translated_text):
            raise FormulaProtectionError("模型返回中存在新增公式")

        records_by_placeholder = {
            self._records[formula_id].placeholder: self._records[formula_id]
            for formula_id in expected_ids
        }

        def replace(match: re.Match[str]) -> str:
            record = records_by_placeholder[match.group(0)]
            digest = hashlib.sha256(record.original.encode("utf-8")).hexdigest()
            if digest != record.digest:
                raise FormulaProtectionError("原始公式哈希校验失败")
            return record.original

        restored = _PLACEHOLDER_RE.sub(replace, translated_text)
        restored_formulas = _extract_formulas(restored)
        originals = tuple(
            self._records[formula_id].original for formula_id in expected_ids
        )
        if restored_formulas != originals:
            raise FormulaProtectionError("恢复后的公式内容不一致")
        if _SENTINEL_MARKER in restored:
            raise FormulaProtectionError("恢复后仍存在公式保护哨兵")
        return restored
