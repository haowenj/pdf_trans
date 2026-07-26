from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf_trans.errors import ContentListError

CROSS_PAGE_REASON = (
    "相邻 text 位于连续页面，且前一个 text "
    "未以完整句结束符 . ! ? : ; 结尾"
)
_SENTENCE_ENDINGS = (".", "!", "?", ":", ";")


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def detect_cross_page_candidates(
    items: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for previous_index in range(len(items) - 1):
        next_index = previous_index + 1
        previous = items[previous_index]
        next_item = items[next_index]

        if not isinstance(previous, dict) or not isinstance(next_item, dict):
            continue
        if previous.get("type") != "text" or next_item.get("type") != "text":
            continue

        previous_page_idx = previous.get("page_idx")
        next_page_idx = next_item.get("page_idx")
        if type(previous_page_idx) is not int or type(next_page_idx) is not int:
            continue
        if next_page_idx != previous_page_idx + 1:
            continue

        previous_text = previous.get("text")
        next_text = next_item.get("text")
        if not _is_non_blank_string(previous_text):
            continue
        if not _is_non_blank_string(next_text):
            continue
        if previous_text.rstrip().endswith(_SENTENCE_ENDINGS):
            continue

        candidates.append(
            {
                "previous_index": previous_index,
                "next_index": next_index,
                "previous_page_idx": previous_page_idx,
                "next_page_idx": next_page_idx,
                "previous_text": previous_text,
                "next_text": next_text,
                "reason": CROSS_PAGE_REASON,
            }
        )

    return candidates


def detect_cross_page_candidates_file(source: Path, output: Path) -> int:
    try:
        with source.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentListError(f"无法读取 content list：{exc}") from exc

    if not isinstance(items, list):
        raise ContentListError("content list 的 JSON 顶层必须是数组")

    candidates = detect_cross_page_candidates(items)
    write_cross_page_candidates_file(candidates, output)
    return len(candidates)


def write_cross_page_candidates_file(
    candidates: list[dict[str, Any]],
    output: Path,
) -> None:
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(candidates, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise ContentListError(f"无法写入跨页候选报告：{exc}") from exc
