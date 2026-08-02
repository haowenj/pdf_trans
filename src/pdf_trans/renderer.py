from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf_trans.errors import ContentListError
from pdf_trans.formula_audit import FormulaAuditReport
from pdf_trans.formula_scanner import (
    FormulaReplacement,
    replace_equation_formula,
    replace_formula_spans,
    replace_table_formula_spans,
)


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_non_blank_string(item)]


def _render_auxiliary_list(
    item: dict[str, Any],
    source_field: str,
    translated_field: str,
) -> list[str]:
    raw_values = item.get(source_field)
    if not isinstance(raw_values, list):
        return []
    translated_values = item.get(translated_field)
    state = item.get("auxiliary_translation")
    entries = state.get(source_field) if isinstance(state, dict) else None
    rendered: list[str] = []
    for index, raw_value in enumerate(raw_values):
        value = raw_value
        if (
            isinstance(translated_values, list)
            and isinstance(entries, list)
            and index < len(translated_values)
            and index < len(entries)
            and isinstance(entries[index], dict)
            and entries[index].get("translation_status") == "success"
            and _is_non_blank_string(translated_values[index])
        ):
            value = translated_values[index]
        if _is_non_blank_string(value):
            rendered.append(value)
    return rendered


def _render_item(
    item: Any,
    replacements: FormulaReplacement,
) -> list[str]:
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    if item_type == "text":
        text = item.get("text")
        translated_text = item.get("translated_text")
        if (
            item.get("translation_status") == "success"
            and _is_non_blank_string(translated_text)
        ):
            text = translated_text
        if not _is_non_blank_string(text):
            return []
        if replacements:
            text = replace_formula_spans(text, replacements)
        text_level = item.get("text_level")
        if type(text_level) is int and text_level in {1, 2}:
            return [f"{'#' * text_level} {text}"]
        return [text]

    if item_type == "ref_text":
        text = item.get("text")
        return [text] if _is_non_blank_string(text) else []

    if item_type in {"image", "chart"}:
        parts: list[str] = []
        img_path = item.get("img_path")
        if _is_non_blank_string(img_path):
            parts.append(f"![]({img_path})")
        parts.extend(
            _render_auxiliary_list(
                item,
                f"{item_type}_caption",
                f"translated_{item_type}_caption",
            )
        )
        parts.extend(
            _render_auxiliary_list(
                item,
                f"{item_type}_footnote",
                f"translated_{item_type}_footnote",
            )
        )
        return parts

    if item_type == "table":
        parts = _render_auxiliary_list(
            item,
            "table_caption",
            "translated_table_caption",
        )
        table_body = item.get("table_body")
        translated_table_body = item.get("translated_table_body")
        if (
            item.get("translation_status") == "success"
            and _is_non_blank_string(translated_table_body)
        ):
            table_body = translated_table_body
        if _is_non_blank_string(table_body):
            if replacements:
                table_body = replace_table_formula_spans(
                    table_body,
                    replacements,
                )
            parts.append(table_body)
        parts.extend(
            _render_auxiliary_list(
                item,
                "table_footnote",
                "translated_table_footnote",
            )
        )
        return parts

    if item_type == "equation":
        text = item.get("text")
        if not _is_non_blank_string(text):
            return []
        if replacements:
            text = replace_equation_formula(text, replacements)
        return [text]

    return []


def render_items(
    items: list[Any],
    *,
    formula_audit: FormulaAuditReport | None = None,
) -> str:
    replacements = (
        {}
        if formula_audit is None
        else formula_audit.accepted_replacements
    )
    parts: list[str] = []
    for item in items:
        parts.extend(_render_item(item, replacements))
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def render_content_list_file(
    source: Path,
    output: Path,
    *,
    formula_audit: FormulaAuditReport | None = None,
) -> None:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    rendered = render_items(
        items,
        formula_audit=formula_audit,
    )
    try:
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise ContentListError(f"无法写入 Markdown：{exc}") from exc
