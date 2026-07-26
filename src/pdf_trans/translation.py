from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pdf_trans.errors import TranslationContentError

SAMPLE_LIMIT = 3


class TextTranslator(Protocol):
    def translate(self, text: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    attempted_count: int
    success_count: int
    failed_count: int
    pending_count: int


def translate_items(
    items: list[dict[str, Any]],
    translator: TextTranslator,
    *,
    sample_limit: int = SAMPLE_LIMIT,
) -> tuple[list[dict[str, Any]], TranslationStats]:
    translated_items = copy.deepcopy(items)
    attempted = success = failed = pending = 0

    for item in translated_items:
        if item.get("type") != "text":
            continue
        if attempted >= sample_limit:
            item["translation_status"] = "pending"
            pending += 1
            continue

        attempted += 1
        try:
            translated_text = translator.translate(item["text"])
        except Exception as exc:
            item["translated_text"] = None
            item["translation_status"] = "failed"
            item["translation_error"] = str(exc) or exc.__class__.__name__
            failed += 1
        else:
            item["translated_text"] = translated_text
            item["translation_status"] = "success"
            success += 1

    return translated_items, TranslationStats(
        attempted_count=attempted,
        success_count=success,
        failed_count=failed,
        pending_count=pending,
    )


def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
) -> TranslationStats:
    try:
        items = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationContentError(f"无法读取规范化内容：{exc}") from exc
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise TranslationContentError("规范化内容的 JSON 顶层必须是对象数组")

    translated_items, stats = translate_items(items, translator)
    try:
        output.write_text(
            json.dumps(translated_items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise TranslationContentError(f"无法写入翻译结果：{exc}") from exc
    return stats
