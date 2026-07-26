from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_trans.errors import ContentListError


@dataclass(frozen=True)
class ContentStats:
    type_counts: dict[str, int]
    text_level_count: int
    text_level_counts: dict[int, int]
    page_idx_counts: dict[int, int]


@dataclass(frozen=True)
class CleaningStats:
    before_count: int
    filtered_count: int
    after_count: int
    content_stats: ContentStats


def _should_filter(item: Any) -> bool:
    if not isinstance(item, dict):
        return False

    item_type = item.get("type")
    if item_type in {"header", "footer", "page_number"}:
        return True

    text = item.get("text")
    return item_type == "text" and isinstance(text, str) and not text.strip()


def summarize_items(items: list[Any]) -> ContentStats:
    type_counts: Counter[str] = Counter()
    text_level_counts: Counter[int] = Counter()
    page_idx_counts: Counter[int] = Counter()

    for item in items:
        if not isinstance(item, dict):
            continue

        item_type = item.get("type")
        if isinstance(item_type, str):
            type_counts[item_type] += 1

        if item_type == "text" and type(item.get("text_level")) is int:
            text_level_counts[item["text_level"]] += 1

        page_idx = item.get("page_idx")
        if type(page_idx) is int:
            page_idx_counts[page_idx] += 1

    return ContentStats(
        type_counts=dict(sorted(type_counts.items())),
        text_level_count=sum(text_level_counts.values()),
        text_level_counts=dict(sorted(text_level_counts.items())),
        page_idx_counts=dict(sorted(page_idx_counts.items())),
    )


def clean_items(items: list[Any]) -> tuple[list[Any], CleaningStats]:
    cleaned = [item for item in items if not _should_filter(item)]
    before_count = len(items)
    after_count = len(cleaned)
    return cleaned, CleaningStats(
        before_count=before_count,
        filtered_count=before_count - after_count,
        after_count=after_count,
        content_stats=summarize_items(cleaned),
    )


def clean_content_list_file_with_items(
    source: Path,
    output: Path,
) -> tuple[list[Any], CleaningStats]:
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
    return cleaned, stats


def clean_content_list_file(source: Path, output: Path) -> CleaningStats:
    _, stats = clean_content_list_file_with_items(source, output)
    return stats
