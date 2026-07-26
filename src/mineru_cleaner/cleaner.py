from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import ContentListError


@dataclass(frozen=True)
class CleaningStats:
    before_count: int
    filtered_count: int
    after_count: int


def _should_filter(item: Any) -> bool:
    if not isinstance(item, dict):
        return False

    item_type = item.get("type")
    if item_type in {"header", "footer", "page_number"}:
        return True

    text = item.get("text")
    return item_type == "text" and isinstance(text, str) and not text.strip()


def clean_items(items: list[Any]) -> tuple[list[Any], CleaningStats]:
    cleaned = [item for item in items if not _should_filter(item)]
    before_count = len(items)
    after_count = len(cleaned)
    return cleaned, CleaningStats(
        before_count=before_count,
        filtered_count=before_count - after_count,
        after_count=after_count,
    )


def clean_content_list_file(source: Path, output: Path) -> CleaningStats:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    cleaned, stats = clean_items(items)
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入清洗结果：{exc}") from exc
    return stats
