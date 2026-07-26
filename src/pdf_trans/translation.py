from __future__ import annotations

import copy
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from pdf_trans.errors import TranslationContentError


class TextTranslator(Protocol):
    def translate(self, text: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    text_count: int
    model_call_count: int
    skipped_success_count: int
    success_count: int
    failed_count: int
    pending_count: int


CheckpointWriter = Callable[[Path, list[dict[str, Any]]], None]


def write_json_atomic(output: Path, items: list[dict[str, Any]]) -> None:
    output = Path(output)
    temporary_path: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(items, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except (OSError, TypeError, ValueError) as exc:
        raise TranslationContentError(f"无法写入翻译结果：{exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_object_array(path: Path, description: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationContentError(f"无法读取{description}：{exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise TranslationContentError(f"{description}的 JSON 顶层必须是对象数组")
    return raw


def _same_identity(normalized: dict[str, Any], existing: dict[str, Any]) -> bool:
    for field in ("type", "text"):
        if (field in normalized) != (field in existing):
            return False
        if normalized.get(field) != existing.get(field):
            return False
    return True


def _prepare_new_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(items)
    for item in prepared:
        if item.get("type") == "text":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
    return prepared


def _prepare_resumed_items(
    normalized: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    if len(normalized) != len(existing):
        raise TranslationContentError("断点文件与规范化内容的对象数量不一致")

    prepared: list[dict[str, Any]] = []
    skipped_success = 0
    for index, (current, old) in enumerate(zip(normalized, existing)):
        if not _same_identity(current, old):
            raise TranslationContentError(
                f"断点文件与规范化内容在第 {index} 个对象的 type 或 text 不一致"
            )
        item = copy.deepcopy(current)
        if current.get("type") != "text":
            prepared.append(item)
            continue

        status = old.get("translation_status")
        if status == "success":
            translated = old.get("translated_text")
            if not isinstance(translated, str) or not translated.strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 success 对象缺少有效 translated_text"
                )
            item["translated_text"] = translated
            item["translation_status"] = "success"
            item.pop("translation_error", None)
            skipped_success += 1
        elif status == "pending":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
        elif status == "failed":
            if old.get("translated_text") is not None or not isinstance(
                old.get("translation_error"), str
            ) or not old["translation_error"].strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 failed 对象状态字段无效"
                )
            item["translated_text"] = None
            item["translation_status"] = "failed"
            item["translation_error"] = old["translation_error"]
        else:
            raise TranslationContentError(
                f"断点文件第 {index} 个 text 对象的 translation_status 无效"
            )
        prepared.append(item)
    return prepared, skipped_success


def _translate_one(
    item: dict[str, Any], translator: TextTranslator, max_retries: int
) -> int:
    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        try:
            translated = translator.translate(item["text"])
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("模型返回空译文")
        except Exception as exc:
            last_error = exc
            continue
        item["translated_text"] = translated
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        return _ + 1

    assert last_error is not None
    item["translated_text"] = None
    item["translation_status"] = "failed"
    item["translation_error"] = str(last_error) or last_error.__class__.__name__
    return max_retries + 1


def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
    *,
    max_retries: int = 1,
    checkpoint_writer: CheckpointWriter | None = None,
) -> TranslationStats:
    if max_retries < 0:
        raise TranslationContentError("max_retries 不能为负数")
    source = Path(source)
    output = Path(output)
    normalized = _read_object_array(source, "规范化内容")
    if output.exists():
        if not output.is_file():
            raise TranslationContentError("断点文件不是普通文件")
        existing = _read_object_array(output, "断点翻译结果")
        items, skipped_success = _prepare_resumed_items(normalized, existing)
    else:
        items = _prepare_new_items(normalized)
        skipped_success = 0

    writer = checkpoint_writer or write_json_atomic
    writer(output, items)

    model_calls = 0
    for item in items:
        if item.get("type") != "text":
            continue
        if item.get("translation_status") == "success":
            continue
        model_calls += _translate_one(item, translator, max_retries)
        writer(output, items)

    counts = Counter(
        item.get("translation_status")
        for item in items
        if item.get("type") == "text"
    )
    pending = counts.get("pending", 0)
    if pending:
        raise TranslationContentError("正式翻译结束后仍存在 pending 对象")
    return TranslationStats(
        text_count=counts.total(),
        model_call_count=model_calls,
        skipped_success_count=skipped_success,
        success_count=counts.get("success", 0),
        failed_count=counts.get("failed", 0),
        pending_count=pending,
    )
