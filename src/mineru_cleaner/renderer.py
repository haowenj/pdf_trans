from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import ContentListError


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_non_blank_string(item)]


def _render_item(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    if item_type == "text":
        text = item.get("text")
        if not _is_non_blank_string(text):
            return []
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
        parts.extend(_string_list(item.get(f"{item_type}_caption")))
        parts.extend(_string_list(item.get(f"{item_type}_footnote")))
        return parts

    if item_type == "table":
        parts = _string_list(item.get("table_caption"))
        table_body = item.get("table_body")
        if _is_non_blank_string(table_body):
            parts.append(table_body)
        parts.extend(_string_list(item.get("table_footnote")))
        return parts

    if item_type == "equation":
        text = item.get("text")
        return [text] if _is_non_blank_string(text) else []

    return []


def render_items(items: list[Any]) -> str:
    parts: list[str] = []
    for item in items:
        parts.extend(_render_item(item))
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def render_content_list_file(source: Path, output: Path) -> None:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    rendered = render_items(items)
    try:
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise ContentListError(f"无法写入 Markdown：{exc}") from exc
